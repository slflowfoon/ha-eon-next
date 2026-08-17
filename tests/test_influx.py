"""Tests for historical InfluxDB point generation."""

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from homeassistant.core import State

from custom_components.eon_next.influx import _historical_point_to_influx
from custom_components.eon_next.models import EonNextRegisterPoint


class HistoricalInfluxPointTests(unittest.TestCase):
    """Test historical point timestamps and metadata."""

    def test_uses_meter_reading_timestamp_and_delta(self) -> None:
        """Keep the source timestamp instead of using the current time."""
        read_at = datetime(2026, 8, 4, tzinfo=UTC)
        state = State(
            "sensor.e_on_next_electricity_standard_meter_reading",
            "20254.637",
            {
                "device_class": "energy",
                "friendly_name": "E.ON Next Electricity Standard meter reading",
                "state_class": "total",
                "unit_of_measurement": "kWh",
            },
        )
        point = EonNextRegisterPoint(
            read_at=read_at,
            value=Decimal("20254.637"),
            source="SMART_METER",
        )

        result = _historical_point_to_influx(
            state,
            point,
            Decimal("19825.259"),
        )

        self.assertEqual(result["time"], read_at)
        self.assertEqual(result["measurement"], "kWh")
        self.assertEqual(
            result["tags"]["entity_id"],
            "e_on_next_electricity_standard_meter_reading",
        )
        self.assertEqual(result["fields"]["value"], 20254.637)
        self.assertAlmostEqual(
            result["fields"]["consumption_since_previous"],
            429.378,
        )
        self.assertEqual(
            result["fields"]["reading_at_str"],
            "2026-08-04T00:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
