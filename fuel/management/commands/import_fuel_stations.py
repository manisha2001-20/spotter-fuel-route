"""Load the assessment fuel-price CSV into the database.

    python manage.py import_fuel_stations
    python manage.py import_fuel_stations --csv path/to/file.csv

Steps:
  1. Read every row and validate it (required fields present, numeric ID,
     positive and plausible price). Invalid rows are counted, reported and skipped.
  2. Count exact duplicate rows (identical in all 7 columns).
  3. Group rows by OPIS Truckstop ID: one station per ID, at its lowest listed
     price (several IDs are listed more than once with different prices).
  4. Attach coordinates from data/city_coordinates.csv (city centre, built
     offline by scripts/build_city_coordinates.py). No network access needed.
  5. Replace the table contents in a single transaction (safe to re-run).
"""

from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel.locations import CANADIAN_PROVINCES, normalize_city
from fuel.models import FuelStation
from fuel.services.station_index import reset_station_index

EXPECTED_COLUMNS = ["OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"]
MAX_PLAUSIBLE_PRICE = Decimal("20")
DEFAULT_CSV = Path(settings.BASE_DIR) / "data" / "fuel-prices-for-be-assessment.csv"
DEFAULT_COORDS = Path(settings.BASE_DIR) / "data" / "city_coordinates.csv"


def parse_row(row: dict) -> tuple[dict | None, str | None]:
    """Validate one CSV row. Returns (clean_row, None) or (None, reason)."""
    try:
        values = {col: (row.get(col) or "").strip() for col in EXPECTED_COLUMNS}
    except AttributeError:
        return None, "malformed row"
    if None in row:  # more fields than columns
        return None, "too many fields"
    for col in ("OPIS Truckstop ID", "Truckstop Name", "City", "State", "Retail Price"):
        if not values[col]:
            return None, f"missing {col}"
    if not values["OPIS Truckstop ID"].isdigit():
        return None, "non-numeric OPIS Truckstop ID"
    try:
        price = Decimal(values["Retail Price"])
    except InvalidOperation:
        return None, "unparseable Retail Price"
    if not price.is_finite() or price <= 0:
        return None, "non-positive Retail Price"
    if price > MAX_PLAUSIBLE_PRICE:
        return None, "implausible Retail Price"
    state = values["State"].upper()
    if len(state) != 2:
        return None, "invalid State"
    return {
        "opis_id": int(values["OPIS Truckstop ID"]),
        "name": values["Truckstop Name"],
        "address": values["Address"],
        "city": values["City"],
        "state": state,
        "rack_id": int(values["Rack ID"]) if values["Rack ID"].isdigit() else None,
        "retail_price": price,
    }, None


def load_city_coordinates(path: Path) -> dict[tuple[str, str], tuple[float, float, str]]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {(r["state"], r["city_key"]): (float(r["latitude"]), float(r["longitude"]), r["source"])
                for r in csv.DictReader(f)}


class Command(BaseCommand):
    help = "Import the fuel-price CSV into the FuelStation table (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
        parser.add_argument("--coordinates", type=Path, default=DEFAULT_COORDS)

    def handle(self, *args, **options):
        csv_path: Path = options["csv"]
        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = [c for c in EXPECTED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise CommandError(f"CSV is missing columns: {', '.join(missing)}")
            raw_rows = list(reader)

        invalid: Counter[str] = Counter()
        seen: set[tuple] = set()
        exact_duplicates = 0
        by_id: dict[int, dict] = {}
        rows_per_id: Counter[int] = Counter()
        for row in raw_rows:
            clean, reason = parse_row(row)
            if clean is None:
                invalid[reason] += 1
                continue
            signature = tuple(clean.values())
            if signature in seen:
                exact_duplicates += 1
            seen.add(signature)
            rows_per_id[clean["opis_id"]] += 1
            current = by_id.get(clean["opis_id"])
            if current is None or clean["retail_price"] < current["retail_price"]:
                by_id[clean["opis_id"]] = clean

        coords = load_city_coordinates(options["coordinates"])
        if not coords:
            self.stderr.write(self.style.WARNING(f"No coordinates file at {options['coordinates']}; "
                                                 "stations will not be usable for routing."))
        stations = []
        located = Counter()
        for opis_id, s in by_id.items():
            country = "CA" if s["state"] in CANADIAN_PROVINCES else "US"
            lat = lon = None
            source = FuelStation.LocationSource.NONE
            if country == "US":
                hit = coords.get((s["state"], normalize_city(s["city"])))
                if hit:
                    lat, lon, source = hit
            located[source if country == "US" else "canada (not routed)"] += 1
            stations.append(FuelStation(country=country, latitude=lat, longitude=lon, location_source=source,
                                        price_rows=rows_per_id[opis_id], **s))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        reset_station_index()

        valid_rows = len(raw_rows) - sum(invalid.values())
        self.stdout.write(f"CSV rows read:              {len(raw_rows)}")
        self.stdout.write(f"Valid rows:                 {valid_rows}")
        self.stdout.write(f"Invalid rows skipped:       {sum(invalid.values())} {dict(invalid) or ''}")
        self.stdout.write(f"Exact duplicate rows:       {exact_duplicates}")
        self.stdout.write(f"Unique stations (OPIS ID):  {len(stations)}")
        for source, n in sorted(located.items()):
            self.stdout.write(f"  location {source:<28} {n}")
        self.stdout.write(self.style.SUCCESS(f"Imported {len(stations)} stations."))
