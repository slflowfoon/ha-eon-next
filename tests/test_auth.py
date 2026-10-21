"""Auth0 renewal, concurrency, failure recovery and credential safety."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import aiohttp
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from test_api import FakeResponse

from custom_components.eon_next.api import EonNextApi
from custom_components.eon_next.auth import Auth0Session
from custom_components.eon_next.const import AUTH0_CLIENT_ID, AUTH0_ISSUER
from custom_components.eon_next.exceptions import (
    EonNextAuthenticationError,
    EonNextConnectionError,
    EonNextResponseError,
)

EMAIL = "person@example.com"


@pytest.fixture
def identity():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test-key"

    def token(**changes):
        claims = {
            "iss": AUTH0_ISSUER,
            "aud": AUTH0_CLIENT_ID,
            "sub": "test-subject",
            "email": EMAIL,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            **changes,
        }
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})

    return token, jwk


class Session:
    def __init__(self, identity, responses):
        self.token, self.jwk = identity
        self.responses = responses
        self.requests = []
        self.key_requests = 0
        self.fail_keys = False

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        assert url == AUTH0_ISSUER + ".well-known/jwks.json"
        self.key_requests += 1
        if self.fail_keys:
            raise aiohttp.ClientError("sensitive remote text")
        return FakeResponse({"keys": [self.jwk]})


def credentials(identity, refresh="rotated", **claims):
    return FakeResponse({"id_token": identity[0](**claims), "refresh_token": refresh})


def auth(session, save=None, **kwargs):
    return Auth0Session(
        session,
        refresh_token="seed",
        email=EMAIL,
        save_refresh_token=save or AsyncMock(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_refresh(identity):
    session = Session(identity, [credentials(identity)])
    save = AsyncMock()
    client = auth(session, save)
    values = await asyncio.gather(*(client.async_get_auth() for _ in range(20)))
    assert len(session.requests) == 1
    assert all(value == values[0] for value in values)
    save.assert_awaited_once_with("rotated")
    assert session.requests[0][1]["allow_redirects"] is False
    assert session.requests[0][1]["json"]["grant_type"] == "refresh_token"
    assert "rotated" not in repr(values[0])
    assert values[0].access_token not in repr(values[0])


@pytest.mark.asyncio
async def test_failed_persistence_does_not_rotate_again(identity):
    session = Session(identity, [credentials(identity)])
    save = AsyncMock(side_effect=[EonNextResponseError("disk full"), None])
    client = auth(session, save)
    with pytest.raises(EonNextResponseError):
        await client.async_get_auth()
    assert session.key_requests == 0
    result = await client.async_get_auth()
    assert result.refresh_token == "rotated"
    assert len(session.requests) == 1
    assert save.await_count == 2


@pytest.mark.asyncio
async def test_failed_key_lookup_keeps_rotated_token(identity):
    session = Session(identity, [credentials(identity)])
    session.fail_keys = True
    save = AsyncMock()
    client = auth(session, save)
    with pytest.raises(EonNextConnectionError) as error:
        await client.async_get_auth()
    assert "sensitive" not in str(error.value)
    session.fail_keys = False
    await client.async_get_auth()
    assert len(session.requests) == 1
    save.assert_awaited_with("rotated")


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "another@example.com"},
        {"iss": "https://wrong.invalid/"},
        {"aud": "wrong-client"},
        {"exp": int(time.time()) - 120},
        {"sub": "another-subject"},
    ],
)
@pytest.mark.asyncio
async def test_rejects_wrong_identity_after_saving_rotation(identity, changes):
    session = Session(identity, [credentials(identity, **changes)])
    save = AsyncMock()
    client = auth(session, save, subject="test-subject")
    for _ in range(2):
        with pytest.raises(EonNextAuthenticationError):
            await client.async_get_auth()
    save.assert_awaited_once_with("rotated")
    assert len(session.requests) == 1


@pytest.mark.parametrize(
    "response,error",
    [
        (
            FakeResponse(
                {"error": "invalid_grant", "error_description": "secret"}, 403
            ),
            EonNextAuthenticationError,
        ),
        (FakeResponse({"error": "invalid_grant"}, 400), EonNextAuthenticationError),
        (FakeResponse({}, 429), EonNextConnectionError),
        (FakeResponse({}, 503), EonNextConnectionError),
        (aiohttp.ClientError("secret"), EonNextConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_auth_vs_transient_errors_are_redacted(identity, response, error):
    session = Session(identity, [response])
    save = AsyncMock()
    with pytest.raises(error) as caught:
        await auth(session, save).async_get_auth()
    assert "secret" not in str(caught.value)
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_rotation_returned_keeps_current_token(identity):
    session = Session(identity, [FakeResponse({"id_token": identity[0]()})])
    result = await auth(session).async_get_auth()
    assert result.refresh_token == "seed"


@pytest.mark.asyncio
async def test_graphql_retry_uses_new_id_token_and_only_once(identity):
    accounts = {"data": {"viewer": {"accounts": [{"number": "example"}]}}}
    session = Session(
        identity,
        [
            credentials(identity, "first"),
            FakeResponse({}, 401),
            credentials(identity, "second"),
            FakeResponse(accounts),
        ],
    )
    saves = []

    async def save(token):
        saves.append((token, len(session.requests)))

    api = EonNextApi(session, auth0=auth(session, save))
    assert (await api.async_get_accounts())[0].account_number == "example"
    assert saves == [("first", 1), ("second", 3)]
    assert session.requests[1][1]["headers"]["Authorization"].startswith("ey")
    assert session.requests[2][1]["json"]["refresh_token"] == "first"


@pytest.mark.asyncio
async def test_graphql_persistent_rejection_does_not_loop(identity):
    session = Session(
        identity,
        [
            credentials(identity),
            FakeResponse({}, 401),
            credentials(identity, "second"),
            FakeResponse({}, 403),
        ],
    )
    with pytest.raises(EonNextAuthenticationError):
        await EonNextApi(session, auth0=auth(session)).async_get_accounts()
    assert len(session.requests) == 4


@pytest.mark.asyncio
async def test_expired_legacy_password_api_never_echoes_input(identity):
    session = Session(
        identity,
        [
            FakeResponse(
                {"errors": [{"message": "invalid input password=secret-token"}]}, 400
            )
        ],
    )
    with pytest.raises(EonNextAuthenticationError) as caught:
        await EonNextApi(session).async_login(EMAIL, "secret-token")
    assert "secret-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_reloading_consumes_latest_durably_stored_token(identity):
    session = Session(
        identity, [credentials(identity, "one"), credentials(identity, "two")]
    )
    stored = {}

    async def save(token):
        stored["token"] = token

    await auth(session, save).async_get_auth()
    reloaded = Auth0Session(
        session, email=EMAIL, refresh_token=stored["token"], save_refresh_token=save
    )
    await reloaded.async_get_auth()
    assert session.requests[1][1]["json"]["refresh_token"] == "one"
    assert stored["token"] == "two"
