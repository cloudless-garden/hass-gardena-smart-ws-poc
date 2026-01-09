from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv
from homeassistant.core import callback
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.components import zeroconf

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


def parse_device_list(data) -> tuple[dict[str, str], dict[str, dict], dict[str, int]]:
    """Parse device list from WebSocket response.

    Returns:
        tuple: (choices, device_capabilities, device_models)
            - choices: dict of device_id -> display_name
            - device_capabilities: dict of device_id -> {temperature: bool, humidity: bool}
            - device_models: dict of device_id -> model_number
    """
    choices: dict[str, str] = {}
    device_capabilities: dict[str, dict] = {}
    device_models: dict[str, int] = {}

    if not isinstance(data, list):
        return choices, device_capabilities, device_models

    for item in data:
        try:
            if not item.get("success", False):
                continue
            payload = item.get("payload") or {}
            if not isinstance(payload, dict):
                continue

            for dev_id, dev_obj in payload.items():
                name = dev_id
                has_temp = False
                has_humidity = False
                model_number = None

                try:
                    device_obj = dev_obj.get("device") if isinstance(dev_obj, dict) else None
                    if device_obj and isinstance(device_obj, dict):
                        inst0 = device_obj.get("0") or {}
                        if isinstance(inst0, dict):
                            dt = inst0.get("device_type")
                            if isinstance(dt, dict):
                                dev_type = dt.get("vs") or dt.get("vb") or dt.get("vo")
                                if dev_type == "Sensor":
                                    name = f"{dev_type} ({dev_id})"
                                else:
                                    continue

                            # Extract model_number
                            model_num = inst0.get("model_number")
                            if isinstance(model_num, dict):
                                model_number = model_num.get("vs") or model_num.get("vi")
                                if isinstance(model_number, str):
                                    try:
                                        model_number = int(model_number)
                                    except ValueError:
                                        model_number = None
                                _LOGGER.debug("Device %s model_number: %s", dev_id, model_number)

                    lb = dev_obj.get("lemonbeat", {}).get("0", {}) if isinstance(dev_obj, dict) else {}
                    if isinstance(lb, dict):
                        has_temp = "soil_temperature" in lb or "ambient_temperature" in lb
                        has_humidity = "soil_moisture" in lb or "soil_humidity" in lb

                except Exception as exc:
                    _LOGGER.warning("Error parsing device %s: %s", dev_id, exc)
                    continue

                if has_temp or has_humidity:
                    choices[dev_id] = name
                    device_capabilities[dev_id] = {
                        "temperature": has_temp,
                        "humidity": has_humidity
                    }
                    if model_number is not None:
                        device_models[dev_id] = model_number
        except Exception as exc:
            _LOGGER.warning("Error parsing device list item: %s", exc)
            continue

    return choices, device_capabilities, device_models


class GardenaSmartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._discovered_host = None
        self._discovered_port = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()

    async def async_step_zeroconf(self, discovery_info: zeroconf.ZeroconfServiceInfo):
        """Handle zeroconf discovery."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        hostname = discovery_info.hostname.rstrip(".")

        # Check if already configured
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured()

        self._discovered_host = host
        self._discovered_port = port

        # Set context for showing in the UI
        self.context["title_placeholders"] = {
            "name": f"GARDENA smart Gateway ({hostname})"
        }

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict | None = None):
        """Confirm discovery."""
        if user_input is None:
            schema = vol.Schema({
                vol.Required(CONF_PASSWORD): str,
            })
            return self.async_show_form(
                step_id="zeroconf_confirm",
                data_schema=schema,
                description_placeholders={
                    "host": self._discovered_host,
                    "port": str(self._discovered_port),
                },
            )

        return await self.async_step_user({
            CONF_HOST: self._discovered_host,
            CONF_PORT: self._discovered_port,
            CONF_PASSWORD: user_input[CONF_PASSWORD],
        })

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is None:
            schema = vol.Schema({
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_PASSWORD): str,
            })
            return self.async_show_form(step_id="user", data_schema=schema)

        host = user_input[CONF_HOST]
        port = user_input[CONF_PORT]
        password = user_input[CONF_PASSWORD]

        try:
            import websockets
            import base64
            from .const import create_insecure_ssl_context

            uri = f"wss://{host}:{port}"
            auth_string = f"_:{password}"
            auth_bytes = auth_string.encode('utf-8')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            additional_headers = {"Authorization": f"Basic {auth_b64}"}

            ssl_context = await self.hass.async_add_executor_job(create_insecure_ssl_context)

            async with websockets.connect(uri, ssl=ssl_context, additional_headers=additional_headers) as ws:
                await ws.send('[{"op":"read","entity":{"service":"lemonbeatd","path":"devices"}}]')
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                _LOGGER.debug("Received from WebSocket: %s", msg)
                data = json.loads(msg)
                _LOGGER.debug("Parsed data: %s", data)
        except Exception as exc:
            _LOGGER.exception("Failed to fetch devices: %s", exc)
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_HOST, default=host): str,
                    vol.Required(CONF_PORT, default=port): int,
                    vol.Required(CONF_PASSWORD, default=password): str,
                }),
                errors={"base": "cannot_connect"},
            )

        choices, device_capabilities, device_models = parse_device_list(data)

        if not choices:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_HOST, default=host): str,
                    vol.Required(CONF_PORT, default=port): int,
                    vol.Required(CONF_PASSWORD, default=password): str,
                }),
                errors={"base": "no_devices"},
            )

        schema = vol.Schema({
            vol.Required("selected", default=list(choices.keys())): cv.multi_select(choices)
        })
        self._devices_choices = choices
        self._device_capabilities = device_capabilities
        self._device_models = device_models
        self._host = host
        self._port = port
        self._password = password
        return self.async_show_form(step_id="select", data_schema=schema)

    async def async_step_select(self, user_input: dict | None = None):
        if user_input is None:
            return self.async_abort()
        selected = user_input.get("selected", [])
        data = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_PASSWORD: self._password,
        }

        capabilities = {dev_id: self._device_capabilities.get(dev_id, {}) for dev_id in selected}
        device_models = {dev_id: self._device_models.get(dev_id) for dev_id in selected if dev_id in self._device_models}
        options = {"devices": selected, "capabilities": capabilities, "device_models": device_models}
        return self.async_create_entry(title=f"GARDENA smart {self._host}:{self._port}", data=data, options=options)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Gardena Smart."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure_connection", "manage_devices"],
        )

    async def async_step_configure_connection(self, user_input: dict[str, Any] | None = None):
        """Configure connection settings."""
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                },
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="configure_connection",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_HOST,
                    default=self.config_entry.data.get(CONF_HOST, DEFAULT_HOST)
                ): str,
                vol.Required(
                    CONF_PORT,
                    default=self.config_entry.data.get(CONF_PORT, DEFAULT_PORT)
                ): int,
                vol.Required(
                    CONF_PASSWORD,
                    default=self.config_entry.data.get(CONF_PASSWORD, "")
                ): str,
            }),
        )

    async def async_step_manage_devices(self, user_input: dict[str, Any] | None = None):
        """Manage device selection."""
        if user_input is not None:
            selected = user_input.get("selected", [])
            current_caps = self.config_entry.options.get("capabilities", {})

            # Preserve capabilities for devices that were already configured
            capabilities = {dev_id: current_caps.get(dev_id, self._device_capabilities.get(dev_id, {}))
                          for dev_id in selected}

            self.hass.config_entries.async_update_entry(
                self.config_entry,
                options={"devices": selected, "capabilities": capabilities}
            )
            await self.hass.config_entries.async_reload(self.config_entry.entry_id)
            return self.async_create_entry(title="", data={})

        # Fetch current device list from bridge
        host = self.config_entry.data.get(CONF_HOST, DEFAULT_HOST)
        port = self.config_entry.data.get(CONF_PORT, DEFAULT_PORT)
        password = self.config_entry.data.get(CONF_PASSWORD, "")

        try:
            import websockets
            import base64
            from .const import create_insecure_ssl_context

            uri = f"wss://{host}:{port}"
            auth_string = f"_:{password}"
            auth_bytes = auth_string.encode('utf-8')
            auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
            additional_headers = {"Authorization": f"Basic {auth_b64}"}

            ssl_context = await self.hass.async_add_executor_job(create_insecure_ssl_context)

            async with websockets.connect(uri, ssl=ssl_context, additional_headers=additional_headers) as ws:
                await ws.send('[{"op":"read","entity":{"service":"lemonbeatd","path":"devices"}}]')
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                data = json.loads(msg)
        except Exception as exc:
            _LOGGER.exception("Failed to fetch devices: %s", exc)
            return self.async_abort(reason="cannot_connect")

        choices, device_capabilities, _ = parse_device_list(data)

        if not choices:
            return self.async_abort(reason="no_devices")

        self._device_capabilities = device_capabilities
        current_devices = self.config_entry.options.get("devices", [])

        return self.async_show_form(
            step_id="manage_devices",
            data_schema=vol.Schema({
                vol.Required("selected", default=current_devices): cv.multi_select(choices)
            }),
            description_placeholders={"device_count": str(len(choices))}
        )
