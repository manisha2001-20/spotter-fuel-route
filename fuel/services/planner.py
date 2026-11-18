"""Orchestrates one route request:

    resolve start & finish  ->  one OSRM route call  ->  stations in the route
    corridor (in memory)  ->  optimal refuelling plan  ->  JSON-ready response
"""

from __future__ import annotations

import time

from django.conf import settings

from fuel.exceptions import FuelRouteError, NoFeasibleFuelPlan, StationsNotLoaded
from fuel.services.geo import cumulative_miles, resample_uniform, simplify_for_display
from fuel.services.geocoding import resolve_location
from fuel.services.http import CallStats
from fuel.services.optimizer import StationCandidate, plan_fuel_stops, remove_dominated
from fuel.services.routing import get_route
from fuel.services.station_index import get_station_index


def plan_trip(start_query: str, finish_query: str, initial_fuel_gallons: float = 0.0) -> dict:
    t_start = time.perf_counter()
    cfg = settings.FUEL_ROUTE
    mpg = cfg["MILES_PER_GALLON"]
    range_miles = cfg["VEHICLE_RANGE_MILES"]
    tank_gallons = range_miles / mpg
    if not 0 <= initial_fuel_gallons <= tank_gallons:
        raise FuelRouteError(f"initial_fuel_gallons must be between 0 and {tank_gallons:g}.")

    index = get_station_index()
    if len(index) == 0:
        raise StationsNotLoaded("No fuel stations are loaded. Run: python manage.py import_fuel_stations")

    stats = CallStats()
    start = resolve_location(start_query, stats)
    finish = resolve_location(finish_query, stats)
    t_external = time.perf_counter()
    route = get_route(start, finish, stats)
    t_route = time.perf_counter()

    # --- Geometry: positions along the route, scaled to OSRM's road distance.
    cum = cumulative_miles(route.coords)
    if len(cum) and cum[-1] > 0:
        cum = cum * (route.distance_miles / cum[-1])
    points, point_miles = resample_uniform(route.coords, cum, step_miles=1.0)
    nearby = index.near_route(points, point_miles, cfg["CORRIDOR_MILES"])

    # --- Optimisation. Stations in the first START_WINDOW_MILES are treated as
    # being at mile 0, so that a vehicle starting empty can make its first
    # fill-up near the origin (the fuel for those first few miles is bought there).
    start_window = cfg["START_WINDOW_MILES"]
    candidates = [
        StationCandidate(0.0 if s.position_miles <= start_window else s.position_miles, s.station.price, s)
        for s in nearby
    ]
    # Drop stations that have an equal-or-cheaper station within a few route
    # miles (station positions are only city-level accurate), which avoids
    # $0.50 "top-ups" two miles apart. If that ever makes the trip infeasible,
    # fall back to the full candidate set.
    pruned = remove_dominated(candidates, cfg["DOMINANCE_WINDOW_MILES"])
    try:
        purchases = plan_fuel_stops(pruned, route.distance_miles, range_miles, initial_fuel_gallons * mpg)
    except NoFeasibleFuelPlan:
        if len(pruned) == len(candidates):
            raise
        pruned = candidates
        purchases = plan_fuel_stops(candidates, route.distance_miles, range_miles, initial_fuel_gallons * mpg)

    # --- Build the response. Gallons are rounded to 3 dp and each stop's cost
    # is computed from the *displayed* gallons and price, so every number can
    # be re-checked by hand: cost = gallons_purchased x price_per_gallon.
    stops = []
    for n, p in enumerate(purchases, start=1):
        s = p.station.payload
        gallons = round(p.purchased_miles / mpg, 3)
        stops.append({
            "stop_number": n,
            "station": {
                "opis_id": s.station.opis_id, "name": s.station.name, "address": s.station.address,
                "city": s.station.city, "state": s.station.state,
                "latitude": s.station.latitude, "longitude": s.station.longitude,
            },
            "mile_marker": round(s.position_miles, 1),
            "distance_from_route_miles": round(s.offset_miles, 1),
            "price_per_gallon": s.station.price,
            "fuel_on_arrival_gallons": round(p.arrival_fuel_miles / mpg, 3),
            "gallons_purchased": gallons,
            "fuel_on_departure_gallons": round(p.departure_fuel_miles / mpg, 3),
            "cost": round(gallons * s.station.price, 2),
        })

    total_cost = round(sum(stop["cost"] for stop in stops), 2)
    gallons_bought = round(sum(stop["gallons_purchased"] for stop in stops), 3)
    gallons_needed = route.distance_miles / mpg
    remaining = max(0.0, initial_fuel_gallons + gallons_bought - gallons_needed)

    t_done = time.perf_counter()
    return {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "stations_in_corridor": len(nearby),
            "stations_considered": len(pruned),
        },
        "fuel_stops": stops,
        "summary": {
            "number_of_stops": len(stops),
            "total_fuel_cost": total_cost,
            "total_gallons_purchased": gallons_bought,
            "total_gallons_used": round(gallons_needed, 3),
            "initial_fuel_gallons": initial_fuel_gallons,
            "fuel_remaining_at_destination_gallons": round(remaining, 3),
            "average_price_per_gallon": round(total_cost / gallons_bought, 4) if gallons_bought else None,
        },
        "map": _geojson(route.coords, cum, start, finish, stops),
        "assumptions": {
            "vehicle_range_miles": range_miles,
            "miles_per_gallon": mpg,
            "tank_capacity_gallons": tank_gallons,
            "initial_fuel_gallons": initial_fuel_gallons,
            "station_corridor_miles": cfg["CORRIDOR_MILES"],
            "start_window_miles": start_window,
            "dominance_window_miles": cfg["DOMINANCE_WINDOW_MILES"],
            "notes": [
                "Fuel used = route miles / miles_per_gallon. Stop cost = gallons_purchased x price_per_gallon.",
                "The plan minimises total fuel cost: fuel is bought only where it is cheapest within reach, "
                "and only as much as needed to reach the next cheaper station or the destination.",
                "A station is skipped if an equal-or-cheaper station lies within dominance_window_miles along "
                "the route (set it to 0 for the strict optimum over every station).",
                "No leg between consecutive fuel stops (or to the destination) exceeds the vehicle range.",
                "Stations are located at their city's centre (the dataset has no coordinates). "
                "mile_marker is the nearest point on the route; the short detour to the station is not counted.",
                "With an empty starting tank, the first fill-up is made at a station within start_window_miles "
                "of the origin, and the fuel for those first miles is bought there.",
            ],
        },
        "meta": {
            **stats.as_dict(),
            "timing_ms": {
                "geocoding": round((t_external - t_start) * 1000, 1),
                "routing": round((t_route - t_external) * 1000, 1),
                "optimisation": round((t_done - t_route) * 1000, 1),
                "total": round((t_done - t_start) * 1000, 1),
            },
        },
    }


def _geojson(coords, cum, start, finish, stops) -> dict:
    features = [
        {"type": "Feature", "properties": {"kind": "route"},
         "geometry": {"type": "LineString", "coordinates": simplify_for_display(coords, cum)}},
        {"type": "Feature", "properties": {"kind": "start", "label": start.label},
         "geometry": {"type": "Point", "coordinates": [start.longitude, start.latitude]}},
        {"type": "Feature", "properties": {"kind": "finish", "label": finish.label},
         "geometry": {"type": "Point", "coordinates": [finish.longitude, finish.latitude]}},
    ]
    for stop in stops:
        st = stop["station"]
        features.append({
            "type": "Feature",
            "properties": {"kind": "fuel_stop", "stop_number": stop["stop_number"], "name": st["name"],
                           "price_per_gallon": stop["price_per_gallon"], "gallons_purchased": stop["gallons_purchased"],
                           "cost": stop["cost"], "mile_marker": stop["mile_marker"]},
            "geometry": {"type": "Point", "coordinates": [st["longitude"], st["latitude"]]},
        })
    return {"type": "FeatureCollection", "features": features}
