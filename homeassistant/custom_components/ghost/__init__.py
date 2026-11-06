"""Ghost for Home Assistant.

* A conversation agent: pick "Ghost" in a voice assistant and every Assist
  satellite (the room screens) talks to Ghost, which knows which room it's in.
* A live link to Ghost Server, so Ghost can speak in a room ("tell the bedroom
  it's time to get up") through the satellites there.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_KEY, CONF_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    link = GhostLink(hass, entry.data[CONF_URL], entry.data[CONF_KEY])
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = link
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    link.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    link: GhostLink = hass.data[DOMAIN].pop(entry.entry_id)
    await link.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _area_name(hass: HomeAssistant, entry) -> str:
    devs, areas = dr.async_get(hass), ar.async_get(hass)
    area_id = entry.area_id
    if not area_id and entry.device_id and (d := devs.async_get(entry.device_id)):
        area_id = d.area_id
    area = areas.async_get_area(area_id) if area_id else None
    return area.name if area else ""


def speakers(hass: HomeAssistant) -> list[tuple[str, str, str]]:
    """Everything Ghost can speak through, as (kind, entity_id, area name):

    * "satellite": Assist satellites (the Ghost room screens, Voice PE)
    * "alexa":     Echo devices from Home Assistant's built-in "Alexa Devices" integration
    * "alexa_media": Echo devices from the "Alexa Media Player" add-on (HACS)
    """
    out = []
    for e in er.async_get(hass).entities.values():
        if e.disabled_by:
            continue
        if e.domain == "assist_satellite":
            kind = "satellite"
        elif e.platform == "alexa_devices" and e.domain == "notify" and str(e.unique_id).endswith("-announce"):
            kind = "alexa"
        elif e.platform == "alexa_media" and e.domain == "media_player":
            kind = "alexa_media"
        else:
            continue
        out.append((kind, e.entity_id, _area_name(hass, e)))
    return out


def satellites(hass: HomeAssistant) -> list[tuple[str, str]]:
    return [(eid, area) for kind, eid, area in speakers(hass) if kind == "satellite"]


def pick_speakers(found: list[tuple[str, str, str]], room: str = "") -> list[tuple[str, str]]:
    """Which speakers to use: one kind per room, Ghost's own screens first, so a room
    with both a screen and an Echo doesn't hear it twice."""
    if room:
        found = [f for f in found if f[2].lower() == room.lower()]
    by_area: dict[str, list[tuple[str, str, str]]] = {}
    for f in found:
        by_area.setdefault(f[2].lower() or f"_{f[1]}", []).append(f)
    chosen = []
    for items in by_area.values():
        for kind in ("satellite", "alexa", "alexa_media"):
            hits = [(k, eid) for k, eid, _ in items if k == kind]
            if hits:
                chosen += hits
                break
    return chosen


class GhostLink:
    """The live WebSocket to Ghost Server (see PROTOCOL.md §4)."""

    def __init__(self, hass: HomeAssistant, url: str, key: str) -> None:
        self.hass, self.url, self.key = hass, url.rstrip("/"), key
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = self.hass.async_create_background_task(self._run(), "ghost_link")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        delay = 1
        session = async_get_clientsession(self.hass)
        ws_url = self.url.replace("http", "ws", 1) + f"/api/v1/agent?key={self.key}"
        while True:
            try:
                async with session.ws_connect(ws_url, heartbeat=30) as ws:
                    rooms = sorted({a for _, _, a in speakers(self.hass) if a})
                    await ws.send_json({"type": "hello", "capabilities": [],
                                        "info": {"platform": "Home Assistant", "rooms": rooms}})
                    delay = 1
                    async for m in ws:
                        if m.type != aiohttp.WSMsgType.TEXT:
                            continue
                        msg = m.json()
                        if msg.get("type") in ("say", "task_done"):
                            text = msg.get("say") or msg.get("text") or ""
                            await self.announce(text, msg.get("room") or "")
                        elif msg.get("type") == "call":
                            await ws.send_json({"type": "result", "id": msg.get("id"), "ok": False,
                                                "error": "Home Assistant has no device actions"})
                    if ws.close_code == 4401:
                        _LOGGER.error("Ghost revoked this Home Assistant's key; remove and re-add the integration")
                        return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — keep reconnecting
                _LOGGER.debug("Ghost link dropped: %s", e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def announce(self, text: str, room: str = "") -> None:
        if not text.strip():
            return
        chosen = pick_speakers(speakers(self.hass), room)
        if not chosen:
            _LOGGER.warning("Ghost wanted to speak in '%s' but found no speaker there", room or "any room")
            return
        sats = [eid for k, eid in chosen if k == "satellite"]
        echos = [eid for k, eid in chosen if k == "alexa"]
        amp = [eid for k, eid in chosen if k == "alexa_media"]
        if sats:
            await self.hass.services.async_call(
                "assist_satellite", "announce", {"message": text, "preannounce": True},
                target={"entity_id": sats}, blocking=False)
        if echos:
            await self.hass.services.async_call(
                "notify", "send_message", {"message": text}, target={"entity_id": echos}, blocking=False)
        if amp:
            await self.hass.services.async_call(
                "notify", "alexa_media", {"message": text, "target": amp, "data": {"type": "announce"}},
                blocking=False)
