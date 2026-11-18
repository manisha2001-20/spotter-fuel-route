"""Driving route from the free OSRM routing service: exactly one HTTP call per
uncached start/finish pair. The call returns the distance, duration and full
route geometry together."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.core.cache import cache

from fuel.exceptions import RouteNotFound, UpstreamError
from fuel.services.geocoding import Location
from fuel.services.http import CallStats, get_json

METERS_PER_MILE = 1609.344


@dataclass(frozen=True)
class Route:
    coords: np.ndarray  # shape (n, 2): latitude, longitude
    distance_miles: float
    duration_hours: float


def get_route(start: Location, finish: Location, stats: CallStats) -> Route:
    key = "route:v1:{:.5f},{:.5f};{:.5f},{:.5f}".format(start.longitude, start.latitude,
                                                       finish.longitude, finish.latitude)
    cached = cache.get(key)
    if cached is not None:
        stats.cache_hits.append("route")
        return Route(np.asarray(cached["coords"]), cached["distance_miles"], cached["duration_hours"])

    stats.routing_calls += 1
    cfg = settings.FUEL_ROUTE
    url = "{}/route/v1/driving/{:.6f},{:.6f};{:.6f},{:.6f}".format(
        cfg["OSRM_BASE_URL"].rstrip("/"), start.longitude, start.latitude, finish.longitude, finish.latitude)
    status, body = get_json("routing (OSRM)", url,
                            {"overview": "full", "geometries": "geojson", "alternatives": "false", "steps": "false"},
                            cfg["ROUTE_TIMEOUT_SECONDS"])

    code = body.get("code") if isinstance(body, dict) else None
    if code in {"NoRoute", "NoSegment"}:
        raise RouteNotFound("No drivable route was found between the start and finish locations.")
    if code != "Ok" or not body.get("routes"):
        raise UpstreamError(f"The routing service returned an unexpected response (HTTP {status}, code {code}).")

    best = body["routes"][0]
    lonlat = np.asarray(best["geometry"]["coordinates"], dtype=float)
    coords = lonlat[:, ::-1].copy()  # GeoJSON is [lon, lat]; we use [lat, lon]
    route = Route(coords, best["distance"] / METERS_PER_MILE, best["duration"] / 3600.0)
    cache.set(key, {"coords": coords.tolist(), "distance_miles": route.distance_miles,
                    "duration_hours": route.duration_hours})
    return route
