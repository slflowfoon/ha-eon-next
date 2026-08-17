"""Sensor platform for E.ON Next."""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, METER_TYPE_ELECTRICITY
from .coordinator import EonNextConfigEntry, EonNextCoordinator
from .models import (
    EonNextMeterData,
    latest_register_point,
    meter_reading_unique_id,
    register_points,
    register_slug,
)
from .statistics import statistic_id

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EonNextConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up E.ON Next sensors from a config entry."""
    coordinator = entry.runtime_data
    created: set[tuple[str, str]] = set()

    @callback
    def _add_new_entities() -> None:
        entities: list[SensorEntity] = []
        for meter_key, meter_data in coordinator.data.items():
            for register_name in meter_data.meter.registers:
                key = (meter_key, register_slug(register_name))
                if key in created:
                    continue
                created.add(key)
                entities.append(
                    EonNextMeterReadingSensor(
                        coordinator, meter_key, meter_data, register_name
                    )
                )

            latest_key = (meter_key, "latest_reading_at")
            if latest_key not in created:
                created.add(latest_key)
                entities.append(
                    EonNextLatestReadingSensor(coordinator, meter_key, meter_data)
                )

        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class EonNextSensorEntity(CoordinatorEntity[EonNextCoordinator], SensorEntity):
    """Base class for E.ON Next meter sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EonNextCoordinator,
        meter_key: str,
        meter_data: EonNextMeterData,
    ) -> None:
        super().__init__(coordinator)
        self._meter_key = meter_key
        meter = meter_data.meter
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, meter_key)},
            name=f"E.ON Next {meter.meter_type.title()} meter {meter.serial_number}",
            manufacturer="E.ON Next",
            model=f"{meter.meter_type.title()} meter",
            serial_number=meter.serial_number,
            configuration_url="https://www.eonnext.com/dashboard",
        )

    @property
    def _meter_data(self) -> EonNextMeterData | None:
        """Return this meter's latest coordinator data."""
        return self.coordinator.data.get(self._meter_key)


class EonNextMeterReadingSensor(EonNextSensorEntity):
    """Represent one cumulative meter register."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: EonNextCoordinator,
        meter_key: str,
        meter_data: EonNextMeterData,
        register_name: str,
    ) -> None:
        super().__init__(coordinator, meter_key, meter_data)
        meter = meter_data.meter
        self._register_name = register_name
        self._attr_name = f"{register_name} meter reading"
        self._attr_unique_id = meter_reading_unique_id(meter, register_name)
        if meter.meter_type == METER_TYPE_ELECTRICITY:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        else:
            self._attr_device_class = SensorDeviceClass.GAS
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    @property
    @override
    def native_value(self) -> float | None:
        """Return the newest usable cumulative reading."""
        if (meter_data := self._meter_data) is None:
            return None
        point = latest_register_point(meter_data, self._register_name)
        return float(point.value) if point else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return reading provenance and the latest consumption delta."""
        if (meter_data := self._meter_data) is None:
            return {}
        points = register_points(meter_data.readings, self._register_name)
        if not points:
            return {}

        latest = points[-1]
        previous = points[-2] if len(points) > 1 else None
        delta = latest.value - previous.value if previous else None
        return {
            "reading_at": latest.read_at.isoformat(),
            "source": latest.source,
            "previous_reading": float(previous.value) if previous else None,
            "consumption_since_previous": (
                float(delta) if delta is not None and delta >= 0 else None
            ),
            "statistics_id": statistic_id(meter_data.meter, self._register_name),
            "supply_point_id": meter_data.meter.supply_point_id,
        }


class EonNextLatestReadingSensor(EonNextSensorEntity):
    """Represent the timestamp of the latest usable meter reading."""

    _attr_name = "Latest reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EonNextCoordinator,
        meter_key: str,
        meter_data: EonNextMeterData,
    ) -> None:
        super().__init__(coordinator, meter_key, meter_data)
        meter = meter_data.meter
        self._attr_unique_id = (
            f"{meter.account_number}_{meter.meter_type}_{meter.meter_id}_"
            "latest_reading_at"
        ).lower()

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the latest non-quarantined reading timestamp."""
        if (meter_data := self._meter_data) is None:
            return None
        points = [
            point
            for register_name in meter_data.meter.registers
            for point in register_points(meter_data.readings, register_name)
        ]
        return max((point.read_at for point in points), default=None)
