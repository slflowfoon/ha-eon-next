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
- Uses E.ON's Auth0 browser sign-in and automatic refresh-token rotation, without
  storing your E.ON password or requiring an account API key.
- Saves replacement refresh tokens immediately in private, atomic Home Assistant
  storage and serializes renewal requests.

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

For accounts migrated to E.ON's Auth0 sign-in, the old email/password API login no
longer works. A one-time browser helper is required because E.ON does not register
Home Assistant's OAuth callback URL.
The helper opens E.ON's own sign-in page in a separate temporary browser. It does
not read your phone's saved credentials or change the phone app.

This uses E.ON's public mobile-client authorization-code/PKCE flow and registered
Android callback. It is not an officially supported third-party sign-in method;
E.ON may change or disable it. Successful renewal does not guarantee an unlimited
session lifetime.

On a desktop computer with Python and [uv](https://docs.astral.sh/uv/), from this
repository:

```sh
uv run --with playwright python -m playwright install chromium
uv run scripts/eon_login.py
```

Sign in in the browser window. The helper validates the returned identity token
and writes `eon-next-session.json` with owner-only permissions on POSIX systems.
On Windows, keep it in your private user directory. Treat it like a password.

Open **Settings > Devices & services > Add integration > E.ON Next** and enter
the file's `email` and `refresh_token` values. For an existing installation, use
**Reconfigure** or the **Reconfigure authentication** repair instead of deleting
the integration. Your entry, meter entities, statistics and exports are retained.
An existing account must be reconnected with that same account's login.

After Home Assistant accepts the session, delete the helper's output file. Do not
reuse it: each renewal may replace the refresh token. Never share a refresh token
with an actively logged-in phone, browser, or another Home Assistant installation.
Do not post the file in issues or chats.

If E.ON revokes or expires the session, the integration asks you to run the helper
again. Network errors are retried separately and do not deliberately discard the
session. The helper needs a graphical desktop; Home Assistant itself does not
need a browser, ADB, or the helper's dependencies.

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
