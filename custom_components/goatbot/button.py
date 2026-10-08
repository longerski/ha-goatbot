"""Button platform for Goatbot."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CMD_STOP
from .coordinator import GoatbotCoordinator
from .entity import GoatbotEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Goatbot buttons from a config entry."""
    coordinator: GoatbotCoordinator = entry.runtime_data
    entities: list[GoatbotEntity] = []
    for device_id in coordinator.data:
        entities.append(GoatbotStopButton(coordinator, device_id))
        entities.append(GoatbotResetCoverageButton(coordinator, device_id))
    async_add_entities(entities)


class GoatbotStopButton(GoatbotEntity, ButtonEntity):
    """Immediately halts the current task in place.

    Unlike Dock, this does not send the mower back to its charging
    station - it just stops wherever it currently is.
    """

    _attr_translation_key = "stop"
    _attr_icon = "mdi:stop"

    def __init__(self, coordinator: GoatbotCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_stop"

    async def async_press(self) -> None:
        await self.coordinator.api.async_send_command(self._device_id, {"cmdId": CMD_STOP})
        await self.coordinator.async_request_refresh()


class GoatbotResetCoverageButton(GoatbotEntity, ButtonEntity):
    """Wipe the accumulated "what's been mown" coverage map.

    The coverage grid otherwise only clears itself when the mower starts a
    fresh whole-lawn cycle; this is the manual override.
    """

    _attr_translation_key = "reset_coverage"
    _attr_icon = "mdi:map-marker-remove-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GoatbotCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_reset_coverage"

    async def async_press(self) -> None:
        await self.coordinator.async_reset_coverage(self._device_id)
