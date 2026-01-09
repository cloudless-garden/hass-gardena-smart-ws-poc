"""Sensor platform for Gardena Smart integration."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import COMMAND_HUM, COMMAND_TEMP_18845, COMMAND_TEMP_19040, DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_TEMP_UNIT = "°C"
DEFAULT_HUM_UNIT = "%"


class SGSensor(Entity):
    def __init__(self, hass, device_id: str, name: str, unit: str, key: str, model_number: int = None):
        self.hass = hass
        self._device_id = device_id
        self._name = name
        self._unit = unit
        self._state = None
        self._key = key
        self._model_number = model_number

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_{self._key}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device_id,
        }

    @property
    def device_class(self):
        return "temperature" if self._key == "temp" else "humidity"

    @property
    def name(self):
        return f"{self._device_id} {self._name}"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit

    def update_state(self, value: Any):
        self._state = value
        try:
            self.hass.loop.call_soon_threadsafe(
                async_dispatcher_send, self.hass, f"{DOMAIN}_update_{self.unique_id}", value
            )
        except Exception as exc:
            _LOGGER.debug("Dispatcher send failed for %s, falling back to direct write: %s", self.unique_id, exc)
            try:
                self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
            except Exception as exc2:
                _LOGGER.exception("Failed to update state for %s: %s", self.unique_id, exc2)

    async def async_added_to_hass(self) -> None:
        @callback
        def _handle(value):
            self._state = value
            self.async_write_ha_state()

        self._unsub = async_dispatcher_connect(
            self.hass, f"{DOMAIN}_update_{self.unique_id}", _handle
        )

    async def async_will_remove_from_hass(self) -> None:
        if hasattr(self, "_unsub") and callable(self._unsub):
            try:
                self._unsub()
            except Exception as exc:
                _LOGGER.debug("Error unsubscribing dispatcher for %s: %s", self.unique_id, exc)

    async def async_update(self):
        pass


class WSManager:
    def __init__(self, hass, uri: str, password: str):
        self.hass = hass
        self.uri = uri
        self.password = password
        self.ws = None
        self._task = None
        self._sensors: dict[str, SGSensor] = {}
        self._device_models: dict[str, int] = {}
        self._connected = asyncio.Event()

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        import base64
        from .const import create_insecure_ssl_context

        ssl_context = await self.hass.async_add_executor_job(create_insecure_ssl_context)

        auth_string = f"_:{self.password}"
        auth_bytes = auth_string.encode('utf-8')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        additional_headers = {"Authorization": f"Basic {auth_b64}"}

        while True:
            try:
                _LOGGER.info("Connecting to WS %s", self.uri)
                async with websockets.connect(self.uri, ssl=ssl_context, additional_headers=additional_headers) as ws:
                    self.ws = ws
                    self._connected.set()
                    await ws.send('[{"op":"read","entity":{"service":"lemonbeatd","path":"devices"}}]')
                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                            _LOGGER.debug("WebSocket message: %s", json.dumps(data, indent=2))
                        except json.JSONDecodeError as exc:
                            _LOGGER.warning("Failed to parse WS message as JSON: %s", exc)
                            continue
                        await self._handle_message(data)
            except (websockets.exceptions.WebSocketException, OSError, ConnectionError) as exc:
                self._connected.clear()
                _LOGGER.warning("WebSocket connection failed, retrying in 2s: %s", exc)
            except Exception as exc:
                self._connected.clear()
                _LOGGER.exception("Unexpected error in WebSocket loop, retrying in 2s: %s", exc)
                await asyncio.sleep(2)

    def _extract_value(self, data):
        """Extract value from vi/vb/vs fields or return data directly."""
        if isinstance(data, dict):
            if "vi" in data:
                return data["vi"]
            if "vb" in data:
                return data["vb"]
            if "vs" in data:
                return data["vs"]
            return None
        return data

    def _update_sensor(self, device: str, key_suffix: str, value: Any, field: str = ""):
        """Update sensor if it exists."""
        if value is None:
            return
        key = f"{device}_{key_suffix}"
        sensor = self._sensors.get(key)
        if sensor:
            field_info = f" from {field}" if field else ""
            _LOGGER.debug("Updating %s for %s%s: %s", key_suffix, device, field_info, value)
            sensor.update_state(value)

    async def _handle_message(self, data):
        if not isinstance(data, list):
            return
        for item in data:
            try:
                entity = item.get("entity", {})
                device = entity.get("device")
                payload = item.get("payload") or {}

                if not isinstance(payload, dict):
                    continue

                # Extract and store model_number
                if "model_number" in payload:
                    model_val = self._extract_value(payload["model_number"])
                    if model_val is not None:
                        self._device_models[device] = model_val
                        _LOGGER.debug("Device %s model: %s", device, model_val)

                model = self._device_models.get(device)
                lb = payload.get("lemonbeat")
                if lb and isinstance(lb, dict):
                    dev = lb.get("0") or lb
                    if isinstance(dev, dict):
                        # Model-specific field selection
                        if model == 18845:
                            temp = self._extract_value(dev.get("ambient_temperature"))
                            hum = self._extract_value(dev.get("soil_humidity"))
                        elif model == 19040:
                            temp = self._extract_value(dev.get("soil_temperature"))
                            hum = self._extract_value(dev.get("soil_moisture"))
                        else:
                            # Default: try both
                            temp = self._extract_value(dev.get("soil_temperature"))
                            hum = self._extract_value(dev.get("soil_moisture") or dev.get("soil_humidity"))
                    else:
                        if model == 18845:
                            temp = self._extract_value(lb.get("ambient_temperature"))
                            hum = self._extract_value(lb.get("soil_humidity"))
                        elif model == 19040:
                            temp = self._extract_value(lb.get("soil_temperature"))
                            hum = self._extract_value(lb.get("soil_moisture"))
                        else:
                            temp = self._extract_value(lb.get("soil_temperature"))
                            hum = self._extract_value(lb.get("soil_moisture") or lb.get("soil_humidity"))
                    self._update_sensor(device, "temp", temp)
                    self._update_sensor(device, "hum", hum)
                else:
                    # Handle individual field updates
                    if model == 18845:
                        if "ambient_temperature" in payload:
                            temp = self._extract_value(payload["ambient_temperature"])
                            self._update_sensor(device, "temp", temp, "ambient_temperature")
                        if "soil_humidity" in payload:
                            hum = self._extract_value(payload["soil_humidity"])
                            self._update_sensor(device, "hum", hum, "soil_humidity")
                    elif model == 19040:
                        if "soil_temperature" in payload:
                            temp = self._extract_value(payload["soil_temperature"])
                            self._update_sensor(device, "temp", temp, "soil_temperature")
                        if "soil_moisture" in payload:
                            hum = self._extract_value(payload["soil_moisture"])
                            self._update_sensor(device, "hum", hum, "soil_moisture")
                    else:
                        # Default behavior
                        if "soil_temperature" in payload:
                            temp = self._extract_value(payload["soil_temperature"])
                            self._update_sensor(device, "temp", temp, "soil_temperature")
                        if "soil_moisture" in payload:
                            hum = self._extract_value(payload["soil_moisture"])
                            self._update_sensor(device, "hum", hum, "soil_moisture")
                        if "soil_humidity" in payload:
                            hum = self._extract_value(payload["soil_humidity"])
                            self._update_sensor(device, "hum", hum, "soil_humidity")

                if "command" in payload:
                    cmd_val = self._extract_value(payload["command"])
                    if cmd_val is not None:
                        _LOGGER.debug("Received command update for %s: %s", device, cmd_val)
            except Exception as exc:
                _LOGGER.warning("Error handling WebSocket message item: %s", exc)

    async def send(self, message: str):
        if self.ws is None:
            _LOGGER.warning("WebSocket not connected; cannot send")
            return
        await self.ws.send(message)

    async def trigger_measurement(self, device: str, mtype: str = "temperature") -> None:
        """Send measurement trigger command for device."""
        model = self._device_models.get(device)
        if model == 18845:
            # Model 18845: Send 5 for temperature
            cmd_val = COMMAND_TEMP_18845 if mtype == "temperature" else COMMAND_HUM
        elif model == 19040:
            # Model 19040: Send 3 for temperature
            cmd_val = COMMAND_TEMP_19040 if mtype == "temperature" else COMMAND_HUM
        else:
            # Default behavior (use 19040 command)
            cmd_val = COMMAND_TEMP_19040 if mtype == "temperature" else COMMAND_HUM
        _LOGGER.debug("Trigger measurement: device=%s type=%s model=%s cmd=%s", device, mtype, model, cmd_val)
        msg = [
            {
                "op": "write",
                "entity": {"device": device, "path": "lemonbeat/0/command"},
                "payload": {"vi": cmd_val},
                "metadata": {},
            }
        ]
        await self.send(json.dumps(msg))

    def register_sensor(self, sensor: SGSensor):
        key = f"{sensor._device_id}_{'temp' if sensor._unit==DEFAULT_TEMP_UNIT else 'hum'}"
        self._sensors[key] = sensor
        if sensor._model_number is not None:
            self._device_models[sensor._device_id] = sensor._model_number

    async def request_initial_values(self):
        """Request initial sensor values for all registered devices."""
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            _LOGGER.warning("WebSocket not connected after 10s, skipping initial requests")
            return

        devices = set(sensor._device_id for sensor in self._sensors.values())
        for device_id in devices:
            _LOGGER.debug("Requesting initial values for device: %s", device_id)
            await self.trigger_measurement(device_id, "temperature")
            await asyncio.sleep(0.1)
            await self.trigger_measurement(device_id, "humidity")
            await asyncio.sleep(0.1)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up sensors from config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return
    manager: WSManager = data.get("manager")
    devices = entry.options.get("devices", [])
    capabilities = entry.options.get("capabilities", {})
    device_models = entry.options.get("device_models", {})

    entities = []
    for dev in devices:
        caps = capabilities.get(dev, {"temperature": True, "humidity": True})
        model = device_models.get(dev)

        if caps.get("temperature", False):
            t = SGSensor(hass, dev, "Temperature", DEFAULT_TEMP_UNIT, "temp", model)
            entities.append(t)
            manager.register_sensor(t)

        if caps.get("humidity", False):
            h = SGSensor(hass, dev, "Humidity", DEFAULT_HUM_UNIT, "hum", model)
            entities.append(h)
            manager.register_sensor(h)

    if entities:
        async_add_entities(entities)
        await manager.request_initial_values()
