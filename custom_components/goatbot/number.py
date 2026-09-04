"""Number platform for Goatbot."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CMD_SET_CUTTING_HEIGHT,
    CUTTING_HEIGHT_MAX,
    CUTTING_HEIGHT_MIN,
    CUTTING_HEIGHT_STEP,
)
from .coordinator import GoatbotCoordinator
from .entity import GoatbotEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Goatbot cutting-height control from a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    async_add_entities(
        GoatbotCuttingHeightNumber(coordinator, device_id) for device_id in coordinator.data
    )


class GoatbotCuttingHeightNumber(GoatbotEntity, NumberEntity):
    """Sets the mower's cutting height, in millimeters.

    The min/max/step here reflect what's been exercised successfully
    against a real Unicut H1 (default 65mm, tested down to 45mm) - the
    device's actual hardware limits may differ; if a value is rejected,
    the cloud API call simply fails without changing anything.
    """

    _attr_translation_key = "cutting_height"
    _attr_icon = "mdi:grass"
    _attr_native_min_value = CUTTING_HEIGHT_MIN
    _attr_native_max_value = CUTTING_HEIGHT_MAX
    _attr_native_step = CUTTING_HEIGHT_STEP
    _attr_native_unit_of_measurement = "mm"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: GoatbotCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_cutting_height_control"

    @property
    def native_value(self) -> float | None:
        return self._data.get("cutting_height")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.api.async_send_command(
            self._device_id,
            {"cmdId": CMD_SET_CUTTING_HEIGHT, "cutting_height": int(value)},
        )
        await self.coordinator.async_request_refresh()
