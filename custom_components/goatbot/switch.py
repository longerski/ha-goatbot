"""Switch platform for Goatbot."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_SET_RAIN_SENSOR
from .coordinator import GoatbotCoordinator
from .entity import GoatbotEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Goatbot rain-sensor switch from a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    async_add_entities(
        GoatbotRainSensorSwitch(coordinator, device_id) for device_id in coordinator.data
    )


class GoatbotRainSensorSwitch(GoatbotEntity, SwitchEntity):
    """Enables/disables the mower's rain sensor."""

    _attr_translation_key = "rain_sensor"
    _attr_icon = "mdi:weather-pouring"

    def __init__(self, coordinator: GoatbotCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_rain_sensor"

    @property
    def is_on(self) -> bool:
        return bool(self._data.get("rain_sensor"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(0)

    async def _async_set(self, value: int) -> None:
        await self.coordinator.api.async_send_command(
            self._device_id, {"cmdId": CMD_SET_RAIN_SENSOR, "rain_sensor": value}
        )
        await self.coordinator.async_request_refresh()
