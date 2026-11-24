"""Shared plumbing for the open sources: polite HTTP, boxes on the map, and cleaning downloaded text."""
import html
import logging
import math
import re
import threading
import time
import unicodedata

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "Adieu/0.1 (+https://github.com/XclaimR/Adieu-2.0)"
WIKIVOYAGE = "en.wikivoyage.org"
WIKIDATA = "www.wikidata.org"
COMMONS = "commons.wikimedia.org"
KM_PER_DEGREE = 111.2

# Wikimedia asks API users to go gently: one request a second, whichever of their sites it is.
# shortcut: the gap is per process; share it through Postgres if the API ever runs several workers.
_WIKIMEDIA_GAP = 1.0
_wikimedia_lock = threading.Lock()
_last_request = 0.0


class OpenDataError(RuntimeError):
    """A source was down, refused us or answered with something unusable."""


def wikimedia(host: str, **params) -> dict:
    """One API call, at most one a second. One retry when the site is busy."""
    global _last_request
    params = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
    for attempt in range(2):
        with _wikimedia_lock:
            time.sleep(max(0.0, _last_request + _WIKIMEDIA_GAP - time.monotonic()))
            try:
                response = httpx.get(f"https://{host}/w/api.php", params=params,
                                     headers={"User-Agent": USER_AGENT}, timeout=20)
            except httpx.HTTPError as e:
                raise OpenDataError(f"{host}: {e!r}") from e
            finally:
                _last_request = time.monotonic()
        busy = response.status_code in (429, 503)
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as e:
                raise OpenDataError(f"{host}: answer was not JSON") from e
            error = data.get("error")
            if not error:
                return data
            busy = error.get("code") == "maxlag"
            if not busy:
                raise OpenDataError(f"{host}: {str(error)[:200]}")
        if busy and not attempt:
            wait = response.headers.get("Retry-After", "")
            time.sleep(min(float(wait), 10) if wait.isdigit() else 3)
            continue
        raise OpenDataError(f"{host} answered {response.status_code}")


# Format characters can hide text from us but not from the model (bidi overrides, Unicode tags).
_HIDDEN = ("Cc", "Cf", "Co", "Cs", "Zl", "Zp")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_SAFE = str.maketrans({"[": "(", "]": ")", "{": "(", "}": ")", "<": " ", ">": " ", "`": "'", "|": "/", "·": ","})


def safe_text(value: object, limit: int) -> str:
    """Downloaded text as one short plain line. It can't fake a [n] reference, a new prompt line or a link."""
    if not isinstance(value, str):
        return ""
    text = html.unescape(value[:20_000])
    text = "".join(" " if unicodedata.category(ch) in _HIDDEN else ch for ch in text)
    text = " ".join(_URL.sub("", text).translate(_SAFE).split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def normalize(text: str) -> str:
    """Lowercase words with accents dropped, in any script: "Malé" == "male"."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[\W_]+", " ", plain).split())


def valid_point(lat: object, lng: object) -> tuple[float, float] | None:
    try:
        point = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    return point if abs(point[0]) <= 90 and abs(point[1]) <= 180 else None


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = b[0] - a[0]
    dx = ((b[1] - a[1] + 180) % 360 - 180) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy) * KM_PER_DEGREE


# A box is the trip's viewport: {"low": {latitude, longitude}, "high": {...}}. West > east crosses the 180th meridian.
def make_box(south: float, west: float, north: float, east: float) -> dict:
    return {"low": {"latitude": south, "longitude": west}, "high": {"latitude": north, "longitude": east}}


def corners(box: dict) -> tuple[float, float, float, float]:
    """south, west, north, east. Boxes saved from Google leave out zeros."""
    low, high = box["low"], box["high"]
    return low.get("latitude", 0), low.get("longitude", 0), high.get("latitude", 0), high.get("longitude", 0)


def box_around(point: tuple[float, float], km: float) -> dict:
    dy = km / KM_PER_DEGREE
    dx = dy / max(0.05, math.cos(math.radians(point[0])))
    return make_box(max(-90.0, point[0] - dy), (point[1] - dx + 180) % 360 - 180,
                    min(90.0, point[0] + dy), (point[1] + dx + 180) % 360 - 180)


def box_centre(box: dict) -> tuple[float, float]:
    south, west, north, east = corners(box)
    return (south + north) / 2, (west + (east - west) % 360 / 2 + 180) % 360 - 180


def box_reach_km(box: dict) -> float:
    """From the centre to a corner."""
    south, west, _, _ = corners(box)
    return distance_km(box_centre(box), (south, west))

