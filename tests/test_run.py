"""A whole run in a real headless Chromium against the fake Facebook in fake_fb.py."""

import json
import os
import time
from pathlib import Path

import pytest

from fbevents import digest
from fbevents.config import Config
from fbevents.run import run
from fbevents.state import State
from tests.fake_fb import FakeFacebook, FakeFbedb

CHROMIUM = os.environ.get("FBEVENTS_CHROMIUM") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "")


@pytest.fixture
def fake():
    f = FakeFacebook(now=time.time()).start()
    yield f
    f.stop()


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr(digest, "send", lambda cfg, subj, text, html=None: mails.append((subj, text)))
    return mails


def make(fake, tmp_path):
    cfg = Config(
        city="Town", latitude=54.35, longitude=18.65, radius_km=30,
        pages=[fake.url + "/venue"], city_urls=[fake.url + "/events/explore/town/123/"],
        smtp_user="me@example.com", mail_to=["me@example.com"],
        chromium_path=CHROMIUM, scrolls=1, pause_seconds=(0, 0), start_delay_minutes=0,
    )
    state = State(tmp_path / "state")
    seed = tmp_path / "secret-auth.json"
    seed.write_text(json.dumps({"cookies": [], "origins": []}))
    return cfg, state, seed


def test_daily_run(fake, tmp_path, sent):
    cfg, state, seed = make(fake, tmp_path)

    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert len(sent) == 1
    subj, text = sent[0]
    assert subj == "2 new events near you"
    assert text.index("Concert at the Venue") < text.index("Street food festival")
    assert "from The Venue" in text and "nearby" in text
    assert "The Venue · 1 km away" in text
    assert "Last month" not in text and "Far away" not in text and "Some other" not in text
    assert set(json.loads(state.seen_file.read_text())) == {"111111", "222222", "444444", "555555"}
    assert state.auth.exists()
    assert not [p for p in fake.requests if p.startswith("/img/")]  # pictures and videos skipped

    # Next day: nothing new, no email, and no event pages opened again.
    fake.requests.clear()
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert len(sent) == 1
    assert not [p for p in fake.requests if p.startswith("/events/") and "explore" not in p]

    # A new event shows up on the Page.
    fake.events["666666"] = ("New gig", fake.now + 9 * 86400, "The Venue", 54.36, 18.64)
    fake.pages["/venue/upcoming_hosted_events"] = fake.pages[
        "/venue/upcoming_hosted_events"].replace("</body>", '<a href="/events/666666/">New</a></body>')
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert sent[-1][0] == "1 new event near you" and "New gig" in sent[-1][1]


def test_dry_run_sends_and_remembers_nothing(fake, tmp_path, sent, capsys):
    cfg, state, seed = make(fake, tmp_path)
    assert run(cfg, state, dry_run=True, delay=False, auth_seed=seed) == 0
    assert sent == []
    assert not state.seen_file.exists()
    assert "Street food festival" in capsys.readouterr().out
    assert "Street food festival" in state.digest.read_text()


def test_login_wall_sends_one_alert(fake, tmp_path, sent):
    cfg, state, seed = make(fake, tmp_path)
    fake.login_wall = True
    assert run(cfg, state, delay=False, auth_seed=seed) == 2
    assert run(cfg, state, delay=False, auth_seed=seed) == 2
    assert [s for s, _ in sent] == ["Facebook events: please log in again"]
    assert not state.seen_file.exists()


def test_new_secret_replaces_saved_session(tmp_path):
    state = State(tmp_path / "state")
    seed = tmp_path / "seed.json"
    seed.write_text("first")
    state.seed_auth(seed)
    assert state.auth.read_text() == "first"
    state.auth.write_text("refreshed by a run")
    state.seed_auth(seed)
    assert state.auth.read_text() == "refreshed by a run"
    seed.write_text("new login")
    state.seed_auth(seed)
    assert state.auth.read_text() == "new login"


def test_empty_page_says_what_facebook_showed(fake, tmp_path, sent, caplog):
    cfg, state, seed = make(fake, tmp_path)
    fake.pages["/gone/upcoming_hosted_events"] = (
        "<html><head><title>Facebook</title></head><body><div role=main>"
        "<h2>This content isn't available right now</h2></div></body></html>")
    cfg.pages, cfg.city_urls = [fake.url + "/gone"], []
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert "no city_urls in the config" in caplog.text
    assert "/gone/upcoming_hosted_events" in caplog.text
    assert "This content isn't available right now" in caplog.text


@pytest.fixture
def edb():
    e = FakeFbedb().start()
    yield e
    e.stop()


def test_with_fbedb_pages_come_from_it_and_only_new_events_are_mailed(fake, edb, tmp_path, sent):
    cfg, state, seed = make(fake, tmp_path)
    cfg.pages = []
    cfg.fbedb_url, cfg.fbedb_token = edb.url, edb.token
    edb.sources = [{"id": 1, "url": fake.url + "/venue", "name": "The Venue", "enabled": True},
                   {"id": 2, "url": fake.url + "/paused", "name": "Paused", "enabled": False}]
    # fbedb already has the street food festival (e.g. from before seen.json was lost).
    edb.events["444444"] = {"url": "https://www.facebook.com/events/444444/"}

    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert not [p for p in fake.requests if p.startswith("/paused")]
    saved = {e["url"].split("/")[-2]: e for e in edb.posts[-1]}
    assert set(saved) == {"111111", "444444"}  # past and far-away events are not saved
    assert saved["111111"]["name"] == "Concert at the Venue"
    assert saved["111111"]["sources"][0] == {"kind": "page", "name": "The Venue",
                                              "url": fake.url + "/venue"}
    assert [s for s, _ in sent] == ["1 new event near you"]
    assert "Concert at the Venue" in sent[0][1] and "Street food" not in sent[0][1]

    # Next day: nothing new; every event found is reported to fbedb as still listed.
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert len(sent) == 1
    assert all(e["touch"] for e in edb.posts[-1])
    assert {e["url"].split("/")[-2] for e in edb.posts[-1]} == {"111111", "222222", "444444", "555555"}


def test_fbedb_gets_the_config_pages_the_first_time(fake, edb, tmp_path, sent):
    cfg, state, seed = make(fake, tmp_path)
    cfg.fbedb_url, cfg.fbedb_token = edb.url, edb.token
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert [s["url"] for s in edb.sources] == [fake.url + "/venue"]
    assert sent[0][0] == "2 new events near you"


def test_fbedb_down_means_nothing_is_remembered(fake, edb, tmp_path, sent):
    cfg, state, seed = make(fake, tmp_path)
    cfg.fbedb_url, cfg.fbedb_token = edb.url, edb.token
    edb.down = True
    assert run(cfg, state, delay=False, auth_seed=seed) == 1
    assert [s for s, _ in sent] == ["Facebook events: fbedb did not answer"]
    assert fake.requests == []  # Facebook isn't opened at all

    # fbedb fails while saving: no digest, nothing remembered, so next run tries again.
    edb.down, edb.fail_saving = False, True
    assert run(cfg, state, delay=False, auth_seed=seed) == 1
    assert [s for s, _ in sent][-1] == "Facebook events: could not save to fbedb"
    assert not state.seen_file.exists()
    edb.fail_saving = False
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert sent[-1][0] == "2 new events near you"


def test_stops_listing_when_enough_is_waiting_and_continues_there_next_run(fake, tmp_path, sent):
    cfg, state, seed = make(fake, tmp_path)
    cfg.max_event_pages = 2
    town = "/events/explore/town/123/"

    # The Venue lists 2 new events: enough for this run, so the town page waits.
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert town not in fake.requests
    assert sent[-1][0] == "1 new event near you"  # the other one is last month's party
    assert state.next_page() == fake.url + town

    def listings():
        return [p for p in fake.requests if p in (town, "/venue/upcoming_hosted_events")]

    # Next run starts with the town page, which has 2 new events itself: stop again.
    fake.requests.clear()
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert listings() == [town]
    assert sent[-1][0] == "1 new event near you"  # street food; the far-away fair is skipped
    assert state.next_page() == fake.url + "/venue"

    # Nothing new anywhere: one full round, starting with the Venue.
    fake.requests.clear()
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert listings() == ["/venue/upcoming_hosted_events", town]
    assert len(sent) == 2
    assert state.next_page() is None


def test_cookies_survive_the_fresh_browser_contexts(fake, tmp_path, sent, monkeypatch):
    from fbevents import browser
    monkeypatch.setattr(browser, "PAGES_PER_CONTEXT", 2)
    cfg, state, seed = make(fake, tmp_path)
    assert run(cfg, state, delay=False, auth_seed=seed) == 0
    assert len(fake.cookies) >= 6  # 2 listings + 4 events: at least 2 context changes
    assert all("xs=fresh" in c for c in fake.cookies[1:])
    assert "xs" in json.loads(state.auth.read_text())["cookies"][0]["name"]
