# Spotter Fuel Route API

A Django REST API that takes a start and a finish location in the USA and returns:

- the driving route, as GeoJSON for drawing on a map;
- the most cost-effective places to refuel along it, for a vehicle with a **500-mile range** (multiple stops when needed);
- the **total money spent on fuel** at **10 miles per gallon**.

Fuel prices come from the provided `fuel-prices-for-be-assessment.csv`, and routing uses the free [OSRM](https://project-osrm.org/) service. A small Leaflet page at `/` draws the API response on a map.

**Stack:** Django 6.1.3 (latest stable), Django REST Framework 3.18.3, NumPy, SQLite, and Python 3.12 or newer (Django 6.x requires it).

---

## Quick start (Windows PowerShell)

```powershell
cd spotter-fuel-route
py -3.13 -m venv .venv            # any Python >= 3.12
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py import_fuel_stations
python manage.py runserver
```

Then open <http://127.0.0.1:8000/> for the map, or call the API:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/route/ `
  -ContentType 'application/json' `
  -Body '{"start": "New York, NY", "finish": "Los Angeles, CA"}'
```

macOS/Linux: the same steps, using `python3 -m venv .venv` and `source .venv/bin/activate`.

No API keys are needed. To change any setting (see `.env.example`), copy `.env.example` to `.env`.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST`/`GET` | `/api/route/` | Plan a trip: route, fuel stops, total cost |
| `GET` | `/api/stations/` | Stations from the dataset, cheapest first. Filters: `state`, `max_price`, `located`, `ordering=-price`; pagination: `limit`, `offset` |
| `GET` | `/api/stations/<opis_id>/` | One station |
| `GET` | `/api/health/` | Liveness check plus a summary of the loaded dataset |
| `GET` | `/` | Map page. `/?start=Dallas, TX&finish=Houston, TX` plans immediately |

A Postman collection with every demo request is in [`docs/postman_collection.json`](docs/postman_collection.json).

### `POST /api/route/`

```json
{ "start": "Dallas, TX", "finish": "Houston, TX", "initial_fuel_gallons": 0 }
```

- `start` / `finish`: any US place or address, or a `"latitude,longitude"` pair. A coordinate pair skips geocoding.
- `initial_fuel_gallons` (optional, 0–50, default 0): fuel already in the tank.

The response below is real; only the `map` coordinates are shortened:

```json
{
  "start":  { "query": "Dallas, TX",  "label": "Dallas, Dallas County, Texas, United States", "latitude": 32.776272, "longitude": -96.796856, "source": "nominatim" },
  "finish": { "query": "Houston, TX", "label": "Houston, Harris County, Texas, United States", "latitude": 29.758938, "longitude": -95.367697, "source": "nominatim" },
  "route": { "distance_miles": 238.5, "duration_hours": 4.22, "stations_in_corridor": 37, "stations_considered": 6 },
  "fuel_stops": [
    {
      "stop_number": 1,
      "station": { "opis_id": 63669, "name": "One9 #1248", "address": "I-45, EXIT 271", "city": "Wilmer", "state": "TX", "latitude": 32.599015, "longitude": -96.681585 },
      "mile_marker": 15.0,
      "distance_from_route_miles": 0.1,
      "price_per_gallon": 2.75566666,
      "fuel_on_arrival_gallons": 0.0,
      "gallons_purchased": 23.848,
      "gallons_for_drive_from_origin": 1.497,
      "fuel_on_departure_gallons": 22.352,
      "cost": 65.72
    }
  ],
  "summary": {
    "number_of_stops": 1,
    "total_fuel_cost": 65.72,
    "total_gallons_purchased": 23.848,
    "total_gallons_used": 23.848,
    "initial_fuel_gallons": 0.0,
    "fuel_remaining_at_destination_gallons": 0.0,
    "average_price_per_gallon": 2.7558
  },
  "map": { "type": "FeatureCollection", "features": [ "route LineString, start/finish Points, one Point per fuel stop" ] },
  "assumptions": { "vehicle_range_miles": 500.0, "miles_per_gallon": 10.0, "tank_capacity_gallons": 50.0, "...": "..." },
  "meta": {
    "external_api_calls": { "geocoding": 2, "routing": 1, "total": 3 },
    "cache_hits": [],
    "timing_ms": { "geocoding": 1594.8, "routing": 1162.5, "optimisation": 1.2, "total": 2758.4 }
  }
}
```

Check: 238.5 mi ÷ 10 mpg = 23.848 gal, and 23.848 gal × $2.75566666 = **$65.72**.
The station is 15 miles from the origin and the trip starts with an empty tank, so 1.497 gal of that purchase pays for those first miles. The truck leaves with 23.848 − 1.497 = 22.352 gal, enough for the remaining 223.5 miles.

### Errors

Every error has the same shape: `{"error": {"code": "...", "message": "...", "details": ...}}`.

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_request` | Missing or blank `start`/`finish`, longer than 200 characters, or `initial_fuel_gallons` outside 0–50 |
| 422 | `location_not_found` | The geocoder found nothing for the text |
| 422 | `location_outside_usa` | The location is not in the USA (e.g. "Toronto, Canada", or "Paris", which resolves to France) |
| 422 | `route_not_found` | No drivable route (e.g. Honolulu → Chicago) |
| 422 | `no_feasible_fuel_plan` | Some stretch of the route has no station within 500 miles; `details` gives the mile where the gap starts |
| 503 | `stations_not_loaded` | `import_fuel_stations` has not been run |
| 502 / 504 | `upstream_error` / `upstream_timeout` | OSRM or Nominatim failed or timed out |

---

## How it works

```
request ─► validate (DRF serializer)
        ─► resolve start & finish ── "lat,lon" parsed locally, otherwise Nominatim (cached 30 days)
        ─► ONE OSRM call ─────────── distance + duration + full geometry (cached)
        ─► stations near the route ─ in-memory NumPy index, no external calls
        ─► optimal fuel plan ─────── greedy algorithm (below)
        ─► JSON + GeoJSON map
```

| Module | Responsibility |
|---|---|
| `fuel/services/optimizer.py` | Pure algorithm: no Django, no I/O |
| `fuel/services/station_index.py` | Stations held in memory as NumPy arrays; corridor matching |
| `fuel/services/geo.py` | Haversine, route resampling, point-to-route distance |
| `fuel/services/geocoding.py`, `routing.py`, `http.py` | External APIs: timeouts, error mapping, caching, call counting |
| `fuel/services/planner.py` | Ties the steps together and builds the response |
| `fuel/management/commands/import_fuel_stations.py` | Validates and loads the CSV |
| `fuel/views.py`, `serializers.py`, `exceptions.py` | Thin HTTP layer |

### External API budget

The PDF asks for one call to the map/routing API, with two or three acceptable.

| Input | Calls |
|---|---|
| Two place names, first time | 3 (2 geocoding + **1 routing**) |
| Coordinates (`"lat,lon"`) | **1** (routing only) |
| Any repeated request | **0** (geocodes and routes are cached on disk) |

Stations are **never** geocoded at request time; their coordinates are prepared once, offline (see "Dataset").

### Finding stations along the route

1. The OSRM geometry is resampled to one point per route mile, and positions are scaled to OSRM's road distance.
2. A bounding-box filter, then a coarse check against every 10th point, discards stations that are clearly far away. Both filters are lossless.
3. Each remaining station gets its exact distance to the nearest route point. Stations within `CORRIDOR_MILES` (10) are candidates. A station's `mile_marker` is its position along the route.

### The optimisation algorithm

This is the classic "gas station problem". Fuel is linear in distance, any amount can be bought, and the tank holds 50 gal (500 miles). The plan uses a greedy rule that is **provably cost-optimal** (Khuller, Malekian & Mestre, *To fill or not to fill*, 2007). At each station:

1. **If a cheaper station is within 500 miles,** buy only enough fuel to reach the *first* such station. The destination counts as "cheapest", so the vehicle never arrives with fuel it paid for but didn't need.
2. **Otherwise,** this is the cheapest fuel within reach: **fill the tank**, then drive to the cheapest station within range.

*Why it is optimal:* in case 1, any extra fuel bought here could be bought for less a little further on. In case 2, every mile of the next 500 must be paid for at a price at least this high, so buying it here is never worse.

If any stretch of the route has no station within 500 miles, the request fails with `no_feasible_fuel_plan` rather than returning an unsafe plan.

**Verified by tests:**
- On 300 random instances, the greedy result equals the cost found by an independent brute-force dynamic programme.
- Every plan is replayed mile by mile to check that the vehicle never runs dry, never exceeds 50 gal, and never buys more than it needs.
- A mutation check confirmed these tests fail when the algorithm is deliberately broken.

### Assumptions

| Assumption | Value / rule |
|---|---|
| Range and efficiency | 500 miles, 10 mpg, so a 50-gallon tank |
| Fuel used | route miles ÷ 10 |
| Stop cost | `gallons_purchased × price_per_gallon` (rounded to cents) |
| Total cost | sum of the stop costs |
| Starting fuel | **Empty by default.** The first fill-up is at the cheapest station within the first `START_WINDOW_MILES` (25) of the route, and the fuel for those first miles is bought there, so every trip's fuel is paid for. That share is reported as `gallons_for_drive_from_origin`, and `fuel_on_departure = fuel_on_arrival + gallons_purchased − gallons_for_drive_from_origin`. Set `initial_fuel_gallons` to start with fuel in the tank. |
| Station location | The CSV has no coordinates, so each station is placed at its **city's centre** (see below) |
| Detours | A station up to `CORRIDOR_MILES` (10) from the route counts as on the route; the short detour to it is not added to the distance |
| Nearby duplicates | A station is skipped if an equal-or-cheaper station lies within `DOMINANCE_WINDOW_MILES` (15) along the route (see below) |
| Final destination | Never a fuel stop; the plan arrives with an empty tank (or with leftover starting fuel) |

#### Why `DOMINANCE_WINDOW_MILES` exists

The strict optimum sometimes buys 0.2 gal at one station to reach a slightly cheaper one 2 miles away. That really is cheaper, but positions are only accurate to city level, so the distinction means nothing. Skipping stations that have a cheaper neighbour within 15 route miles gives cleaner plans at a negligible cost. Measured:

| Route | Strict optimum (window 0) | Default (15 mi) |
|---|---|---|
| New York → Los Angeles | 17 stops, $847.20 | 15 stops, $847.31 |
| Chicago → Denver | 7 stops, $292.03 | 5 stops, $292.08 |
| Boston → San Francisco | 25 stops, $967.84 | 18 stops, $968.87 |

Skipped stations keep their true positions, so the 500-mile limit is still enforced exactly. If pruning ever made a trip infeasible, the planner falls back to every station. Set `DOMINANCE_WINDOW_MILES=0` for the strict optimum.

---

## Dataset

`data/fuel-prices-for-be-assessment.csv` is the provided file, unmodified: 8,151 rows, 7 columns.

`python manage.py import_fuel_stations` handles it as follows (output from a real run):

```
CSV rows read:              8151
Valid rows:                 8151
Invalid rows skipped:       0
Exact duplicate rows:       26
Unique stations (OPIS ID):  6738
  location canada (not routed)          112
  location census_county_subdivision    150
  location census_place                 6267
  location nominatim                    203
  location none                         6
```

- **Validation:** every row is checked for a numeric ID, the required fields, and a price that is positive, finite and at most $20. Bad rows are skipped and counted; tests cover each case. The real file has none.
- **Duplicates:** 678 OPIS IDs appear on several rows, with the same address but different prices. One station is stored per ID, at its **lowest listed price**; `price_rows` records how many rows it had.
- **Canada:** 112 stations are in Canadian provinces. They are kept in the table but not used for US routing.

### Station coordinates

The CSV has addresses like `I-44, EXIT 283 & US-69` but no latitude/longitude, and geocoding 6,700 stations on every request would break the API-call budget. Instead, `scripts/build_city_coordinates.py` was run **once** to build `data/city_coordinates.csv`, a committed file that places each station at its city's centre:

1. **US Census Bureau 2024 Gazetteer** (public domain): incorporated places and census-designated places, then county subdivisions. This resolves 3,668 of the 3,808 city/state pairs.
2. **OpenStreetMap Nominatim** for the remainder, mostly unincorporated communities like Breezewood, PA. A result is accepted only if Nominatim confirms the same state. This resolves 135 more.
3. **5 pairs stayed unresolved** (6 stations: St. Johns FL, Etters PA, Pueblo of Acoma NM, Hot Springs National Park AR, Mc Alpin FL). Those stations are left unlocated rather than guessed.

**Result:** 6,620 of 6,626 US stations (99.9%) are usable for routing. Truck stops sit on highways at the edge of their town, which is why the corridor is 10 miles.

---

## Performance

Measured on a laptop:

| Scenario | Time |
|---|---|
| First request, two place names (3 external calls) | 2.2–2.7 s, almost all of it waiting on Nominatim/OSRM |
| First request with coordinates (1 external call) | ~0.3 s |
| Repeated request (cached) | 18–107 ms (cross-country routes are the slowest) |
| Station matching + optimisation, cross-country | ~70–90 ms |

- Stations load from SQLite into memory **once per process**. After that, each request costs one cheap `COUNT/MAX` query to detect a re-import.
- The route geometry in the response is thinned to about one point per 0.5 miles (about 120 KB for New York → Los Angeles).

---

## Tests

```powershell
python -m pytest
```

There are 357 tests and they run in about 2 seconds. The external services are replaced by a fake, so tests never use the network; an unmocked HTTP call fails the test.

- `test_optimizer.py`:
  - hand-worked examples;
  - the 500-mile limit, including exactly-500-mile legs;
  - multiple stops, starting fuel, infeasible journeys and invalid prices;
  - **300 random instances checked against a brute-force optimum**.
- `test_api.py`:
  - every endpoint;
  - per-stop and total arithmetic recomputed independently;
  - fuel continuity between stops;
  - a hand-calculated optimal cost;
  - caching and the external-call count;
  - every error path (bad input, unknown or non-US location, no route, impossible journey, no data, upstream timeout, outage, HTTP 500, non-JSON response).
- `test_import.py`:
  - the **real CSV**: all 8,151 rows accounted for, and every stored price equals that station's lowest CSV price;
  - crafted invalid, duplicate and malformed files.
- `test_geo.py`: distances, resampling, nearest-point projection.

There is also a live end-to-end check against the real APIs, run against a running server:

```powershell
python scripts/smoke_test.py --base http://127.0.0.1:8000
```

---

## Project layout

```
config/                  Django settings and URLs
fuel/
  models.py              FuelStation
  views.py, urls.py, serializers.py, exceptions.py
  services/              optimizer, planner, station_index, geo, geocoding, routing, http
  management/commands/   import_fuel_stations
  templates/fuel/        map.html (Leaflet demo page)
  tests/
data/
  fuel-prices-for-be-assessment.csv   provided dataset (unchanged)
  city_coordinates.csv                station city coordinates (built once, offline)
scripts/
  build_city_coordinates.py           rebuilds data/city_coordinates.csv
  smoke_test.py                       live end-to-end check
docs/postman_collection.json
```

## Limitations and possible next steps

- **Station positions are city-level** because the dataset has no coordinates. Geocoding each exit address, or using OPIS coordinates, would allow a tighter corridor and exact detour distances.
- **Detour miles** to an off-route station are not added to the fuel used.
- **No cost per stop.** The plan minimises fuel cost only; real fleets also value time. A per-stop cost would need a dynamic programme instead of the greedy rule.
- **Prices are static,** taken from the provided snapshot.
- **The free public OSRM and Nominatim servers are rate-limited** and meant for light use. For production, point `OSRM_BASE_URL` / `NOMINATIM_BASE_URL` at self-hosted instances and use a shared cache such as Redis instead of the file cache.
- Routes that leave the USA (e.g. through Ontario) use only US stations.
