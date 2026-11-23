"""Destination name -> a real place and its map area.

Countries come from a small table in the repo and need no network. Every other name is looked up once in Wikidata
(CC0), which also covers what town and boundary files miss: atolls, mountain ranges, coasts, lakes.
"""
import csv
import functools
import math
import re
from pathlib import Path

from app.services.open_data import (
    WIKIDATA, WIKIVOYAGE, box_around, box_centre, box_reach_km, distance_km, make_box, normalize, safe_text,
    valid_point, wikimedia,
)

_COUNTRIES = Path(__file__).resolve().parent.parent / "data" / "countries.tsv"

_LOCATION, _AREA, _GEONAMES, _POPULATION, _COUNTRY = "P625", "P2046", "P1566", "P1082", "P17"
_EXTREMES = ("P1332", "P1333", "P1334", "P1335")  # northernmost, southernmost, easternmost, westernmost point
# An organization's marks (industry, chief executive, headquarters): a shop or airline is never a destination.
_BUSINESS = ("P452", "P169", "P159")
_SQUARE_KM = {"Q712226": 1.0, "Q35852": 0.01, "Q25343": 1e-6, "Q232291": 2.58999, "Q81292": 0.00404686}
# GeoNames ids are on hotels, stations and schools too.
_NOT_A_DESTINATION = re.compile(
    r"\b(hotel|restaurant|railway station|metro station|bus station|school|university|hospital|company|shop|store|"
    r"shopping (mall|cent(re|er))|office building|football club|festival|painting|ship)\b", re.I)
# By the first words of Wikidata's description; the most specific kinds first.
_KINDS = (
    ("atoll", re.compile(r"\b(atoll|archipelago|island group)\b", re.I)),
    ("city", re.compile(r"\b(city|town|village|municipality|comune|commune|hamlet|capital|borough|settlement|"
                        r"parish)\b", re.I)),
    ("region", re.compile(r"\b(region|subregion|province|state|prefecture|mountains?|range|island|islands|coast|"
                          r"coastline|riviera|peninsula|valley|lake|national park|county|district|governorate|desert|"
                          r"division|territory|area)\b", re.I)),
)
_REACH_KM = {"city": 12.0, "place": 10.0, "region": 35.0, "atoll": 50.0}
_MAX_REACH_KM = 600.0


def _key(name: str) -> str:
    return normalize(name).removeprefix("the ")


@functools.lru_cache
def _countries() -> list[dict]:
    with _COUNTRIES.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader((line for line in file if not line.startswith("#")), delimiter="\t"))


@functools.lru_cache
def _by_name() -> dict[str, dict]:
    names: dict[str, dict] = {}
    for row in sorted(_countries(), key=lambda r: r["kind"] != "country"):
        for name in (row["name"], *row["aliases"].split(";")):
            if name:
                names.setdefault(_key(name), row)
    return names


def _country_area(row: dict) -> dict:
    viewport = make_box(*(float(row[side]) for side in ("south", "west", "north", "east")))
    return {"place_id": row["id"], "name": row["name"], "formatted_address": row["name"],
            "viewport": {**viewport, "id": row["id"]}, "photo_name": None, "kind": "country", "code": row["code"],
            "article": row["article"] or None, "centre": list(box_centre(viewport))}


def find_country(name: str, territories: bool = False) -> dict | None:
    """Offline: the country of exactly this name (or a usual other name) and its area, else None.
    "Panama City" and "Lisbon" are not countries. `territories` also accepts Puerto Rico, Greenland and the like."""
    row = _by_name().get(_key(name))
    return _country_area(row) if row and (territories or row["kind"] == "country") else None


def area_by_id(place_id: str) -> dict | None:
    row = next((r for r in _countries() if r["id"] == place_id), None)
    return _country_area(row) if row else None


def _values(entity: dict, prop: str) -> list:
    claims = entity.get("claims")
    found = claims.get(prop, []) if isinstance(claims, dict) else []  # an entity without claims sends a list
    found = [c for c in found if c.get("rank") != "deprecated"]
    found = [c for c in found if c.get("rank") == "preferred"] or found
    return [v for c in found if (v := ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")) is not None]


def _points(entity: dict, prop: str) -> list[tuple[float, float]]:
    on_earth = [v for v in _values(entity, prop) if isinstance(v, dict) and str(v.get("globe", "Q2")).endswith("Q2")]
    return [p for v in on_earth if (p := valid_point(v.get("latitude"), v.get("longitude")))]


def _square_km(entity: dict) -> float | None:
    for value in _values(entity, _AREA):
        factor = _SQUARE_KM.get(str(value.get("unit", "")).rsplit("/", 1)[-1])
        if factor:
            try:
                return abs(float(value["amount"])) * factor
            except (KeyError, ValueError):
                continue
    return None


def _description(entity: dict) -> str:
    return safe_text(((entity.get("descriptions") or {}).get("en") or {}).get("value"), 160)


def _label(entity: dict) -> str:
    return safe_text(((entity.get("labels") or {}).get("en") or {}).get("value"), 80)


def _article(entity: dict) -> str | None:
    title = ((entity.get("sitelinks") or {}).get("enwikivoyage") or {}).get("title")
    return title if isinstance(title, str) else None


def _is_destination(entity: dict) -> bool:
    if not _points(entity, _LOCATION):
        return False
    if _article(entity):
        return True  # it has a travel guide
    if any(_values(entity, p) for p in _BUSINESS) or _NOT_A_DESTINATION.search(_description(entity)):
        return False
    return bool(_values(entity, _GEONAMES) or _values(entity, _POPULATION))


def _area(entity: dict) -> dict:
    description = _description(entity)
    head = re.split(r",| in | of | on | at ", description, maxsplit=1)[0]
    kind = next((k for k, words in _KINDS if words.search(head)), "place")
    point = _points(entity, _LOCATION)[0]

    reach = _REACH_KM[kind]
    size = _square_km(entity)
    if size:
        # An area only gives a size, not a shape; real regions are longer than they are round.
        # A town's municipality can be huge, so there it counts for less.
        share, most = (0.5, 25.0) if kind in ("city", "place") else (0.7, _MAX_REACH_KM)
        reach = min(max(reach, math.sqrt(size) * share), most)
    viewport = box_around(point, reach)
    edges = [_points(entity, p) for p in _EXTREMES]
    if all(edges):
        north, south = max(p[0] for p in edges[0]), min(p[0] for p in edges[1])
        east, west = max(p[1] for p in edges[2]), min(p[1] for p in edges[3])
        inside = south <= point[0] <= north and west <= point[1] <= east
        if inside and distance_km((south, west), (north, east)) > max(_REACH_KM["city"], reach / 2):
            viewport = make_box(south, west, north, east)

    owners = {v.get("id") for v in _values(entity, _COUNTRY) if isinstance(v, dict)}
    country = next((r for r in _countries() if r["id"] in owners), None)
    name = _label(entity)
    return {"place_id": entity["id"], "name": name,
            "formatted_address": ", ".join(filter(None, [name, country and country["name"]])),
            "viewport": {**viewport, "id": entity["id"]}, "photo_name": None, "kind": kind,
            "code": country and country["code"], "article": _article(entity), "centre": list(point)}


def _entities(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    found = wikimedia(WIKIDATA, action="wbgetentities", ids="|".join(ids), languages="en",
                      props="claims|sitelinks|labels|descriptions|aliases").get("entities", {})
    return [found[i] for i in ids if i in found and "missing" not in found[i] and _label(found[i])]


def find_destination(name: str, near: dict | None = None) -> dict | None:
    """The best-known destination of this name. "Paris, Texas" narrows it, and so does `near` (a saved trip's box).
    Never a business: only places with a travel guide, or geographic features and settlements."""
    name = safe_text(name, 80)
    if not _key(name):
        return None
    centre = box_centre(near) if near else None
    if country := find_country(name, territories=True):
        # A trip saved to Georgia, the US state, must not jump to the country of that name.
        if not centre or distance_km(centre, country["centre"]) <= 2 * box_reach_km(country["viewport"]):
            return country

    head, _, qualifier = name.partition(",")
    search = re.sub(r"^the\s+", "", head.strip(), flags=re.I)
    within = find_country(qualifier.split(",")[-1], territories=True) if qualifier.strip() else None
    hits = wikimedia(WIKIDATA, action="wbsearchentities", search=search, language="en", uselang="en",
                     type="item", limit=15).get("search", [])
    wanted = _key(search)
    exact = [h["id"] for h in hits if wanted in (_key(h.get("label") or ""),
                                                 _key((h.get("match") or {}).get("text") or ""))]
    found = [e for e in _entities(exact) if _is_destination(e)]
    if not found:
        # A guide under another title or a redirect, else a longer name starting with what was typed.
        pages = wikimedia(WIKIVOYAGE, action="query", prop="pageprops", titles=head.strip(), redirects=1)
        props = [p.get("pageprops") or {} for p in (pages.get("query") or {}).get("pages", [])]
        ids = [p["wikibase_item"] for p in props if "wikibase_item" in p and "disambiguation" not in p]
        ids += [h["id"] for h in hits if h["id"] not in exact and wanted in _key(h.get("label") or "")]
        found = [e for e in _entities(ids[:5]) if _article(e) and _is_destination(e)]
    if not found:
        return None

    def score(entity: dict) -> tuple:
        here = 0.0
        if centre:
            here = -round(distance_km(centre, _points(entity, _LOCATION)[0]) / 100)
        hinted = False
        if within:
            owners = {v.get("id") for v in _values(entity, _COUNTRY) if isinstance(v, dict)}
            hinted = within["place_id"] in owners
        elif qualifier.strip():
            hinted = _key(qualifier) in normalize(f"{_description(entity)} {_article(entity) or ''}")
        # More Wikipedias write about the better-known place: Paris in France before Paris in Texas.
        return hinted, here, bool(_article(entity)), len(entity.get("sitelinks") or {})

    best = max(found, key=score)
    return area_by_id(best["id"]) or _area(best)
