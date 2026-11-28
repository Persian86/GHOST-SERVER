"""The household modes every screen should know about, so Ghost can dress for them:
a guard's cap in Security Guard mode, the chosen hat when Ghost is Unplugged.

    {"type": "modes", "guard": bool, "unplugged": bool}

Sent to each app when it connects and to every connected app whenever a mode changes.
"""
from __future__ import annotations

from . import store


def snapshot() -> dict:
    from . import guard
    return {"type": "modes", "guard": bool(guard._status.get("on")), "unplugged": bool(store.get("unplugged"))}


def broadcast() -> None:
    """Tell every connected app (safe from any thread; never raises)."""
    try:
        from . import access
        from .hub import hub
        msg = snapshot()
        for did in list(hub.conns):
            if access.limited(did):
                continue
            try:
                hub.send_sync(did, msg)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
