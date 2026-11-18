"""End-to-end API tests. Nominatim and OSRM are replaced by a fake HTTP
session, so these tests are fast, deterministic and never use the network.

The fake route is a straight east-west line along latitude 40N from
longitude -100 to -80 (about 1,056 miles), and test stations sit on it.
"""

from decimal import Decimal

import numpy as np
import pytest
import requests
from rest_framework.test import APIClient

from fuel.models import FuelStation
from fuel.services import http
from fuel.services.geo import haversine_miles

START, FINISH = (40.0, -100.0), (40.0, -80.0)
PLACES = {"start city": START, "finish city": FINISH, "near city": (40.0, -97.0)}
MPG, RANGE = 10, 500


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeUpstream:
    """Stands in for requests.Session.get; records every call."""

    def __init__(self):
        self.calls = []
        self.fail = None  # "timeout" | "connection" | "500" | "html" | "noroute"

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        assert headers and headers.get("User-Agent"), "external calls must send a User-Agent"
        if self.fail == "timeout":
            raise requests.Timeout("simulated timeout")
        if self.fail == "connection":
            raise requests.ConnectionError("simulated outage")
        if self.fail == "500":
            return FakeResponse(500, {"error": "boom"})
        if self.fail == "html":
            return FakeResponse(200, ValueError("not json"))
        if "nominatim" in url:
            q = params["q"].lower()
            if q == "toronto":
                return FakeResponse(200, [{"lat": "43.65", "lon": "-79.38", "display_name": "Toronto, Canada",
                                           "address": {"country_code": "ca"}}])
            if q not in PLACES:
                return FakeResponse(200, [])
            lat, lon = PLACES[q]
            return FakeResponse(200, [{"lat": str(lat), "lon": str(lon), "display_name": q.title(),
                                       "address": {"country_code": "us"}}])
        if "/route/v1/driving/" in url:
            if self.fail == "noroute":
                return FakeResponse(400, {"code": "NoRoute", "message": "Impossible route"})
            (lon1, lat1), (lon2, lat2) = [map(float, p.split(",")) for p in url.rsplit("/", 1)[1].split(";")]
            lons = np.linspace(lon1, lon2, 200)
            lats = np.linspace(lat1, lat2, 200)
            miles = float(np.sum(haversine_miles(lats[:-1], lons[:-1], lats[1:], lons[1:])))
            return FakeResponse(200, {"code": "Ok", "routes": [{
                "distance": miles * 1609.344, "duration": miles / 60 * 3600,
                "geometry": {"type": "LineString", "coordinates": [[a, b] for a, b in zip(lons, lats)]},
            }]})
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def upstream():
    fake = FakeUpstream()
    original = http._session.get
    http._session.get = fake
    yield fake
    http._session.get = original


@pytest.fixture
def client():
    return APIClient()


def add_station(opis_id, lon, price, lat=40.0, country="US", located=True, state="KS"):
    return FuelStation.objects.create(
        opis_id=opis_id, name=f"STATION {opis_id}", address="I-70, EXIT 1", city=f"Town{opis_id}", state=state,
        country=country, rack_id=1, retail_price=Decimal(str(price)),
        latitude=lat if located else None, longitude=lon if located else None,
        location_source="census_place" if located else "none")


@pytest.fixture
def stations(db):
    # Longitude -> roughly 53 miles per degree at 40N.
    add_station(1, -99.95, 3.40)            # ~3 mi from start (start window)
    add_station(2, -99.90, 3.20)            # ~5 mi: cheaper, also in start window
    add_station(3, -96.00, 2.90)            # ~212 mi: cheap
    add_station(4, -94.00, 3.60)            # ~318 mi
    add_station(5, -91.00, 3.10)            # ~477 mi
    add_station(6, -88.00, 3.00)            # ~636 mi
    add_station(7, -84.00, 3.80)            # ~848 mi
    add_station(8, -90.00, 1.00, lat=41.5)  # very cheap but ~100 mi off the route: must be ignored
    add_station(9, -95.00, 1.00, country="CA", located=False)  # not routable


def post_route(client, **body):
    return client.post("/api/route/", body, format="json")


# --- Health & stations --------------------------------------------------------

def test_health_reports_dataset(client, stations):
    r = client.get("/api/health/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["stations"] == {"total": 9, "us": 8, "routable": 8}
    assert body["vehicle"] == {"range_miles": 500.0, "miles_per_gallon": 10.0}


@pytest.mark.django_db
def test_health_degraded_without_data(client):
    assert client.get("/api/health/").json()["status"] == "degraded"


def test_station_list_filter_order_and_detail(client, stations):
    r = client.get("/api/stations/", {"state": "ks", "max_price": "3.2"})
    assert r.status_code == 200
    prices = [s["retail_price"] for s in r.json()["results"]]
    assert prices == sorted(prices) and max(prices) <= 3.2
    assert client.get("/api/stations/3/").json()["retail_price"] == 2.9
    assert client.get("/api/stations/999999/").status_code == 404


def test_map_page_renders(client, db):
    r = client.get("/")
    assert r.status_code == 200 and b"/api/route/" in r.content


# --- Route: happy path & calculations -------------------------------------------

def test_long_route_plan_is_consistent_and_within_range(client, stations, upstream):
    r = post_route(client, start="Start City", finish="Finish City")
    assert r.status_code == 200, r.json()
    d = r.json()
    total = d["route"]["distance_miles"]
    assert 1000 < total < 1100
    stops = d["fuel_stops"]
    assert len(stops) >= 2, "a >1,000 mile trip needs several stops"

    # Off-route and Canadian stations are never used.
    used = {s["station"]["opis_id"] for s in stops}
    assert not used & {8, 9}

    # Stops are in journey order and no leg exceeds the range.
    markers = [s["mile_marker"] for s in stops]
    assert markers == sorted(markers)
    legs = np.diff([0.0] + markers[1:] + [total])  # first stop is the start-window fill-up
    assert legs.max() <= RANGE + 0.1

    # Per-stop arithmetic: cost = gallons x price, departure = arrival + purchase <= tank.
    for s in stops:
        assert s["cost"] == round(s["gallons_purchased"] * s["price_per_gallon"], 2)
        assert s["fuel_on_departure_gallons"] == pytest.approx(
            s["fuel_on_arrival_gallons"] + s["gallons_purchased"], abs=0.002)
        assert s["fuel_on_departure_gallons"] <= RANGE / MPG + 1e-6
        assert s["gallons_purchased"] > 0

    # Fuel continuity between consecutive stops.
    for a, b in zip(stops[1:], stops[2:]):
        burned = (b["mile_marker"] - a["mile_marker"]) / MPG
        assert b["fuel_on_arrival_gallons"] == pytest.approx(a["fuel_on_departure_gallons"] - burned, abs=0.02)

    # Totals.
    summary = d["summary"]
    assert summary["total_fuel_cost"] == round(sum(s["cost"] for s in stops), 2)
    assert summary["total_gallons_used"] == pytest.approx(total / MPG, abs=0.01)
    assert summary["total_gallons_purchased"] == pytest.approx(total / MPG, abs=0.01)  # starts empty
    assert summary["fuel_remaining_at_destination_gallons"] == pytest.approx(0, abs=0.01)
    assert summary["number_of_stops"] == len(stops)

    # The first fill-up uses the cheaper of the two start-window stations,
    # and only enough to reach the cheap station at ~212 mi.
    assert stops[0]["station"]["opis_id"] == 2
    assert stops[1]["station"]["opis_id"] == 3
    assert stops[1]["gallons_purchased"] == pytest.approx(50.0, abs=0.01)  # fills up at the cheapest

    # Map is GeoJSON with the route line and one point per stop.
    kinds = [f["properties"]["kind"] for f in d["map"]["features"]]
    assert kinds.count("route") == 1 and kinds.count("fuel_stop") == len(stops)

    # Exactly 2 geocoding calls + 1 routing call.
    assert d["meta"]["external_api_calls"] == {"geocoding": 2, "routing": 1, "total": 3}


def test_cost_matches_independent_hand_calculation(client, stations, upstream):
    """Recompute the optimum independently from the stations' route positions."""
    d = post_route(client, start="Start City", finish="Finish City").json()
    total = d["route"]["distance_miles"]
    # Worked by hand (positions ~ S2 5, S3 212, S5 477, S6 636, total ~1056):
    #   S2 ($3.20, cheapest in the start window): buy just enough to reach S3, the first cheaper station.
    #   S3 ($2.90): nothing cheaper within 500 mi -> fill 50 gal, drive to the cheapest station in
    #               range, S6 ($3.00, 424 mi on); S5 ($3.10) and S4 ($3.60) are skipped.
    #   S6: the destination is within 500 mi -> buy only what is missing to finish.
    by_id = {s["station"]["opis_id"]: s for s in d["fuel_stops"]}
    assert set(by_id) == {2, 3, 6}
    m3 = by_id[3]["mile_marker"]
    expected = (m3 / MPG * 3.20                        # start (snapped to mile 0) -> S3
                + 50 * 2.90                            # full tank at S3
                + ((total - m3) - 500) / MPG * 3.00)   # S6: the remaining miles beyond one tank
    assert d["summary"]["total_fuel_cost"] == pytest.approx(expected, abs=0.10)


def test_short_route_single_stop(client, stations, upstream):
    d = post_route(client, start="Start City", finish="Near City").json()
    assert d["route"]["distance_miles"] < 500
    assert d["summary"]["number_of_stops"] == 1
    assert d["summary"]["total_gallons_purchased"] == pytest.approx(d["route"]["distance_miles"] / MPG, abs=0.01)


def test_full_tank_short_route_needs_no_stop(client, stations, upstream):
    d = post_route(client, start="Start City", finish="Near City", initial_fuel_gallons=50).json()
    assert d["fuel_stops"] == [] and d["summary"]["total_fuel_cost"] == 0


def test_get_with_query_parameters_and_coordinates_skip_geocoding(client, stations, upstream):
    r = client.get("/api/route/", {"start": "40.0,-100.0", "finish": "40.0,-80.0"})
    assert r.status_code == 200
    assert r.json()["meta"]["external_api_calls"] == {"geocoding": 0, "routing": 1, "total": 1}


def test_repeat_request_is_served_from_cache(client, stations, upstream):
    post_route(client, start="Start City", finish="Finish City")
    n = len(upstream.calls)
    d = post_route(client, start="Start City", finish="Finish City").json()
    assert len(upstream.calls) == n
    assert d["meta"]["external_api_calls"]["total"] == 0


# --- Route: validation & failure modes -------------------------------------------

@pytest.mark.parametrize("body", [
    {}, {"start": "Start City"}, {"start": "", "finish": "Finish City"},
    {"start": "x" * 201, "finish": "Finish City"},
    {"start": "Start City", "finish": "Finish City", "initial_fuel_gallons": 51},
    {"start": "Start City", "finish": "Finish City", "initial_fuel_gallons": -1},
    {"start": "Start City", "finish": "Finish City", "initial_fuel_gallons": "lots"},
])
def test_invalid_input_returns_400(client, stations, upstream, body):
    r = post_route(client, **body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert upstream.calls == []


def test_unknown_location_returns_422(client, stations, upstream):
    r = post_route(client, start="Nowhere Special", finish="Finish City")
    assert r.status_code == 422 and r.json()["error"]["code"] == "location_not_found"


@pytest.mark.parametrize("start", ["Toronto", "51.5,-0.12"])
def test_location_outside_usa_returns_422(client, stations, upstream, start):
    r = post_route(client, start=start, finish="Finish City")
    assert r.status_code == 422 and r.json()["error"]["code"] == "location_outside_usa"


def test_no_drivable_route_returns_422(client, stations, upstream):
    upstream.fail = "noroute"
    r = post_route(client, start="40.0,-100.0", finish="40.0,-80.0")
    assert r.status_code == 422 and r.json()["error"]["code"] == "route_not_found"


def test_impossible_journey_returns_422_with_gap(client, db, upstream):
    add_station(1, -99.95, 3.0)
    add_station(2, -96.0, 3.0)  # then nothing for the remaining ~840 miles
    r = post_route(client, start="Start City", finish="Finish City")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "no_feasible_fuel_plan" and "gap_starts_at_mile" in err["details"]


def test_no_station_near_start_returns_422(client, db, upstream):
    add_station(1, -97.0, 3.0)  # ~160 mi in, but the tank starts empty
    r = post_route(client, start="Start City", finish="Near City")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_feasible_fuel_plan"


@pytest.mark.django_db
def test_no_stations_loaded_returns_503(client, upstream):
    r = post_route(client, start="Start City", finish="Finish City")
    assert r.status_code == 503 and r.json()["error"]["code"] == "stations_not_loaded"
    assert upstream.calls == []


@pytest.mark.parametrize("failure,status,code", [
    ("timeout", 504, "upstream_timeout"),
    ("connection", 502, "upstream_error"),
    ("500", 502, "upstream_error"),
    ("html", 502, "upstream_error"),
])
def test_external_api_failures(client, stations, upstream, failure, status, code):
    upstream.fail = failure
    r = post_route(client, start="Start City", finish="Finish City")
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_routing_failure_after_geocoding(client, stations, upstream):
    # Coordinates skip geocoding, so the first external call is the route.
    upstream.fail = "timeout"
    r = post_route(client, start="40.0,-100.0", finish="40.0,-80.0")
    assert r.status_code == 504
