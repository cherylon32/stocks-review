"""Send alerts to your phone (ntfy), Telegram and/or email. All are free; set any of them up."""
from __future__ import annotations

import html
import logging
from datetime import datetime
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

import requests

from .analyze import CATEGORY_LABELS

log = logging.getLogger(__name__)


@dataclass
class Alert:
    heading: str   # short line, e.g. "🔥 NVIDIA · Big price move"
    headline: str  # the news headline
    link: str
    detail: str    # source and why it was flagged


def posted_ago(iso: str, now: datetime) -> str:
    """'posted 25 min ago' style text, so alerts make sense in any time zone."""
    mins = max(0, round((now - datetime.fromisoformat(iso)).total_seconds() / 60))
    if mins < 60:
        return f"posted {mins} min ago" if mins else "posted just now"
    hours = round(mins / 60)
    return f"posted {hours} h ago"


def from_story(story: dict, now: datetime) -> Alert:
    cats = [c for c in story["categories"] if c != "move"]
    if "ipo" in story["categories"]:
        icon, label = "🆕", CATEGORY_LABELS["ipo"]
    elif "move" in story["categories"]:
        icon, label = "🔥", CATEGORY_LABELS["move"]
    else:
        icon, label = "🔥", CATEGORY_LABELS.get(cats[0], "Big news") if cats else "Big news"
    subject = " / ".join(story["companies"][:2]) or ("Economy" if "macro" in cats else "Tech & AI")
    outlets = story["outlets"]
    source = story["source"] + (f" + {outlets - 1} more outlets" if outlets > 1 else "")
    why = " · ".join([r for r in story["reasons"] if not r.startswith(("Reported by", "Trusted source"))][:3])
    return Alert(f"{icon} {subject} · {label}", story["title"], story["link"],
                 f"{source} · {posted_ago(story['published'], now)} · score {story['score']:g}"
                 + (f" · {why}" if why else ""))


def from_ipo(row: dict) -> Alert:
    bits = [f"{row['company']}" + (f" ({row['ticker']})" if row.get("ticker") else "")]
    bits.append(f"plans to list on {row['exchange'] or 'a US exchange'}")
    if row.get("date"):
        bits.append(f"around {row['date']}")
    if row.get("price"):
        bits.append(f"at ${row['price']} per share")
    if row.get("offer_usd"):
        bits.append(f"raising about ${row['offer_usd'] / 1e6:,.0f}M")
    return Alert(f"🆕 Upcoming IPO · {row['company']}", " ".join(bits), row["link"], "Nasdaq IPO calendar")


class Channels:
    def __init__(self, env: dict, dashboard_url: str = ""):
        self.env = env
        self.dashboard_url = dashboard_url
        self.ntfy_topic = env.get("NTFY_TOPIC", "").strip()
        self.ntfy_server = (env.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
        self.tg_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.tg_chat = env.get("TELEGRAM_CHAT_ID", "").strip()
        self.smtp_user = env.get("SMTP_USER", "").strip()
        self.smtp_password = env.get("SMTP_PASSWORD", "").replace(" ", "")
        self.email_to = (env.get("EMAIL_TO") or self.smtp_user).strip()

    @property
    def names(self) -> list[str]:
        out = []
        if self.ntfy_topic:
            out.append("ntfy")
        if self.tg_token and self.tg_chat:
            out.append("telegram")
        if self.smtp_user and self.smtp_password and self.email_to:
            out.append("email")
        return out

    def send(self, alerts: list[Alert], title: str) -> bool:
        """Send to every configured channel. True if at least one channel worked."""
        ok = False
        for name in self.names:
            try:
                getattr(self, f"_send_{name}")(alerts, title)
                log.info("Sent %d alert(s) via %s", len(alerts), name)
                ok = True
            except Exception as exc:
                log.error("Sending via %s failed: %s", name, exc)
        return ok

    # ntfy.sh: free push notifications. Install the ntfy app and subscribe to your topic.
    def _send_ntfy(self, alerts: list[Alert], title: str) -> None:
        headers = {}
        if self.env.get("NTFY_TOKEN"):
            headers["Authorization"] = f"Bearer {self.env['NTFY_TOKEN']}"
        for alert in alerts:
            body = {
                "topic": self.ntfy_topic,
                "title": alert.heading,
                "message": f"{alert.headline}\n{alert.detail}",
                "click": alert.link or self.dashboard_url,
                "priority": 4,
                "tags": ["chart_with_upwards_trend"],
            }
            if self.dashboard_url:
                body["actions"] = [{"action": "view", "label": "Open dashboard", "url": self.dashboard_url}]
            requests.post(self.ntfy_server, json=body, headers=headers, timeout=20).raise_for_status()

    def _send_telegram(self, alerts: list[Alert], title: str) -> None:
        esc = html.escape
        parts = [f"<b>{esc(title)}</b>"]
        for a in alerts:
            parts.append(f"<b>{esc(a.heading)}</b>\n<a href=\"{esc(a.link, quote=True)}\">{esc(a.headline)}</a>"
                         f"\n<i>{esc(a.detail)}</i>")
        if self.dashboard_url:
            parts.append(f"<a href=\"{esc(self.dashboard_url, quote=True)}\">Open dashboard</a>")
        text = "\n\n".join(parts)[:4000]
        resp = requests.post(
            f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
            json={"chat_id": self.tg_chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        resp.raise_for_status()

    def _send_email(self, alerts: list[Alert], title: str) -> None:
        esc = html.escape
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self.smtp_user
        msg["To"] = self.email_to
        lines = [f"{a.heading}\n{a.headline}\n{a.detail}\n{a.link}" for a in alerts]
        if self.dashboard_url:
            lines.append(f"Dashboard: {self.dashboard_url}")
        msg.set_content("\n\n".join(lines))
        blocks = "".join(
            f"<p style='margin:0 0 18px'><b>{esc(a.heading)}</b><br>"
            f"<a href=\"{esc(a.link, quote=True)}\" style='font-size:16px'>{esc(a.headline)}</a><br>"
            f"<span style='color:#666'>{esc(a.detail)}</span></p>" for a in alerts)
        footer = (f"<p><a href=\"{esc(self.dashboard_url, quote=True)}\">Open the dashboard</a></p>"
                  if self.dashboard_url else "")
        msg.add_alternative(f"<div style='font-family:sans-serif'>{blocks}{footer}</div>", subtype="html")
        host = self.env.get("SMTP_HOST") or "smtp.gmail.com"
        port = int(self.env.get("SMTP_PORT") or 465)
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(msg)


def title_for(alerts: list[Alert]) -> str:
    if len(alerts) == 1:
        return f"Stocks Review: {alerts[0].heading}"
    return f"Stocks Review: {len(alerts)} big stories"


def welcome(dashboard_url: str) -> Alert:
    return Alert("✅ Stocks Review is running",
                 "You'll get a message here only when big news breaks. Quiet days stay quiet.",
                 dashboard_url, "Checks the news every 15 minutes")


def test_alert(dashboard_url: str) -> Alert:
    return Alert("✅ Test notification", "Notifications from Stocks Review are working.",
                 dashboard_url, "Sent because you ran the workflow with 'send a test notification'")
