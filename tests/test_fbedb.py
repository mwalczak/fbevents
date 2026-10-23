import socket
import urllib.error

import pytest

from fbevents import fbedb
from fbevents.fbedb import Fbedb, FbedbError


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.text.encode()


def fake_urlopen(monkeypatch, outcomes):
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(fbedb.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(fbedb.time, "sleep", lambda s: None)
    return calls


def dns_error():
    return urllib.error.URLError(socket.gaierror(-3, "Temporary failure in name resolution"))


def test_a_dns_hiccup_is_tried_again(monkeypatch):
    calls = fake_urlopen(monkeypatch, [dns_error(), dns_error(), '{"new": ["1"]}'])
    assert Fbedb("http://fbedb", "t").save([], [], ["https://www.facebook.com/x"]) == {"1"}
    assert len(calls) == 3


def test_gives_up_after_the_last_wait(monkeypatch):
    calls = fake_urlopen(monkeypatch, [dns_error()] * 4)
    with pytest.raises(FbedbError, match="name resolution"):
        Fbedb("http://fbedb", "t", retry_waits=(1, 1, 1)).sources()
    assert len(calls) == 4


def test_a_timeout_is_not_tried_again(monkeypatch):
    # The save may have gone through: posting again would hide which events are new.
    calls = fake_urlopen(monkeypatch, [urllib.error.URLError(TimeoutError("timed out")), '{"new": []}'])
    with pytest.raises(FbedbError, match="timed out"):
        Fbedb("http://fbedb", "t").save([], [], ["https://www.facebook.com/x"])
    assert len(calls) == 1
