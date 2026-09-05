"""Lawn mower platform for Goatbot."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CMD_RETURN_TO_DOCK,
    CMD_TASK_CONTROL,
    TASK_CONTROL_PAUSE,
    TASK_CONTROL_START,
)
from .coordinator import GoatbotCoordinator
from .entity import GoatbotEntity

# task_state values observed from the cloud API/MQTT while mowing or
# heading back to the dock (both "start" and "running" show up during
# each of those phases).
_ACTIVE_TASK_STATES = {"start", "running"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Goatbot lawn mower from a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    async_add_entities(
        GoatbotLawnMower(coordinator, device_id) for device_id in coordinator.data
    )


class GoatbotLawnMower(GoatbotEntity, LawnMowerEntity):
    """Represents the mower's overall mowing task."""

    _attr_name = None
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: GoatbotCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_mower"

    @property
    def activity(self) -> LawnMowerActivity | None:
        if self._state.get("errorCode"):
            return LawnMowerActivity.ERROR
        task_state = self._data.get("task_state")
        if task_state == "pause":
            return LawnMowerActivity.PAUSED
        if task_state in _ACTIVE_TASK_STATES:
            return LawnMowerActivity.MOWING
        if task_state == "idle":
            # "idle" covers both "docked and charging" and "stopped out on
            # the lawn" (e.g. after the Stop button) - the mower isn't
            # actually docked unless it's also charging.
            if self._state.get("charging"):
                return LawnMowerActivity.DOCKED
            return LawnMowerActivity.PAUSED
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the lawn map and live position for a custom map card.

        The map (boundary/zones/dock) comes from the polled `/maps/active`
        response; `x`/`y`/`heading` are pushed in real time over MQTT (see
        realtime.py) and are only present once at least one update has
        arrived since HA started.
        """
        attrs: dict[str, Any] = {}
        lawn_map = self._device.get("map")
        if lawn_map:
            attrs["map_graph"] = lawn_map.get("mapGraph")
            attrs["region_info"] = lawn_map.get("regionInfo")
            attrs["base_info"] = lawn_map.get("baseInfo")
        position = self.coordinator.live_position.get(self._device_id)
        if position:
            attrs["x"] = position.get("x")
            attrs["y"] = position.get("y")
            attrs["heading"] = position.get("heading")
            attrs["cut_progress"] = position.get("cut_progress")
        return attrs

    async def async_start_mowing(self) -> None:
        await self.coordinator.api.async_send_command(
            self._device_id, {"cmdId": CMD_TASK_CONTROL, **TASK_CONTROL_START}
        )
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        await self.coordinator.api.async_send_command(
            self._device_id, {"cmdId": CMD_TASK_CONTROL, **TASK_CONTROL_PAUSE}
        )
        await self.coordinator.async_request_refresh()

    async def async_dock(self) -> None:
        await self.coordinator.api.async_send_command(
            self._device_id, {"cmdId": CMD_RETURN_TO_DOCK}
        )
        await self.coordinator.async_request_refresh()
