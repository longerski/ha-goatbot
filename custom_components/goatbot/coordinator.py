"""Data update coordinator for the Goatbot integration."""
from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
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
    than polled. `trail` accumulates the points seen since the last
    `reset_trail()` call (made when a mow starts), for drawing what's
    already been covered.

    `trail` is deliberately kept out of the recorder (see
    `_unrecorded_attributes` on the lawn_mower entity - it's a big,
    fast-churning live snapshot, not history), which also means it can't
    be restored from recorder state after an HA restart. Instead it's
    mirrored to a small `.storage/` file per device (debounced, not on
    every point) and reloaded once at startup, so a restart mid-mow
    doesn't blank the map card's covered-area trail.
    """

    config_entry: ConfigEntry

    _TRAIL_MAX_POINTS = 3000
    _TRAIL_SAVE_DELAY = 30  # seconds, debounced via Store.async_delay_save

    def __init__(self, hass: HomeAssistant, api: GoatbotApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.live_position: dict[str, dict[str, Any]] = {}
        self.trail: dict[str, deque[list[float]]] = {}
        self._trail_stores: dict[str, Store] = {}
        self.realtime: Any = None  # set to a GoatbotRealtimeClient after setup

    def _trail_store(self, device_id: str) -> Store:
        store = self._trail_stores.get(device_id)
        if store is None:
            store = Store(self.hass, 1, f"{DOMAIN}_trail_{device_id}")
            self._trail_stores[device_id] = store
        return store

    async def async_load_trail(self, device_id: str) -> None:
        """Restore a trail persisted before an HA restart, if any."""
        data = await self._trail_store(device_id).async_load()
        if data:
            self.trail[device_id] = deque(data, maxlen=self._TRAIL_MAX_POINTS)

    async def async_reset_trail(self, device_id: str) -> None:
        """Clear the covered-path trail, called when a new mow begins."""
        self.trail[device_id] = deque(maxlen=self._TRAIL_MAX_POINTS)
        await self._trail_store(device_id).async_save([])

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
        trail = self.trail.setdefault(device_id, deque(maxlen=self._TRAIL_MAX_POINTS))
        trail.append([position["x"], position["y"]])
        # Debounced: coalesces the ~1/s updates while mowing into one
        # actual disk write roughly every _TRAIL_SAVE_DELAY seconds.
        self._trail_store(device_id).async_delay_save(
            lambda: list(trail), self._TRAIL_SAVE_DELAY
        )
        if self.data is not None and device_id in self.data:
            self.async_set_updated_data(self.data)
