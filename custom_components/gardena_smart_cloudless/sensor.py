"""Sensor platform for Smart System Local integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .smart_system_local import BaseDevice

from .const import DOMAIN
from .coordinator import SmartSystemLocalCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class GardenaLocalSensorEntityDescription(SensorEntityDescription):
    """Describes Gardena Local sensor entity."""

    value_fn: Callable[[BaseDevice], float | int | None]
    available_fn: Callable[[BaseDevice], bool] = lambda _: True


SENSOR_TYPES: tuple[GardenaLocalSensorEntityDescription, ...] = (
    GardenaLocalSensorEntityDescription(
        key="battery_level",
        name="Battery Level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.battery_level if hasattr(device, "battery_level") else None,
        available_fn=lambda device: hasattr(device, "battery_level") and device.battery_level is not None,
    ),
    GardenaLocalSensorEntityDescription(
        key="soil_temperature",
        name="Soil Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.soil_temperature if hasattr(device, "soil_temperature") else None,
        available_fn=lambda device: hasattr(device, "soil_temperature") and device.soil_temperature is not None,
    ),
    GardenaLocalSensorEntityDescription(
        key="soil_moisture",
        name="Soil Moisture",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.MOISTURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.soil_moisture if hasattr(device, "soil_moisture") else None,
        available_fn=lambda device: hasattr(device, "soil_moisture") and device.soil_moisture is not None,
    ),
    GardenaLocalSensorEntityDescription(
        key="soil_humidity",
        name="Soil Humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.soil_humidity if hasattr(device, "soil_humidity") else None,
        available_fn=lambda device: hasattr(device, "soil_humidity") and device.soil_humidity is not None,
    ),
    GardenaLocalSensorEntityDescription(
        key="ambient_temperature",
        name="Ambient Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.ambient_temperature if hasattr(device, "ambient_temperature") else None,
        available_fn=lambda device: hasattr(device, "ambient_temperature") and device.ambient_temperature is not None,
    ),
    GardenaLocalSensorEntityDescription(
        key="rf_link_quality",
        name="RF Link Quality",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:wifi",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.rf_link_quality if hasattr(device, "rf_link_quality") else None,
        available_fn=lambda device: hasattr(device, "rf_link_quality") and device.rf_link_quality is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gardena Local sensors from a config entry."""
    coordinator: SmartSystemLocalCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Wait for initial device discovery
    await coordinator.async_config_entry_first_refresh()

    # Track which entities have been added
    added_entities: set[tuple[str, str]] = set()

    def _add_new_entities() -> None:
        """Add entities for newly discovered devices."""
        entities: list[GardenaLocalSensor] = []
        
        for device_id, device in coordinator.get_all_devices().items():
            for description in SENSOR_TYPES:
                entity_key = (device_id, description.key)
                if entity_key not in added_entities and description.available_fn(device):
                    entities.append(
                        GardenaLocalSensor(
                            coordinator,
                            device_id,
                            description,
                        )
                    )
                    added_entities.add(entity_key)
        
        if entities:
            async_add_entities(entities)

    # Add initial entities
    _add_new_entities()

    # Listen for coordinator updates to add new entities
    entry.async_on_unload(
        coordinator.async_add_listener(_add_new_entities)
    )


class GardenaLocalSensor(CoordinatorEntity[SmartSystemLocalCoordinator], SensorEntity):
    """Representation of a Gardena Local sensor."""

    entity_description: GardenaLocalSensorEntityDescription

    def __init__(
        self,
        coordinator: SmartSystemLocalCoordinator,
        device_id: str,
        description: GardenaLocalSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_has_entity_name = True

    @property
    def device_info(self):
        """Return device information."""
        device = self.coordinator.get_device(self._device_id)
        model = device.__class__.__name__ if device else "Unknown"
        
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device_id,
            "manufacturer": "Gardena",
            "model": model,
        }

    @property
    def native_value(self) -> float | int | None:
        """Return the state of the sensor."""
        device = self.coordinator.get_device(self._device_id)
        if device is None:
            return None
        return self.entity_description.value_fn(device)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not super().available:
            return False
        device = self.coordinator.get_device(self._device_id)
        return device is not None and device.is_online
