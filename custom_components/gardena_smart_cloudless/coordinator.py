"""Coordinator for Smart System Local integration."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl

import websockets
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .smart_system_local import BaseDevice, DeviceFactory, DeviceCommand, create_device

_LOGGER = logging.getLogger(__name__)


class SmartSystemLocalCoordinator(DataUpdateCoordinator[dict[str, BaseDevice]]):
    """Coordinator to manage Gardena Local WebSocket connection and devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Gardena Local",
        )
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.uri = f"wss://{host}:{port}"
        
        # Create base64 encoded auth header
        auth_string = f"{username}:{password}"
        auth_bytes = auth_string.encode("utf-8")
        self.auth_b64 = base64.b64encode(auth_bytes).decode("ascii")
        
        self._ws = None
        self._task = None
        self._devices: dict[str, BaseDevice] = {}
        self._ssl_context = None

    async def _async_update_data(self) -> dict[str, BaseDevice]:
        """Update data via DataUpdateCoordinator."""
        return self._devices

    async def async_connect(self) -> None:
        """Connect to WebSocket and start listening."""
        if self._ssl_context is None:
            self._ssl_context = await self.hass.async_add_executor_job(
                self._create_ssl_context
            )
        self._task = self.hass.async_create_background_task(
            self._ws_loop(), "smart_system_local_websocket"
        )

    async def async_disconnect(self) -> None:
        """Disconnect from WebSocket."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            await self._ws.close()

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create an insecure SSL context for self-signed certificates."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    async def _ws_loop(self) -> None:
        """Main WebSocket loop."""
        while True:
            try:
                async with websockets.connect(
                    self.uri,
                    ssl=self._ssl_context,
                    additional_headers={"Authorization": f"Basic {self.auth_b64}"},
                ) as ws:
                    self._ws = ws
                    _LOGGER.info("Connected to Gardena Local gateway at %s", self.uri)
                    
                    # Request device list immediately on connection
                    await self._request_devices()
                    
                    # Wait a moment for the response
                    await asyncio.sleep(1)
                    
                    # Trigger initial data refresh
                    await self.async_refresh()
                    
                    # Listen for messages
                    async for message in ws:
                        if isinstance(message, bytes):
                            message = message.decode('utf-8')
                        await self._handle_message(message)
                        
            except asyncio.CancelledError:
                _LOGGER.debug("WebSocket loop cancelled")
                break
            except Exception as err:
                _LOGGER.error("WebSocket error: %s", err)
                await asyncio.sleep(5)

    async def _request_devices(self) -> None:
        """Request device list from gateway."""
        if not self._ws:
            return
            
        command = DeviceFactory.discover()
        await self._ws.send(json.dumps([command]))
        _LOGGER.debug("Requested device list")

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            _LOGGER.debug("Received WebSocket message: %s", json.dumps(message))

            # First try to parse as JSON
            data = json.loads(message)
            
            if not isinstance(data, list):
                _LOGGER.debug("Received non-list message, ignoring")
                return
            
            for item in data:
                if not isinstance(item, dict):
                    continue
                    
                # Check if this is a device list response
                payload = item.get("payload", {})
                if isinstance(payload, dict):
                    for device_id, device_data in payload.items():
                        if isinstance(device_data, dict):
                            await self._update_device(device_id, device_data)
                
                # Check for updates to existing devices
                device_id = item.get("device_id")
                if device_id and device_id in self._devices:
                    # Update device with new data
                    if item.get("success") and "payload" in item:
                        await self._update_device(device_id, self._devices[device_id].raw)
            
            # Notify listeners
            self.async_set_updated_data(self._devices)
                        
        except json.JSONDecodeError:
            _LOGGER.debug("Ignoring non-JSON message from gateway: %s", message)
        except Exception as err:
            _LOGGER.debug("Error handling message (may be non-critical): %s - Message: %s", err, message)

    async def _update_device(self, device_id: str, device_data: dict) -> None:
        """Update or create a device."""
        try:
            device = await create_device(device_id, device_data)
            self._devices[device_id] = device
            _LOGGER.debug("Updated device: %s", device_id)
        except Exception as err:
            _LOGGER.debug("Failed to create device %s: %s", device_id, err)

    async def execute_command(
        self, device_id: str, command: DeviceCommand
    ) -> None:
        """Execute a command on a device."""
        if not self._ws:
            _LOGGER.error("WebSocket not connected")
            return
        
        device = self._devices.get(device_id)
        if not device:
            _LOGGER.error("Device %s not found", device_id)
            return
        
        # Build command using device's build_command method
        command_data = device.build_command(command)
        
        await self._ws.send(json.dumps([command_data]))
        _LOGGER.debug("Sent command %s to device %s", command, device_id)

    def get_device(self, device_id: str) -> BaseDevice | None:
        """Get a device by ID."""
        return self._devices.get(device_id)

    def get_all_devices(self) -> dict[str, BaseDevice]:
        """Get all devices."""
        return self._devices
