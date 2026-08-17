"""Asynchronous client for the E.ON Next Kraken GraphQL API."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp

from .const import (
    API_URL,
    METER_TYPE_ELECTRICITY,
    METER_TYPE_GAS,
)
from .models import (
    EonNextAccount,
    EonNextAuth,
    EonNextMeter,
    EonNextReading,
    EonNextRegisterReading,
)


class EonNextError(Exception):
    """Base E.ON Next exception."""


class EonNextAuthenticationError(EonNextError):
    """Raised when E.ON Next rejects authentication."""


class EonNextConnectionError(EonNextError):
    """Raised when E.ON Next cannot be reached."""


class EonNextResponseError(EonNextError):
    """Raised when E.ON Next returns an unexpected response."""


AUTH_MUTATION = """
mutation loginEmailAuthentication($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    payload
    refreshExpiresIn
    refreshToken
    token
  }
}
"""

ACCOUNTS_QUERY = """
query headerGetLoggedInUser {
  viewer {
    accounts {
      ... on AccountType {
        number
      }
    }
  }
}
"""

METERS_QUERY = """
query getAccountMeterSelector($accountNumber: String!, $showInactive: Boolean!) {
  properties(accountNumber: $accountNumber) {
    electricityMeterPoints {
      id
      meters(includeInactive: $showInactive) {
        activeTo
        id
        registers {
          name
        }
        serialNumber
      }
    }
    gasMeterPoints {
      id
      meters(includeInactive: $showInactive) {
        activeTo
        id
        registers {
          name
        }
        serialNumber
      }
    }
  }
}
"""

ELECTRICITY_READINGS_QUERY = """
query meterReadingsHistoryTableElectricityReadings(
  $accountNumber: String!
  $cursor: String
  $meterId: String!
  $pageSize: Int!
) {
  readings: electricityMeterReadings(
    accountNumber: $accountNumber
    after: $cursor
    first: $pageSize
    meterId: $meterId
  ) {
    edges {
      node {
        id
        readAt
        readingSource
        registers {
          isQuarantined
          name
          value
        }
        source
      }
    }
    pageInfo {
      endCursor
      hasNextPage
    }
  }
}
"""

GAS_READINGS_QUERY = """
query meterReadingsHistoryTableGasReadings(
  $accountNumber: String!
  $cursor: String
  $meterId: String!
  $pageSize: Int!
) {
  readings: gasMeterReadings(
    accountNumber: $accountNumber
    after: $cursor
    first: $pageSize
    meterId: $meterId
  ) {
    edges {
      node {
        id
        readAt
        readingSource
        registers {
          isQuarantined
          name
          value
        }
        source
      }
    }
    pageInfo {
      endCursor
      hasNextPage
    }
  }
}
"""


class EonNextApi:
    """Client for E.ON Next's undocumented Kraken GraphQL endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        refresh_token: str | None = None,
    ) -> None:
        self._session = session
        self._access_token: str | None = None
        self._access_token_expires_at = 0
        self.refresh_token = refresh_token

    async def async_login(self, email: str, password: str) -> EonNextAuth:
        """Authenticate with account credentials and retain the refresh token."""
        auth = await self._async_obtain_token({"email": email, "password": password})
        self._store_auth(auth)
        return auth

    async def async_login_with_refresh_token(self) -> EonNextAuth:
        """Authenticate with the current refresh token."""
        if not self.refresh_token:
            raise EonNextAuthenticationError("No refresh token is available")
        auth = await self._async_obtain_token({"refreshToken": self.refresh_token})
        self._store_auth(auth)
        return auth

    async def async_get_accounts(self) -> tuple[EonNextAccount, ...]:
        """Return all energy accounts available to the authenticated user."""
        result = await self._async_graphql("headerGetLoggedInUser", ACCOUNTS_QUERY, {})
        try:
            raw_accounts = result["data"]["viewer"]["accounts"]
        except (KeyError, TypeError) as err:
            raise EonNextResponseError("Account data was missing") from err

        accounts = {
            item["number"]
            for item in raw_accounts
            if isinstance(item, Mapping) and item.get("number")
        }
        return tuple(EonNextAccount(number) for number in sorted(accounts))

    async def async_get_meters(
        self, account: EonNextAccount
    ) -> tuple[EonNextMeter, ...]:
        """Return active electricity and gas meters for an account."""
        result = await self._async_graphql(
            "getAccountMeterSelector",
            METERS_QUERY,
            {"accountNumber": account.account_number, "showInactive": False},
        )
        try:
            properties = result["data"]["properties"]
        except (KeyError, TypeError) as err:
            raise EonNextResponseError("Meter data was missing") from err

        return parse_meters(account.account_number, properties)

    async def async_get_readings(
        self, meter: EonNextMeter, *, page_size: int = 100
    ) -> tuple[EonNextReading, ...]:
        """Return all available readings for a meter using cursor pagination."""
        operation = (
            "meterReadingsHistoryTableElectricityReadings"
            if meter.meter_type == METER_TYPE_ELECTRICITY
            else "meterReadingsHistoryTableGasReadings"
        )
        query = (
            ELECTRICITY_READINGS_QUERY
            if meter.meter_type == METER_TYPE_ELECTRICITY
            else GAS_READINGS_QUERY
        )
        cursor: str | None = None
        readings: list[EonNextReading] = []

        for _page in range(50):
            result = await self._async_graphql(
                operation,
                query,
                {
                    "accountNumber": meter.account_number,
                    "cursor": cursor,
                    "meterId": meter.meter_id,
                    "pageSize": page_size,
                },
            )
            try:
                connection = result["data"]["readings"]
                edges = connection["edges"]
                page_info = connection["pageInfo"]
            except (KeyError, TypeError) as err:
                raise EonNextResponseError("Reading data was missing") from err

            readings.extend(parse_readings(edges))
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == cursor:
                raise EonNextResponseError("Reading pagination did not advance")
            cursor = next_cursor
        else:
            raise EonNextResponseError("Reading pagination exceeded 50 pages")

        unique = {
            (
                reading.read_at,
                reading.reading_id,
                reading.source,
                reading.registers,
            ): reading
            for reading in readings
        }
        return tuple(sorted(unique.values(), key=lambda item: item.read_at))

    async def _async_obtain_token(self, token_input: dict[str, str]) -> EonNextAuth:
        result = await self._async_graphql(
            "loginEmailAuthentication",
            AUTH_MUTATION,
            {"input": token_input},
            authenticated=False,
        )
        try:
            token_data = result["data"]["obtainKrakenToken"]
            access_token = token_data["token"]
            refresh_token = token_data["refreshToken"]
            payload = token_data["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
            raise EonNextAuthenticationError("Authentication failed") from err

        if not access_token or not refresh_token:
            raise EonNextAuthenticationError("Authentication failed")
        return EonNextAuth(access_token, expires_at, refresh_token)

    async def _async_ensure_authenticated(self) -> None:
        if self._access_token and self._access_token_expires_at > int(time.time()) + 60:
            return
        await self.async_login_with_refresh_token()

    async def _async_graphql(
        self,
        operation: str,
        query: str,
        variables: dict[str, Any],
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if authenticated:
            await self._async_ensure_authenticated()

        headers = {"Accept": "application/json"}
        if authenticated and self._access_token:
            headers["Authorization"] = f"JWT {self._access_token}"

        try:
            async with self._session.post(
                API_URL,
                json={
                    "operationName": operation,
                    "variables": variables,
                    "query": query,
                },
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise EonNextAuthenticationError("Authentication was rejected")
                if response.status >= 400:
                    raise EonNextConnectionError(
                        f"E.ON Next returned HTTP {response.status}"
                    )
                result = await response.json(content_type=None)
        except EonNextError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EonNextConnectionError("Unable to reach E.ON Next") from err
        except (json.JSONDecodeError, ValueError) as err:
            raise EonNextResponseError("E.ON Next returned invalid JSON") from err

        if not isinstance(result, dict):
            raise EonNextResponseError("E.ON Next returned an invalid response")
        if errors := result.get("errors"):
            message = "; ".join(
                str(error.get("message", "Unknown GraphQL error"))
                for error in errors
                if isinstance(error, Mapping)
            )
            if not authenticated or any(
                marker in message.casefold()
                for marker in ("auth", "credential", "invalid data", "token")
            ):
                raise EonNextAuthenticationError(message or "Authentication failed")
            raise EonNextResponseError(message or "E.ON Next GraphQL error")
        return result

    def _store_auth(self, auth: EonNextAuth) -> None:
        self._access_token = auth.access_token
        self._access_token_expires_at = auth.access_token_expires_at
        self.refresh_token = auth.refresh_token


def parse_meters(
    account_number: str, properties: list[dict[str, Any]]
) -> tuple[EonNextMeter, ...]:
    """Parse meters from the meter-selector response."""
    meters: dict[tuple[str, str], EonNextMeter] = {}
    point_types = (
        ("electricityMeterPoints", METER_TYPE_ELECTRICITY),
        ("gasMeterPoints", METER_TYPE_GAS),
    )
    for property_data in properties:
        if not isinstance(property_data, Mapping):
            continue
        for point_key, meter_type in point_types:
            for point in property_data.get(point_key) or []:
                supply_point_id = str(point.get("id") or "")
                for meter in point.get("meters") or []:
                    meter_id = str(meter.get("id") or "")
                    serial_number = str(meter.get("serialNumber") or meter_id)
                    if not meter_id:
                        continue
                    registers = tuple(
                        sorted(
                            {
                                str(register["name"])
                                for register in meter.get("registers") or []
                                if register.get("name")
                            }
                        )
                    )
                    meters[(meter_type, meter_id)] = EonNextMeter(
                        account_number=account_number,
                        meter_id=meter_id,
                        meter_type=meter_type,
                        serial_number=serial_number,
                        supply_point_id=supply_point_id,
                        registers=registers,
                    )
    return tuple(
        sorted(meters.values(), key=lambda item: (item.meter_type, item.meter_id))
    )


def parse_readings(edges: list[dict[str, Any]]) -> tuple[EonNextReading, ...]:
    """Parse reading edges, discarding malformed records."""
    parsed: list[EonNextReading] = []
    for edge in edges:
        node = edge.get("node") if isinstance(edge, Mapping) else None
        if not isinstance(node, Mapping) or not node.get("readAt"):
            continue
        try:
            read_at = datetime.fromisoformat(str(node["readAt"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if read_at.tzinfo is None:
            read_at = read_at.replace(tzinfo=UTC)

        registers: list[EonNextRegisterReading] = []
        for register in node.get("registers") or []:
            if not isinstance(register, Mapping) or not register.get("name"):
                continue
            try:
                value = Decimal(str(register["value"]))
            except InvalidOperation, KeyError:
                continue
            registers.append(
                EonNextRegisterReading(
                    name=str(register["name"]),
                    value=value,
                    is_quarantined=bool(register.get("isQuarantined", False)),
                )
            )
        if not registers:
            continue

        parsed.append(
            EonNextReading(
                read_at=read_at.astimezone(UTC),
                source=str(
                    node.get("source") or node.get("readingSource") or "UNKNOWN"
                ),
                registers=tuple(registers),
                reading_id=str(node["id"]) if node.get("id") is not None else None,
            )
        )
    return tuple(parsed)
