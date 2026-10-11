"""The daily email."""

import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from zoneinfo import ZoneInfo


def when(ev, tz):
    if ev.get("start"):
        start = datetime.fromtimestamp(ev["start"], ZoneInfo(tz))
        text = start.strftime("%a %d %b %Y, %H:%M")
        if ev.get("end"):
            end = datetime.fromtimestamp(ev["end"], ZoneInfo(tz))
            text += "–" + end.strftime("%H:%M" if end.date() == start.date() else "%a %d %b, %H:%M")
        return text
    return ev.get("when_text") or ""


def where(ev):
    parts = [p for p in (ev.get("place"), ev.get("city")) if p]
    if len(parts) == 2 and parts[1] in parts[0]:
        parts = parts[:1]
    text = ", ".join(parts)
    if ev.get("online"):
        text = (text + " (online)").strip()
    if ev.get("distance_km") is not None:
        text += f" · {ev['distance_km']:.0f} km away"
    return text


def source(ev):
    pages = [s["name"] for s in ev["sources"] if s["kind"] == "page"]
    return "from " + ", ".join(pages) if pages else "nearby"


def subject(events):
    n = len(events)
    return f"{n} new event{'' if n == 1 else 's'} near you" if n else "No new events today"


def render_text(events, tz):
    if not events:
        return "No new events today.\n"
    out = []
    for ev in events:
        out.append(ev.get("name") or "(no name)")
        for line in (when(ev, tz), where(ev), source(ev), ev["url"]):
            if line:
                out.append("  " + line)
        if not ev.get("start") and not ev.get("when_text") and ev.get("card"):
            out.append("  " + ev["card"])
        out.append("")
    return "\n".join(out)


def render_html(events, tz):
    items = []
    for ev in events:
        rows = [f'<a href="{escape(ev["url"])}" style="font-weight:600;font-size:16px;'
                f'color:#1b4f9c;text-decoration:none">{escape(ev.get("name") or "(no name)")}</a>']
        for line in (when(ev, tz), where(ev)):
            if line:
                rows.append(f'<div>{escape(line)}</div>')
        if not ev.get("start") and not ev.get("when_text") and ev.get("card"):
            rows.append(f'<div style="color:#555">{escape(ev["card"])}</div>')
        rows.append(f'<div style="color:#777;font-size:13px">{escape(source(ev))}</div>')
        items.append('<li style="margin:0 0 16px">' + "".join(rows) + "</li>")
    body = "<ul style='list-style:none;padding:0'>" + "".join(items) + "</ul>" if items else (
        "<p>No new events today.</p>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{escape(subject(events))}</title></head>"
            "<body style='font-family:system-ui,sans-serif;line-height:1.4;max-width:640px'>"
            f"<h2 style='font-weight:600'>{escape(subject(events))}</h2>{body}</body></html>")


def send(cfg, subj, text, html=None):
    if not (cfg.smtp_host and cfg.smtp_user and cfg.mail_to):
        raise RuntimeError("Email is not configured: set [email] in config.toml")
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = formataddr((cfg.mail_from_name, cfg.mail_from or cfg.smtp_user))
    msg["To"] = ", ".join(cfg.mail_to)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if cfg.smtp_port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ctx, timeout=60) as s:
            s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(cfg.smtp_user, cfg.smtp_password)
            s.send_message(msg)
