"""City-name normalisation shared by the station importer and the
`scripts/build_city_coordinates.py` data-preparation script.

The fuel-price CSV has no coordinates, only free-text highway addresses such as
"I-44, EXIT 283 & US-69". Stations are therefore located at the centre of
their city/town (see README "Station coordinates"). The same normaliser must
be used when the lookup file is built and when it is read, so it lives here.
"""

import re

CANADIAN_PROVINCES = frozenset({
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
})

_ABBREVIATIONS = (
    (re.compile(r"\bst\b\.?"), "saint"),
    (re.compile(r"\bste\b\.?"), "sainte"),
    (re.compile(r"\bft\b\.?"), "fort"),
    (re.compile(r"\bmt\b\.?"), "mount"),
)


def normalize_city(name: str) -> str:
    """Lower-case, strip punctuation and expand common abbreviations.

    >>> normalize_city("  Ft. Worth ")
    'fort worth'
    >>> normalize_city("Mc Calla")
    'mc calla'
    """
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    for pattern, replacement in _ABBREVIATIONS:
        s = pattern.sub(replacement, s)
    return " ".join(s.split())


def city_keys(state: str, city: str) -> list[tuple[str, str]]:
    """Lookup keys for a (state, city) pair: the normalised name and a
    space-free variant, so "Mc Calla" matches "McCalla" and "De Forest"
    matches "DeForest"."""
    state = (state or "").strip().upper()
    norm = normalize_city(city)
    keys = [(state, norm)]
    compact = norm.replace(" ", "")
    if compact != norm:
        keys.append((state, compact))
    return keys
