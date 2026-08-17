"""Tests for the asynchronous E.ON Next API client."""

import importlib
import importlib.util
import sys
import time
import types
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


def _load_api():
    """Load API modules without importing the Home Assistant integration."""
    root = Path(__file__).parents[1]
    package_name = "custom_components.eon_next"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root / "custom_components" / "eon_next")]
    sys.modules.setdefault(package_name, package)
    for name in ("const", "models"):
        module_name = f"{package_name}.{name}"
        if module_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                module_name,
                root / "custom_components" / "eon_next" / f"{name}.py",
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
    return importlib.import_module(f"{package_name}.api")


api_module = _load_api()
models = sys.modules["custom_components.eon_next.models"]


class FakeResponse:
    """Minimal asynchronous aiohttp response context manager."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self, *, content_type=None):
        return self.payload


class FakeSession:
    """Return queued GraphQL responses and retain request metadata."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def post(self, url, *, json, headers, timeout):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    """Test refresh-token authentication."""

    async def test_refreshes_before_authenticated_request(self) -> None:
        """Exchange a refresh token and use the resulting access token."""
        session = FakeSession(
            [
                FakeResponse(
                    {
                        "data": {
                            "obtainKrakenToken": {
                                "payload": {"exp": int(time.time()) + 3600},
                                "refreshToken": "rotated-refresh",
                                "token": "short-lived-access",
                            }
                        }
                    }
                ),
                FakeResponse(
                    {"data": {"viewer": {"accounts": [{"number": "A-EXAMPLE"}]}}}
                ),
            ]
        )
        client = api_module.EonNextApi(session, refresh_token="initial-refresh")

        accounts = await client.async_get_accounts()

        self.assertEqual(accounts[0].account_number, "A-EXAMPLE")
        self.assertEqual(client.refresh_token, "rotated-refresh")
        self.assertNotIn("Authorization", session.requests[0]["headers"])
        self.assertEqual(
            session.requests[1]["headers"]["Authorization"],
            "JWT short-lived-access",
        )


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    """Test cursor pagination and response parsing."""

    async def test_reads_every_page_and_preserves_precision(self) -> None:
        """Follow pageInfo until the reading connection is exhausted."""
        page_one = {
            "data": {
                "readings": {
                    "edges": [
                        {
                            "node": {
                                "id": "one",
                                "readAt": "2026-01-01T00:00:00+00:00",
                                "source": "SMART_METER",
                                "registers": [
                                    {
                                        "isQuarantined": False,
                                        "name": "Standard",
                                        "value": "100.12345",
                                    }
                                ],
                            }
                        }
                    ],
                    "pageInfo": {"endCursor": "next", "hasNextPage": True},
                }
            }
        }
        page_two = {
            "data": {
                "readings": {
                    "edges": [
                        {
                            "node": {
                                "id": "two",
                                "readAt": "2026-02-01T00:00:00Z",
                                "source": "CUSTOMER",
                                "registers": [
                                    {
                                        "isQuarantined": False,
                                        "name": "Standard",
                                        "value": "111.98765",
                                    }
                                ],
                            }
                        }
                    ],
                    "pageInfo": {"endCursor": None, "hasNextPage": False},
                }
            }
        }
        session = FakeSession([FakeResponse(page_one), FakeResponse(page_two)])
        client = api_module.EonNextApi(session, refresh_token="unused")
        client._access_token = "access"
        client._access_token_expires_at = int(time.time()) + 3600
        meter = models.EonNextMeter(
            account_number="A-EXAMPLE",
            meter_id="meter-1",
            meter_type="electricity",
            serial_number="serial-1",
            supply_point_id="supply-1",
            registers=("Standard",),
        )

        readings = await client.async_get_readings(meter, page_size=1)

        self.assertEqual(len(readings), 2)
        self.assertEqual(readings[0].registers[0].value, Decimal("100.12345"))
        self.assertEqual(readings[1].read_at, datetime(2026, 2, 1, tzinfo=UTC))
        self.assertIsNone(session.requests[0]["json"]["variables"]["cursor"])
        self.assertEqual(session.requests[1]["json"]["variables"]["cursor"], "next")


class ParsingTests(unittest.TestCase):
    """Test meter and reading parsing."""

    def test_discovers_all_meter_types_and_registers(self) -> None:
        """Parse active electricity and gas meters from account properties."""
        properties = [
            {
                "electricityMeterPoints": [
                    {
                        "id": "electric-supply",
                        "meters": [
                            {
                                "id": "electric-meter",
                                "serialNumber": "electric-serial",
                                "registers": [{"name": "Day"}, {"name": "Night"}],
                            }
                        ],
                    }
                ],
                "gasMeterPoints": [
                    {
                        "id": "gas-supply",
                        "meters": [
                            {
                                "id": "gas-meter",
                                "serialNumber": "gas-serial",
                                "registers": [{"name": "Standard"}],
                            }
                        ],
                    }
                ],
            }
        ]

        meters = api_module.parse_meters("A-EXAMPLE", properties)

        self.assertEqual([meter.meter_type for meter in meters], ["electricity", "gas"])
        self.assertEqual(meters[0].registers, ("Day", "Night"))
        self.assertEqual(meters[1].registers, ("Standard",))

    def test_drops_malformed_readings(self) -> None:
        """Ignore invalid timestamps and values without failing an update."""
        edges = [
            {
                "node": {
                    "id": "bad-time",
                    "readAt": "not-a-time",
                    "registers": [{"name": "Standard", "value": "1"}],
                }
            },
            {
                "node": {
                    "id": "bad-value",
                    "readAt": "2026-01-01T00:00:00Z",
                    "registers": [{"name": "Standard", "value": "not-a-number"}],
                }
            },
        ]

        self.assertEqual(api_module.parse_readings(edges), ())


if __name__ == "__main__":
    unittest.main()
