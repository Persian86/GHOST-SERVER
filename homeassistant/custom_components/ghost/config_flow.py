"""Pair Home Assistant with Ghost Server using a code from Ghost's admin page."""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_DEVICE_ID, CONF_KEY, CONF_URL, DEFAULT_URL, DOMAIN


class GhostConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            pem = ""
            from . import tls
            got = await tls.upgrade(async_get_clientsession(self.hass), url)   # pair over HTTPS when offered
            if got:
                url, pem = got
            try:
                async with async_get_clientsession(self.hass).post(
                    f"{url}/api/v1/pair",
                    json={"code": user_input["code"].strip(), "name": user_input["name"].strip(),
                          "kind": "homeassistant", "info": {"platform": "Home Assistant"}},
                    timeout=aiohttp.ClientTimeout(total=15),
                    ssl=tls.ssl_for(url, pem),
                ) as r:
                    data = await r.json(content_type=None)
                    if r.status == 400:
                        errors["code"] = "bad_code"
                    elif r.status != 200:
                        errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            if not errors:
                await self.async_set_unique_id(data["device_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=data.get("assistant_name") or "Ghost",
                    data={CONF_URL: url, CONF_KEY: data["device_key"], CONF_DEVICE_ID: data["device_id"],
                          tls.CONF_TLS_PEM: pem},
                )
        schema = vol.Schema({
            vol.Required(CONF_URL, default=DEFAULT_URL): str,
            vol.Required("code"): str,
            vol.Required("name", default="Home Assistant"): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
