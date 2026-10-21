"""Exercise the real Home Assistant private storage implementation."""

import stat
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.eon_next.auth_storage import AuthStore
from custom_components.eon_next.exceptions import EonNextResponseError


@pytest.mark.asyncio
async def test_private_store_survives_a_new_instance(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    store = AuthStore(hass, "test-session")
    assert await store.async_load() is None
    await store.async_save("first")
    await store.async_save("replacement")
    assert await AuthStore(hass, "test-session").async_load() == "replacement"
    path = tmp_path / ".storage/eon_next.auth_test-session"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    await store.async_remove()
    assert not path.exists()


@pytest.mark.asyncio
async def test_save_failure_is_detected_even_when_store_does_not_raise(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    store = AuthStore(hass, "test-session")
    await store.async_save("first")
    with (
        patch.object(store._store, "async_save", AsyncMock()),
        pytest.raises(EonNextResponseError),
    ):
        await store.async_save("replacement")
    assert await store.async_load() == "first"
