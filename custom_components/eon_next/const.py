"""Constants for the E.ON Next integration."""

from datetime import timedelta

DOMAIN = "eon_next"

CONF_EMAIL = "email"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_AUTH_STORE_ID = "auth_store_id"
CONF_AUTH_SUBJECT = "auth_subject"

AUTH0_ISSUER = "https://auth.eonnext.com/"
AUTH0_CLIENT_ID = "kN3SN5h3H30hxpOjlhgmGeMfELKgn5Xg"

API_URL = "https://api.eonnext-kraken.energy/v1/graphql/"
DEFAULT_UPDATE_INTERVAL = timedelta(hours=6)

METER_TYPE_ELECTRICITY = "electricity"
METER_TYPE_GAS = "gas"
