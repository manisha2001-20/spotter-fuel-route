"""Live end-to-end check against a running server, using the REAL free APIs.

    python manage.py runserver            # in one terminal
    python scripts/smoke_test.py          # in another
    python scripts/smoke_test.py --base http://127.0.0.1:8000

For every scenario it re-checks the arithmetic in the response independently:
cost = gallons x price per stop, totals add up, gallons used = miles / 10,
no leg longer than 500 miles, and stops in journey order.
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

OK_ROUTES = [
    ("Dallas, TX", "Houston, TX"),                # short: one stop
    ("Chicago, IL", "Denver, CO"),                # ~1,000 mi: several stops
    ("New York, NY", "Los Angeles, CA"),          # cross-country
    ("41.8781,-87.6298", "39.7392,-104.9903"),    # coordinates: no geocoding call
]
ERROR_CASES = [
    ({"start": "Toronto, Canada", "finish": "Chicago, IL"}, 422, "location_outside_usa"),
    ({"start": "Qwxzv Plkjh", "finish": "Chicago, IL"}, 422, "location_not_found"),
    ({"start": "Honolulu, HI", "finish": "Chicago, IL"}, 422, "route_not_found"),
    ({"start": "Chicago, IL"}, 400, "invalid_request"),
    ({"start": "Dallas, TX", "finish": "Houston, TX", "initial_fuel_gallons": 80}, 400, "invalid_request"),
]


def check_plan(d: dict, mpg: float, rng: float) -> list[str]:
    problems = []
    total = d["route"]["distance_miles"]
    stops = d["fuel_stops"]
    for s in stops:
        if abs(round(s["gallons_purchased"] * s["price_per_gallon"], 2) - s["cost"]) > 0.005:
            problems.append(f"stop {s['stop_number']}: cost != gallons x price")
        if s["fuel_on_departure_gallons"] > rng / mpg + 1e-6:
            problems.append(f"stop {s['stop_number']}: tank over capacity")
    if abs(sum(s["cost"] for s in stops) - d["summary"]["total_fuel_cost"]) > 0.005:
        problems.append("total cost != sum of stop costs")
    if abs(d["summary"]["total_gallons_used"] - total / mpg) > 0.01:
        problems.append("gallons used != miles / mpg")
    markers = [s["mile_marker"] for s in stops]
    if markers != sorted(markers):
        problems.append("stops not in journey order")
    # The first stop may be a start-window fill-up (treated as mile 0).
    legs = [b - a for a, b in zip([0.0] + markers[1:], markers[1:] + [total])]
    if legs and max(legs) > rng + 0.1:
        problems.append(f"a leg of {max(legs):.1f} miles exceeds the range")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    base = parser.parse_args().base.rstrip("/")
    failures = 0

    health = requests.get(f"{base}/api/health/", timeout=10).json()
    print(f"health: {health['status']}  stations={health['stations']}  django={health['django_version']}")
    mpg, rng = health["vehicle"]["miles_per_gallon"], health["vehicle"]["range_miles"]

    for start, finish in OK_ROUTES:
        t = time.perf_counter()
        r = requests.post(f"{base}/api/route/", json={"start": start, "finish": finish}, timeout=60)
        ms = (time.perf_counter() - t) * 1000
        d = r.json()
        if r.status_code != 200:
            print(f"FAIL {start} -> {finish}: HTTP {r.status_code} {d}")
            failures += 1
            continue
        problems = check_plan(d, mpg, rng)
        failures += bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} {start} -> {finish}: {d['route']['distance_miles']} mi, "
              f"{d['summary']['number_of_stops']} stops, ${d['summary']['total_fuel_cost']}, "
              f"external calls={d['meta']['external_api_calls']['total']}, {ms:.0f} ms {problems or ''}")

    for body, status, code in ERROR_CASES:
        r = requests.post(f"{base}/api/route/", json=body, timeout=60)
        got = r.json().get("error", {}).get("code")
        ok = r.status_code == status and got == code
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {body} -> HTTP {r.status_code} {got}")

    print("\nALL PASSED" if not failures else f"\n{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
