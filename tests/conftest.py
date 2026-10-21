"""Load standalone API modules before tests import integration submodules."""

from test_api import _load_api

_load_api()
