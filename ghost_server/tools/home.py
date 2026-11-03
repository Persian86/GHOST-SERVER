"""Home Assistant — ported from Ghost Desktop's plugin, settings from the server store."""
from __future__ import annotations

import re

from . import Ctx, tool
from .. import store


def _done(what, **facts):
    return f"Done ({what}). " + "; ".join(f"{k}: {v}" for k, v in facts.items())


def _failed(what, why):
    return f"Couldn't {what}: {why}"


_SENSITIVE = {("lock", "unlock"), ("lock", "open"), ("alarm_control_panel", "alarm_disarm"),
              ("cover", "open_cover")}
_COLORS = {"warm white": {"color_temp_kelvin": 2700}, "cool white": {"color_temp_kelvin": 5500},
           "white": {"color_temp_kelvin": 4000}}


# ─────────────────────────────────────────────────────────────────────────────
def _cfg(cfg: dict | None = None) -> dict:
    if cfg is not None:
        return cfg
    return {"url": store.get("ha_url"), "token": store.get("ha_token")}


def _req(method: str, path: str, cfg: dict | None = None, **kw):
    import httpx
    c = _cfg(cfg)
    url = str(c.get("url") or "").rstrip("/")
    token = str(c.get("token") or "").strip()
    if not url or not token:
        raise RuntimeError("Home Assistant isn't set up — add the address and token on the Ghost Server admin page")
    return httpx.request(method, url + path, timeout=10, trust_env=False,
                         headers={"Authorization": f"Bearer {token}"}, **kw)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower().replace("_", " ")).split())


def match_entities(states: list[dict], target: str, domains: tuple = ()) -> list[dict]:
    """Best entities for a spoken name. An exact name wins; otherwise every
    entity whose name contains all the words (so 'lounge lights' can mean the
    two lamps in the lounge)."""
    q = _norm(target)
    q = re.sub(r"\b(the|my|please)\b", " ", q).split()
    plural_light = any(w in ("lights", "lamps") for w in q)
    words = [w.rstrip("s") if w in ("lights", "lamps", "fans", "plugs", "blinds") else w for w in q]
    cands = [s for s in states if not domains or s["entity_id"].split(".")[0] in domains]

    def name(s):
        return _norm(s.get("attributes", {}).get("friendly_name") or s["entity_id"].split(".", 1)[1])

    exact = [s for s in cands if name(s) == " ".join(q) or s["entity_id"] == target.strip()]
    if exact:
        return exact[:1]
    generic = {"light", "lamp", "fan", "plug", "switch", "blind"}
    key = [w for w in words if w not in generic] or words
    hits = [s for s in cands if all(w in name(s) or w in s["entity_id"] for w in key)]
    if plural_light:
        hits = [s for s in hits if s["entity_id"].startswith("light.")] or hits
    # Prefer lights/switches for on/off talk, and drop helpers
    hits.sort(key=lambda s: (s["entity_id"].split(".")[0] not in ("light", "switch", "fan", "climate", "cover",
                                                                  "lock", "media_player"), len(name(s))))
    return hits if plural_light else hits[:1]


def _describe(s: dict) -> str:
    a = s.get("attributes", {})
    name = a.get("friendly_name") or s["entity_id"]
    st = s.get("state")
    unit = a.get("unit_of_measurement") or ""
    extra = []
    if s["entity_id"].startswith("light.") and st == "on" and a.get("brightness") is not None:
        extra.append(f"{round(a['brightness'] / 2.55)}%")
    if s["entity_id"].startswith("climate."):
        if a.get("current_temperature") is not None:
            extra.append(f"room {a['current_temperature']}°")
        if a.get("temperature") is not None:
            extra.append(f"set to {a['temperature']}°")
    return f"{name}: {st}{unit and ' ' + unit}" + (f" ({', '.join(extra)})" if extra else "")


def summary(states: list[dict]) -> str:
    on = [s for s in states if s["entity_id"].split(".")[0] in ("light", "fan", "switch")
          and s["state"] == "on" and not s["entity_id"].endswith(("_led", "_indicator", "_lock"))]
    open_ = [s for s in states if (s["entity_id"].startswith("binary_sensor.") and s["state"] == "on"
                                   and s.get("attributes", {}).get("device_class") in ("door", "window", "garage_door", "opening"))
             or (s["entity_id"].startswith("lock.") and s["state"] == "unlocked")
             or (s["entity_id"].startswith("cover.") and s["state"] == "open")]
    clim = [s for s in states if s["entity_id"].startswith("climate.") and s["state"] not in ("off", "unavailable")]
    low = [s for s in states if s.get("attributes", {}).get("device_class") == "battery"
           and str(s["state"]).replace(".", "", 1).isdigit() and float(s["state"]) < 15]
    parts = []
    parts.append(f"{len(on)} on: " + ", ".join(s["attributes"].get("friendly_name", s["entity_id"]) for s in on[:8])
                 if on else "everything is off")
    if open_:
        parts.append("open/unlocked: " + ", ".join(s["attributes"].get("friendly_name", s["entity_id"]) for s in open_))
    if clim:
        parts.append("climate: " + "; ".join(_describe(s) for s in clim[:3]))
    if low:
        parts.append("low batteries: " + ", ".join(s["attributes"].get("friendly_name", s["entity_id"]) for s in low[:5]))
    return ". ".join(parts)


def _call(domain: str, service: str, data: dict) -> int:
    return _req("POST", f"/api/services/{domain}/{service}", json=data).status_code


def _ask(text: str) -> str:
    r = _req("POST", "/api/conversation/process", json={"text": text, "language": "en"})
    if r.status_code != 200:
        return f"Home Assistant couldn't handle that (HTTP {r.status_code})."
    speech = (((r.json().get("response") or {}).get("speech") or {}).get("plain") or {}).get("speech")
    return speech or "Done."


def _run(parameters: dict, ctx: Ctx) -> str:
    act = str(parameters.get("action") or "").lower().strip()
    target = str(parameters.get("target") or "").strip()
    try:
        if act == "ask":
            return _ask(str(parameters.get("text") or target))
        states = _req("GET", "/api/states").json()

        if act == "status":
            if not target or target.lower() in ("summary", "home", "house", "everything"):
                return _done("home summary", summary=summary(states))
            hits = match_entities(states, target)
            if not hits:
                return _ask(f"what is the status of {target}")
            return _done("check device", status="; ".join(_describe(s) for s in hits))

        if act == "list":
            dom = str(parameters.get("domain") or target or "light").lower().rstrip("s")
            rows = [_describe(s) for s in states if s["entity_id"].startswith(dom + ".")]
            return _done(f"list {dom}", devices="; ".join(rows[:40]) or "none")

        if act in ("scene", "script"):
            hits = match_entities(states, target, (act,))
            if not hits:
                return _failed(f"run {act}", f"no {act} called '{target}'")
            code = _call(act, "turn_on", {"entity_id": hits[0]["entity_id"]})
            return _done(f"run {act}", name=hits[0]["attributes"].get("friendly_name"), http=code)

        if act in ("on", "off", "toggle", "set", "open", "close", "lock", "unlock"):
            hits = match_entities(states, target)
            if not hits:
                return _ask(f"{'turn ' + act if act in ('on', 'off') else act} {target}")
            done = []
            for s in hits:
                eid = s["entity_id"]
                dom = eid.split(".")[0]
                data = {"entity_id": eid}
                if act == "set":
                    if dom == "climate" and parameters.get("temperature") is not None:
                        service = "set_temperature"; data["temperature"] = float(parameters["temperature"])
                    else:
                        service = "turn_on"
                        if parameters.get("brightness") is not None:
                            data["brightness_pct"] = max(0, min(100, int(parameters["brightness"])))
                        col = str(parameters.get("color") or "").lower().strip()
                        if col:
                            data.update(_COLORS.get(col, {"color_name": col.replace(" ", "")}))
                elif dom == "lock":
                    service = "unlock" if act in ("off", "unlock", "open") else "lock"
                elif dom == "cover":
                    service = "open_cover" if act in ("on", "open") else "close_cover" if act in ("off", "close") else "toggle"
                elif dom in ("scene", "script"):
                    service = "turn_on"
                else:
                    service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle",
                               "open": "turn_on", "close": "turn_off"}.get(act, "turn_on")
                    if dom in ("sensor", "binary_sensor"):
                        return _done("check device", status=_describe(s))
                    if dom not in ("light", "switch", "fan", "climate", "media_player", "input_boolean",
                                   "humidifier", "vacuum", "automation"):
                        dom = "homeassistant"
                name = s["attributes"].get("friendly_name", eid)
                if (eid.split(".")[0], service) in _SENSITIVE or (
                        eid.startswith("cover.") and service == "open_cover"
                        and s["attributes"].get("device_class") in ("garage", "door", "gate")):
                    if not parameters.get("confirmed"):
                        return (f"CONFIRMATION NEEDED: {service.replace('_', ' ')} {name} is a security action. "
                                "Ask the user to confirm out loud, then call again with confirmed=true.")
                _call(dom, service, data)
                done.append(name)
            # Read back what actually happened
            after = {s["entity_id"]: s for s in _req("GET", "/api/states").json()}
            seen = "; ".join(_describe(after[s["entity_id"]]) for s in hits if s["entity_id"] in after)
            return _done(f"{act} {target}", now=seen or ", ".join(done))
        return _failed("home_assistant", f"unknown action '{act}'")
    except RuntimeError as e:
        return _failed("home_assistant", str(e))
    except Exception as e:
        return _failed("home_assistant", f"couldn't reach Home Assistant ({type(e).__name__}: {e})")


@tool("home_assistant", """Control and check the smart home via Home Assistant: 'on', 'off', 'toggle'
(target = device or room, e.g. 'lounge lights'), 'set' (brightness 0-100, temperature, colour), 'status'
(a device, or 'summary' for what's on/open), 'scene' / 'script' by name, 'list' a domain, or 'ask' to
pass any sentence to Home Assistant's own assistant. Unlocking/opening security devices needs the
user's spoken confirmation first.""", {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "on | off | toggle | set | status | scene | script | list | ask"},
            "target": {"type": "STRING", "description": "Device, room, scene or script name"},
            "brightness": {"type": "INTEGER", "description": "0-100 for set"},
            "temperature": {"type": "NUMBER", "description": "Target temperature for set"},
            "color": {"type": "STRING", "description": "Colour name for set"},
            "text": {"type": "STRING", "description": "Full sentence for 'ask'"},
            "domain": {"type": "STRING", "description": "For list: light, switch, climate, cover, lock, sensor"},
            "confirmed": {"type": "BOOLEAN", "description": "Only true after the user said yes to a security action"},
        },
        "required": ["action"],
    })
def home_assistant(args: dict, ctx: Ctx) -> str:
    return _run(args, ctx)
