"""Settings from config.toml, with defaults for everything that has a sensible one."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # [location]
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    radius_km: float = 30.0
    timezone: str = "Europe/Warsaw"
    # [sources]
    pages: list[str] = field(default_factory=list)
    city_urls: list[str] = field(default_factory=list)
    pages_use_radius: bool = False
    # [email]
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    mail_to: list[str] = field(default_factory=list)
    send_when_empty: bool = False
    # [browser]
    headless: bool = True
    user_agent: str = ""
    locale: str = "pl-PL"
    chromium_path: str = ""
    max_event_pages: int = 40
    scrolls: int = 3
    pause_seconds: tuple[float, float] = (4.0, 12.0)
    start_delay_minutes: float = 30.0
    seen_days: int = 180

    @classmethod
    def load(cls, path, required=True):
        path = Path(path)
        if not path.exists():
            if required:
                raise SystemExit(f"Config file {path} not found. Copy config.example.toml to it.")
            return cls(smtp_password=os.environ.get("SMTP_PASSWORD", ""))
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"{path} (the CONFIG_TOML variable on the server) is not valid TOML: {e}."
                             " Text needs quotes, decimals use a dot, one value per line.")
        loc, src = raw.get("location", {}), raw.get("sources", {})
        mail, br = raw.get("email", {}), raw.get("browser", {})
        to = mail.get("to", [])
        c = cls(
            city=loc.get("city", ""),
            latitude=loc.get("latitude"),
            longitude=loc.get("longitude"),
            radius_km=float(loc.get("radius_km", 30)),
            timezone=loc.get("timezone", "Europe/Warsaw"),
            pages=list(src.get("pages", [])),
            city_urls=list(src.get("city_urls", [])),
            pages_use_radius=bool(src.get("pages_use_radius", False)),
            smtp_host=mail.get("smtp_host", "smtp.gmail.com"),
            smtp_port=int(mail.get("smtp_port", 587)),
            smtp_user=mail.get("username", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD") or mail.get("password", ""),
            mail_from=mail.get("from") or mail.get("username", ""),
            mail_to=[to] if isinstance(to, str) else list(to),
            send_when_empty=bool(mail.get("send_when_empty", False)),
            headless=bool(br.get("headless", True)),
            user_agent=br.get("user_agent", ""),
            locale=br.get("locale", "pl-PL"),
            chromium_path=os.environ.get("FBEVENTS_CHROMIUM") or br.get("chromium_path", ""),
            max_event_pages=int(br.get("max_event_pages", 40)),
            scrolls=int(br.get("scrolls", 3)),
            pause_seconds=tuple(br.get("pause_seconds", (4.0, 12.0))),
            start_delay_minutes=float(br.get("start_delay_minutes", 30)),
            seen_days=int(raw.get("seen_days", 180)),
        )
        if not c.mail_to and c.smtp_user:
            c.mail_to = [c.smtp_user]
        return c
