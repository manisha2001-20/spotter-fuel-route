"""Turn a user-supplied start/finish into coordinates.

* "lat,lon" input (e.g. "40.7128,-74.0060") is parsed locally: no external call.
* Anything else is geocoded with OpenStreetMap Nominatim, restricted to the
  USA. Results are cached for 30 days, so a repeated place name costs nothing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from fuel.exceptions import LocationNotFound, LocationOutsideUSA
from fuel.services.geo import is_in_usa
from fuel.services.http import CallStats, get_json

_COORDS = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    latitude: float
    longitude: float
    source: str

    def as_dict(self) -> dict:
        return {"query": self.query, "label": self.label, "latitude": round(self.latitude, 6),
                "longitude": round(self.longitude, 6), "source": self.source}


def resolve_location(query: str, stats: CallStats) -> Location:
    query = " ".join(query.split())
    match = _COORDS.match(query)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise LocationNotFound(f"'{query}' is not a valid 'latitude,longitude' pair.")
        if not is_in_usa(lat, lon):
            raise LocationOutsideUSA(f"'{query}' is outside the USA. Start and finish must both be in the USA.")
        return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")

    cache_key = "geocode:v2:" + hashlib.sha256(query.lower().encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        stats.cache_hits.append(f"geocode:{query}")
        return Location(query=query, **cached)

    stats.geocoding_calls += 1
    cfg = settings.FUEL_ROUTE
    _, results = get_json(
        "geocoding (Nominatim)",
        f"{cfg['NOMINATIM_BASE_URL'].rstrip('/')}/search",
        # No country filter on purpose: with `countrycodes=us`, "Toronto, Canada"
        # silently matched some US place. Instead the best worldwide match is
        # taken and rejected if it is not in the USA.
        {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1},
        cfg["GEOCODE_TIMEOUT_SECONDS"],
    )
    if not isinstance(results, list) or not results:
        raise LocationNotFound(f"Could not find a location matching '{query}'.")

    hit = results[0]
    lat, lon = float(hit["lat"]), float(hit["lon"])
    country = (hit.get("address") or {}).get("country_code", "")
    if country != "us" or not is_in_usa(lat, lon):
        raise LocationOutsideUSA(
            f"'{query}' resolved to '{hit.get('display_name', '?')}', which is outside the USA. "
            "Start and finish must both be in the USA; add the state if the name is ambiguous (e.g. 'Paris, TX')."
        )

    data = {"label": hit.get("display_name", query), "latitude": lat, "longitude": lon, "source": "nominatim"}
    cache.set(cache_key, data)
    return Location(query=query, **data)
