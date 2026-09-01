# E.ON Next for Home Assistant

An unofficial Home Assistant integration for E.ON Next electricity and gas meter
readings.

## Features

- Discovers every active electricity and gas meter on the account.
- Creates a cumulative reading sensor for every meter register.
- Retains the reading timestamp, source, previous value, and latest delta as
  sensor attributes.
- Imports all available dated readings into Home Assistant long-term statistics.
- Writes historical readings directly to a loaded Home Assistant InfluxDB
  integration with their original timestamps.
- Follows GraphQL pagination instead of limiting history to the first page.
- Polls every six hours and rotates the E.ON Next refresh token automatically.
- Renews E.ON Next's time-limited refresh session automatically. The password is
  stored in the Home Assistant config entry and redacted from diagnostics.

Electricity readings are exposed in kWh. Gas readings remain in cubic metres so
the integration does not guess a calorific value or conversion factor.

## Installation

### HACS

1. Add this repository as a custom integration repository in HACS.
2. Install **E.ON Next**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/eon_next` into the `custom_components` directory in your
Home Assistant configuration, then restart Home Assistant.

## Setup

Open **Settings > Devices & services > Add integration**, search for **E.ON
Next**, and enter the email address and password for the account.

The integration creates external statistic IDs in this format:

```text
eon_next:electricity_<meter_id>_<register>_meter_reading
eon_next:gas_<meter_id>_<register>_meter_reading
```

Select the E.ON Next external electricity statistic as a grid-consumption source
in the Home Assistant Energy dashboard to include the imported history. Gas is
exposed as the meter's native cumulative volume reading.

When Home Assistant's InfluxDB integration is loaded, the E.ON integration
writes each historical reading directly with the source timestamp. Do not add
the E.ON meter-reading sensors to the ordinary InfluxDB entity filter: doing so
also exports their restored current state at Home Assistant startup and creates
a synthetic point at the restart time. Replaying history is idempotent because
InfluxDB uses the measurement, tag set, and timestamp as the point identity.

## Limitations

E.ON Next does not publish this consumer GraphQL API as a supported third-party
interface, so authentication or response fields may change without notice.
Only the reading history available from E.ON Next can be imported.

This project builds on the API investigation and initial integration by
[madmachinations/eon-next](https://github.com/madmachinations/eon-next), while
adding renewable authentication, pagination, precision preservation, current
Home Assistant APIs, and historical statistics import.
