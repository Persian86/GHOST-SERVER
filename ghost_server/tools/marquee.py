"""Marquee media server, through its /ext/v1 assistant API."""
from __future__ import annotations

from . import Ctx, tool
from .. import store


def _req(method: str, path: str, **kw):
    import httpx
    key = str(store.get("marquee_key") or "").strip()
    if not key:
        raise RuntimeError("Marquee isn't connected — make an 'assistant' app key in Marquee → Settings → "
                           "Connected apps and paste it on the Ghost Server admin page")
    url = str(store.get("marquee_url") or "").rstrip("/")
    return httpx.request(method, url + path, timeout=15, trust_env=False,
                         headers={"Authorization": f"Bearer {key}"}, **kw)


@tool("marquee", """Control the user's Marquee media server (their own movies, TV and music on their TVs).
'command' passes a natural request straight to Marquee: 'play Bluey on the lounge TV', 'pause on the
lounge TV', 'what should we watch tonight', 'start movie night', 'screen time for Zoey'. 'continue' lists
what's in progress; 'new' lists recent arrivals. Use this, not YouTube, for their movies and shows.""",
      {"type": "OBJECT", "properties": {
          "action": {"type": "STRING", "description": "command | continue | new"},
          "text": {"type": "STRING", "description": "The request for 'command'"}},
       "required": ["action"]})
def marquee(args: dict, ctx: Ctx) -> str:
    act = str(args.get("action") or "command").lower()
    try:
        if act == "command":
            text = str(args.get("text") or "").strip()
            if not text:
                return "Say what to do, e.g. 'play Bluey on the lounge TV'."
            d = _req("POST", "/ext/v1/assistant", json={"text": text, "device": ctx.device_name or "Ghost"}).json()
            return d.get("speech") or "Done."
        rows = _req("GET", "/ext/v1/widget").json().get("continueWatching" if act == "continue" else "newArrivals") or []
        if not rows:
            return "Nothing to show."
        return "; ".join(f"{x.get('title')} — {x.get('subtitle', '')}"
                         + (f" ({int(x['progress'] * 100)}%)" if x.get("progress") else "") for x in rows[:8])
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"Couldn't reach Marquee ({type(e).__name__}: {e})."
