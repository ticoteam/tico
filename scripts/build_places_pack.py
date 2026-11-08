#!/usr/bin/env python3
"""Regenerate the public places pack in backend/entities/places/data/<version>/.

The pack is the one place tree every company's Directory uses: countries (ISO 3166-1), US states, DC and territories,
US counties, and US incorporated places of 50,000 people or more. Each row has a stable id, a kind, a parent and, for
US rows, the Census code. It is public reference data only: no people, no laws.

    python3 scripts/build_places_pack.py                # download the sources into the cache, then build
    python3 scripts/build_places_pack.py --offline      # build from the cache only

Writes places.csv (one row per place) and pack.json (sources with URL, license, sha256 and size; the rules applied;
row and fallback counts). Do not edit places.csv by hand: change this script and run it again.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "backend" / "entities" / "places" / "data"
DEFAULT_VERSION = "2026.11.1"
MIN_CITY_POPULATION = 50_000
POPULATION_COLUMN = "POPESTIMATE2024"
COUNTRY_COUNT = 249  # ISO 3166-1 officially assigned alpha-2 codes
COLUMNS = ("id", "kind", "name", "parent", "usps", "iso_alpha3", "census_geoid", "governing_body", "aliases")
KINDS = ("country", "state", "territory", "county", "city")

# U.S. Minor Outlying Islands: no civil government and no population, so no place row.
EXCLUDED_STATE_CODES = frozenset({"UM"})
# Census county and place files cover the 50 states, DC and Puerto Rico. The other territories get a territory row only.
TERRITORY_CODES = frozenset({"AS", "GU", "MP", "PR", "VI"})

# Countries whose governing-body name takes "the" in ordinary use. Every other country reads "Government of <name>".
GOVERNMENT_THE = {"US": "the ", "GB": "the ", "NL": "the ", "PH": "the "}

# Curated, not sourced: alternate names people write for a few countries, so find() reaches them.
COUNTRY_ALIASES = {
    "AE": ["UAE", "U.A.E."],
    "CD": ["DR Congo", "DRC", "Democratic Republic of the Congo"],
    "CG": ["Republic of the Congo"],
    "CI": ["Ivory Coast"],
    "CV": ["Cape Verde"],
    "CZ": ["Czech Republic"],
    "FM": ["Micronesia"],
    "GB": ["UK", "U.K.", "Britain", "Great Britain"],
    "MK": ["Macedonia"],
    "MM": ["Burma"],
    "MO": ["Macau"],
    "NL": ["Holland"],
    "PS": ["Palestine"],
    "RU": ["Russia"],
    "SZ": ["Swaziland"],
    "TL": ["East Timor"],
    "TR": ["Turkey"],
    "US": ["U.S.", "U.S.A.", "United States of America"],
    "VA": ["Vatican", "Vatican City"],
}

# AP style abbreviations for the 50 states, plus names people use for DC and the territories.
STATE_ALIASES = {
    "AL": ["Ala."],
    "AZ": ["Ariz."],
    "AR": ["Ark."],
    "CA": ["Calif."],
    "CO": ["Colo."],
    "CT": ["Conn."],
    "DE": ["Del."],
    "FL": ["Fla."],
    "GA": ["Ga."],
    "IL": ["Ill."],
    "IN": ["Ind."],
    "KS": ["Kan."],
    "KY": ["Ky."],
    "LA": ["La."],
    "MD": ["Md."],
    "MA": ["Mass."],
    "MI": ["Mich."],
    "MN": ["Minn."],
    "MS": ["Miss."],
    "MO": ["Mo."],
    "MT": ["Mont."],
    "NE": ["Neb."],
    "NV": ["Nev."],
    "NH": ["N.H."],
    "NJ": ["N.J."],
    "NM": ["N.M."],
    "NY": ["N.Y."],
    "NC": ["N.C."],
    "ND": ["N.D."],
    "OK": ["Okla."],
    "OR": ["Ore."],
    "PA": ["Pa."],
    "RI": ["R.I."],
    "SC": ["S.C."],
    "SD": ["S.D."],
    "TN": ["Tenn."],
    "VT": ["Vt."],
    "VA": ["Va."],
    "WA": ["Wash."],
    "WV": ["W.Va."],
    "WI": ["Wis."],
    "WY": ["Wyo."],
    "DC": ["D.C.", "Washington, D.C.", "Washington DC"],
    "MP": ["CNMI", "Northern Mariana Islands"],
    "VI": ["U.S. Virgin Islands", "Virgin Islands", "USVI"],
}

# Governing bodies for DC and the territories. Every other state reads "State of <name>".
TERRITORY_BODIES = {
    "AS": "Government of American Samoa",
    "DC": "Government of the District of Columbia",
    "GU": "Government of Guam",
    "MP": "Commonwealth of the Northern Mariana Islands",
    "PR": "Commonwealth of Puerto Rico",
    "VI": "Government of the United States Virgin Islands",
}

# Curated city short names (usps, Census gazetteer name). Each key must match a city in the pack; the build checks.
CITY_ALIASES = {
    ("NY", "New York"): ["NYC", "New York City"],
    ("CA", "Los Angeles"): ["LA"],
    ("CA", "San Francisco"): ["SF"],
    ("PA", "Philadelphia"): ["Philly"],
    ("NV", "Las Vegas"): ["Vegas"],
    ("LA", "New Orleans"): ["NOLA"],
    ("IN", "Indianapolis"): ["Indy"],
}


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    url: str
    file: str
    license: str
    sha256: str | None = None


CENSUS_LICENSE = "Public domain: U.S. Census Bureau data (17 U.S.C. 105)"
CODES = "https://www2.census.gov/geo/docs/reference/codes2020/"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/"
SOURCES = (
    Source(
        "iso3166-1",
        "ISO 3166-1 codes and English names, from pycountry 26.2.16 (pycountry/databases/iso3166-1.json)",
        "https://files.pythonhosted.org/packages/9c/42/7703bd45b62fecd44cd7d3495423097e2f7d28bc2e99e7c1af68892ab157/"
        "pycountry-26.2.16-py3-none-any.whl",
        "pycountry-26.2.16-py3-none-any.whl",
        "Codes and names are facts. The pycountry package is LGPL-2.1-only; only its ISO data file is read.",
        sha256="115c4baf7cceaa30f59a4694d79483c9167dbce7a9de4d3d571c5f3ea77c305a",
    ),
    Source(
        "census-states-2020",
        "Census 2020 national state codes",
        CODES + "national_state2020.txt",
        "national_state2020.txt",
        CENSUS_LICENSE,
    ),
    Source(
        "census-counties-2020",
        "Census 2020 national county codes",
        CODES + "national_county2020.txt",
        "national_county2020.txt",
        CENSUS_LICENSE,
    ),
    Source(
        "census-place-by-county-2020",
        "Census 2020 place-by-county reference",
        CODES + "national_place_by_county2020.txt",
        "national_place_by_county2020.txt",
        CENSUS_LICENSE,
    ),
    Source(
        "census-gazetteer-places-2025",
        "Census 2025 Gazetteer, places (names and GEOIDs)",
        GAZETTEER + "2025_Gaz_place_national.zip",
        "2025_Gaz_place_national.zip",
        CENSUS_LICENSE,
    ),
    Source(
        "census-popest-subcounty-2024",
        "Census Vintage 2024 population estimates, sub-county file (incorporated places, POPESTIMATE2024)",
        "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv",
        "sub-est2024.csv",
        CENSUS_LICENSE,
    ),
)


@dataclass
class Row:
    id: str
    kind: str
    name: str
    parent: str = ""
    usps: str = ""
    iso_alpha3: str = ""
    census_geoid: str = ""
    governing_body: str = ""
    aliases: list[str] = field(default_factory=list)

    def as_csv(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "parent": self.parent,
            "usps": self.usps,
            "iso_alpha3": self.iso_alpha3,
            "census_geoid": self.census_geoid,
            "governing_body": self.governing_body,
            "aliases": "|".join(self.aliases),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(source: Source, cache: Path, offline: bool) -> Path:
    path = cache / source.file
    if not path.exists():
        if offline:
            sys.exit(f"missing {path}; run without --offline to download it")
        cache.mkdir(parents=True, exist_ok=True)
        print(f"downloading {source.url}", file=sys.stderr)
        with urllib.request.urlopen(source.url, timeout=180) as response:
            data = response.read()
        fd, tmp = tempfile.mkstemp(dir=cache, suffix=".part")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    if source.sha256 and _sha256(path) != source.sha256:
        sys.exit(f"{path} does not match the pinned sha256 for {source.key}; delete it and fetch again")
    return path


def _read_table(path: Path, *, delimiter: str, encoding: str = "utf-8") -> list[dict[str, str]]:
    """Rows of a Census table. A .zip holds one .txt; a bare file is read as it is."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            member = next(name for name in archive.namelist() if name.endswith(".txt"))
            text = archive.read(member).decode(encoding)
    else:
        text = path.read_bytes().decode(encoding)
    rows = []
    for raw in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None})
    return rows


INCORPORATED_SUFFIXES = (
    "city and borough",
    "metropolitan government",
    "metro government",
    "consolidated government",
    "unified government",
    "urban county",
    "city",
    "town",
    "village",
    "borough",
    "municipality",
    "corporation",
    "government",
)


def _place_name(gazetteer_name: str) -> str:
    """The name without its type word: the Gazetteer says 'Los Angeles city'; the pack says 'Los Angeles'."""
    for suffix in INCORPORATED_SUFFIXES:
        if gazetteer_name.endswith(" " + suffix):
            return gazetteer_name[: -len(suffix) - 1].strip()
    return gazetteer_name.strip()


def _unique(name: str, candidates: list[str]) -> list[str]:
    seen = {name.casefold()}
    out: list[str] = []
    for candidate in candidates:
        value = candidate.strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


def _countries(path: Path) -> list[Row]:
    with zipfile.ZipFile(path) as archive:
        records = json.loads(archive.read("pycountry/databases/iso3166-1.json"))["3166-1"]
    if len(records) != COUNTRY_COUNT:
        sys.exit(f"ISO data has {len(records)} countries, expected {COUNTRY_COUNT}")
    rows = []
    for record in sorted(records, key=lambda r: r["alpha_2"]):
        code = record["alpha_2"]
        name = record.get("common_name") or record["name"]
        aliases = _unique(name, [record["name"], record.get("official_name", "")] + COUNTRY_ALIASES.get(code, []))
        article = GOVERNMENT_THE.get(code, "")
        rows.append(
            Row(
                id=f"place:{code.lower()}",
                kind="country",
                name=name,
                iso_alpha3=record["alpha_3"],
                governing_body=f"Government of {article}{name}",
                aliases=aliases,
            )
        )
    return rows


def _states(path: Path) -> tuple[list[Row], dict[str, str]]:
    rows: list[Row] = []
    kind_by_code: dict[str, str] = {}
    for record in _read_table(path, delimiter="|"):
        code = record["STATE"]
        if code in EXCLUDED_STATE_CODES:
            continue
        kind = "territory" if code in TERRITORY_CODES else "state"
        name = record["STATE_NAME"]
        rows.append(
            Row(
                id=f"place:us-{code.lower()}",
                kind=kind,
                name=name,
                parent="place:us",
                usps=code,
                census_geoid=record["STATEFP"],
                governing_body=TERRITORY_BODIES.get(code, f"State of {name}"),
                aliases=_unique(name, STATE_ALIASES.get(code, [])),
            )
        )
        kind_by_code[code] = kind
    return rows, kind_by_code


def _counties(path: Path, county_codes: set[str]) -> list[Row]:
    rows = []
    for record in _read_table(path, delimiter="|"):
        code = record["STATE"]
        if code not in county_codes:
            continue
        geoid = record["STATEFP"] + record["COUNTYFP"]
        rows.append(
            Row(
                id=f"place:us-{code.lower()}-{geoid}",
                kind="county",
                name=record["COUNTYNAME"],
                parent=f"place:us-{code.lower()}",
                usps=code,
                census_geoid=geoid,
            )
        )
    return rows


def build(cache: Path, version: str, built_on: str, offline: bool) -> None:
    files = {source.key: _fetch(source, cache, offline) for source in SOURCES}
    stats: dict[str, int] = defaultdict(int)

    countries = _countries(files["iso3166-1"])
    states, kind_by_code = _states(files["census-states-2020"])
    if sum(kind == "state" for kind in kind_by_code.values()) != 51:
        sys.exit("expected 50 states and DC")
    county_codes = {code for code, kind in kind_by_code.items() if kind == "state"} | {"PR"}
    counties = _counties(files["census-counties-2020"], county_codes)
    county_geoids = {row.census_geoid for row in counties}

    # Incorporated places: Census PEP lists incorporated places at summary level 162. Population decides who is in.
    population: dict[str, int] = {}
    for record in _read_table(files["census-popest-subcounty-2024"], delimiter=",", encoding="latin-1"):
        if record["SUMLEV"] == "162":
            population[record["STATE"] + record["PLACE"]] = int(record[POPULATION_COLUMN])
    big = {geoid for geoid, people in population.items() if people >= MIN_CITY_POPULATION}

    names: dict[str, tuple[str, str]] = {}
    for record in _read_table(files["census-gazetteer-places-2025"], delimiter="|"):
        names[record["GEOID"]] = (record["USPS"], record["NAME"])

    counties_of: dict[str, set[str]] = defaultdict(set)
    for record in _read_table(files["census-place-by-county-2020"], delimiter="|"):
        if record["TYPE"] == "INCORPORATED PLACE":
            counties_of[record["STATEFP"] + record["PLACEFP"]].add(record["STATEFP"] + record["COUNTYFP"])

    cities: list[Row] = []
    used_city_aliases: set[tuple[str, str]] = set()
    for geoid in sorted(big):
        if geoid not in names:
            stats["city_missing_from_gazetteer"] += 1
            continue
        usps, gazetteer_name = names[geoid]
        if gazetteer_name.endswith("(balance)"):
            # A consolidated city-government's own row, e.g. 'Indianapolis city (balance)' is Indianapolis itself,
            # less any town inside it that is a place of its own.
            stats["city_balance_name_cleaned"] += 1
            gazetteer_name = gazetteer_name[: -len("(balance)")].strip()
        name = _place_name(gazetteer_name)
        if usps not in kind_by_code:
            stats["city_state_unknown"] += 1
            continue
        found = counties_of.get(geoid, set())
        if len(found) == 1 and next(iter(found)) in county_geoids:
            parent = f"place:us-{usps.lower()}-{next(iter(found))}"
            stats["city_in_county"] += 1
        else:
            parent = f"place:us-{usps.lower()}"
            if len(found) > 1:
                stats["city_in_state_spans_counties"] += 1
            elif found:
                stats["city_in_state_county_not_in_pack"] += 1
            else:
                stats["city_in_state_no_county_match"] += 1
        aliases = list(CITY_ALIASES.get((usps, name), []))
        if (usps, name) in CITY_ALIASES:
            used_city_aliases.add((usps, name))
        if name.startswith("Saint "):
            aliases.append("St. " + name[len("Saint ") :])
        cities.append(
            Row(
                id=f"place:us-{usps.lower()}-{geoid}",
                kind="city",
                name=name,
                parent=parent,
                usps=usps,
                census_geoid=geoid,
                aliases=_unique(name, aliases),
            )
        )
    unused = set(CITY_ALIASES) - used_city_aliases
    if unused:
        sys.exit(f"curated city aliases match no city in the pack: {sorted(unused)}")

    rows = sorted(countries + states + counties + cities, key=lambda r: r.id)
    ids = [row.id for row in rows]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate place ids")
    known = set(ids)
    for row in rows:
        if row.parent and row.parent not in known:
            sys.exit(f"{row.id} has parent {row.parent}, which is not in the pack")
    by_kind = {kind: sum(row.kind == kind for row in rows) for kind in KINDS}
    if by_kind["county"] < 3100:
        sys.exit(f"only {by_kind['county']} counties; expected about 3,143 plus Puerto Rico")

    out_dir = OUT_ROOT / version
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "places.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv())

    manifest = {
        "pack": "places",
        "version": version,
        "built_on": built_on,
        "generator": "scripts/build_places_pack.py",
        "file": "places.csv",
        "file_sha256": _sha256(csv_path),
        "file_bytes": csv_path.stat().st_size,
        "rows": len(rows),
        "rows_by_kind": by_kind,
        "sources": [
            {
                "key": source.key,
                "title": source.title,
                "url": source.url,
                "license": source.license,
                "sha256": _sha256(files[source.key]),
                "bytes": files[source.key].stat().st_size,
            }
            for source in SOURCES
        ],
        "rules": [
            "Country id place:<ISO alpha-2 lowercase>. Name is the ISO common name where ISO has one, else the ISO "
            "English short name; the ISO short name and official name are aliases. iso_alpha3 holds the ISO alpha-3.",
            "Country governing_body is 'Government of <name>' (with 'the' for a few countries), derived from the name. "
            "It is a label, not a legal name.",
            "US state and DC id place:us-<USPS lowercase>. DC is a Census state-equivalent, so kind state.",
            "Territories (AS, GU, MP, PR, VI) are kind territory. U.S. Minor Outlying Islands (UM) has no row.",
            "County id place:us-<USPS>-<5-digit county GEOID> from the Census 2020 county list (50 states, DC, "
            "Puerto Rico municipios). Census 2020 lists Connecticut's eight counties; the 2022 planning regions are "
            "not in this pack. Territory counties are not in this pack.",
            "City (incorporated place) id place:us-<USPS>-<7-digit place GEOID>. Included when POPESTIMATE2024 is at "
            f"least {MIN_CITY_POPULATION:,}. Name from the Census 2025 Gazetteer, without its type word ('city', "
            "'town', 'village'...). A consolidated city-government's '(balance)' row is the city itself, so it keeps "
            "its name.",
            "City parent is its county when the Census 2020 place-by-county reference lists exactly one county for "
            "the place and that county is in the pack. Otherwise the parent is the state.",
            "Aliases: AP state abbreviations, DC and territory names, ISO alternate country names, a few city short "
            "names (NYC, LA, SF), and 'St.' for 'Saint' city names. Curated by hand, not from a source.",
            "find() matches names, aliases, USPS codes and ISO alpha-3 codes, ignoring case, accents and punctuation.",
        ],
        "counts": dict(sorted(stats.items())),
        "license": "U.S. Census Bureau data: public domain. ISO 3166-1 codes and names are facts; the packaged "
        "country data is read from pycountry (LGPL-2.1-only). The rules and curated aliases are part of this "
        "repository.",
    }
    (out_dir / "pack.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} rows to {csv_path}")
    print(json.dumps({"rows_by_kind": by_kind, "counts": manifest["counts"]}, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--version", default=DEFAULT_VERSION, help="pack version, e.g. 2026.11.1")
    parser.add_argument(
        "--built-on",
        default=datetime.now(timezone.utc).astimezone().date().isoformat(),
        help="build date recorded in pack.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "tico-places-pack",
        help="where downloaded sources are kept",
    )
    parser.add_argument("--offline", action="store_true", help="use the cache only; do not download")
    args = parser.parse_args(argv)
    build(args.cache, args.version, args.built_on, args.offline)


if __name__ == "__main__":
    main()
