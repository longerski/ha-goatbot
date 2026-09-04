"""Shared base entity for Goatbot devices."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GoatbotCoordinator


class GoatbotEntity(CoordinatorEntity[GoatbotCoordinator]):
    """Base entity tied to one mower (device_id) on the account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GoatbotCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        device = self._device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device.get("nickname") or device_id,
            manufacturer="Goatbot",
            model=device.get("product", {}).get("productName", "Robotic mower"),
            sw_version=device.get("state", {}).get("data", {}).get("version"),
            serial_number=device.get("device", {}).get("sn"),
        )

    @property
    def _device(self) -> dict[str, Any]:
        """Return this entity's raw device entry from the coordinator."""
        return self.coordinator.data.get(self._device_id, {})

    @property
    def _state(self) -> dict[str, Any]:
        return self._device.get("state", {})

    @property
    def _data(self) -> dict[str, Any]:
        return self._state.get("data", {})

    @property
    def available(self) -> bool:
        return super().available and self._device_id in self.coordinator.data
