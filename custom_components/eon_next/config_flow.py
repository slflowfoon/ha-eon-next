"""Set up or reconnect a dedicated E.ON Next browser-login session."""

from __future__ import annotations

from typing import Any, override
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import EonNextApi
from .auth import Auth0Session
from .auth_storage import AuthStore
from .const import (
    CONF_AUTH_STORE_ID,
    CONF_AUTH_SUBJECT,
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from .exceptions import EonNextAuthenticationError, EonNextConnectionError, EonNextError


class EonNextConfigFlow(ConfigFlow, domain=DOMAIN):
    """Import a session produced by the separate interactive login helper."""

    VERSION = 1

    def __init__(self) -> None:
        self._candidate: tuple[str, str] | None = None
        self._api: EonNextApi | None = None
        self._auth: Auth0Session | None = None
        self._store_id: str | None = None

    async def _async_validate(
        self, email: str, token: str, subject: str | None = None
    ) -> dict[str, Any]:
        # Preserve the rotated token on form retries after a later API failure.
        if self._candidate != (email, token):
            self._store_id = uuid4().hex
            store = AuthStore(self.hass, self._store_id)
            await store.async_save(token)
            session = async_get_clientsession(self.hass)
            self._auth = Auth0Session(
                session,
                refresh_token=token,
                email=email,
                subject=subject,
                save_refresh_token=store.async_save,
            )
            self._api = EonNextApi(session, auth0=self._auth)
            self._candidate = (email, token)
        assert self._api is not None and self._auth is not None
        if not await self._api.async_get_accounts():
            raise EonNextAuthenticationError("No E.ON Next accounts were found")
        return {
            CONF_EMAIL: email,
            CONF_AUTH_STORE_ID: self._store_id,
            CONF_AUTH_SUBJECT: self._auth.subject,
        }

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().casefold()
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()
            try:
                data = await self._async_validate(
                    email, user_input[CONF_REFRESH_TOKEN].strip()
                )
            except EonNextAuthenticationError:
                errors["base"] = "invalid_auth"
            except EonNextConnectionError:
                errors["base"] = "cannot_connect"
            except EonNextError:
                errors["base"] = "invalid_response"
            else:
                return self.async_create_entry(title="E.ON Next", data=data)
        return self.async_show_form(
            step_id="user", data_schema=_credentials_schema(True), errors=errors
        )

    @override
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_reconnect("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_reconnect("reconfigure", user_input)

    async def _async_reconnect(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        entry = (
            self._get_reauth_entry()
            if step_id == "reauth_confirm"
            else self._get_reconfigure_entry()
        )
        email = entry.data[CONF_EMAIL]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_validate(
                    email,
                    user_input[CONF_REFRESH_TOKEN].strip(),
                    entry.data.get(CONF_AUTH_SUBJECT),
                )
            except EonNextAuthenticationError:
                errors["base"] = "invalid_auth"
            except EonNextConnectionError:
                errors["base"] = "cannot_connect"
            except EonNextError:
                errors["base"] = "invalid_response"
            else:
                # Keep the entry ID and settings; remove obsolete credentials.
                data = {
                    **{
                        key: value
                        for key, value in entry.data.items()
                        if key not in (CONF_PASSWORD, CONF_REFRESH_TOKEN)
                    },
                    **data,
                }
                return self.async_update_reload_and_abort(
                    entry,
                    data=data,
                    reason="reauth_successful"
                    if step_id == "reauth_confirm"
                    else "reconfigure_successful",
                )
        return self.async_show_form(
            step_id=step_id,
            data_schema=_credentials_schema(False),
            errors=errors,
            description_placeholders={"email": email},
        )


def _credentials_schema(include_email: bool) -> vol.Schema:
    schema: dict[vol.Marker, Any] = {}
    if include_email:
        schema[vol.Required(CONF_EMAIL)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL)
        )
    schema[vol.Required(CONF_REFRESH_TOKEN)] = TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )
    return vol.Schema(schema)
