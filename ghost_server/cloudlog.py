"""Who actually answered — so "am I really unplugged?" has proof, not a promise.

Every Claude request, Gemini voice session and local-model answer is counted here.
While Ghost is Unplugged, any attempt to reach Claude or Gemini is BLOCKED (it raises
before a byte leaves the house) and counted as blocked. The apps, the admin page and
"Ghost, are you unplugged?" all read the same numbers:

    {"unplugged": true, "since": …, "last_answer_by": "local", "local_model": "qwen3.5",
     "local_answers": 12, "cloud_calls": 0, "blocked": 0, "last_cloud": {…} | null}
"""
from __future__ import annotations

import threading
import time

from . import store

_lock = threading.Lock()
_s = {"since": 0.0, "local": 0, "cloud": 0, "blocked": 0, "last_by": "", "last_at": 0.0,
      "local_model": "", "last_cloud": None}


class Unplugged(RuntimeError):
    """Raised instead of contacting the cloud while Ghost is unplugged."""


def reset() -> None:
    """Counting starts again (when Unplugged is switched on)."""
    with _lock:
        _s.update(since=time.time(), local=0, cloud=0, blocked=0, last_cloud=None)


def cloud(provider: str, what: str = "") -> None:
    """Call right before contacting Claude or Gemini. Blocks it while unplugged."""
    now = time.time()
    with _lock:
        if store.get("unplugged"):
            _s["blocked"] += 1
            _s["last_cloud"] = {"provider": provider, "what": what, "at": now, "blocked": True}
            raise Unplugged(f"Ghost is unplugged — {provider} was not contacted")
        _s["cloud"] += 1
        _s["last_by"], _s["last_at"] = provider, now
        _s["last_cloud"] = {"provider": provider, "what": what, "at": now, "blocked": False}


def local(model: str = "") -> None:
    with _lock:
        _s["local"] += 1
        _s["last_by"], _s["last_at"], _s["local_model"] = "local", time.time(), model or _s["local_model"]


def status() -> dict:
    with _lock:
        out = dict(_s)
    out["unplugged"] = bool(store.get("unplugged"))
    out["last_answer_by"] = out.pop("last_by")
    out["local_answers"], out["cloud_calls"] = out.pop("local"), out.pop("cloud")
    return out


def summary() -> str:
    """One sentence for voice and the Settings screen."""
    s = status()
    who = {"local": "the local AI on the AI PC", "claude": "Claude (cloud)", "gemini": "Gemini (cloud)"}.get(
        s["last_answer_by"], "nobody yet")
    if s["unplugged"]:
        tail = f" {s['blocked']} cloud request{'s were' if s['blocked'] != 1 else ' was'} blocked." if s["blocked"] else ""
        return (f"Unplugged: {s['local_answers']} answer{'s' if s['local_answers'] != 1 else ''} from the local AI, "
                f"{s['cloud_calls']} from the cloud since switching. Last answer: {who}.{tail}")
    return f"Plugged in: Claude thinks and Gemini talks. Last answer: {who}."
