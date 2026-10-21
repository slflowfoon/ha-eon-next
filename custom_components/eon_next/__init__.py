"""The E.ON Next integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import Auth0Session
from .auth_storage import AuthStore
from .const import CONF_AUTH_STORE_ID, CONF_AUTH_SUBJECT, CONF_EMAIL
from .coordinator import EonNextConfigEntry, EonNextCoordinator
from .influx import async_export_historical_readings

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: EonNextConfigEntry) -> bool:
    """Set up E.ON Next from a config entry."""
    if not (store_id := entry.data.get(CONF_AUTH_STORE_ID)):
        raise ConfigEntryAuthFailed("Reconnect using the E.ON browser-login helper")
    store = AuthStore(hass, store_id)
    if not (refresh_token := await store.async_load()):
        raise ConfigEntryAuthFailed("Reconnect using the E.ON browser-login helper")
    auth = Auth0Session(
        async_get_clientsession(hass),
        refresh_token=refresh_token,
        email=entry.data[CONF_EMAIL],
        subject=entry.data.get(CONF_AUTH_SUBJECT),
        save_refresh_token=store.async_save,
    )
    coordinator = EonNextCoordinator(hass, entry, auth)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.enable_influx_export()
    await async_export_historical_readings(hass, coordinator.data)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EonNextConfigEntry) -> bool:
    """Unload an E.ON Next config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: EonNextConfigEntry) -> None:
    """Remove dedicated credentials when the integration entry is deleted."""
    if store_id := entry.data.get(CONF_AUTH_STORE_ID):
        await AuthStore(hass, store_id).async_remove()
