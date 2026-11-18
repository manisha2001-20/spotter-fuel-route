"""Minimum-cost refuelling plan along a fixed route ("gas station problem").

Model
-----
* The route is a line from mile 0 to mile `total_miles`.
* Stations sit at known mile positions with a known price per gallon, and any
  quantity can be bought at a station.
* The tank holds at most `range_miles` worth of fuel (500 miles = 50 gallons at
  10 mpg). Fuel is tracked in *miles of range* internally, converted to gallons
  by the caller.
* The vehicle starts with `initial_fuel_miles` of fuel at mile 0. That fuel is
  free (already in the tank), and there is no station at mile 0 unless one is
  passed in.

Algorithm (greedy, optimal for this model; Khuller, Malekian & Mestre,
"To fill or not to fill", ESA 2007)
------------------------------------------------------------------------
At the current station i:
  1. If some station within range is *cheaper* than i (the destination counts
     as the cheapest of all), buy only enough fuel to reach the *first* such
     station, then drive there.
  2. Otherwise i is the cheapest place to buy within reach: fill the tank,
     then drive to the cheapest station within range (the farthest, on ties).
Why it is optimal: in case 1, fuel bought at i beyond what is needed to reach a
cheaper station j could have been bought at j for less. In case 2, every
mile of the next `range` miles must be paid at a price >= price(i), so buying
it at i is never worse.

If any gap between consecutive stops (or to the destination) exceeds the
range, the journey is impossible and `NoFeasibleFuelPlan` is raised.
Complexity: O(n * k) for n stations with k stations per range window.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from fuel.exceptions import NoFeasibleFuelPlan

EPS = 1e-9


@dataclass(frozen=True)
class StationCandidate:
    position_miles: float
    price_per_gallon: float
    payload: Any = field(default=None, compare=False)


@dataclass(frozen=True)
class Purchase:
    station: StationCandidate
    arrival_fuel_miles: float
    purchased_miles: float

    @property
    def departure_fuel_miles(self) -> float:
        return self.arrival_fuel_miles + self.purchased_miles


def plan_fuel_stops(
    stations: list[StationCandidate],
    total_miles: float,
    range_miles: float,
    initial_fuel_miles: float = 0.0,
) -> list[Purchase]:
    """Return the purchases (in journey order) of a minimum-cost plan.

    Stations where nothing needs to be bought are driven past and not returned.
    """
    if range_miles <= 0:
        raise ValueError("range_miles must be positive")
    if total_miles < 0:
        raise ValueError("total_miles must be non-negative")
    if not 0 <= initial_fuel_miles <= range_miles + EPS:
        raise ValueError("initial_fuel_miles must be between 0 and range_miles")

    ordered = sorted(
        (s for s in stations if -EPS <= s.position_miles <= total_miles + EPS and s.price_per_gallon > 0),
        key=lambda s: (s.position_miles, s.price_per_gallon),
    )
    # Several stations can share a position (e.g. same city centre). Only the
    # cheapest one there can ever be worth using, so drop the others; this does
    # not change the optimal cost and avoids zero-distance "hops".
    usable: list[StationCandidate] = []
    for s in ordered:
        if usable and abs(usable[-1].position_miles - s.position_miles) <= EPS:
            continue
        usable.append(s)
    # Node 0 is the virtual start (nothing can be bought there: price = +inf).
    # The last node is the destination (price = -inf: always "cheaper").
    positions = [0.0] + [max(0.0, s.position_miles) for s in usable] + [total_miles]
    prices = [float("inf")] + [s.price_per_gallon for s in usable] + [float("-inf")]
    dest = len(positions) - 1

    purchases: list[Purchase] = []
    i, fuel = 0, initial_fuel_miles
    while i != dest:
        here = positions[i]
        window_end = bisect_right(positions, here + range_miles + EPS, lo=i + 1)
        if window_end <= i + 1:
            _raise_gap(i, positions, range_miles, fuel)

        nxt = next((j for j in range(i + 1, window_end) if prices[j] < prices[i]), None)
        if nxt is not None:
            buy = max(0.0, (positions[nxt] - here) - fuel)
        else:
            nxt = min(range(i + 1, window_end), key=lambda j: (prices[j], -positions[j]))
            buy = range_miles - fuel

        if buy > EPS:
            if i == 0:  # cannot buy at the virtual start
                _raise_gap(i, positions, range_miles, fuel)
            purchases.append(Purchase(usable[i - 1], arrival_fuel_miles=fuel, purchased_miles=buy))
            fuel += buy
        fuel = max(0.0, fuel - (positions[nxt] - here))
        i = nxt
    return purchases


def remove_dominated(stations: list[StationCandidate], window_miles: float) -> list[StationCandidate]:
    """Drop every station that has another station within `window_miles`
    (along the route, either direction) that is cheaper, or equally cheap and
    farther along. The survivors keep their true positions, so range checks
    stay exact. `window_miles <= 0` returns the input unchanged."""
    if window_miles <= 0 or len(stations) < 2:
        return list(stations)
    ordered = sorted(stations, key=lambda s: s.position_miles)
    positions = [s.position_miles for s in ordered]
    kept = []
    for i, s in enumerate(ordered):
        lo = bisect_right(positions, s.position_miles - window_miles - EPS)
        hi = bisect_right(positions, s.position_miles + window_miles + EPS)
        dominated = any(
            j != i and (ordered[j].price_per_gallon < s.price_per_gallon
                        or (ordered[j].price_per_gallon == s.price_per_gallon and j > i))
            for j in range(lo, hi)
        )
        if not dominated:
            kept.append(s)
    return kept


def _raise_gap(i: int, positions: list[float], range_miles: float, fuel: float):
    dest = len(positions) - 1
    here, next_pos = positions[i], positions[i + 1]
    if i == 0 and dest == 1:
        message = (
            f"There are no fuel stations on the route, and the destination (mile {next_pos:.1f}) "
            f"is beyond the vehicle's starting fuel ({fuel:.1f} miles of range)."
        )
    elif i == 0:
        message = (
            f"The vehicle starts with fuel for {fuel:.1f} miles, but the first fuel station "
            f"on the route is at mile {next_pos:.1f}."
        )
    elif i + 1 == dest:
        message = (
            f"No fuel station within {range_miles:.0f} miles before the destination "
            f"(last station at mile {here:.1f}, destination at mile {positions[dest]:.1f})."
        )
    else:
        message = (
            f"No fuel station within {range_miles:.0f} miles after mile {here:.1f} "
            f"(next station at mile {next_pos:.1f})."
        )
    raise NoFeasibleFuelPlan(message, details={"gap_starts_at_mile": round(here, 1),
                                               "next_stop_at_mile": round(next_pos, 1)})
