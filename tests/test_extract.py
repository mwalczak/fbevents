import json
from datetime import datetime, timezone

from fbevents.extract import (
    collect_links,
    distance_km,
    event_id_from_url,
    page_events_url,
    page_title,
    parse_event_html,
)
from tests.fake_fb import event_page


def test_event_id_from_url():
    assert event_id_from_url("https://www.facebook.com/events/1234567890/?ref=x") == "1234567890"
    assert event_id_from_url("/events/1234567/") == "1234567"
    assert event_id_from_url("https://www.facebook.com/events/explore/gdansk/108153709211375/") is None
    assert event_id_from_url("/events/create/") is None


def test_page_events_url():
    assert page_events_url("https://www.facebook.com/SomeVenue") == (
        "https://www.facebook.com/SomeVenue/upcoming_hosted_events")
    assert page_events_url("https://www.facebook.com/SomeVenue/?ref=bookmarks") == (
        "https://www.facebook.com/SomeVenue/upcoming_hosted_events")
    assert page_events_url("https://www.facebook.com/profile.php?id=100064&ref=x") == (
        "https://www.facebook.com/profile.php?id=100064&sk=events")
    assert page_events_url("https://www.facebook.com/SomeVenue/events") == (
        "https://www.facebook.com/SomeVenue/events")


def test_collect_links_merges_cards_and_skips_non_events():
    links = collect_links("https://www.facebook.com/events/explore/town/1/", [
        ("/events/111111/?ref=a", ""),
        ("/events/111111/", "SAT, 12 OCT AT 20:00\nConcert\nThe Venue"),
        ("/events/111111/", "Concert"),
        ("https://www.facebook.com/events/222222/", "Other"),
        ("/events/create/", "Create"),
        ("/SomeVenue", "Venue"),
        (None, "x"),
    ])
    assert set(links) == {"111111", "222222"}
    assert links["111111"] == {"url": "https://www.facebook.com/events/111111/",
                               "card": "SAT, 12 OCT AT 20:00 · Concert · The Venue"}


def test_parse_event_ignores_suggested_events_and_follows_place_stub():
    html = event_page("111111", "Concert", 1_900_000_000, "The Venue", 54.36, 18.64,
                      end=1_900_007_200)
    ev = parse_event_html(html, "111111")
    assert ev["name"] == "Concert"
    assert ev["start"] == 1_900_000_000
    assert ev["end"] == 1_900_007_200
    assert ev["place"] == "The Venue"
    assert (ev["lat"], ev["lon"]) == (54.36, 18.64)


def test_parse_event_schema_org_fallback():
    ld = {"@context": "http://schema.org", "@type": "Event", "name": "Open air",
          "url": "https://www.facebook.com/events/333333/",
          "startDate": "2030-06-01T18:00:00+02:00",
          "location": {"@type": "Place", "name": "Park",
                       "geo": {"latitude": 54.1, "longitude": 18.2}}}
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    ev = parse_event_html(html, "333333")
    assert ev["name"] == "Open air"
    assert ev["start"] == datetime(2030, 6, 1, 16, tzinfo=timezone.utc).timestamp()
    assert ev["place"] == "Park" and ev["lat"] == 54.1


def test_parse_event_only_title_left():
    html = '<meta property="og:title" content="Jazz &amp; Wine">'
    assert parse_event_html(html, "1234567") == {"id": "1234567", "name": "Jazz & Wine"}


def test_page_title_and_distance():
    assert page_title("(3) The Venue | Facebook") == "The Venue"
    assert page_title("") is None
    assert 78 < distance_km(54.35, 18.65, 55.07, 18.65) < 82


def test_config_ignores_pasted_invisible_characters(tmp_path):
    from fbevents.config import Config
    p = tmp_path / "config.toml"
    p.write_text('﻿[location]\ncity = "Rybnik"\nlatitude = 50.09713 \n'
                 'longitude = 18.54179​\n', encoding="utf-8")
    c = Config.load(p)
    assert (c.city, c.latitude, c.longitude) == ("Rybnik", 50.09713, 18.54179)


def test_email_comes_from_fbevents(monkeypatch):
    from fbevents import digest
    from fbevents.config import Config
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(digest.smtplib, "SMTP", FakeSMTP)
    cfg = Config(smtp_user="me@gmail.com", mail_from="me@gmail.com", mail_to=["me@gmail.com"])
    digest.send(cfg, "1 new event near you", "text")
    assert sent[0]["From"] == "FBEvents <me@gmail.com>"
