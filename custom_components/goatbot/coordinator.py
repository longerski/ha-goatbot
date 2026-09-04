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
    raw entry from the `/app/devices` response.
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

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.api.async_get_devices()
        except GoatbotError as err:
            raise UpdateFailed(str(err)) from err
        return {device["deviceId"]: device for device in devices}
