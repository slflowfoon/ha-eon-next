"""Historical InfluxDB export for E.ON Next meter readings."""

from __future__ import annotations

import logging
import math
from decimal import Decimal
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .models import (
    EonNextMeterData,
    EonNextRegisterPoint,
    meter_reading_unique_id,
    register_points,
)

_LOGGER = logging.getLogger(__name__)

INFLUXDB_DOMAIN = "influxdb"


def _historical_point_to_influx(
    state: State,
    point: EonNextRegisterPoint,
    previous_value: Decimal | None,
) -> dict[str, Any]:
    """Build a historical point matching Home Assistant's InfluxDB schema."""
    attributes = dict(state.attributes)
    delta = point.value - previous_value if previous_value is not None else None
    attributes.update(
        {
            "reading_at": point.read_at.isoformat(),
            "source": point.source,
            "previous_reading": (
                float(previous_value) if previous_value is not None else None
            ),
            "consumption_since_previous": (
                float(delta) if delta is not None and delta >= 0 else None
            ),
        }
    )

    fields: dict[str, Any] = {"value": float(point.value)}
    tags = {
        "domain": state.domain,
        "entity_id": state.object_id,
        "source": "HA",
    }
    if friendly_name := attributes.pop("friendly_name", None):
        tags["friendly_name"] = str(friendly_name)

    measurement = str(attributes.pop("unit_of_measurement", None) or state.entity_id)
    for key, value in attributes.items():
        try:
            numeric_value = float(value)
        except TypeError, ValueError:
            fields[f"{key}_str"] = str(value)
            continue
        if math.isfinite(numeric_value):
            fields[key] = numeric_value

    return {
        "measurement": measurement,
        "tags": tags,
        "time": point.read_at,
        "fields": fields,
    }


async def async_export_historical_readings(
    hass: HomeAssistant, meter_data: dict[str, EonNextMeterData]
) -> None:
    """Write dated meter readings through loaded InfluxDB integrations."""
    influx_entries = [
        entry
        for entry in hass.config_entries.async_entries(INFLUXDB_DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not influx_entries:
        _LOGGER.debug("No loaded InfluxDB integration; skipping historical export")
        return

    entity_registry = er.async_get(hass)
    influx_points: list[dict[str, Any]] = []

    for data in meter_data.values():
        meter = data.meter
        for register_name in meter.registers:
            unique_id = meter_reading_unique_id(meter, register_name)
            entity_id = entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if (
                entity_id is None
                or (current_state := hass.states.get(entity_id)) is None
            ):
                _LOGGER.warning(
                    "Cannot export historical readings because entity %s is "
                    "unavailable",
                    unique_id,
                )
                continue

            points = register_points(data.readings, register_name)
            previous_value = None
            for point in points:
                influx_points.append(
                    _historical_point_to_influx(
                        current_state,
                        point,
                        previous_value,
                    )
                )
                previous_value = point.value

    if not influx_points:
        return

    for entry in influx_entries:
        runtime: Any = entry.runtime_data
        writer = getattr(runtime, "write_to_influxdb", None)
        if not callable(writer):
            _LOGGER.warning(
                "InfluxDB entry %s does not expose the expected writer interface",
                entry.entry_id,
            )
            continue

        await hass.async_add_executor_job(writer, influx_points)
        _LOGGER.info(
            "Wrote %s historical E.ON Next readings to InfluxDB",
            len(influx_points),
        )
