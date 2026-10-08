"""The Goatbot robotic mower integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GoatbotApiClient, GoatbotAuthError, GoatbotError
from .const import DOMAIN
from .coordinator import GoatbotCoordinator
from .realtime import GoatbotRealtimeClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.LAWN_MOWER,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Goatbot from a config entry."""
    session = async_get_clientsession(hass)
    api = GoatbotApiClient(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )

    try:
        await api.async_login()
    except GoatbotAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GoatbotError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = GoatbotCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()

    for device_id in coordinator.data:
        await coordinator.async_load_trail(device_id)
        await coordinator.async_load_coverage(device_id)

    coordinator.realtime = GoatbotRealtimeClient(hass, api, coordinator)
    try:
        await coordinator.realtime.async_start()
    except GoatbotError as err:
        # Live position is a bonus on top of the core polled entities -
        # don't fail the whole config entry just because the real-time
        # channel couldn't be reached.
        _LOGGER.warning("Could not start the real-time position channel: %s", err)

    @callback
    def _stop_realtime(_event: Event) -> None:
        """Tear the MQTT channel down promptly on HA shutdown.

        Without this the paho network thread and the reconnect loop keep
        running through every shutdown stage (~minutes of "still running"
        warnings) because `async_unload_entry` isn't called on a plain
        Core stop.
        """
        if coordinator.realtime is not None:
            coordinator.realtime.stop_sync()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_realtime)
    )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    if coordinator.realtime is not None:
        await coordinator.realtime.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
