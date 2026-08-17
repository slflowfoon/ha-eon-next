"""Constants for the E.ON Next integration."""

from datetime import timedelta

DOMAIN = "eon_next"

CONF_EMAIL = "email"
CONF_REFRESH_TOKEN = "refresh_token"

API_URL = "https://api.eonnext-kraken.energy/v1/graphql/"
DEFAULT_UPDATE_INTERVAL = timedelta(hours=6)

METER_TYPE_ELECTRICITY = "electricity"
METER_TYPE_GAS = "gas"
