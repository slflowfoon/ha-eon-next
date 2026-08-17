"""Long-term statistics import for E.ON Next meter readings."""

from __future__ import annotations

import logging

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter

from .const import DOMAIN, METER_TYPE_ELECTRICITY
from .models import (
    EonNextMeter,
    EonNextMeterData,
    historical_statistics,
    register_points,
    register_slug,
)

_LOGGER = logging.getLogger(__name__)


def statistic_id(meter: EonNextMeter, register_name: str) -> str:
    """Return the external statistic ID for a meter register."""
    return (
        f"{DOMAIN}:{meter.meter_type}_{meter.meter_id}_"
        f"{register_slug(register_name)}_meter_reading"
    ).lower()


def async_import_meter_statistics(
    hass: HomeAssistant, meter_data: EonNextMeterData
) -> None:
    """Import all available dated readings for a meter."""
    meter = meter_data.meter
    is_electricity = meter.meter_type == METER_TYPE_ELECTRICITY
    unit = UnitOfEnergy.KILO_WATT_HOUR if is_electricity else UnitOfVolume.CUBIC_METERS
    unit_class = (
        EnergyConverter.UNIT_CLASS if is_electricity else VolumeConverter.UNIT_CLASS
    )

    for register_name in meter.registers:
        points = register_points(meter_data.readings, register_name)
        historical = historical_statistics(points)
        if not historical:
            continue

        stats = [
            StatisticData(
                start=item.start,
                state=float(item.state),
                sum=float(item.sum),
            )
            for item in historical
        ]
        meter_statistic_id = statistic_id(meter, register_name)
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=(
                f"E.ON Next {meter.meter_type.title()} "
                f"{meter.serial_number} {register_name} meter reading"
            ),
            source=DOMAIN,
            statistic_id=meter_statistic_id,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )
        _LOGGER.debug(
            "Importing %s statistics for %s",
            len(stats),
            meter_statistic_id,
        )
        async_add_external_statistics(hass, metadata, stats)
