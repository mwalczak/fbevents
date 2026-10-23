"""Client for fbedb (https://github.com/mwalczak/fbedb): the Pages to check, and where events are saved."""

import json
import logging
import socket
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

# Waits before trying a call again when it never reached fbedb (the cluster's DNS can miss a
# beat). Only then: a timeout or an error answer may mean fbedb already did the work.
RETRY_WAITS = (5, 15, 40)

EVENT_FIELDS = ("name", "start", "end", "when_text", "place", "city", "lat", "lon",
                "distance_km", "online", "card")


class FbedbError(Exception):
    pass


class Fbedb:
    def __init__(self, url, token, timeout=30, retry_waits=RETRY_WAITS):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retry_waits = retry_waits

    def _call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "fbevents",
        })
        for wait in (*self.retry_waits, None):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode()
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:200]
                raise FbedbError(f"{method} {path}: HTTP {e.code} {detail}") from None
            except (urllib.error.URLError, OSError) as e:
                reason = getattr(e, "reason", e)
                if wait is None or not isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
                    raise FbedbError(f"{method} {path}: {e}") from None
                log.warning("fbedb not reached (%s), trying again in %ds", e, wait)
                time.sleep(wait)
        return json.loads(text) if text.strip() else None

    def sources(self):
        """The Pages on the list: [{"id", "url", "name", "enabled", ...}]."""
        return self._call("GET", "/api/sources")["sources"]

    def add_page(self, url):
        try:
            self._call("POST", "/api/sources", {"url": url})
        except FbedbError as e:
            if "HTTP 409" not in str(e):  # already there is fine
                raise

    def save(self, events, touches, checked=()):
        """Saves full events and refreshes `touches` (events found again but not opened), and
        reports the Pages `checked` this run so fbedb can schedule their next check.
        Returns the set of Facebook event ids that fbedb didn't have before."""
        payload = [
            {"url": ev["url"], "sources": ev["sources"],
             **{k: ev[k] for k in EVENT_FIELDS if ev.get(k) is not None}}
            for ev in events
        ] + [{"url": t["url"], "sources": t["sources"], "touch": True} for t in touches]
        if not payload and not checked:
            return set()
        body = {"events": payload, "checked": list(checked)}
        return set(self._call("POST", "/api/events", body)["new"])
