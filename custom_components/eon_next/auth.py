"""Auth0 sessions, independent of the phone/browser that performed sign-in."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
import jwt

from .const import AUTH0_CLIENT_ID, AUTH0_ISSUER
from .exceptions import (
    EonNextAuthenticationError,
    EonNextConnectionError,
    EonNextError,
    EonNextResponseError,
)
from .models import EonNextAuth


class Auth0Session:
    """Serialize renewal and durably save rotated tokens before API requests."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        refresh_token: str,
        email: str,
        save_refresh_token: Callable[[str], Awaitable[None]],
        subject: str | None = None,
    ) -> None:
        self._session = session
        self._refresh_token = refresh_token
        self._email = email.strip().casefold()
        self._save_refresh_token = save_refresh_token
        self.subject = subject
        self._auth: EonNextAuth | None = None
        self._lock = asyncio.Lock()
        self._pending: dict[str, Any] | None = None
        self._rejected = False
        self._keys: list[dict[str, Any]] = []

    def invalidate(self, rejected_token: str) -> None:
        """Do not invalidate newer credentials following a concurrent 401."""
        if self._auth and self._auth.access_token == rejected_token:
            self._auth = None

    async def async_get_auth(self) -> EonNextAuth:
        """Return verified credentials, renewing at most once concurrently."""
        async with self._lock:
            if self._rejected:
                raise EonNextAuthenticationError("Sign in to E.ON Next again")
            if self._auth and self._auth.access_token_expires_at > time.time() + 60:
                return self._auth
            if self._pending is None:
                self._pending = await self._async_request_tokens()
                replacement = self._pending.get("refresh_token")
                if replacement is not None:
                    if not isinstance(replacement, str) or not replacement:
                        self._rejected = True
                        raise EonNextAuthenticationError("Invalid replacement session")
                    self._refresh_token = replacement

            # Keep the response in memory until both persistence and validation
            # succeed. A disk/JWKS failure must not replay a consumed token.
            await self._save_refresh_token(self._refresh_token)
            claims = await self._async_verify_id_token(self._pending.get("id_token"))
            self._auth = EonNextAuth(
                self._pending["id_token"], int(claims["exp"]), self._refresh_token
            )
            self.subject = claims["sub"]
            self._pending = None
            return self._auth

    async def _async_request_tokens(self) -> dict[str, Any]:
        try:
            async with self._session.post(
                AUTH0_ISSUER + "oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "client_id": AUTH0_CLIENT_ID,
                    "refresh_token": self._refresh_token,
                },
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=False,
            ) as response:
                if response.status == 429 or response.status >= 500:
                    raise EonNextConnectionError("E.ON sign-in temporarily unavailable")
                data = await response.json(content_type=None)
                if response.status in (400, 401, 403):
                    self._rejected = True
                    raise EonNextAuthenticationError("Sign in to E.ON Next again")
                if response.status != 200 or not isinstance(data, dict):
                    raise EonNextResponseError("Unexpected E.ON sign-in response")
                if data.get("error"):
                    self._rejected = True
                    raise EonNextAuthenticationError("Sign in to E.ON Next again")
                return data
        except EonNextError:
            raise
        except aiohttp.ClientError, TimeoutError:
            raise EonNextConnectionError("Unable to reach E.ON sign-in") from None
        except ValueError, json.JSONDecodeError:
            raise EonNextResponseError("Invalid E.ON sign-in response") from None

    async def _async_verify_id_token(self, token: Any) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise ValueError
        except jwt.PyJWTError, ValueError, TypeError:
            self._rejected = True
            raise EonNextAuthenticationError("Invalid E.ON identity token") from None

        key = next((key for key in self._keys if key.get("kid") == header["kid"]), None)
        if key is None:
            try:
                async with self._session.get(
                    AUTH0_ISSUER + ".well-known/jwks.json",
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise EonNextConnectionError("Unable to verify E.ON identity")
                    data = await response.json(content_type=None)
                self._keys = data["keys"]
                if not isinstance(self._keys, list) or not all(
                    isinstance(item, dict) for item in self._keys
                ):
                    raise ValueError
            except EonNextError:
                raise
            except aiohttp.ClientError, TimeoutError:
                raise EonNextConnectionError("Unable to verify E.ON identity") from None
            except KeyError, TypeError, ValueError:
                self._keys = []
                raise EonNextResponseError("Invalid E.ON signing keys") from None
            key = next(
                (key for key in self._keys if key.get("kid") == header["kid"]), None
            )
        try:
            if key is None:
                raise ValueError
            claims = jwt.decode(
                token,
                jwt.PyJWK.from_dict(key).key,
                algorithms=["RS256"],
                audience=AUTH0_CLIENT_ID,
                issuer=AUTH0_ISSUER,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub", "email"],
                    "strict_aud": True,
                },
                leeway=30,
            )
            if not isinstance(claims["email"], str):
                raise ValueError
            if claims["email"].strip().casefold() != self._email:
                raise ValueError
            if self.subject is not None and claims["sub"] != self.subject:
                raise ValueError
        except jwt.PyJWTError, ValueError, TypeError, KeyError:
            self._rejected = True
            raise EonNextAuthenticationError(
                "E.ON identity did not match account"
            ) from None
        return claims
