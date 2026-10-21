"""Reconnect in place and preserve renewed credentials across form retries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.eon_next.config_flow import EonNextConfigFlow
from custom_components.eon_next.exceptions import (
    EonNextAuthenticationError,
    EonNextConnectionError,
    EonNextResponseError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["reauth_confirm", "reconfigure"])
async def test_reconnect_keeps_entry_and_settings_but_removes_password(step):
    entry = SimpleNamespace(
        data={
            "email": "person@example.com",
            "password": "obsolete-password",
            "refresh_token": "obsolete-kraken-token",
            "auth_subject": "existing-subject",
            "account_number": "A-EXAMPLE",
        }
    )
    flow = EonNextConfigFlow()
    new_data = {
        "email": "person@example.com",
        "auth_subject": "existing-subject",
        "auth_store_id": "dedicated-session",
    }
    with (
        patch.object(flow, "_get_reauth_entry", return_value=entry),
        patch.object(flow, "_get_reconfigure_entry", return_value=entry),
        patch.object(
            flow, "_async_validate", AsyncMock(return_value=new_data)
        ) as validate,
        patch.object(flow, "async_update_reload_and_abort", return_value={}) as update,
    ):
        await flow._async_reconnect(step, {"refresh_token": " new-session "})
    validate.assert_awaited_once_with(
        "person@example.com", "new-session", "existing-subject"
    )
    assert update.call_args.args == (entry,)
    assert update.call_args.kwargs["data"] == {
        **new_data,
        "account_number": "A-EXAMPLE",
    }
    assert update.call_args.kwargs["reason"] == (
        "reauth_successful" if step == "reauth_confirm" else "reconfigure_successful"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (EonNextAuthenticationError, "invalid_auth"),
        (EonNextConnectionError, "cannot_connect"),
        (EonNextResponseError, "invalid_response"),
    ],
)
async def test_reconnect_failure_does_not_update_existing_entry(error, expected):
    flow = EonNextConfigFlow()
    entry = SimpleNamespace(data={"email": "person@example.com"})
    with (
        patch.object(flow, "_get_reauth_entry", return_value=entry),
        patch.object(flow, "_async_validate", AsyncMock(side_effect=error("private"))),
        patch.object(flow, "async_show_form", return_value={}) as form,
        patch.object(flow, "async_update_reload_and_abort") as update,
    ):
        await flow._async_reconnect("reauth_confirm", {"refresh_token": "session"})
    update.assert_not_called()
    assert form.call_args.kwargs["errors"] == {"base": expected}


@pytest.mark.asyncio
async def test_form_retry_keeps_same_auth_session_after_later_failure(tmp_path):
    flow = EonNextConfigFlow()
    flow.hass = HomeAssistant(str(tmp_path))
    client = MagicMock()
    client.async_get_accounts = AsyncMock(
        side_effect=[EonNextConnectionError("unavailable"), ["A-EXAMPLE"]]
    )
    with (
        patch("custom_components.eon_next.config_flow.async_get_clientsession"),
        patch(
            "custom_components.eon_next.config_flow.EonNextApi", return_value=client
        ) as constructor,
        patch("custom_components.eon_next.config_flow.Auth0Session") as auth,
    ):
        auth.return_value.subject = "verified-subject"
        with pytest.raises(EonNextConnectionError):
            await flow._async_validate("person@example.com", "seed")
        store_id = flow._store_id
        data = await flow._async_validate("person@example.com", "seed")
    constructor.assert_called_once()
    assert data["auth_store_id"] == store_id
    assert data["auth_subject"] == "verified-subject"
    assert "refresh_token" not in data


@pytest.mark.asyncio
async def test_accountless_session_is_not_added(tmp_path):
    flow = EonNextConfigFlow()
    flow.hass = HomeAssistant(str(tmp_path))
    client = MagicMock(async_get_accounts=AsyncMock(return_value=[]))
    with (
        patch("custom_components.eon_next.config_flow.async_get_clientsession"),
        patch("custom_components.eon_next.config_flow.EonNextApi", return_value=client),
        pytest.raises(EonNextAuthenticationError),
    ):
        await flow._async_validate("person@example.com", "seed")
