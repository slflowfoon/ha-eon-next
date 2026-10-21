"""Private, atomic refresh-token storage using Home Assistant's storage API."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONF_REFRESH_TOKEN, DOMAIN
from .exceptions import EonNextResponseError


class AuthStore:
    """Keep live credentials outside the delayed config-entry save queue."""

    def __init__(self, hass: HomeAssistant, store_id: str) -> None:
        self._store = Store[dict[str, str]](
            hass, 1, f"{DOMAIN}.auth_{store_id}", private=True, atomic_writes=True
        )

    async def async_load(self) -> str | None:
        data = await self._store.async_load()
        if data is None:
            return None
        token = data.get(CONF_REFRESH_TOKEN)
        if not isinstance(token, str) or not token:
            raise EonNextResponseError("Stored E.ON session is invalid")
        return token

    async def async_save(self, refresh_token: str) -> None:
        await self._store.async_save({CONF_REFRESH_TOKEN: refresh_token})
        # Store logs some disk errors rather than raising. Read-back makes a
        # failed write visible and prevents the next rotation from proceeding.
        if await self.async_load() != refresh_token:
            raise EonNextResponseError("Could not save the E.ON session")

    async def async_remove(self) -> None:
        await self._store.async_remove()
