"""Data update coordinator for E.ON Next."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EonNextApi,
    EonNextAuthenticationError,
    EonNextConnectionError,
    EonNextError,
)
from .const import CONF_REFRESH_TOKEN, DEFAULT_UPDATE_INTERVAL, DOMAIN
from .influx import async_export_historical_readings
from .models import EonNextMeterData
from .statistics import async_import_meter_statistics

_LOGGER = logging.getLogger(__name__)

type EonNextConfigEntry = ConfigEntry[EonNextCoordinator]


class EonNextCoordinator(DataUpdateCoordinator[dict[str, EonNextMeterData]]):
    """Fetch meter readings and import historical statistics."""

    config_entry: EonNextConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: EonNextConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="E.ON Next",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.api = EonNextApi(
            async_get_clientsession(hass),
            refresh_token=config_entry.data[CONF_REFRESH_TOKEN],
        )
        self._influx_export_enabled = False

        @callback
        def _handle_coordinator_update() -> None:
            """Export new history and keep periodic updates active."""
            if not self._influx_export_enabled or not self.data:
                return
            self.config_entry.async_create_background_task(
                self.hass,
                async_export_historical_readings(self.hass, self.data),
                "E.ON Next historical InfluxDB export",
            )

        self.async_add_listener(_handle_coordinator_update)

    @callback
    def enable_influx_export(self) -> None:
        """Enable InfluxDB replay after the sensor platform is ready."""
        self._influx_export_enabled = True

    @override
    async def _async_update_data(self) -> dict[str, EonNextMeterData]:
        try:
            accounts = await self.api.async_get_accounts()
            meter_data: dict[str, EonNextMeterData] = {}
            for account in accounts:
                meters = await self.api.async_get_meters(account)
                for meter in meters:
                    readings = await self.api.async_get_readings(meter)
                    reading_registers = {
                        register.name
                        for reading in readings
                        for register in reading.registers
                    }
                    if reading_registers.difference(meter.registers):
                        meter = replace(
                            meter,
                            registers=tuple(
                                sorted(set(meter.registers) | reading_registers)
                            ),
                        )
                    key = f"{meter.account_number}:{meter.meter_id}"
                    data = EonNextMeterData(meter=meter, readings=readings)
                    meter_data[key] = data
                    async_import_meter_statistics(self.hass, data)
        except EonNextAuthenticationError as err:
            raise ConfigEntryAuthFailed from err
        except EonNextConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
        except EonNextError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_response",
            ) from err

        if (
            self.api.refresh_token
            and self.api.refresh_token != self.config_entry.data[CONF_REFRESH_TOKEN]
        ):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_REFRESH_TOKEN: self.api.refresh_token,
                },
            )

        return meter_data
