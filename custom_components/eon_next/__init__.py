"""The E.ON Next integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import EonNextConfigEntry, EonNextCoordinator
from .influx import async_export_historical_readings

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: EonNextConfigEntry) -> bool:
    """Set up E.ON Next from a config entry."""
    coordinator = EonNextCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.enable_influx_export()
    await async_export_historical_readings(hass, coordinator.data)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EonNextConfigEntry) -> bool:
    """Unload an E.ON Next config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
