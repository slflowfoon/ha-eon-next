"""Data models and pure helpers for E.ON Next."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class EonNextAuth:
    """Authentication tokens returned by Kraken."""

    access_token: str
    access_token_expires_at: int
    refresh_token: str


@dataclass(frozen=True, slots=True)
class EonNextAccount:
    """An E.ON Next energy account."""

    account_number: str


@dataclass(frozen=True, slots=True)
class EonNextMeter:
    """An electricity or gas meter."""

    account_number: str
    meter_id: str
    meter_type: str
    serial_number: str
    supply_point_id: str
    registers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EonNextRegisterReading:
    """A single register value within a dated meter reading."""

    name: str
    value: Decimal
    is_quarantined: bool


@dataclass(frozen=True, slots=True)
class EonNextReading:
    """A dated meter reading returned by E.ON Next."""

    read_at: datetime
    source: str
    registers: tuple[EonNextRegisterReading, ...]
    reading_id: str | None = None


@dataclass(frozen=True, slots=True)
class EonNextMeterData:
    """A meter and all readings retrieved for it."""

    meter: EonNextMeter
    readings: tuple[EonNextReading, ...]


@dataclass(frozen=True, slots=True)
class EonNextRegisterPoint:
    """A normalized reading for one meter register."""

    read_at: datetime
    value: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class EonNextHistoricalStatistic:
    """A normalized historical statistic derived from cumulative readings."""

    start: datetime
    state: Decimal
    sum: Decimal


SOURCE_PRIORITY = {
    "ESTIMATE": 0,
    "DATA_COLLECTOR": 1,
    "SMART_METER": 2,
    "CUSTOMER": 3,
}


def register_slug(register_name: str) -> str:
    """Return a stable identifier component for a register name."""
    slug = re.sub(r"[^a-z0-9]+", "_", register_name.casefold()).strip("_")
    return slug or "register"


def meter_reading_unique_id(meter: EonNextMeter, register_name: str) -> str:
    """Return the Home Assistant unique ID for a meter register."""
    return (
        f"{meter.account_number}_{meter.meter_type}_{meter.meter_id}_"
        f"{register_slug(register_name)}_meter_reading"
    ).lower()


def register_points(
    readings: tuple[EonNextReading, ...], register_name: str
) -> tuple[EonNextRegisterPoint, ...]:
    """Return sorted, de-duplicated, non-quarantined points for a register."""
    points: dict[datetime, EonNextRegisterPoint] = {}

    for reading in readings:
        for register in reading.registers:
            if register.name != register_name or register.is_quarantined:
                continue

            candidate = EonNextRegisterPoint(
                read_at=reading.read_at,
                value=register.value,
                source=reading.source,
            )
            existing = points.get(reading.read_at)
            if existing is None or SOURCE_PRIORITY.get(
                candidate.source, -1
            ) >= SOURCE_PRIORITY.get(existing.source, -1):
                points[reading.read_at] = candidate

    return tuple(points[timestamp] for timestamp in sorted(points))


def latest_register_point(
    meter_data: EonNextMeterData, register_name: str
) -> EonNextRegisterPoint | None:
    """Return the latest usable point for a register."""
    points = register_points(meter_data.readings, register_name)
    return points[-1] if points else None


def historical_statistics(
    points: tuple[EonNextRegisterPoint, ...],
) -> tuple[EonNextHistoricalStatistic, ...]:
    """Build monotonic consumption statistics from cumulative meter readings."""
    if not points:
        return ()

    # Home Assistant stores long-term statistics on hourly boundaries. If E.ON
    # returns multiple readings in an hour, retain the latest one.
    hourly: dict[datetime, EonNextRegisterPoint] = {}
    for point in points:
        start = point.read_at.replace(minute=0, second=0, microsecond=0)
        current = hourly.get(start)
        if current is None or point.read_at >= current.read_at:
            hourly[start] = point

    result: list[EonNextHistoricalStatistic] = []
    previous_value: Decimal | None = None
    cumulative = Decimal(0)
    for start in sorted(hourly):
        point = hourly[start]
        if previous_value is not None:
            delta = point.value - previous_value
            # Corrections should not turn an import-only consumption sum
            # backwards. The next valid reading is still compared with the last
            # accepted cumulative reading.
            if delta < 0:
                continue
            cumulative += delta
        result.append(
            EonNextHistoricalStatistic(
                start=start,
                state=point.value,
                sum=cumulative,
            )
        )
        previous_value = point.value

    return tuple(result)
