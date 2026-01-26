"""Switch platform for Smart System Local integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .smart_system_local import BaseDevice
from .const import DOMAIN
from .coordinator import SmartSystemLocalCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class GardenaLocalSwitchEntityDescription(SwitchEntityDescription):
    """Describes Gardena Local switch entity."""

    value_fn: Callable[[BaseDevice], int | None]
    available_fn: Callable[[BaseDevice], bool] = lambda _: True


SWITCH_TYPES: tuple[GardenaLocalSwitchEntityDescription, ...] = (
    GardenaLocalSwitchEntityDescription(
        key="power_timer",
        name="Power Timer",
        icon="mdi:power-plug",
        value_fn=lambda device: device.get_value("lemonbeat", "0", "power_timer"),
        available_fn=lambda device: device.get_value("lemonbeat", "0", "power_timer") is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gardena Local switches from a config entry."""
    coordinator: SmartSystemLocalCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Wait for initial device discovery
    await coordinator.async_config_entry_first_refresh()

    # Track which entities have been added
    added_entities: set[tuple[str, str]] = set()

    def _add_new_entities() -> None:
        """Add entities for newly discovered devices."""
        entities: list[GardenaLocalSwitch] = []
        
        for device_id, device in coordinator.get_all_devices().items():
            for description in SWITCH_TYPES:
                entity_key = (device_id, description.key)
                if entity_key not in added_entities and description.available_fn(device):
                    entities.append(
                        GardenaLocalSwitch(
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


class GardenaLocalSwitch(CoordinatorEntity[SmartSystemLocalCoordinator], SwitchEntity):
    """Representation of a Gardena Local switch."""

    entity_description: GardenaLocalSwitchEntityDescription

    def __init__(
        self,
        coordinator: SmartSystemLocalCoordinator,
        device_id: str,
        description: GardenaLocalSwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        
        device: BaseDevice | None = coordinator.get_device(device_id)
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_name = f"{device.model_number if device else device_id} {description.name}"
        
        # Device info
        if device:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, device_id)},
                "name": f"{device.device_type} {device.serial_number}",
                "manufacturer": device.manufacturer,
                "model": device.model_number,
                "sw_version": device.software_version,
            }

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        device = self.coordinator.get_device(self._device_id)
        if device is None:
            return None
        
        value = self.entity_description.value_fn(device)
        if value is None:
            return None
        
        # Power timer: on if > 0, off if 0
        return value > 0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        device = self.coordinator.get_device(self._device_id)
        if device is None:
            return
        
        command_data = device.turn_on()
        await self.coordinator._ws.send(json.dumps([command_data]))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        device = self.coordinator.get_device(self._device_id)
        if device is None:
            return
        
        command_data = device.turn_off()
        await self.coordinator._ws.send(json.dumps([command_data]))
