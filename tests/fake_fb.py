"""A tiny stand-in for Facebook: listing pages and event pages shaped like the real ones."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def script(data):
    return f'<script type="application/json" data-sjs>{json.dumps(data)}</script>'


def event_blob(eid, name, start, place, lat, lon, end=None, stub_place=False):
    place_obj = {"__typename": "Page", "id": f"9{eid}", "name": place,
                 "location": {"latitude": lat, "longitude": lon}}
    event = {"id": eid, "__typename": "Event", "name": name, "start_timestamp": start,
             "day_time_sentence": "some day", "event_place": place_obj}
    if end:
        event["end_timestamp"] = end
    blob = {"require": [["RelayPrefetchedStreamCache", "next", [],
                         ["adp_EventQuery", {"__bbox": {"result": {"data": {"event": event}}}}]]]}
    if stub_place:
        event["event_place"] = {"id": place_obj["id"]}
        blob["require"].append(["Other", {"node": place_obj}])
    return blob


def event_page(eid, name, start, place, lat, lon, end=None):
    # A "suggested event" comes first, as on real pages, with another date and place.
    other = event_blob("700000" + eid[-2:], "Some other event", start + 86400 * 30,
                       "Elsewhere", lat + 3, lon + 3)
    stub = {"id": eid, "__typename": "Event", "url": f"/events/{eid}/"}
    return (f"<html><head><title>{name} | Facebook</title>"
            f'<meta property="og:title" content="{name}"></head><body>'
            + script(other) + script({"x": [stub]})
            + script(event_blob(eid, name, start, place, lat, lon, end, stub_place=True))
            + f'<h1>{name}</h1><video src="/img/promo.mp4" autoplay></video></body></html>')


def listing_page(title, links):
    items = "".join(
        f'<div><a href="{href}"><img alt="" src="/img/cover.jpg"></a><a href="{href}">{text}</a></div>'
        for href, text in links)
    return f"<html><head><title>{title} | Facebook</title></head><body>{items}</body></html>"


LOGIN_WALL = ('<html><head><title>Log in to Facebook</title></head><body><form>'
              '<input name="email"><input name="pass" type="password"></form></body></html>')


class FakeFacebook:
    def __init__(self, now):
        self.now = now
        self.login_wall = False
        self.requests = []
        self.cookies = []  # Cookie header of each page request
        day = 86400
        near, far = (54.36, 18.64), (55.07, 18.65)  # ~1 km and ~80 km from 54.35, 18.65
        self.events = {
            "111111": ("Concert at the Venue", now + 3 * day, "The Venue", *near),
            "222222": ("Last month's party", now - 30 * day, "The Venue", *near),
            "444444": ("Street food festival", now + 5 * day, "Old Town", *near),
            "555555": ("Far away fair", now + 6 * day, "Another Town", *far),
        }
        self.pages = {
            "/venue/upcoming_hosted_events": listing_page("The Venue", [
                ("/events/111111/?ref=page", "SAT, 12 OCT · Concert at the Venue"),
                ("/events/222222/", "Last month's party"),
                ("/venue/about", "About"),
            ]),
            "/events/explore/town/123/": listing_page("Events in Town", [
                ("https://HOST/events/444444/", "Street food festival"),
                ("/events/555555/?acontext=x", "Far away fair"),
                ("/events/111111/", "Concert at the Venue"),
                ("/events/create/", "Create event"),
            ]),
        }

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.requests.append(self.path)
                path = self.path.split("?")[0]
                if not path.startswith("/img/"):
                    fake.cookies.append(self.headers.get("Cookie") or "")
                if fake.login_wall:
                    body = LOGIN_WALL
                elif path in fake.pages:
                    body = fake.pages[path].replace("https://HOST", fake.url)
                elif path.startswith("/events/") and path.strip("/").split("/")[-1] in fake.events:
                    eid = path.strip("/").split("/")[-1]
                    body = event_page(eid, *fake.events[eid])
                else:
                    self.send_error(404)
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                if len(fake.cookies) == 1:  # the first page sets the session, like a login refresh
                    self.send_header("Set-Cookie", "xs=fresh; Path=/; Max-Age=3600")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()


class FakeFbedb:
    """Stand-in for fbedb's API (https://github.com/mwalczak/fbedb): Pages list and event saving."""

    def __init__(self, token="edb-token"):
        self.token = token
        self.sources = []
        self.events = {}
        self.posts = []
        self.down = False
        self.fail_saving = False

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _ok(self):
                if fake.down:
                    self._send(503, {"error": "down"})
                    return False
                if self.headers.get("Authorization") != f"Bearer {fake.token}":
                    self._send(401, {"error": "token required"})
                    return False
                return True

            def do_GET(self):
                if self._ok() and self.path == "/api/sources":
                    self._send(200, {"sources": fake.sources})

            def do_POST(self):
                if not self._ok():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/sources":
                    fake.sources.append({"id": len(fake.sources) + 1, "url": body["url"],
                                         "name": None, "enabled": True})
                    self._send(201, fake.sources[-1])
                elif self.path == "/api/events" and fake.fail_saving:
                    self._send(500, {"error": "server error"})
                elif self.path == "/api/events":
                    fake.posts.append(body["events"])
                    new = []
                    for ev in body["events"]:
                        eid = ev["url"].rstrip("/").split("/")[-1]
                        if ev.get("touch") or eid in fake.events:
                            continue
                        fake.events[eid] = ev
                        new.append(eid)
                    self._send(200, {"new": new, "updated": [], "unknown": [], "invalid": 0})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
