"""Pure geometry helpers (no Django, no network)."""

from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_DEGREE_LAT = 69.0

# Rough bounding boxes: contiguous US, Alaska, Hawaii.
_USA_BOXES = (
    (24.3, 49.5, -125.0, -66.8),
    (51.0, 71.6, -180.0, -129.0),
    (18.8, 22.4, -160.5, -154.7),
)


def is_in_usa(lat: float, lon: float) -> bool:
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in _USA_BOXES)


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles; works on scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def resample_uniform(coords: np.ndarray, cum_miles: np.ndarray, step_miles: float = 1.0):
    """Re-sample a (lat, lon) polyline at a fixed spacing along its length.

    OSRM geometry is very dense on curves (tens of thousands of vertices on a
    cross-country route) but can be several miles between vertices on straight
    interstates. A uniform 1-mile spacing gives a small, predictable number of
    points (about one per route mile) and bounds the station-distance error to
    half a mile. Returns (points, miles_along_route)."""
    total = float(cum_miles[-1]) if len(cum_miles) else 0.0
    if total <= 0:
        return coords[:1].copy(), np.zeros(1)
    n = max(2, math.ceil(total / step_miles) + 1)
    targets = np.linspace(0.0, total, n)
    lat = np.interp(targets, cum_miles, coords[:, 0])
    lon = np.interp(targets, cum_miles, coords[:, 1])
    return np.column_stack([lat, lon]), targets


def cumulative_miles(coords: np.ndarray) -> np.ndarray:
    """Distance along the polyline from its first point to each vertex."""
    if len(coords) < 2:
        return np.zeros(len(coords))
    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    return np.concatenate([[0.0], np.cumsum(seg)])


def nearest_points_on_route(route: np.ndarray, points: np.ndarray, chunk: int = 512):
    """For each (lat, lon) in `points`, return (index of nearest route vertex,
    distance in miles to it). `route` should be resampled first (see
    `resample_uniform`), so the vertex distance is within half a step of the
    true distance to the line.

    Uses a local equirectangular projection (accurate to well under 1% at the
    ~10 mile scale that matters here) and processes points in chunks to bound
    memory use.
    """
    idx = np.empty(len(points), dtype=np.int64)
    dist = np.empty(len(points))
    r_lat = route[:, 0] * MILES_PER_DEGREE_LAT
    for start in range(0, len(points), chunk):
        p = points[start:start + chunk]
        cos_lat = np.cos(np.radians(p[:, 0]))[:, None]
        dy = r_lat[None, :] - (p[:, 0] * MILES_PER_DEGREE_LAT)[:, None]
        dx = (route[None, :, 1] - p[:, 1][:, None]) * MILES_PER_DEGREE_LAT * cos_lat
        d2 = dx * dx + dy * dy
        best = d2.argmin(axis=1)
        idx[start:start + chunk] = best
        dist[start:start + chunk] = np.sqrt(d2[np.arange(len(p)), best])
    return idx, dist


def simplify_for_display(coords: np.ndarray, cum_miles: np.ndarray, step_miles: float = 0.5) -> list[list[float]]:
    """Keep roughly one vertex per `step_miles` of route (plus both ends) so
    the JSON response stays small. Returns GeoJSON order [lon, lat]."""
    if len(coords) == 0:
        return []
    bucket = np.floor(cum_miles / step_miles)
    keep = np.concatenate([[True], bucket[1:] != bucket[:-1]])
    keep[-1] = True
    return [[round(float(lon), 5), round(float(lat), 5)] for lat, lon in coords[keep]]
