"""Binary sensor platform for Goatbot."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoatbotCoordinator
from .entity import GoatbotEntity


@dataclass(frozen=True, kw_only=True)
class GoatbotBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Goatbot binary sensor derived from a device's state payload."""

    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSOR_TYPES: tuple[GoatbotBinarySensorEntityDescription, ...] = (
    GoatbotBinarySensorEntityDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: bool(d.get("state", {}).get("charging")),
    ),
    GoatbotBinarySensorEntityDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.get("device", {}).get("isOnline")),
    ),
    GoatbotBinarySensorEntityDescription(
        key="unfinished_mow_pending",
        translation_key="unfinished_mow_pending",
        icon="mdi:progress-clock",
        # `cover_pause_flag` only appears in state.data while a paused/
        # unfinished whole-lawn mow is waiting to be resumed - it's what
        # drives the app's "continue previous work?" prompt. Absent (None)
        # the rest of the time, so bool() gives a clean False.
        value_fn=lambda d: bool(
            d.get("state", {}).get("data", {}).get("cover_pause_flag")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Goatbot binary sensors from a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    async_add_entities(
        GoatbotBinarySensor(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in BINARY_SENSOR_TYPES
    )


class GoatbotBinarySensor(GoatbotEntity, BinarySensorEntity):
    """A boolean value derived from a mower's state payload."""

    entity_description: GoatbotBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: GoatbotCoordinator,
        device_id: str,
        description: GoatbotBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        if (
            self.entity_description.key == "charging"
            and self.coordinator.is_mowing_live(self._device_id)
        ):
            # Out on the lawn right now - the polled "charging" flag is
            # stale, left set from when it last docked.
            return False
        return self.entity_description.value_fn(self._device)
