"""Files kept between runs: seen events, the Facebook session, alerts already sent."""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path


class State:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.auth = self.dir / "auth.json"
        self.seen_file = self.dir / "seen.json"
        self.alerts_file = self.dir / "alerts.json"
        self.geocode_file = self.dir / "geocode.json"
        self.digest = self.dir / "last_digest.html"
        self.log = self.dir / "run.log"

    def _read(self, path, default):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    def _write(self, path, data):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True))
        os.replace(tmp, path)

    # seen events: {event_id: unix time first reported}
    def load_seen(self, keep_days):
        seen = self._read(self.seen_file, {})
        cutoff = time.time() - keep_days * 86400
        return {k: v for k, v in seen.items() if v >= cutoff}

    def save_seen(self, seen):
        self._write(self.seen_file, seen)

    # throttled alerts (e.g. "log in again"), so a broken session doesn't mail every hour
    def should_alert(self, key, every_hours=20):
        last = self._read(self.alerts_file, {}).get(key, 0)
        return time.time() - last >= every_hours * 3600

    def mark_alert(self, key):
        alerts = self._read(self.alerts_file, {})
        alerts[key] = time.time()
        self._write(self.alerts_file, alerts)

    def clear_alert(self, key):
        alerts = self._read(self.alerts_file, {})
        if alerts.pop(key, None) is not None:
            self._write(self.alerts_file, alerts)

    def geocode_cache(self):
        return self._read(self.geocode_file, {})

    def save_geocode(self, cache):
        self._write(self.geocode_file, cache)

    def seed_auth(self, seed_path):
        """Take a newly uploaded session (from the k8s secret) over the one in the state dir.

        The state dir copy is refreshed after every run, so it wins until a different seed
        file shows up.
        """
        if not seed_path or not Path(seed_path).is_file():
            return
        data = Path(seed_path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        marker = self.dir / "auth.seed.sha256"
        old = marker.read_text().strip() if marker.exists() else ""
        if digest != old or not self.auth.exists():
            shutil.copyfile(seed_path, self.auth)
            marker.write_text(digest)
