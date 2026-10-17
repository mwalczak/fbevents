"""The one part that talks to Facebook: a headless Chromium with your saved session."""

import json
import logging
import random
import re
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .extract import collect_links, page_title

log = logging.getLogger("fbevents")

LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    # Keep Chromium small on a 2-CPU server: one renderer process per tab instead of one per
    # site, no GPU emulation, no sound, no background extras. (Not one renderer for all tabs:
    # then closing a tab doesn't give its memory back.)
    "--disable-site-isolation-trials",
    "--disable-features=site-per-process,IsolateOrigins,Translate,MediaRouter,OptimizationHints",
    "--disable-gpu",
    "--mute-audio",
    "--disable-extensions",
]
# Pictures, videos and fonts are most of Facebook's weight; the event data is in the HTML.
BLOCKED_TYPES = {"image", "media", "font"}
# Start a fresh browser context (same cookies) after this many pages, so whatever Facebook's
# scripts leave behind can't add up over a run.
PAGES_PER_CONTEXT = 10


class LoginRequired(Exception):
    pass


def _skip_heavy(route):
    if route.request.resource_type in BLOCKED_TYPES:
        route.abort()
    else:
        route.continue_()


def _user_agent(cfg, browser):
    if cfg.user_agent:
        return cfg.user_agent
    major = browser.version.split(".")[0]
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def _launch(pw, cfg, headless):
    opts = {"headless": headless, "args": LAUNCH_ARGS}
    if cfg.chromium_path:
        opts["executable_path"] = cfg.chromium_path
    elif headless:
        opts["channel"] = "chromium"  # the full browser in new headless mode, not headless-shell
    return pw.chromium.launch(**opts)


def _context_options(cfg, browser):
    return {
        "user_agent": _user_agent(cfg, browser),
        "locale": cfg.locale,
        "timezone_id": cfg.timezone,
        "viewport": {"width": 1366, "height": 900},
    }


def save_session(context, path):
    """Keep only cookies: that is the whole Facebook login, and it stays small enough for a
    GitHub secret."""
    state = context.storage_state()
    state["origins"] = []
    path.write_text(json.dumps(state, indent=1))


class Facebook:
    def __init__(self, cfg, auth_path):
        self.cfg = cfg
        self.auth_path = auth_path
        self.pages_opened = 0
        self.context = None
        self.page = None

    def _new_context(self):
        """A new context with the current cookies (from the old context, else the saved file)."""
        cookies = str(self.auth_path)
        if self.context is not None:
            cookies = self.context.storage_state()
            self.context.close()
            self.page = None
        self.context = self.browser.new_context(
            storage_state=cookies, **_context_options(self.cfg, self.browser))
        self.context.set_default_timeout(45_000)
        self.context.route("**/*", _skip_heavy)
        self.context_pages = 0

    def __enter__(self):
        self._pw = sync_playwright().start()
        try:
            self.browser = _launch(self._pw, self.cfg, self.cfg.headless)
            self._new_context()
        except Exception:
            self._pw.stop()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def save_session(self):
        save_session(self.context, self.auth_path)

    def _pause(self):
        if self.pages_opened:
            lo, hi = self.cfg.pause_seconds
            time.sleep(random.uniform(lo, hi))
        self.pages_opened += 1

    def _check_logged_in(self):
        path = urlparse(self.page.url).path
        if re.search(r"^/(login|checkpoint|recover)", path) or self.page.locator(
            'input[name="pass"]'
        ).count():
            raise LoginRequired(self.page.url)

    def _goto(self, url):
        self._pause()
        log.debug("open %s", url)
        # A fresh tab per page: Facebook's single-page app keeps growing in one tab, and
        # closing it hands the memory back.
        if self.context_pages >= PAGES_PER_CONTEXT:
            self._new_context()
        if self.page is not None:
            self.page.close()
        self.page = self.context.new_page()
        self.context_pages += 1
        resp = self.page.goto(url, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("load", timeout=15_000)
        except PlaywrightError:
            pass
        self.page.wait_for_timeout(random.uniform(1500, 3000))
        self._check_logged_in()
        if resp is not None and resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp

    def listing(self, url, scrolls):
        """Event links on a Page's events tab or a city's events page.
        Returns (page title, {event_id: {"url", "card"}}, the address it ended up at)."""
        self._goto(url)
        for _ in range(scrolls):
            self.page.mouse.wheel(0, random.randint(1800, 2600))
            self.page.wait_for_timeout(random.uniform(1500, 2800))
        # Only the page's own content: Facebook's menus and sidebars around it link to events
        # too (e.g. ones you're going to), which would otherwise show up on every Page.
        anchors = self.page.evaluate(
            "() => [...(document.querySelector('[role=main]') || document)"
            ".querySelectorAll('a[href*=\"/events/\"]')]"
            ".map(e => [e.getAttribute('href'),"
            " [e.innerText, e.getAttribute('aria-label')].filter(Boolean).join('\\n')])"
        )
        links = collect_links(self.page.url, anchors)
        if not links:
            # Say what Facebook showed instead, e.g. "This content isn't available right now".
            heading = self.page.evaluate(
                "() => (document.querySelector('[role=main] h1, [role=main] h2, h1, h2')"
                " || {}).innerText || ''")
            log.warning("no events on %s (ended up at %s, heading: %r)",
                        url, self.page.url, " ".join(heading.split())[:120])
        return page_title(self.page.title()), links, self.page.url

    def event_html(self, url):
        """Raw HTML of an event page (it carries the event data as JSON), plus the DOM."""
        resp = self._goto(url)
        raw = ""
        try:
            raw = resp.text() if resp is not None else ""
        except PlaywrightError:
            pass
        return raw + "\n" + self.page.content()


def interactive_login(cfg, auth_path):
    """Open a visible browser, let the person log in, save the session cookies."""
    with sync_playwright() as pw:
        browser = _launch(pw, cfg, headless=False)
        opts = _context_options(cfg, browser)
        if auth_path.exists():
            opts["storage_state"] = str(auth_path)
        context = browser.new_context(**opts)
        page = context.new_page()
        page.goto("https://www.facebook.com/login")
        input("\nLog in to Facebook in the browser window (finish any 2FA check),\n"
              "wait until you see your feed, then press Enter here... ")
        cookies = {c["name"] for c in context.cookies("https://www.facebook.com")}
        if "c_user" not in cookies:
            browser.close()
            raise SystemExit("Not logged in yet (no c_user cookie). Nothing saved; try again.")
        save_session(context, auth_path)
        browser.close()
