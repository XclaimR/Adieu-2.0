"""Places from Overture Maps (CDLA-Permissive-2.0, Apache-2.0, CC0), read from its public files by map area.

Only the rows inside the destination are read: a few MB from files that total 12 GB.
"""
import logging
import math
import re
import tempfile
import threading

import duckdb
import httpx

from app.services.open_data import (
    KM_PER_DEGREE, OpenDataError, USER_AGENT, box_around, corners, safe_text, valid_point,
)

logger = logging.getLogger(__name__)

_CATALOG = "https://stac.overturemaps.org"
_RELEASE = re.compile(r"\d{4}-\d\d-\d\d\.\d+")
_FILE = re.compile(r"s3://overturemaps-us-west-2/release/[\w.-]+/theme=places/type=place/[\w.-]+\.parquet")

QUERY_SECONDS = 45
# An area whose file blocks hold more than this is read town by town: Tokyo alone has 230,000 places.
WHOLE_AREA_ROWS = 180_000
MAX_TOWNS = 10
KEEP = {"do": 500, "eat": 350, "stay": 200}

_SIGHTS = {
    "historic_site", "monument", "castle", "palace", "fort", "lighthouse", "pilgrimage_site", "museum",
    "art_museum", "history_museum", "community_museum", "beach", "waterfall", "lake", "mountain", "cave",
    "hot_springs", "nature_reserve", "botanical_garden", "island", "national_park", "hiking_trail", "zoo",
    "aquarium", "wildlife_sanctuary", "rock_climbing_spot", "scuba_diving_center", "diving_center", "boat_tour",
}
_OTHER_DO = {
    "park", "state_park", "garden", "forest", "river", "marina", "pier", "surfing", "ski_resort", "ski_area",
    "fishing_charter", "horseback_riding_service", "water_park", "amusement_park", "art_gallery", "theatre_venue",
    "performing_arts_venue", "music_venue", "dance_club", "nightlife_venue", "comedy_club", "karaoke_venue",
    "cultural_center", "farmers_market", "public_market", "flea_market", "night_market", "spa", "day_spa",
    "health_spa", "historical_tour", "food_tour", "walking_tour", "bike_tour", "sightseeing_tour_agency",
    "snorkeling", "kayaking", "bike_rental", "boat_rental_and_training", "buddhist_place_of_worship",
    "hindu_place_of_worship", "muslim_place_of_worship", "roman_catholic_place_of_worship",
    "christian_place_of_worship", "shinto_place_of_worship", "jewish_place_of_worship",
}
_NOT_A_STAY = {"rv_park", "ski_resort", "holiday_rental_agency"}
_NOT_A_MEAL = {
    "fast_food_restaurant", "internet_cafe", "airport_lounge", "food_truck_park", "caterer", "food_delivery_service",
    "convenience_store", "grocery_store", "supermarket", "butcher_shop", "liquor_store",
}


def latest_release() -> dict:
    """The newest release and each of its files' boxes. Overture keeps only the last few releases."""
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as client:
            catalog = client.get(f"{_CATALOG}/catalog.json")
            catalog.raise_for_status()
            release = catalog.json().get("latest") or ""
            if not _RELEASE.fullmatch(release):
                raise OpenDataError(f"Overture named an odd release: {release[:40]!r}")
            index = client.get(f"{_CATALOG}/{release}/collections.parquet")
            index.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".parquet") as file, duckdb.connect() as db:
            file.write(index.content)
            file.flush()
            rows = db.execute("select assets.aws.alternate.s3.href, bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax "
                              "from read_parquet(?) where collection = 'place'", [file.name]).fetchall()
    except (httpx.HTTPError, ValueError, duckdb.Error) as e:
        raise OpenDataError(f"Overture catalog: {e!r}") from e
    files = [{"path": path, "box": box} for path, *box in rows if _FILE.fullmatch(path or "")]
    if not files:
        raise OpenDataError("Overture listed no place files")
    return {"release": release, "files": files}


def _unwrap(box: dict) -> list[tuple[float, float, float, float]]:
    """west, south, east, north; two boxes where the area crosses the 180th meridian."""
    south, west, north, east = corners(box)
    if west <= east:
        return [(west, south, east, north)]
    return [(west, south, 180.0, north), (-180.0, south, east, north)]


def _files_in(files: list[dict], box: tuple[float, float, float, float]) -> list[str]:
    west, south, east, north = box
    return [f["path"] for f in files if f["box"][0] <= east and f["box"][2] >= west
            and f["box"][1] <= north and f["box"][3] >= south]


def _rows_to_read(db: duckdb.DuckDBPyConnection, files: list[dict], boxes: list[tuple]) -> int:
    """An upper bound from the files' own statistics, before reading any place."""
    total = 0
    for west, south, east, north in boxes:
        if paths := _files_in(files, (west, south, east, north)):
            total += db.execute("""
                select coalesce(sum(size), 0) from (
                  select max(row_group_num_rows) as size from parquet_metadata(?)
                  where path_in_schema in ('bbox, xmin', 'bbox, ymin')
                  group by file_name, row_group_id
                  having min(case when path_in_schema = 'bbox, xmin' then stats_min_value::double end) <= ?
                     and max(case when path_in_schema = 'bbox, xmin' then stats_max_value::double end) >= ?
                     and min(case when path_in_schema = 'bbox, ymin' then stats_min_value::double end) <= ?
                     and max(case when path_in_schema = 'bbox, ymin' then stats_max_value::double end) >= ?)""",
                [paths, east, west, north, south]).fetchone()[0]
    return total


def _read(db: duckdb.DuckDBPyConnection, files: list[dict], boxes: list[tuple], centres: list[tuple],
          country: str | None) -> list[tuple]:
    """The likeliest sights, meals and stays, shared out over the towns. No popularity data exists to rank by:
    this goes by how sure Overture is that the place exists, its type, and how near a town it is."""
    scans, args = [], []
    for box in boxes:
        if paths := _files_in(files, box):
            scans.append("""select id, names."primary" as local_name, names.common['en'] as english, bbox.xmin as lng,
                              bbox.ymin as lat, taxonomy."primary" as kind, taxonomy.hierarchy[1] as family,
                              confidence, brand.names."primary" as brand, addresses[1].locality as locality,
                              addresses[1].country as country, operating_status as status
                            from read_parquet(?) where bbox.xmin between ? and ? and bbox.ymin between ? and ?""")
            args += [paths, box[0], box[2], box[1], box[3]]
    if not scans:
        return []
    squash = math.cos(math.radians(centres[0][0]))  # a degree of longitude shrinks toward the poles
    towns = [f"[{float(lat)}, {float(lng)}]" for lat, lng in centres]
    args = [*args, sorted(_NOT_A_STAY), sorted(_NOT_A_MEAL), sorted(_SIGHTS | _OTHER_DO), sorted(_SIGHTS),
            country, country]
    return db.execute(f"""
        with raw as ({" union all ".join(scans)}),
        sorted_out as (
          select *, case
              when family = 'lodging' and not list_contains(?, kind) then 'stay'
              when family = 'food_and_drink' and not list_contains(?, kind) then 'eat'
              when list_contains(?, kind) then 'do' end as category,
            list_contains(?, kind) as sight,
            list_transform([{", ".join(towns)}],
              t -> sqrt(pow(lat - t[1], 2) + pow((lng - t[2]) * {squash}, 2)) * {KM_PER_DEGREE}) as to_towns
          from raw
          where confidence >= 0.5 and coalesce(status, 'open') = 'open'
            and (?::varchar is null or country is null or country = ?)),
        scored as (
          select *, list_min(to_towns) as km, list_position(to_towns, list_min(to_towns)) as town,
            confidence
              + case when category = 'do' and sight then 0.4 else 0 end
              - case when category = 'eat' and brand is not null then 0.5 else 0 end
              - least(0.4, list_min(to_towns) / 25) as score
          from sorted_out where category is not null)
        select id, local_name, english, lat, lng, kind, category, score, locality from scored
        qualify row_number() over (partition by category, town order by score desc, id)
          <= ceil(case category when 'do' then {KEEP["do"]} when 'eat' then {KEEP["eat"]} else {KEEP["stay"]} end
                  / {len(centres)}.0)
        order by score desc, id""", args).fetchall()


def load(files: list[dict], area: dict, hubs: list[dict]) -> list[dict]:
    """Places in the whole area when it's small or sparse (an atoll, Iceland), else around its main towns."""
    towns = [tuple(h["point"]) for h in hubs if not h["district"]][:MAX_TOWNS]
    centre = tuple(area["centre"])
    small = area["kind"] in ("city", "place")
    centres = [centre] if small or not towns else towns
    try:
        with duckdb.connect() as db:
            db.execute("LOAD httpfs; SET s3_region = 'us-west-2'; SET enable_progress_bar = false; "
                       "SET memory_limit = '700MB'; SET threads = 12; SET http_timeout = 20; SET http_retries = 1; "
                       "SET enable_http_metadata_cache = true")
            deadline = threading.Timer(QUERY_SECONDS, db.interrupt)
            deadline.start()
            try:
                boxes = _unwrap(area["viewport"])
                if _rows_to_read(db, files, boxes) > WHOLE_AREA_ROWS:
                    if small:
                        spots = [centre, *[t for t in towns if _apart(centre, t)][:2]]  # Sintra with Lisbon
                        centres, km = spots, 4.0
                    elif towns:
                        km = 2.5 if len(towns) > 4 else 5.0
                    else:
                        km = 15.0
                    boxes = [part for c in centres for part in _unwrap(box_around(c, km))]
                rows = _read(db, files, boxes, centres, area.get("code") if area["kind"] == "country" else None)
            finally:
                deadline.cancel()
    except duckdb.Error as e:
        raise OpenDataError(f"Overture places: {str(e)[:300]}") from e

    places = []
    for place_id, local, english, lat, lng, kind, category, score, locality in rows:
        name, point = safe_text(english or local, 80), valid_point(lat, lng)
        if len(name) < 2 or not point or not re.fullmatch(r"[0-9a-f-]{32,36}", place_id or ""):
            continue
        places.append({"id": f"ov-{place_id}", "name": name, "alt": safe_text(local, 80) if english else "",
                       "category": category, "kind": safe_text((kind or "").replace("_", " "), 40).capitalize(),
                       "text": None, "lat": round(point[0], 5), "lng": round(point[1], 5),
                       "area": safe_text(locality, 50) or None, "guide": None, "tier": None, "image": None,
                       "score": round(score, 3)})
    return places


def _apart(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Far enough to need its own box."""
    return math.dist(a, b) * KM_PER_DEGREE > 8
