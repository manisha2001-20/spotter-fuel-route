"""In-memory snapshot of located US fuel stations, matched against a route.

Stations are read from the database once per process into numpy arrays. Each
request then costs one cheap aggregate query to check the snapshot is still
current (it is rebuilt automatically after `import_fuel_stations` runs).
No external API is ever called for stations.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from django.db.models import Count, Max

from fuel.models import FuelStation
from fuel.services.geo import MILES_PER_DEGREE_LAT, nearest_points_on_route


@dataclass(frozen=True)
class StationRecord:
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    price: float


@dataclass(frozen=True)
class NearbyStation:
    station: StationRecord
    position_miles: float  # distance along the route from the start
    offset_miles: float    # straight-line distance from the route line


class StationIndex:
    def __init__(self, records: list[StationRecord], version: tuple):
        self.records = records
        self.version = version
        self.lat = np.array([r.latitude for r in records], dtype=float)
        self.lon = np.array([r.longitude for r in records], dtype=float)

    def __len__(self) -> int:
        return len(self.records)

    def near_route(self, route_points: np.ndarray, route_miles: np.ndarray, corridor_miles: float) -> list[NearbyStation]:
        """Stations within `corridor_miles` of the (uniformly resampled) route."""
        if not self.records or len(route_points) == 0:
            return []
        # 1. Cheap bounding-box prefilter.
        pad_lat = corridor_miles / MILES_PER_DEGREE_LAT
        max_abs_lat = min(89.0, float(np.abs(route_points[:, 0]).max()) + pad_lat)
        pad_lon = corridor_miles / (MILES_PER_DEGREE_LAT * np.cos(np.radians(max_abs_lat)))
        mask = ((self.lat >= route_points[:, 0].min() - pad_lat) & (self.lat <= route_points[:, 0].max() + pad_lat)
                & (self.lon >= route_points[:, 1].min() - pad_lon) & (self.lon <= route_points[:, 1].max() + pad_lon))
        candidates = np.flatnonzero(mask)
        if len(candidates) == 0:
            return []
        # 2. Coarse filter against every 10th route point (10 miles apart).
        # Any route point is at most 5 miles from a coarse point, so a station
        # within the corridor is always within corridor + 5 miles of one.
        spacing = float(route_miles[1] - route_miles[0]) if len(route_miles) > 1 else 0.0
        every = max(1, int(10.0 / spacing)) if spacing > 0 else 1
        coarse = route_points[::every]
        _, coarse_dist = nearest_points_on_route(coarse, np.column_stack([self.lat[candidates], self.lon[candidates]]))
        candidates = candidates[coarse_dist <= corridor_miles + every * spacing / 2 + 0.5]
        if len(candidates) == 0:
            return []
        # 3. Exact nearest-vertex distance for the remaining stations.
        pts = np.column_stack([self.lat[candidates], self.lon[candidates]])
        vertex, dist = nearest_points_on_route(route_points, pts)
        keep = dist <= corridor_miles
        return [
            NearbyStation(self.records[c], float(route_miles[v]), float(d))
            for c, v, d in zip(candidates[keep], vertex[keep], dist[keep])
        ]


_lock = threading.Lock()
_index: StationIndex | None = None


def _located_queryset():
    return FuelStation.objects.filter(country="US", latitude__isnull=False, longitude__isnull=False)


def get_station_index() -> StationIndex:
    global _index
    stats = _located_queryset().aggregate(n=Count("id"), last=Max("id"))
    version = (stats["n"], stats["last"])
    if _index is not None and _index.version == version:
        return _index
    with _lock:
        if _index is None or _index.version != version:
            rows = _located_queryset().values_list(
                "opis_id", "name", "address", "city", "state", "latitude", "longitude", "retail_price")
            records = [StationRecord(o, n, a, c, s, lat, lon, float(p)) for o, n, a, c, s, lat, lon, p in rows]
            _index = StationIndex(records, version)
    return _index


def reset_station_index() -> None:
    global _index
    with _lock:
        _index = None
