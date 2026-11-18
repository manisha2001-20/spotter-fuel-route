"""Correctness of the refuelling optimiser.

Besides hand-worked examples, the greedy result is compared with an
independent brute-force dynamic programme on hundreds of random instances.
"""

import random

import pytest

from fuel.exceptions import NoFeasibleFuelPlan
from fuel.services.optimizer import StationCandidate as S
from fuel.services.optimizer import plan_fuel_stops, remove_dominated

MPG = 10


def cost(purchases):
    """Fuel cost in dollars; fuel is tracked in miles, so gallons = miles / MPG."""
    return sum(p.purchased_miles / MPG * p.station.price_per_gallon for p in purchases)


def simulate(purchases, total, rng, initial=0.0):
    """Drive the plan from mile 0 and check every physical constraint.
    Returns the fuel (miles) left at the destination."""
    fuel, pos = initial, 0.0
    for p in purchases:
        fuel -= p.station.position_miles - pos
        assert fuel >= -1e-6, "ran out of fuel before a stop"
        assert p.arrival_fuel_miles == pytest.approx(max(fuel, 0), abs=1e-6)
        assert p.purchased_miles > 0
        fuel += p.purchased_miles
        assert fuel <= rng + 1e-6, "tank over-filled"
        pos = p.station.position_miles
    fuel -= total - pos
    assert fuel >= -1e-6, "ran out of fuel before the destination"
    return fuel


def brute_force_cost(stations, total, rng, initial=0):
    """Exact DP over integer fuel levels (all positions are integers), passing
    every station in order and trying every purchase amount."""
    INF = float("inf")
    stations = sorted(stations, key=lambda s: s.position_miles)
    best = {initial: 0.0}  # fuel on hand -> min cost so far
    pos = 0
    for s in stations:
        d = int(s.position_miles) - pos
        arrive = {}
        for f, c in best.items():
            if f >= d:
                arrive[f - d] = min(arrive.get(f - d, INF), c)
        best = {}
        for f, c in arrive.items():
            for buy in range(0, rng - f + 1):
                nf = f + buy
                nc = c + buy / MPG * s.price_per_gallon
                if nc < best.get(nf, INF):
                    best[nf] = nc
        pos = int(s.position_miles)
    d = total - pos
    return min((c for f, c in best.items() if f >= d), default=INF)


# --- Hand-worked examples -------------------------------------------------

def test_single_fill_short_trip_buys_exactly_what_is_needed():
    plan = plan_fuel_stops([S(0, 3.00)], total_miles=300, range_miles=500)
    assert len(plan) == 1
    assert plan[0].purchased_miles == pytest.approx(300)       # 30 gallons
    assert cost(plan) == pytest.approx(30 * 3.00)              # $90.00


def test_buys_little_at_expensive_station_then_fills_at_cheap_one():
    # Start at an expensive station; a cheap one is 100 miles ahead.
    stations = [S(0, 4.00), S(100, 3.00), S(550, 3.50)]
    plan = plan_fuel_stops(stations, total_miles=900, range_miles=500)
    assert [p.station.position_miles for p in plan] == [0, 100, 550]
    assert plan[0].purchased_miles == pytest.approx(100)      # just enough to reach the cheap one
    assert plan[1].purchased_miles == pytest.approx(500)      # fill up at the cheapest
    assert plan[2].purchased_miles == pytest.approx(350 - 50)  # top up just enough for the destination
    # 10 gal x 4.00 + 50 gal x 3.00 + 30 gal x 3.50
    assert cost(plan) == pytest.approx(40 + 150 + 105)


def test_trip_over_500_miles_needs_multiple_stops_and_respects_range():
    stations = [S(0, 3.0), S(450, 3.2), S(900, 3.1), S(1300, 3.3)]
    plan = plan_fuel_stops(stations, total_miles=1700, range_miles=500)
    assert len(plan) >= 3
    left = simulate(plan, 1700, 500)
    assert left == pytest.approx(0, abs=1e-6)  # never buys more than needed


def test_destination_is_never_a_fuel_stop_and_no_leftover_fuel():
    plan = plan_fuel_stops([S(0, 3.0), S(200, 2.0)], total_miles=400, range_miles=500)
    assert all(p.station.position_miles < 400 for p in plan)
    assert simulate(plan, 400, 500) == pytest.approx(0, abs=1e-6)


def test_initial_fuel_is_used_first_and_no_stop_when_enough():
    assert plan_fuel_stops([S(10, 3.0)], total_miles=300, range_miles=500, initial_fuel_miles=500) == []


def test_initial_fuel_reaches_cheap_station_without_buying_at_start_area():
    plan = plan_fuel_stops([S(0, 5.0), S(150, 2.0)], total_miles=600, range_miles=500, initial_fuel_miles=200)
    assert [p.station.price_per_gallon for p in plan] == [2.0]


def test_gap_longer_than_range_is_infeasible():
    with pytest.raises(NoFeasibleFuelPlan) as exc:
        plan_fuel_stops([S(0, 3.0), S(400, 3.0), S(950, 3.0)], total_miles=1200, range_miles=500)
    assert exc.value.details == {"gap_starts_at_mile": 400.0, "next_stop_at_mile": 950.0}


def test_no_station_near_start_with_empty_tank_is_infeasible():
    with pytest.raises(NoFeasibleFuelPlan, match="first fuel station"):
        plan_fuel_stops([S(40, 3.0)], total_miles=300, range_miles=500)


def test_no_stations_at_all_is_infeasible():
    with pytest.raises(NoFeasibleFuelPlan, match="no fuel stations"):
        plan_fuel_stops([], total_miles=300, range_miles=500)


def test_last_leg_longer_than_range_is_infeasible():
    with pytest.raises(NoFeasibleFuelPlan, match="before the destination"):
        plan_fuel_stops([S(0, 3.0)], total_miles=501, range_miles=500)


def test_exactly_500_mile_legs_are_allowed():
    plan = plan_fuel_stops([S(0, 3.0), S(500, 3.0)], total_miles=1000, range_miles=500)
    assert simulate(plan, 1000, 500) == pytest.approx(0, abs=1e-6)


def test_invalid_prices_and_off_route_positions_are_ignored():
    stations = [S(0, 3.0), S(100, 0.0), S(150, -1.0), S(-5, 1.0), S(2000, 1.0)]
    plan = plan_fuel_stops(stations, total_miles=400, range_miles=500)
    assert [p.station.price_per_gallon for p in plan] == [3.0]


def test_co_located_stations_use_the_cheapest():
    plan = plan_fuel_stops([S(0, 3.5), S(0, 3.1), S(0, 3.3)], total_miles=200, range_miles=500)
    assert [p.station.price_per_gallon for p in plan] == [3.1]


def test_rejects_bad_arguments():
    with pytest.raises(ValueError):
        plan_fuel_stops([], total_miles=10, range_miles=0)
    with pytest.raises(ValueError):
        plan_fuel_stops([], total_miles=10, range_miles=500, initial_fuel_miles=600)


# --- Optimality against brute force ----------------------------------------

@pytest.mark.parametrize("seed", range(300))
def test_greedy_matches_brute_force_optimum(seed):
    rnd = random.Random(seed)
    rng = rnd.randint(4, 12)
    total = rnd.randint(1, 60)
    n = rnd.randint(0, 12)
    stations = [S(rnd.randint(0, total), round(rnd.uniform(2.5, 4.5), 2)) for _ in range(n)]
    initial = rnd.randint(0, rng)
    expected = brute_force_cost(stations, total, rng, initial)
    if expected == float("inf"):
        with pytest.raises(NoFeasibleFuelPlan):
            plan_fuel_stops(stations, total, rng, initial)
        return
    plan = plan_fuel_stops(stations, total, rng, initial)
    simulate(plan, total, rng, initial)
    assert cost(plan) == pytest.approx(expected, abs=1e-9)


# --- Dominance pruning ------------------------------------------------------

def test_remove_dominated_keeps_cheapest_within_window():
    stations = [S(0, 3.0), S(5, 3.2), S(12, 2.9), S(40, 3.5)]
    kept = remove_dominated(stations, 15)
    assert [s.price_per_gallon for s in kept] == [2.9, 3.5]


def test_remove_dominated_ties_keep_one_and_zero_window_is_identity():
    stations = [S(0, 3.0), S(5, 3.0)]
    assert len(remove_dominated(stations, 15)) == 1
    assert remove_dominated(stations, 0) == stations
