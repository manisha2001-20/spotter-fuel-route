import pytest
from django.core.cache import cache

from fuel.services import http
from fuel.services.station_index import reset_station_index


@pytest.fixture(autouse=True)
def isolated_environment(settings):
    """Every test gets an in-memory cache, a fresh station snapshot and NO
    network access (an unmocked external call fails the test loudly)."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.FUEL_ROUTE = {**settings.FUEL_ROUTE, "DOMINANCE_WINDOW_MILES": 0.0}
    cache.clear()
    reset_station_index()

    def no_network(*args, **kwargs):
        raise AssertionError(f"Unexpected real HTTP call: {args} {kwargs}")

    original = http._session.get
    http._session.get = no_network
    yield
    http._session.get = original
    reset_station_index()
