"""Ghost as a Home Assistant conversation agent."""
from __future__ import annotations

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import tls
from .const import CONF_KEY, CONF_URL


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([GhostAgent(entry)])


class GhostAgent(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(identifiers={("ghost", entry.entry_id)},
                                               name=entry.title or "Ghost", manufacturer="Ghost")

    @property
    def supported_languages(self):
        return MATCH_ALL

    def _where(self, device_id: str | None, satellite_id: str | None = None) -> tuple[str, str]:
        """(room, speaker name) of the satellite that heard the request."""
        area_id, name = None, ""
        if satellite_id and (ent := er.async_get(self.hass).async_get(satellite_id)):
            area_id, device_id = ent.area_id, device_id or ent.device_id
            name = ent.name or ent.original_name or ""
        dev = dr.async_get(self.hass).async_get(device_id) if device_id else None
        if dev is not None:
            area_id = area_id or dev.area_id
            name = dev.name_by_user or dev.name or name
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        return (area.name if area else ""), name

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        room, satellite = self._where(getattr(user_input, "device_id", None),
                                      getattr(user_input, "satellite_id", None))
        try:
            async with async_get_clientsession(self.hass).post(
                self.entry.data[CONF_URL] + "/api/v1/assist",
                headers={"Authorization": f"Bearer {self.entry.data[CONF_KEY]}"},
                json={"text": user_input.text, "room": room, "satellite": satellite,
                      "conversation_id": user_input.conversation_id, "language": user_input.language},
                timeout=aiohttp.ClientTimeout(total=120),
                ssl=tls.ssl_for(self.entry.data[CONF_URL], self.entry.data.get(tls.CONF_TLS_PEM, "")),
            ) as r:
                data = await r.json(content_type=None)
                speech = data.get("text") if r.status == 200 else (data.get("detail") or "Ghost had a problem.")
        except (aiohttp.ClientError, TimeoutError, ValueError):
            speech = "I can't reach the Ghost server right now."
        conv_id = user_input.conversation_id     # Home Assistant owns the session; Ghost keeps history per room
        resp = intent.IntentResponse(language=user_input.language)
        resp.async_set_speech(speech or "Done.")
        follow_up = (speech or "").rstrip().endswith("?")
        try:
            return conversation.ConversationResult(response=resp, conversation_id=conv_id,
                                                   continue_conversation=follow_up)
        except TypeError:   # older Home Assistant
            return conversation.ConversationResult(response=resp, conversation_id=conv_id)
