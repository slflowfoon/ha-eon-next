"""Config flow for E.ON Next."""

from __future__ import annotations

from typing import Any, override

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    EonNextApi,
    EonNextAuthenticationError,
    EonNextConnectionError,
    EonNextError,
)
from .const import CONF_EMAIL, CONF_REFRESH_TOKEN, DOMAIN


async def _async_validate_credentials(
    hass: HomeAssistant, email: str, password: str
) -> str:
    """Validate credentials and return a renewable refresh token."""
    api = EonNextApi(async_get_clientsession(hass))
    auth = await api.async_login(email, password)
    if not await api.async_get_accounts():
        raise EonNextAuthenticationError("No E.ON Next accounts were found")
    return auth.refresh_token


class EonNextConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle an E.ON Next config flow."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle initial setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().casefold()
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            try:
                refresh_token = await _async_validate_credentials(
                    self.hass, email, user_input[CONF_PASSWORD]
                )
            except EonNextAuthenticationError:
                errors["base"] = "invalid_auth"
            except EonNextConnectionError:
                errors["base"] = "cannot_connect"
            except EonNextError:
                errors["base"] = "invalid_response"
            except Exception:
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="E.ON Next",
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _credentials_schema(include_email=True), user_input
            ),
            errors=errors,
        )

    @override
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Begin reauthentication for an expired refresh token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and replace the refresh token."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        email = reauth_entry.data[CONF_EMAIL]

        if user_input is not None:
            try:
                refresh_token = await _async_validate_credentials(
                    self.hass, email, user_input[CONF_PASSWORD]
                )
            except EonNextAuthenticationError:
                errors["base"] = "invalid_auth"
            except EonNextConnectionError:
                errors["base"] = "cannot_connect"
            except EonNextError:
                errors["base"] = "invalid_response"
            except Exception:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REFRESH_TOKEN: refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(include_email=False),
            errors=errors,
            description_placeholders={"email": email},
        )


def _credentials_schema(*, include_email: bool) -> vol.Schema:
    """Return the credentials form schema."""
    schema: dict[vol.Marker, Any] = {}
    if include_email:
        schema[vol.Required(CONF_EMAIL)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL)
        )
    schema[vol.Required(CONF_PASSWORD)] = TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )
    return vol.Schema(schema)
