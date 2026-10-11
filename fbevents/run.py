"""One daily run: collect event links, open the new ones, filter, email, remember."""

import json
import logging
import random
import time
import traceback
import urllib.parse
import urllib.request

from . import digest
from .browser import Facebook, LoginRequired
from .extract import distance_km, page_events_url, parse_event_html
from .fbedb import Fbedb, FbedbError

log = logging.getLogger("fbevents")

LOGIN_HELP = """Facebook asked for a login, so no events were collected today.

To fix it, on your computer in the fbevents folder:

  .venv/bin/python -m fbevents login

then paste the contents of state/auth.json into the FB_AUTH_JSON secret on GitHub
(https://github.com/mwalczak/fbevents/settings/secrets/actions) and run the
"Update settings" workflow.

Page that asked for the login: {url}
"""


def home_location(cfg, state):
    """(lat, lon) of the configured town, looked up once on OpenStreetMap and cached."""
    if cfg.latitude is not None and cfg.longitude is not None:
        return cfg.latitude, cfg.longitude
    if not cfg.city:
        return None
    cache = state.geocode_cache()
    if cfg.city not in cache:
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": cfg.city, "format": "json", "limit": 1})
        req = urllib.request.Request(url, headers={"User-Agent": "fbevents-digest/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                hits = json.load(r)
        except Exception as e:  # noqa: BLE001 - no radius filter beats no digest
            log.warning("could not look up %r (%s); add latitude/longitude to config", cfg.city, e)
            return None
        if not hits:
            log.warning("OpenStreetMap does not know %r; add latitude/longitude", cfg.city)
            return None
        cache[cfg.city] = [float(hits[0]["lat"]), float(hits[0]["lon"])]
        state.save_geocode(cache)
    return tuple(cache[cfg.city])


def alert(cfg, state, key, subj, text, dry_run):
    if dry_run:
        log.warning("%s\n%s", subj, text)
        return
    if not state.should_alert(key):
        log.info("alert %r already sent recently", key)
        return
    try:
        digest.send(cfg, subj, text)
        state.mark_alert(key)
    except Exception as e:  # noqa: BLE001
        log.error("could not send alert email: %s", e)


def pages_to_check(cfg, edb, dry_run):
    """The Pages list from fbedb (or the config without fbedb). The first time, when fbedb has
    no Pages at all, the config's Pages are copied there."""
    if not edb:
        return cfg.pages
    sources = edb.sources()
    if not sources and cfg.pages:
        log.info("fbedb has no Pages yet: adding the %d from the config", len(cfg.pages))
        if not dry_run:
            for url in cfg.pages:
                edb.add_page(url)
        return cfg.pages
    paused = sum(not s["enabled"] for s in sources)
    if paused:
        log.info("%d Pages paused in fbedb", paused)
    return [s["url"] for s in sources if s["enabled"]]


def collect(fb, cfg, pages):
    """{event_id: {"url", "card", "sources": [{"kind", "name", "url"}]}} from all listing pages."""
    found = {}
    log.info("checking %d Pages and %d town pages", len(pages), len(cfg.city_urls))
    if not cfg.city_urls:
        log.warning("no city_urls in the config, so no nearby events")

    def add(links, kind, name, url):
        for eid, link in links.items():
            item = found.setdefault(eid, {**link, "sources": []})
            if len(link["card"]) > len(item["card"]):
                item["card"] = link["card"]
            if not any(s["kind"] == kind and s["url"] == url for s in item["sources"]):
                item["sources"].append({"kind": kind, "name": name, "url": url})

    for url in pages:
        try:
            title, links = fb.listing(page_events_url(url), cfg.scrolls)
        except LoginRequired:
            raise
        except Exception as e:  # noqa: BLE001 - one broken Page shouldn't stop the run
            log.warning("page %s failed: %s", url, e)
            continue
        log.info("page %s (%s): %d events", title or url, url, len(links))
        add(links, "page", title or url, url)
    for url in cfg.city_urls:
        try:
            title, links = fb.listing(url, cfg.scrolls)
        except LoginRequired:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("city page %s failed: %s", url, e)
            continue
        log.info("nearby %s (%s): %d events", title or url, url, len(links))
        add(links, "nearby", title or url, url)
    return found


def keep_reason(ev, cfg, home, now):
    """None if the event goes in the digest, else why not."""
    if ev.get("canceled"):
        return "canceled"
    ends = ev.get("end") or ev.get("start")
    if ends and ends < now:
        return "past"
    from_page = any(s["kind"] == "page" for s in ev["sources"])
    if ev.get("distance_km") is not None and (not from_page or cfg.pages_use_radius):
        if ev["distance_km"] > cfg.radius_km:
            return f"{ev['distance_km']:.0f} km away"
    return None


def run(cfg, state, *, dry_run=False, delay=True, auth_seed=None):
    """Returns the process exit code: 0 ok, 2 needs a new login, 1 anything else."""
    state.seed_auth(auth_seed)
    if not state.auth.exists():
        alert(cfg, state, "login", "Facebook events: please log in",
              LOGIN_HELP.format(url="(no saved session yet)"), dry_run)
        return 2
    seen = state.load_seen(cfg.seen_days)
    home = home_location(cfg, state)
    if delay and cfg.start_delay_minutes > 0:
        wait = random.uniform(0, cfg.start_delay_minutes * 60)
        log.info("waiting %.0f min before starting", wait / 60)
        time.sleep(wait)

    edb = None
    if cfg.fbedb_url and cfg.fbedb_token:
        edb = Fbedb(cfg.fbedb_url, cfg.fbedb_token)
    elif cfg.fbedb_url:
        log.warning("fbedb url is set but FBEDB_TOKEN is not, so fbedb is not used")
    try:
        pages = pages_to_check(cfg, edb, dry_run)
    except FbedbError as e:
        log.error("fbedb: %s", e)
        alert(cfg, state, "fbedb-read", "Facebook events: fbedb did not answer",
              f"The daily run stopped before opening Facebook because fbedb did not answer:\n\n{e}\n",
              dry_run)
        return 1
    if edb:
        state.clear_alert("fbedb-read")

    opened = []
    touches = []
    try:
        with Facebook(cfg, state.auth) as fb:
            found = collect(fb, cfg, pages)
            # Listed again but opened on an earlier run: fbedb just notes they're still listed.
            touches = [found[eid] for eid in found if eid in seen]
            new = [eid for eid in found if eid not in seen]
            log.info("%d events listed, %d new", len(found), len(new))
            if len(new) > cfg.max_event_pages:
                log.info("opening %d now, the rest next time", cfg.max_event_pages)
            for eid in new[: cfg.max_event_pages]:
                item = found[eid]
                info = {}
                try:
                    info = parse_event_html(fb.event_html(item["url"]), eid)
                except LoginRequired:
                    raise
                except Exception as e:  # noqa: BLE001 - fall back to the card text
                    log.warning("event %s failed: %s", item["url"], e)
                ev = {**item, **info, "id": eid}
                if home and ev.get("lat") is not None and ev.get("lon") is not None:
                    ev["distance_km"] = distance_km(home[0], home[1], ev["lat"], ev["lon"])
                if not info.get("start"):
                    log.info("event %s: no date found, card: %s", eid, item["card"])
                opened.append(ev)
            fb.save_session()
    except LoginRequired as e:
        log.error("Facebook wants a new login (%s)", e)
        alert(cfg, state, "login", "Facebook events: please log in again",
              LOGIN_HELP.format(url=e), dry_run)
        return 2
    except Exception:
        log.exception("run failed")
        alert(cfg, state, "error", "Facebook events: the daily run failed",
              traceback.format_exc(), dry_run)
        return 1
    state.clear_alert("login")

    now = time.time()
    events = []
    for ev in opened:
        reason = keep_reason(ev, cfg, home, now)
        log.debug("event %s %r: %s", ev["id"], ev.get("name"), reason or "new")
        if not reason:
            events.append(ev)
    events.sort(key=lambda e: e.get("start") or float("inf"))
    log.info("%d events found that pass the filters", len(events))

    if edb and not dry_run:
        try:
            new_ids = edb.save(events, touches)
        except FbedbError as e:
            log.error("could not save events to fbedb: %s", e)
            alert(cfg, state, "fbedb-save", "Facebook events: could not save to fbedb",
                  f"The events were collected but not saved, so they are tried again next run:\n\n{e}\n",
                  dry_run)
            return 1  # not remembered, so the same events are opened and saved next run
        state.clear_alert("fbedb-save")
        events = [ev for ev in events if ev["id"] in new_ids]
        log.info("saved to fbedb, %d of them new", len(events))

    html = digest.render_html(events, cfg.timezone)
    text = digest.render_text(events, cfg.timezone)
    state.digest.write_text(html)
    if dry_run:
        print(f"\n{digest.subject(events)}\n\n{text}")
        print(f"(dry run: nothing sent, saved or remembered; see {state.digest})")
        return 0
    if events or cfg.send_when_empty:
        try:
            digest.send(cfg, digest.subject(events), text, html)
        except Exception:
            log.exception("could not send the digest")
            return 1  # not remembered, so the same events are tried again next run
    # Everything opened is remembered, including past and far-away events, so their pages
    # aren't opened again tomorrow.
    for ev in opened:
        seen[ev["id"]] = int(now)
    state.save_seen(seen)
    return 0
