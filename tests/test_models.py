"""Tests for E.ON Next data normalization."""

import importlib.util
import sys
import types
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


def _load_models():
    """Load the pure model module without importing Home Assistant."""
    root = Path(__file__).parents[1]
    package_name = "custom_components.eon_next"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root / "custom_components" / "eon_next")]
    sys.modules.setdefault(package_name, package)
    module_name = f"{package_name}.models"
    spec = importlib.util.spec_from_file_location(
        module_name, root / "custom_components" / "eon_next" / "models.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


models = _load_models()


class RegisterPointTests(unittest.TestCase):
    """Test register point selection."""

    def test_filters_quarantined_and_prefers_customer_reading(self) -> None:
        """Use the highest-priority valid reading at a timestamp."""
        timestamp = datetime(2026, 8, 4, tzinfo=UTC)
        readings = (
            models.EonNextReading(
                timestamp,
                "SMART_METER",
                (models.EonNextRegisterReading("Standard", Decimal("100.125"), False),),
            ),
            models.EonNextReading(
                timestamp,
                "CUSTOMER",
                (models.EonNextRegisterReading("Standard", Decimal("100.250"), False),),
            ),
            models.EonNextReading(
                datetime(2026, 8, 5, tzinfo=UTC),
                "SMART_METER",
                (models.EonNextRegisterReading("Standard", Decimal("999"), True),),
            ),
        )

        points = models.register_points(readings, "Standard")

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].value, Decimal("100.250"))
        self.assertEqual(points[0].source, "CUSTOMER")

    def test_preserves_multiple_registers(self) -> None:
        """Select only the requested register from a multi-rate reading."""
        readings = (
            models.EonNextReading(
                datetime(2026, 8, 4, tzinfo=UTC),
                "SMART_METER",
                (
                    models.EonNextRegisterReading("Day", Decimal("10"), False),
                    models.EonNextRegisterReading("Night", Decimal("20"), False),
                ),
            ),
        )

        self.assertEqual(
            models.register_points(readings, "Night")[0].value, Decimal("20")
        )


class HistoricalStatisticsTests(unittest.TestCase):
    """Test long-term statistic generation."""

    def test_builds_cumulative_delta_and_skips_negative_correction(self) -> None:
        """Do not make consumption totals decrease on a corrected reading."""
        points = (
            models.EonNextRegisterPoint(
                datetime(2026, 1, 1, 0, 10, tzinfo=UTC), Decimal("100.0"), "SMART_METER"
            ),
            models.EonNextRegisterPoint(
                datetime(2026, 1, 1, 0, 55, tzinfo=UTC), Decimal("101.5"), "SMART_METER"
            ),
            models.EonNextRegisterPoint(
                datetime(2026, 2, 1, tzinfo=UTC), Decimal("99.0"), "ESTIMATE"
            ),
            models.EonNextRegisterPoint(
                datetime(2026, 3, 1, tzinfo=UTC), Decimal("105.0"), "SMART_METER"
            ),
        )

        result = models.historical_statistics(points)

        self.assertEqual(
            [item.state for item in result], [Decimal("101.5"), Decimal("105.0")]
        )
        self.assertEqual([item.sum for item in result], [Decimal("0"), Decimal("3.5")])
        self.assertEqual(result[0].start.minute, 0)


class SlugTests(unittest.TestCase):
    """Test register identifier normalization."""

    def test_register_slug(self) -> None:
        self.assertEqual(models.register_slug("Night / Rate 2"), "night_rate_2")

    def test_meter_reading_unique_id(self) -> None:
        meter = models.EonNextMeter(
            account_number="A-EXAMPLE",
            meter_id="meter-1",
            meter_type="electricity",
            serial_number="serial-1",
            supply_point_id="supply-1",
            registers=("Standard",),
        )

        self.assertEqual(
            models.meter_reading_unique_id(meter, "Night / Rate 2"),
            "a-example_electricity_meter-1_night_rate_2_meter_reading",
        )


if __name__ == "__main__":
    unittest.main()
