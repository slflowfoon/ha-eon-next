"""Diagnostics support for E.ON Next."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_EMAIL, CONF_REFRESH_TOKEN
from .coordinator import EonNextConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_REFRESH_TOKEN,
    "account_number",
    "meter_id",
    "serial_number",
    "supply_point_id",
    "title",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EonNextConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for an E.ON Next config entry."""
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "last_update_success": coordinator.last_update_success,
            "meters": [
                {
                    "account_number": data.meter.account_number,
                    "meter_id": data.meter.meter_id,
                    "meter_type": data.meter.meter_type,
                    "serial_number": data.meter.serial_number,
                    "supply_point_id": data.meter.supply_point_id,
                    "registers": list(data.meter.registers),
                    "reading_count": len(data.readings),
                    "latest_reading_at": (
                        max(reading.read_at for reading in data.readings).isoformat()
                        if data.readings
                        else None
                    ),
                }
                for data in coordinator.data.values()
            ],
        },
        TO_REDACT,
    )
