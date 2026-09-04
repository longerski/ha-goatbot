"""Sensor platform for Goatbot."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoatbotCoordinator
from .entity import GoatbotEntity


@dataclass(frozen=True, kw_only=True)
class GoatbotSensorEntityDescription(SensorEntityDescription):
    """Describes a Goatbot sensor derived from a device's state payload."""

    value_fn: Callable[[dict[str, Any]], Any]


def _hours(device: dict[str, Any]) -> float | None:
    seconds = device.get("state", {}).get("workTotalTime")
    return round(seconds / 3600, 1) if seconds is not None else None


SENSOR_TYPES: tuple[GoatbotSensorEntityDescription, ...] = (
    GoatbotSensorEntityDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("state", {}).get("batteryLevel"),
    ),
    GoatbotSensorEntityDescription(
        key="task_state",
        translation_key="task_state",
        value_fn=lambda d: d.get("state", {}).get("data", {}).get("task_state"),
    ),
    GoatbotSensorEntityDescription(
        key="work_mode",
        translation_key="work_mode",
        value_fn=lambda d: d.get("state", {}).get("data", {}).get("work_mode"),
    ),
    GoatbotSensorEntityDescription(
        key="error_code",
        translation_key="error_code",
        value_fn=lambda d: d.get("state", {}).get("errorCode"),
    ),
    GoatbotSensorEntityDescription(
        key="cutting_height",
        translation_key="cutting_height",
        native_unit_of_measurement="mm",
        value_fn=lambda d: d.get("state", {}).get("data", {}).get("cutting_height"),
    ),
    GoatbotSensorEntityDescription(
        key="total_area",
        translation_key="total_area",
        native_unit_of_measurement="m²",
        value_fn=lambda d: d.get("state", {}).get("data", {}).get("totalArea"),
    ),
    GoatbotSensorEntityDescription(
        key="work_total_time",
        translation_key="work_total_time",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_hours,
    ),
    GoatbotSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("state", {}).get("data", {}).get("version"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Goatbot sensors from a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    async_add_entities(
        GoatbotSensor(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in SENSOR_TYPES
    )


class GoatbotSensor(GoatbotEntity, SensorEntity):
    """A single read-only value derived from a mower's state payload."""

    entity_description: GoatbotSensorEntityDescription

    def __init__(
        self,
        coordinator: GoatbotCoordinator,
        device_id: str,
        description: GoatbotSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._device)
