"""Reading Facebook pages. No browser here, only text in and data out, so it is easy to test."""

import json
import math
import re
from datetime import datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

EVENT_ID_RE = re.compile(r"/events/(\d{5,})")
JSON_SCRIPT_RE = re.compile(
    r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', re.S | re.I
)
META_RE = re.compile(r'<meta[^>]+property="og:([a-z_:]+)"[^>]+content="([^"]*)"', re.I)

START_KEYS = ("start_timestamp", "utc_start_timestamp", "startTimestamp")
END_KEYS = ("end_timestamp", "utc_end_timestamp", "endTimestamp")


def event_id_from_url(url):
    m = EVENT_ID_RE.search(urlparse(url).path)
    return m.group(1) if m else None


def event_url(origin_url, event_id):
    p = urlparse(origin_url)
    return f"{p.scheme}://{p.netloc}/events/{event_id}/"


def page_events_url(page_url):
    """The events tab of a Page: /SomeVenue -> /SomeVenue/upcoming_hosted_events."""
    p = urlparse(page_url)
    if "events" in p.path or "sk=events" in p.query:
        return page_url
    if p.path.rstrip("/").endswith("profile.php"):
        q = [kv for kv in parse_qsl(p.query) if kv[0] == "id"] + [("sk", "events")]
        return urlunparse(p._replace(query=urlencode(q)))
    return urlunparse(p._replace(path=p.path.rstrip("/") + "/upcoming_hosted_events", query=""))


def collect_links(base_url, anchors):
    """anchors: [(href, text), ...] from a listing page -> {event_id: {"url", "card"}}.

    The same event is often linked several times (picture, title, date line), so the card
    text is the union of all of them.
    """
    found = {}
    for href, text in anchors:
        if not href:
            continue
        eid = event_id_from_url(urljoin(base_url, href))
        if not eid:
            continue
        item = found.setdefault(eid, {"url": event_url(base_url, eid), "lines": []})
        for line in (text or "").splitlines():
            line = " ".join(line.split())
            if line and line not in item["lines"]:
                item["lines"].append(line)
    return {
        eid: {"url": item["url"], "card": " · ".join(item["lines"][:6])}
        for eid, item in found.items()
    }


def _walk(obj):
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            yield o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)


def _blobs(html):
    for m in JSON_SCRIPT_RE.finditer(html):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            continue


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first(d, keys):
    for k in keys:
        if d.get(k) not in (None, "", 0):
            return d[k]
    return None


def _iso_ts(s):
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except (AttributeError, ValueError):
        return None


def parse_event_html(html, event_id):
    """Pull name, time and place for one event out of an event page.

    Event pages also carry other ("suggested") events, so only objects whose id is this
    event's id are used, and the place is read from inside those objects.
    """
    by_id = {}
    ld_events = []
    for blob in _blobs(html):
        for d in _walk(blob):
            if "id" in d and d["id"] is not None:
                by_id.setdefault(str(d["id"]), []).append(d)
            if d.get("@type") == "Event":
                ld_events.append(d)

    ev = {}
    for d in by_id.get(event_id, []):
        for k, v in d.items():
            if v not in (None, "", [], {}) and k not in ev:
                ev[k] = v

    out = {"id": event_id}
    if ev:
        out["name"] = ev.get("name") or ev.get("title")
        start = _first(ev, START_KEYS)
        end = _first(ev, END_KEYS)
        out["start"] = int(start) if _num(start) else None
        out["end"] = int(end) if _num(end) else None
        out["when_text"] = ev.get("day_time_sentence") or ev.get("start_time_formatted")
        out["online"] = bool(ev.get("is_online"))
        out["canceled"] = bool(ev.get("is_canceled") or ev.get("is_cancelled"))
        place = ev.get("event_place") or ev.get("place") or ev.get("location")
        if isinstance(place, dict):
            # The place may be a stub ({"id": ...}) with its details stored elsewhere.
            full = dict(place)
            for d in by_id.get(str(place.get("id")), []) if place.get("id") else []:
                for k, v in d.items():
                    full.setdefault(k, v)
            out["place"] = full.get("contextual_name") or full.get("name")
            loc = full.get("location") if isinstance(full.get("location"), dict) else full
            out["lat"], out["lon"] = _num(loc.get("latitude")), _num(loc.get("longitude"))
            city = full.get("city")
            if isinstance(city, dict):
                out["city"] = city.get("contextual_name") or city.get("name")
            if not out.get("city") and isinstance(loc.get("reverse_geocode"), dict):
                out["city"] = loc["reverse_geocode"].get("city")
        if not out.get("place") and ev.get("one_line_address"):
            out["place"] = ev["one_line_address"]

    # schema.org data (shown on public event pages), used to fill gaps
    mine = [d for d in ld_events if event_id in str(d.get("url", ""))]
    if not mine and len(ld_events) == 1:
        mine = ld_events
    for d in mine[:1]:
        out["name"] = out.get("name") or d.get("name")
        if not out.get("start"):
            out["start"] = _iso_ts(d.get("startDate"))
        if not out.get("end"):
            out["end"] = _iso_ts(d.get("endDate"))
        loc = d.get("location")
        if isinstance(loc, dict) and not out.get("place"):
            out["place"] = loc.get("name")
            geo = loc.get("geo") or {}
            out["lat"], out["lon"] = _num(geo.get("latitude")), _num(geo.get("longitude"))

    if not out.get("name"):
        meta = dict((k.lower(), unescape(v)) for k, v in META_RE.findall(html))
        out["name"] = meta.get("title")
    return {k: v for k, v in out.items() if v not in (None, "")}


def page_title(html_title):
    """'Some Venue | Facebook' -> 'Some Venue'."""
    t = re.sub(r"^\(\d+\)\s*", "", html_title or "").strip()
    t = re.sub(r"\s*[|\-–]\s*Facebook\s*$", "", t)
    return t or None


def distance_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
