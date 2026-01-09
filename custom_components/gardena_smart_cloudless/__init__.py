"""Gardena Smart Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    password = entry.data.get(CONF_PASSWORD, "")
    uri = f"wss://{host}:{port}"

    _LOGGER.debug("Loading config entry: host=%s port=%s password='%s'", host, port, password)

    from .sensor import WSManager

    manager = WSManager(hass, uri, password)
    await manager.start()

    hass.data[DOMAIN][entry.entry_id] = {"manager": manager}

    _LOGGER.info("Setting up GARDENA smart for %s:%s (entry %s)", host, port, entry.entry_id)

    async def _handle_trigger(call):
        _LOGGER.debug("gardena_smart.trigger_measurement called with data: %s", call.data)
        device = call.data.get("device")
        entity_id = call.data.get("entity_id")
        mtype = call.data.get("type", "temperature")

        if not device and entity_id:
            try:
                ent_reg = er.async_get(hass)
                entry_reg = ent_reg.async_get(entity_id)
                if entry_reg and entry_reg.unique_id:
                    uid = entry_reg.unique_id
                    device = uid.rsplit("_", 1)[0]
                    try:
                        suffix = uid.rsplit("_", 1)[1].lower()
                        if suffix in ("hum", "humidity", "moisture", "moist", "raw"):
                            mtype = "humidity"
                        elif suffix in ("temp", "temperature"):
                            mtype = "temperature"
                    except (IndexError, AttributeError):
                        pass
            except (KeyError, AttributeError) as exc:
                _LOGGER.debug("Could not extract device from entity_id %s: %s", entity_id, exc)
                device = None

        if entity_id and not device:
            try:
                name = entity_id.split(".", 1)[-1].lower()
                if name.endswith("_humidity") or name.endswith("_hum") or "_moist" in name:
                    mtype = "humidity"
                elif name.endswith("_temperature") or name.endswith("_temp"):
                    mtype = "temperature"
            except (IndexError, AttributeError):
                pass

        if not device:
            raise ValueError("device or entity_id is required")

        await manager.trigger_measurement(device, mtype)

    hass.services.async_register(DOMAIN, "trigger_measurement", _handle_trigger)
    _LOGGER.info("Registered service gardena_smart.trigger_measurement for entry %s", entry.entry_id)

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data and data.get("manager"):
        mgr = data["manager"]
        if mgr._task:
            mgr._task.cancel()
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    return True
