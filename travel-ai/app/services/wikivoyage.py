"""Places with descriptions from Wikivoyage guides (CC BY-SA 4.0), and openly licensed photos from Commons.

Guide text is untrusted: anyone can edit it. It is cut to short plain lines before it is stored.
"""
import hashlib
import logging
import re
from urllib.parse import quote, unquote, urlsplit

from app.services.open_data import (
    COMMONS, WIKIVOYAGE, box_reach_km, distance_km, normalize, safe_text, valid_point, wikimedia,
)

logger = logging.getLogger(__name__)

NAME_LIMIT = 80
TEXT_LIMIT = 220
MAX_GUIDES = 24
MAX_DISTRICTS = 20
PAGES_PER_CALL = 10  # big guides come back in parts; fewer per call keeps each batch within a few parts
MAX_PHOTOS = 50
# Nearby guides count as "in reach" of a town: Sintra is 22 km from Lisbon.
NEARBY_KM = 30

_CATEGORY = {"see": "do", "do": "do", "eat": "eat", "drink": "eat", "sleep": "stay"}
_KIND = {"see": "Sight", "do": "Activity", "eat": "Place to eat", "drink": "Bar or cafe", "sleep": "Place to stay"}
_TIER_HEADINGS = (("budget", "budget"), ("mid", "mid"), ("splurge", "luxury"))
_HUB_HEADING = re.compile(r"\b(cities|towns|islands|villages|destinations)\b", re.I)
_HEADING = re.compile(r"^(={2,5})[ \t]*([^=\n]+?)[ \t]*\1[ \t]*$", re.M)
_TEMPLATE = re.compile(r"\{\{\s*(see|do|eat|drink|sleep|listing|marker)\s*(?=\|)", re.I)
_BOLD_ITEM = re.compile(r"^\*+[ \t]*'''([^'\n]{3,120})'''[ \t]*([^\n]{0,1500})$", re.M)
_LINK = re.compile(r"\[\[([^\[\]|#\n]{1,120})(?:#[^\[\]|\n]*)?(?:\|([^\[\]\n]{1,160}))?\]\]")
# The guide can be years old, so sentences with a price or an hour are not passed on as facts.
_STALE = re.compile(r"[€$£¥₹₩฿]\s?\d|\d\s?(€|USD|EUR|GBP|INR|JPY|IDR|JOD|ISK|MVR)\b|\b(USD|EUR|GBP|INR|JPY|IDR|"
                    r"JOD|ISK|MVR|Rs\.?)\s?\d|\b\d{1,2}:\d\d\b|\b\d{1,2}\s?(am|pm)\b", re.I)
_CLOSED = re.compile(r"\b(permanently closed|closed (down|permanently)|no longer (exists|operat\w+)|out of business)\b",
                     re.I)
# Guides that aren't places to stay around, and listings that are dated events, not places.
_NOT_A_TOWN = re.compile(r"\b(airport|station|port|terminal|itinerar(y|ies)|phrasebook)\b", re.I)
_EVENT = re.compile(r"\b(festival|matsuri|carnival|parade|fireworks|marathon|fair|feast)\b|^\(?\s*(\w+\s)?"
                    r"(january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I)
_OPEN_LICENCE = re.compile(r"^(cc0|cc[ -]by(-sa)?([ -]\d|$)|public domain|pdm|pd\b)", re.I)


def article_url(title: str) -> str:
    return f"https://{WIKIVOYAGE}/wiki/{quote(title.replace(' ', '_'), safe='/(),')}"


def _plain(text: str) -> str:
    text = re.sub(r"<!--.*?-->|<ref[^>/]*/>|<ref[^>]*>.*?</ref>", " ", text, flags=re.S)
    text = re.sub(r"\{\{\s*(km|m|miles?|ft|kg)\s*\|\s*([\d.,]+)[^{}]*\}\}", r"\2 \1", text, flags=re.I)
    for _ in range(3):  # templates and files nest
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
        text = re.sub(r"\[\[(?:file|image):[^\[\]]*\]\]", "", text, flags=re.I)
        text = _LINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\[(?:https?:)?//[^\s\]]+\s*([^\]]*)\]", r"\1", text)
    return re.sub(r"'{2,}|<[^>]{0,200}>", "", text)


def _description(text: str) -> str | None:
    """The first sentences that fit, without prices and hours, or None."""
    sentences = re.split(r"(?<=[.!?])\s+", safe_text(_plain(text[:5000]), 1200).lstrip("-–—:;,. "))
    kept = " ".join(s for s in sentences if not _STALE.search(s))
    return safe_text(kept, TEXT_LIMIT) if len(kept) >= 8 else None


def _templates(text: str):
    """(name, named fields, the rest of its line) for every listing or marker template."""
    for match in _TEMPLATE.finditer(text):
        depth, links, i = 1, 0, match.end()
        parts, start = [], i + 1
        stop = min(len(text), i + 8000)  # a template left open can't swallow the article
        i = start
        while i < stop and depth:
            pair = text[i:i + 2]
            if pair in ("{{", "}}", "[[", "]]"):
                depth += (pair == "{{") - (pair == "}}")
                links += (pair == "[[") - (pair == "]]")
                i += 2
                continue
            if text[i] == "|" and depth == 1 and links <= 0:
                parts.append(text[start:i])
                start = i + 1
            i += 1
        if depth:
            continue
        parts.append(text[start:i - 2])
        fields = {k.strip().lower(): v.strip() for k, sep, v in (p.partition("=") for p in parts) if sep}
        end_of_line = text.find("\n", i)
        yield match.group(1).lower(), fields, text[i:end_of_line if end_of_line >= 0 else len(text)]


def _sections(text: str):
    """(the == heading ==, the nearest heading, text) for each piece of an article."""
    top, sub, start = "", "", 0
    for match in _HEADING.finditer(text):
        yield top, sub, text[start:match.start()]
        sub = _plain(match.group(2))
        if len(match.group(1)) == 2:
            top = sub
        start = match.end()
    yield top, sub, text[start:]


def _item(title: str, category: str, kind: str, heading: str, name: str, text: str,
          fields: dict | None = None) -> dict | None:
    fields = fields or {}
    name = safe_text(_plain(name), NAME_LIMIT)
    if len(name) < 2 or not normalize(name) or _CLOSED.search(text):
        return None
    if category == "do" and (_EVENT.search(name) or _EVENT.match(_plain(text).strip())):
        return None
    tier = None
    if category == "stay":
        tier = next((t for word, t in _TIER_HEADINGS if word in heading.lower()), None)
    point = valid_point(fields.get("lat"), fields.get("long"))
    image = re.sub(r"^(file|image):", "", fields.get("image", ""), flags=re.I).strip()
    return {"id": "wv-" + hashlib.sha1(f"{title}:{category}:{name}".encode()).hexdigest()[:16],
            "name": name, "alt": safe_text(_plain(fields.get("alt", "")), NAME_LIMIT), "category": category,
            "kind": kind, "text": _description(text), "lat": point and point[0], "lng": point and point[1],
            "area": title.rsplit("/", 1)[-1], "town": title.split("/")[0], "guide": title, "tier": tier,
            "image": image if image and len(image) < 200 and not re.search(r"[|\[\]{}<>\n]", image) else None}


def parse(title: str, text: str) -> dict:
    """An article's places, the towns it points to, and its district pages."""
    text = text[:600_000]
    items, hubs = [], {}
    for top, heading, body in _sections(text):
        default = top.lower() if top.lower() in _CATEGORY else None
        templates = list(_templates(body))
        for template, fields, rest in templates:
            section = template if template in _CATEGORY else fields.get("type", "").lower() or default
            if section in _CATEGORY:
                item = _item(title, _CATEGORY[section], _KIND[section], heading, fields.get("name", ""),
                             fields.get("content") or fields.get("description") or rest, fields)
                if item:
                    items.append(item)
        if default:  # small guides list places as bold bullets, without templates
            for match in _BOLD_ITEM.finditer(body):
                if item := _item(title, _CATEGORY[default], _KIND[default], heading, *match.groups()):
                    items.append(item)
        if _HUB_HEADING.search(f"{top} {heading}"):
            worth_a_visit = bool(re.search(r"destinations|islands|villages", f"{top} {heading}", re.I))
            for template, fields, rest in templates:
                if template != "marker" or fields.get("type", "").lower() in _CATEGORY:
                    continue
                link = _LINK.search(fields.get("name", ""))
                name = safe_text(_plain(fields.get("name", "")), NAME_LIMIT)
                if name:
                    hubs.setdefault(name, {"name": name, "link": link and link.group(1).strip(), "visit": worth_a_visit,
                                           "text": rest, "point": valid_point(fields.get("lat"), fields.get("long"))})
    districts = [t for t in dict.fromkeys(m.group(1).strip() for m in _LINK.finditer(text))
                 if t.startswith(f"{title}/")]
    return {"title": title, "size": len(text), "items": items, "hubs": list(hubs.values()), "districts": districts}


def _fetch(titles: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    """Parsed articles by their real title, and which title each redirect leads to."""
    titles = [t for t in dict.fromkeys(titles) if t and "|" not in t]
    raw: dict[str, dict] = {}
    moved: dict[str, str] = {}
    for start in range(0, len(titles), PAGES_PER_CALL):
        params = {"action": "query", "titles": "|".join(titles[start:start + PAGES_PER_CALL]), "redirects": 1,
                  "prop": "revisions|coordinates", "rvprop": "content", "rvslots": "main", "colimit": "max"}
        more: dict = {}
        for _ in range(4):  # long answers come in parts
            data = wikimedia(WIKIVOYAGE, **params, **more)
            query = data.get("query") or {}
            moved |= {x["from"]: x["to"] for x in query.get("normalized", []) + query.get("redirects", [])}
            for page in query.get("pages", []):
                if "title" in page:
                    entry = raw.setdefault(page["title"], {})
                    entry |= {k: page[k] for k in ("revisions", "coordinates") if page.get(k)}
            more = data.get("continue") or {}
            if not more:
                break
        else:
            logger.warning("Wikivoyage answer still incomplete for %s", titles[start])
    pages = {}
    for title, entry in raw.items():
        if "revisions" not in entry:
            continue
        slots = entry["revisions"][0].get("slots") or {}
        content = (slots.get("main") or {}).get("content") or ""
        page = parse(title, content)
        at = next(iter(entry.get("coordinates") or []), {})
        pages[title] = page | {"point": valid_point(at.get("lat"), at.get("lon")),
                               "words": f" {normalize(_plain(content[:300_000]))} "}
    return pages, moved


def _near(point: tuple[float, float], km: float, mentioning: str = "") -> list[str]:
    """Guides around a point, the better-known first; only those using the words `mentioning`, if given."""
    words = " ".join(re.findall(r"\w+", mentioning))
    near = f"nearcoord:{round(km)}km,{point[0]:.4f},{point[1]:.4f}"
    data = wikimedia(WIKIVOYAGE, action="query", list="search", srlimit=MAX_GUIDES,
                     srsearch=f'"{words}" {near}' if words else near)
    return [hit["title"] for hit in (data.get("query") or {}).get("search", [])]


def _real_title(title: str | None, moved: dict[str, str]) -> str | None:
    for _ in range(3):
        title = moved.get(title, title)
    return title


def load(area: dict) -> dict:
    """{"items": described places, "hubs": the towns to load places around, best known first}."""
    centre = tuple(area["centre"])
    reach = box_reach_km(area["viewport"])
    kind = area["kind"]
    small = kind in ("city", "place")
    main, pages, moved = None, {}, {}
    if area.get("article"):
        pages, moved = _fetch([area["article"]])
        main = pages.get(_real_title(area["article"], moved))

    listed = main["hubs"] if main and not small else []
    titles = [h["link"] for h in listed if h["link"] and not _NOT_A_TOWN.search(h["link"])]
    if kind != "country" or not listed:
        # Without a guide of its own (the Dolomites), the guides that write about it.
        titles += _near(centre, max(reach, NEARBY_KM) if small else min(reach, 200), "" if main else area["name"])
    more, redirected = _fetch([t for t in titles if not _NOT_A_TOWN.search(t)][:MAX_GUIDES])
    pages |= more
    moved |= redirected
    for hub in listed:
        found = pages.get(_real_title(hub["link"], moved))
        hub["point"] = hub["point"] or (found and found["point"])

    by_size = sorted(pages.values(), key=lambda p: -p["size"])
    if small:
        wanted = main["districts"][:MAX_DISTRICTS] if main else []
    else:  # a big city's places are on its district pages
        wanted = [t for p in [q for q in by_size if q is not main][:5] for t in p["districts"][:5]]
    if wanted := [t for t in wanted if t not in pages]:
        more, _ = _fetch(wanted)
        pages |= more

    def outside(point: tuple[float, float] | None) -> bool:
        return kind != "country" and point is not None and distance_km(centre, point) > max(reach * 1.15, NEARBY_KM)

    items, hubs = [], []
    for page in sorted(pages.values(), key=lambda p: (p is not main, -p["size"])):
        # A country or province that merely turned up nearby: keep only what it places inside the area.
        wider = page is not main and len(page["hubs"]) >= 3
        if not wider and outside(page["point"]):
            continue
        # Without its own guide (the Dolomites), a nearby guide counts only if it talks about the place.
        if not main and kind != "country" and f" {normalize(area['name'])} " not in page["words"]:
            continue
        for item in page["items"]:
            point = valid_point(item["lat"], item["lng"])
            if outside(point) or (wider and not point):
                continue
            items.append(item)
        is_town = not wider and "/" not in page["title"] and page["point"] and (small or page is not main)
        if is_town:
            hubs.append({"name": safe_text(page["title"], NAME_LIMIT), "point": list(page["point"]), "district": False})
        elif not wider and "/" in page["title"] and page["point"]:  # only for saying which part of town
            hubs.append({"name": safe_text(page["title"].rsplit("/", 1)[-1], NAME_LIMIT), "point": list(page["point"]),
                         "district": True})
    named = {h["name"] for h in hubs}
    for hub in listed:
        if not hub["point"] or outside(hub["point"]):
            continue
        if hub["name"] not in named:
            hubs.append({"name": hub["name"], "point": list(hub["point"]), "district": False})
        if hub["visit"]:  # Petra and Wadi Rum are destinations in their own right
            fields = {"lat": hub["point"][0], "long": hub["point"][1]}
            if item := _item(main["title"], "do", "Place to explore", "", hub["name"], hub["text"], fields):
                items.append(item | {"area": hub["name"], "town": hub["name"]})
    return {"items": items, "hubs": hubs}


def open_photos(files: list[str]) -> dict[str, dict]:
    """Commons files under an open licence, with the credit each one needs."""
    files = list(dict.fromkeys(files))[:MAX_PHOTOS]
    if not files:
        return {}
    data = wikimedia(COMMONS, action="query", prop="imageinfo", iiprop="url|extmetadata", iiurlwidth=500,
                     iiextmetadatafilter="Artist|Attribution|LicenseShortName",
                     titles="|".join(f"File:{name}" for name in files))
    query = data.get("query") or {}
    asked = {x["to"]: x["from"] for x in query.get("normalized", [])}
    photos = {}
    for page in query.get("pages", []):
        info = next(iter(page.get("imageinfo") or []), {})
        meta = info.get("extmetadata") or {}

        def value(key: str) -> str:
            return safe_text(re.sub(r"<[^>]*>", " ", str((meta.get(key) or {}).get("value", ""))), 100)

        licence, url, source = value("LicenseShortName"), info.get("thumburl", ""), info.get("descriptionurl", "")
        if (not _OPEN_LICENCE.match(licence) or urlsplit(url).scheme != "https"
                or urlsplit(url).hostname not in ("upload.wikimedia.org", "thumb.wikimedia.org") or urlsplit(source).hostname != COMMONS):
            continue
        # Without an author field the credit names Commons and links the file page, which names the author.
        by = value("Attribution") or value("Artist")
        name = asked.get(page["title"], page["title"]).removeprefix("File:")
        photos[unquote(name)] = {"url": url, "source": source, "licence": licence, "by": by or "Wikimedia Commons"}
    return photos
