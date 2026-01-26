"""Gardena Smart Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import SmartSystemLocalCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

# Service constants
SERVICE_TRIGGER_MEASUREMENT = "trigger_measurement"
ATTR_DEVICE = "device"
ATTR_ENTITY_ID = "entity_id"
ATTR_TYPE = "type"

# Service schema
SERVICE_TRIGGER_MEASUREMENT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE): cv.string,
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_TYPE): vol.In(["temperature", "humidity", "soil_temperature", "soil_moisture", "light", "battery"]),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart System Local from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    username = entry.data.get(CONF_USERNAME, "_")
    password = entry.data.get(CONF_PASSWORD, "")

    coordinator = SmartSystemLocalCoordinator(hass, host, port, username, password)
    
    await coordinator.async_connect()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    async def handle_trigger_measurement(call: ServiceCall) -> None:
        """Handle the trigger_measurement service call."""
        device_id = call.data.get(ATTR_DEVICE)
        entity_id = call.data.get(ATTR_ENTITY_ID)
        measurement_type = call.data.get(ATTR_TYPE)

        # Get device_id from entity_id if provided
        if entity_id and not device_id:
            # Extract device_id from entity_id (format: sensor.{device_id}_{sensor_type})
            parts = entity_id.split(".")
            if len(parts) == 2:
                # Remove platform prefix and extract device ID from the rest
                device_part = parts[1]
                # Device ID is typically before the last underscore
                device_id = device_part.rsplit("_", 1)[0].upper()

        if not device_id:
            _LOGGER.error("No device or entity_id specified for trigger_measurement service")
            return

        # Find the coordinator that has this device
        device = None
        target_coordinator = None
        for coord_entry_id, coord in hass.data[DOMAIN].items():
            if isinstance(coord, SmartSystemLocalCoordinator):
                device = coord.get_device(device_id)
                if device:
                    target_coordinator = coord
                    break

        if not device or not target_coordinator:
            _LOGGER.error("Device %s not found", device_id)
            return

        # Determine which measurement to trigger
        if measurement_type:
            method_map = {
                "temperature": "measure_ambient_temperature",
                "humidity": "measure_ambient_temperature",  # Usually same sensor
                "soil_temperature": "measure_soil_temperature",
                "soil_moisture": "measure_soil_moisture",
                "light": "measure_light",
                "battery": "measure_battery",
            }
            method_name = method_map.get(measurement_type)
            if method_name and hasattr(device, method_name):
                try:
                    command_data = getattr(device, method_name)()
                    await target_coordinator._ws.send(command_data) if target_coordinator._ws else None
                    _LOGGER.info("Triggered %s measurement for device %s", measurement_type, device_id)
                except Exception as err:
                    _LOGGER.error("Failed to trigger %s measurement: %s", measurement_type, err)
            else:
                _LOGGER.error("Device %s does not support %s measurement", device_id, measurement_type)
        else:
            # Trigger all available measurements
            _LOGGER.info("Triggering all measurements for device %s", device_id)
            for method_name in ["measure_ambient_temperature", "measure_soil_temperature", 
                               "measure_soil_moisture", "measure_light", "measure_battery"]:
                if hasattr(device, method_name):
                    try:
                        command_data = getattr(device, method_name)()
                        await target_coordinator._ws.send(command_data) if target_coordinator._ws else None
                    except Exception as err:
                        _LOGGER.debug("Could not trigger %s: %s", method_name, err)

    # Register the service only once (check if not already registered)
    if not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_MEASUREMENT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_TRIGGER_MEASUREMENT,
            handle_trigger_measurement,
            schema=SERVICE_TRIGGER_MEASUREMENT_SCHEMA,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: SmartSystemLocalCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_disconnect()

        # Unregister services if this was the last entry
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_TRIGGER_MEASUREMENT)

    return unload_ok
