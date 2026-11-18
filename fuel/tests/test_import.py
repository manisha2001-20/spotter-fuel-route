"""The importer against the real assessment CSV and against crafted bad input."""

import csv
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from fuel.models import FuelStation

REAL_CSV = Path(settings.BASE_DIR) / "data" / "fuel-prices-for-be-assessment.csv"
HEADER = ["OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"]


def write_csv(tmp_path, rows, header=HEADER):
    path = tmp_path / "prices.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def run_import(**kwargs):
    out = StringIO()
    call_command("import_fuel_stations", stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


@pytest.mark.django_db
def test_real_dataset_every_row_accounted_for():
    with open(REAL_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 8151
    assert list(rows[0].keys()) == HEADER

    output = run_import()
    assert "CSV rows read:              8151" in output
    assert "Invalid rows skipped:       0" in output
    assert "Exact duplicate rows:       26" in output

    assert FuelStation.objects.count() == len({r["OPIS Truckstop ID"] for r in rows}) == 6738
    # Each station's stored price is the lowest of its rows in the CSV.
    lowest = {}
    for r in rows:
        oid = int(r["OPIS Truckstop ID"])
        lowest[oid] = min(lowest.get(oid, Decimal("99")), Decimal(r["Retail Price"]))
    stored = dict(FuelStation.objects.values_list("opis_id", "retail_price"))
    assert stored == lowest
    # Row counts per station add back up to every CSV row.
    assert sum(FuelStation.objects.values_list("price_rows", flat=True)) == 8151
    # Nearly all US stations get coordinates; Canadian ones are kept but not routed.
    us = FuelStation.objects.filter(country="US")
    assert us.filter(latitude__isnull=False).count() / us.count() > 0.99
    assert FuelStation.objects.filter(country="CA", latitude__isnull=False).count() == 0


@pytest.mark.django_db
def test_invalid_rows_are_skipped_and_reported(tmp_path):
    path = write_csv(tmp_path, [
        ["1", "GOOD", "I-1", "Dallas", "TX", "10", "3.10"],
        ["2", "NEGATIVE", "I-1", "Dallas", "TX", "10", "-3.10"],
        ["3", "ZERO", "I-1", "Dallas", "TX", "10", "0"],
        ["4", "TEXT PRICE", "I-1", "Dallas", "TX", "10", "abc"],
        ["5", "NO PRICE", "I-1", "Dallas", "TX", "10", ""],
        ["X", "BAD ID", "I-1", "Dallas", "TX", "10", "3.00"],
        ["7", "", "I-1", "Dallas", "TX", "10", "3.00"],
        ["8", "HUGE", "I-1", "Dallas", "TX", "10", "999"],
        ["9", "NAN", "I-1", "Dallas", "TX", "10", "NaN"],
    ])
    output = run_import(csv=path)
    assert "Invalid rows skipped:       8" in output
    assert list(FuelStation.objects.values_list("name", flat=True)) == ["GOOD"]


@pytest.mark.django_db
def test_duplicates_collapse_to_lowest_price(tmp_path):
    path = write_csv(tmp_path, [
        ["1", "A", "I-1", "Dallas", "TX", "10", "3.30"],
        ["1", "A", "I-1", "Dallas", "TX", "10", "3.10"],
        ["1", "A", "I-1", "Dallas", "TX", "10", "3.10"],
    ])
    output = run_import(csv=path)
    assert "Exact duplicate rows:       1" in output
    station = FuelStation.objects.get()
    assert station.retail_price == Decimal("3.10") and station.price_rows == 3
    assert station.latitude is not None  # Dallas, TX is in the coordinates file


@pytest.mark.django_db
def test_reimport_is_idempotent(tmp_path):
    path = write_csv(tmp_path, [["1", "A", "I-1", "Dallas", "TX", "10", "3.30"]])
    run_import(csv=path)
    run_import(csv=path)
    assert FuelStation.objects.count() == 1


@pytest.mark.django_db
def test_missing_columns_and_missing_file_are_errors(tmp_path):
    with pytest.raises(CommandError, match="missing columns"):
        run_import(csv=write_csv(tmp_path, [["1", "A"]], header=["OPIS Truckstop ID", "Truckstop Name"]))
    with pytest.raises(CommandError, match="not found"):
        run_import(csv=tmp_path / "nope.csv")
