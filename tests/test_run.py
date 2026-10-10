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
from tests.fake_fb import FakeFacebook

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
