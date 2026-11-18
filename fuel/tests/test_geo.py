import numpy as np
import pytest

from fuel.services.geo import (cumulative_miles, haversine_miles, is_in_usa, nearest_points_on_route,
                               resample_uniform, simplify_for_display)


def test_haversine_known_distance_new_york_to_los_angeles():
    # Great-circle NYC-LA is about 2,445 miles.
    assert float(haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)) == pytest.approx(2445, rel=0.01)


def test_one_degree_of_latitude_is_about_69_miles():
    assert float(haversine_miles(40, -100, 41, -100)) == pytest.approx(69.1, rel=0.01)


def test_is_in_usa():
    assert is_in_usa(39.74, -104.99)       # Denver
    assert is_in_usa(61.22, -149.90)       # Anchorage
    assert is_in_usa(21.31, -157.86)       # Honolulu
    assert not is_in_usa(51.51, -0.13)     # London
    assert not is_in_usa(19.43, -99.13)    # Mexico City


def test_resample_uniform_spacing_and_ends():
    coords = np.array([[40.0, -100.0], [40.0, -99.0], [40.0, -95.0]])
    cum = cumulative_miles(coords)
    pts, miles = resample_uniform(coords, cum, 1.0)
    assert miles[0] == 0 and miles[-1] == pytest.approx(cum[-1])
    assert np.diff(miles).max() <= 1.0 + 1e-9
    assert pts[0].tolist() == coords[0].tolist()
    assert pts[-1] == pytest.approx(coords[-1])


def test_nearest_point_distance_and_position():
    coords = np.array([[40.0, -100.0], [40.0, -98.0]])
    cum = cumulative_miles(coords)
    pts, miles = resample_uniform(coords, cum, 0.5)
    # A point 0.1 deg north (about 6.9 miles) of the line's midpoint.
    idx, dist = nearest_points_on_route(pts, np.array([[40.1, -99.0]]))
    assert dist[0] == pytest.approx(6.9, abs=0.3)
    assert miles[idx[0]] == pytest.approx(cum[-1] / 2, abs=0.5)


def test_simplify_for_display_keeps_endpoints_in_geojson_order():
    coords = np.array([[40.0, -100.0], [40.0, -99.999], [40.0, -99.0]])
    out = simplify_for_display(coords, cumulative_miles(coords), 0.5)
    assert out[0] == [-100.0, 40.0] and out[-1] == [-99.0, 40.0]
    assert len(out) == 2
