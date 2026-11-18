"""One-off data preparation: build data/city_coordinates.csv.

The assessment CSV contains no latitude/longitude, so every station is
located at the centre of its city. This script resolves each distinct
(state, city) in the dataset ONCE, offline, and writes the result to a small
CSV that is committed to the repository. The API itself never geocodes
stations at request time.

Resolution order
  1. US Census Bureau 2024 Gazetteer - incorporated places and census-designated
     places (public domain).
  2. US Census Bureau 2024 Gazetteer - county subdivisions (townships etc.).
  3. OpenStreetMap Nominatim, only for names still unresolved (unincorporated
     communities such as "Breezewood, PA"). The result is accepted only if
     Nominatim confirms the same US state. Requests are rate-limited to
     1 per second, as Nominatim's usage policy requires.

Usage (from the repository root):
    python scripts/build_city_coordinates.py
    python scripts/build_city_coordinates.py --no-nominatim
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fuel.locations import CANADIAN_PROVINCES, city_keys, normalize_city  # noqa: E402

GAZETTEER_BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
GAZETTEER_FILES = {
    "census_place": "2024_Gaz_place_national.zip",
    "census_county_subdivision": "2024_Gaz_cousubs_national.zip",
}
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "spotter-fuel-route/1.0 (offline data preparation)"

# Census NAME values carry a legal/statistical suffix: "Abbeville city", "Abanda CDP".
_SUFFIX = re.compile(
    r"\s+(city and borough|city|town|village|borough|cdp|municipality|"
    r"charter township|township|plantation|ccd|ut|gore|grant|location|"
    r"purchase|reservation|comunidad|zona urbana)$",
    re.IGNORECASE,
)


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def load_gazetteer(source: str, filename: str) -> dict[tuple[str, str], tuple[float, float, str, str]]:
    print(f"Downloading {filename} ...")
    archive = zipfile.ZipFile(io.BytesIO(_download(GAZETTEER_BASE + filename)))
    text = archive.read(archive.namelist()[0]).decode("latin-1")
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    header = [h.strip() for h in next(reader)]
    index: dict[tuple[str, str], tuple[float, float, str, str]] = {}
    for row in reader:
        rec = dict(zip(header, (c.strip() for c in row)))
        full_name = rec["NAME"]
        base = _SUFFIX.sub("", full_name).strip()
        value = (float(rec["INTPTLAT"]), float(rec["INTPTLONG"]), source, full_name)
        names = [base]
        # "Boise City city" is the Census name of Boise, ID.
        if base.lower().endswith(" city"):
            names.append(base[:-5])
        for name in names:
            for key in city_keys(rec["USPS"], name):
                index.setdefault(key, value)
    return index


def nominatim_lookup(city: str, state: str) -> tuple[float, float, str] | None:
    params = urllib.parse.urlencode({
        "city": city, "state": state, "country": "USA",
        "format": "jsonv2", "addressdetails": 1, "limit": 1, "countrycodes": "us",
    })
    try:
        import json
        results = json.loads(_download(f"{NOMINATIM_URL}?{params}"))
    except Exception as exc:  # network problems are reported, not fatal
        print(f"  ! Nominatim failed for {city}, {state}: {exc}")
        return None
    if not results:
        return None
    hit = results[0]
    iso = (hit.get("address") or {}).get("ISO3166-2-lvl4", "")
    if iso != f"US-{state}":
        return None
    return float(hit["lat"]), float(hit["lon"]), hit.get("display_name", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", default=ROOT / "data" / "fuel-prices-for-be-assessment.csv", type=Path)
    parser.add_argument("--out", default=ROOT / "data" / "city_coordinates.csv", type=Path)
    parser.add_argument("--no-nominatim", action="store_true", help="skip the Nominatim fallback")
    args = parser.parse_args()

    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        wanted: dict[tuple[str, str], str] = {}
        for row in csv.DictReader(f):
            state = row["State"].strip().upper()
            if state in CANADIAN_PROVINCES:
                continue
            city = row["City"].strip()
            wanted.setdefault((state, normalize_city(city)), city)
    print(f"Distinct US (state, city) pairs in dataset: {len(wanted)}")

    gazetteers = [load_gazetteer(src, fn) for src, fn in GAZETTEER_FILES.items()]

    resolved: dict[tuple[str, str], tuple[float, float, str, str]] = {}
    unresolved: list[tuple[str, str]] = []
    for key, city in wanted.items():
        hit = None
        for gaz in gazetteers:
            for k in city_keys(key[0], city):
                hit = gaz.get(k)
                if hit:
                    break
            if hit:
                break
        if hit:
            resolved[key] = hit
        else:
            unresolved.append(key)
    print(f"Resolved from Census Gazetteer: {len(resolved)}; unresolved: {len(unresolved)}")

    if not args.no_nominatim and unresolved:
        print("Resolving the remainder with Nominatim (1 request/second) ...")
        still: list[tuple[str, str]] = []
        for key in unresolved:
            city = wanted[key]
            hit = nominatim_lookup(city, key[0])
            time.sleep(1.1)
            if hit:
                resolved[key] = (hit[0], hit[1], "nominatim", hit[2][:120])
            else:
                still.append(key)
        unresolved = still

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["state", "city_key", "latitude", "longitude", "source", "matched_name"])
        for (state, city_key), (lat, lon, source, matched) in sorted(resolved.items()):
            writer.writerow([state, city_key, f"{lat:.6f}", f"{lon:.6f}", source, matched])
    print(f"Wrote {len(resolved)} rows to {args.out}")
    if unresolved:
        print(f"Still unresolved ({len(unresolved)}): " + ", ".join(f"{c} {s}" for s, c in unresolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
