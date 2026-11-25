"""Builds one destination's library: Wikivoyage's described places, topped up and located with Overture's."""
import hashlib
import logging
import math
import time
from collections.abc import Callable

from app.services import overture, wikivoyage
from app.services.open_data import OpenDataError, distance_km, normalize, valid_point

logger = logging.getLogger(__name__)

KEEP = {"do": 450, "eat": 300, "stay": 170}
# Bump when a library gains something old ones lack, so they are rebuilt (2: fame and tips, Ticket #67).
VERSION = 2
ENOUGH_FROM_ONE_SOURCE = 8
SAME_PLACE_KM = 1.0
SAME_TOWN_KM = 12
SAME_DISTRICT_KM = 3


def _point(place: dict) -> tuple[float, float] | None:
    return valid_point(place.get("lat"), place.get("lng"))


def merge(described: list[dict], mapped: list[dict], hubs: list[dict], destination: str) -> list[dict]:
    """One list without repeats. A described place takes its position and exact type from the map data."""
    towns = {h["name"]: tuple(h["point"]) for h in hubs}
    by_name: dict[str, list[dict]] = {}
    for place in mapped:
        by_name.setdefault(normalize(place["name"]), []).append(place)
        if place["alt"]:
            by_name.setdefault(normalize(place["alt"]), []).append(place)

    used: set[str] = set()
    merged: list[dict] = []
    seen: dict[str, list[dict]] = {}

    def add(place: dict) -> None:
        key, point = normalize(place["name"]), _point(place)
        for other in seen.get(key, []):
            there = _point(other)
            if (not point or not there) and place["area"] == other["area"]:
                return
            if point and there and distance_km(point, there) < SAME_PLACE_KM:
                return
        seen.setdefault(key, []).append(place)
        merged.append(place)

    # A town or the destination itself isn't a stop ("Amed" as a thing to do in Amed), from either source.
    not_places = {normalize(destination), *map(normalize, towns)}
    for item in described:
        if normalize(item["name"]) in not_places:
            continue
        point = _point(item)
        around, limit = (point, SAME_PLACE_KM) if point else (towns.get(item["area"]), SAME_TOWN_KM)
        matches = by_name.get(normalize(item["name"]), []) + by_name.get(normalize(item["alt"]), [])
        if around:
            matches = sorted((m for m in matches if distance_km(around, _point(m)) <= limit),
                             key=lambda m: distance_km(around, _point(m)))
        elif len(matches) != 1:
            matches = []  # several branches and nothing to say which one
        place = {**item, "score": 2.0 + bool(item["text"])}
        if matches:
            match = matches[0]
            used.add(match["id"])
            place["score"] += 0.5  # two sources agree it exists
            if not point:
                place |= {"lat": match["lat"], "lng": match["lng"]}
            if match["category"] == item["category"] and match["kind"]:
                place["kind"] = match["kind"]
        add(place)

    for place in mapped:
        if place["id"] not in used and normalize(place["name"]) not in not_places:
            add(place)

    for place in merged:
        point = _point(place)
        place.setdefault("town", None)
        if not point or not hubs:
            continue
        towns_near = [(distance_km(point, tuple(h["point"])), h["name"]) for h in hubs if not h["district"]]
        if not place["town"] and towns_near and min(towns_near)[0] <= SAME_TOWN_KM:
            place["town"] = min(towns_near)[1]  # which main town it belongs to, for sharing out a country
        if place["guide"] and normalize(place["area"] or "") != normalize(destination):
            continue  # its guide already says where it is
        near = [(distance_km(point, tuple(h["point"])), h) for h in hubs]
        near = [(km, h) for km, h in near if km <= (SAME_DISTRICT_KM if h["district"] else SAME_TOWN_KM)]
        if near:
            place["area"] = min(near, key=lambda n: (not n[1]["district"], n[0]))[1]["name"]
    return merged


def _cap(places: list[dict]) -> list[dict]:
    kept = []
    for category, limit in KEEP.items():
        ranked = sorted((p for p in places if p["category"] == category), key=lambda p: -p["score"])
        kept += ranked[:limit]
    return kept


def build(area: dict, releases: Callable[[], dict]) -> dict:
    """Everything stored for a destination. One source being down still gives a (short-lived) library;
    raises OpenDataError when there is too little to plan from."""
    started = time.monotonic()
    failed: list[str] = []
    guide: dict = {"items": [], "hubs": []}
    try:
        guide = wikivoyage.load(area)
    except OpenDataError as e:
        logger.warning("Wikivoyage failed for %s: %s", area["name"], e)
        failed.append("wikivoyage")
    mapped: list[dict] = []
    release = None
    try:
        latest = releases()
        release = latest["release"]
        mapped = overture.load(latest["files"], area, guide["hubs"])
    except OpenDataError as e:
        logger.warning("Overture failed for %s: %s", area["name"], e)
        failed.append("overture")

    places = merge(guide["items"], mapped, guide["hubs"], area["name"])
    try:
        known = wikivoyage.fame([p["wikidata"] for p in places if p.get("wikidata")])
    except OpenDataError as e:  # the plan still works, it just can't tell the icons apart
        logger.warning("Wikidata fame failed for %s: %s", area["name"], e)
        known = {}
    for place in places:
        place["fame"] = known.get(place.pop("wikidata", None) or "", 0)
        place["score"] += 0.5 * math.log2(1 + place["fame"])  # 39 Wikipedias (Fushimi Inari) add 2.7, 8 add 1.6
    places = _cap(places)
    if failed and len(places) < ENOUGH_FROM_ONE_SOURCE:
        raise OpenDataError(f"Couldn't load places for {area['name']}: {', '.join(failed)} unavailable")

    if len(places) < ENOUGH_FROM_ONE_SOURCE:
        failed.append("few places")  # kept only briefly, in case a source was having a bad day
    photos = {}
    if files := [p["image"] for p in places if p.get("image")]:
        try:
            found = wikivoyage.open_photos(files)
        except OpenDataError as e:  # nice to have
            logger.warning("Photos failed for %s: %s", area["name"], e)
            found = {}
        for place in places:
            name = (place.get("image") or "").replace("_", " ")
            photo = found.get(place.get("image") or "") or found.get(name[:1].upper() + name[1:])
            if photo:
                place["photo"] = "commons/" + hashlib.sha1(photo["source"].encode()).hexdigest()[:16]
                photos[place["photo"]] = photo
    for place in places:
        place.pop("image", None)
        place.pop("alt", None)
    logger.info("Loaded %d places for %s in %.1fs", len(places), area["name"], time.monotonic() - started)
    return {"places": places, "hubs": guide["hubs"], "photos": photos, "release": release, "partial": failed,
            "version": VERSION}
