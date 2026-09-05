"""Data update coordinator for the Goatbot integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GoatbotApiClient, GoatbotError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class GoatbotCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls the Goatbot cloud for all mowers on the account.

    `self.data` is keyed by device_id, each value being that device's
    raw entry from the `/app/devices` response, plus a `map` key with
    the mower's active lawn map (boundary/zones/dock position).

    Live position updates arrive out-of-band from the real-time MQTT
    channel (see realtime.py) and are stored separately in
    `live_position`, keyed by device_id, since they're pushed rather
    than polled.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, api: GoatbotApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.live_position: dict[str, dict[str, Any]] = {}
        self.realtime: Any = None  # set to a GoatbotRealtimeClient after setup

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.api.async_get_devices()
            data = {device["deviceId"]: device for device in devices}
            for device_id, device in data.items():
                device["map"] = await self.api.async_get_active_map(device_id)
        except GoatbotError as err:
            raise UpdateFailed(str(err)) from err
        return data

    def async_update_live_position(self, device_id: str, position: dict[str, Any]) -> None:
        """Record a live position update pushed over MQTT.

        Called from the real-time client, which runs on its own thread -
        must only be scheduled onto the event loop, never called directly.
        """
        self.live_position[device_id] = position
        if self.data is not None and device_id in self.data:
            self.async_set_updated_data(self.data)
