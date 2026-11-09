"""Send alerts to your phone (ntfy), Telegram and/or email. All are free; set any of them up."""
from __future__ import annotations

import html
import logging
from datetime import datetime
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

import requests

from .analyze import CATEGORY_LABELS

log = logging.getLogger(__name__)


@dataclass
class Alert:
    heading: str   # short line, e.g. "🔥 NVIDIA · Big price move"
    headline: str  # the news headline
    link: str
    detail: str    # source and why it was flagged
    tag: str = ""  # who it's about, e.g. "🔥 NVIDIA" (used to build email subjects)


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
                 + (f" · {why}" if why else ""), tag=f"{icon} {subject}")


def from_ipo(row: dict) -> Alert:
    bits = [f"{row['company']}" + (f" ({row['ticker']})" if row.get("ticker") else "")]
    bits.append(f"plans to list on {row['exchange'] or 'a US exchange'}")
    if row.get("date"):
        bits.append(f"around {row['date']}")
    if row.get("price"):
        bits.append(f"at ${row['price']} per share")
    if row.get("offer_usd"):
        bits.append(f"raising about ${row['offer_usd'] / 1e6:,.0f}M")
    return Alert(f"🆕 Upcoming IPO · {row['company']}", " ".join(bits), row["link"], "Nasdaq IPO calendar",
                 tag=f"🆕 IPO: {row['company']}")


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
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = formataddr(("Stocks Review", self.smtp_user))
        msg["To"] = self.email_to
        # Ask mail apps to treat it as urgent (Outlook and Apple Mail show a flag; Gmail mostly ignores it).
        msg["X-Priority"] = "1"
        msg["Importance"] = "High"
        lines = [f"{a.heading}\n{a.headline}\n{a.detail}\nRead: {a.link}" for a in alerts]
        if self.dashboard_url:
            lines.append(f"Dashboard: {self.dashboard_url}")
        lines.append("Not financial advice. Read the article, then decide calmly.")
        msg.set_content("\n\n".join(lines))
        msg.add_alternative(email_html(alerts, self.dashboard_url), subtype="html")
        host = self.env.get("SMTP_HOST") or "smtp.gmail.com"
        port = int(self.env.get("SMTP_PORT") or 465)
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(self.smtp_user, self.smtp_password)
            smtp.send_message(msg)


def email_html(alerts: list[Alert], dashboard_url: str) -> str:
    """A phone-friendly email: one card per story with a big 'Read the article' button."""
    esc = html.escape
    button = ("display:inline-block;padding:10px 16px;border-radius:8px;text-decoration:none;"
              "font-weight:600;font-size:15px;")
    cards = "".join(
        f"<div style='border:1px solid #e3e6eb;border-left:4px solid #b42318;border-radius:10px;"
        f"padding:14px 16px;margin:0 0 14px'>"
        f"<div style='font-size:13px;color:#b42318;font-weight:700;margin-bottom:4px'>{esc(a.heading)}</div>"
        f"<div style='font-size:18px;line-height:1.35;font-weight:600;color:#14171c;margin-bottom:8px'>{esc(a.headline)}</div>"
        f"<div style='font-size:13px;color:#5b6472;margin-bottom:12px'>{esc(a.detail)}</div>"
        f"<a href=\"{esc(a.link, quote=True)}\" style='{button}background:#2457d6;color:#ffffff'>Read the article</a>"
        f"</div>" for a in alerts)
    footer = (f"<a href=\"{esc(dashboard_url, quote=True)}\" style='{button}background:#e8eefc;color:#2457d6'>"
              f"Open the dashboard</a>" if dashboard_url else "")
    return (f"<div style='font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:560px;color:#14171c'>"
            f"{cards}{footer}<p style='font-size:12px;color:#8a919c;margin-top:16px'>"
            f"Sent by your Stocks Review bot. Not financial advice: read the article, then decide calmly.</p></div>")


def title_for(alerts: list[Alert]) -> str:
    """Subject line that says what happened, so the phone notification alone tells you the news.
    Ends with [Stocks Review] so you can filter these emails (e.g. a Gmail filter on that text)."""
    if len(alerts) == 1:
        a = alerts[0]
        text = f"{a.tag}: {a.headline}" if a.tag else f"{a.heading}: {a.headline}"
    else:
        names = ", ".join(dict.fromkeys(a.tag.split(" ", 1)[-1] for a in alerts if a.tag))
        text = f"🔥 {len(alerts)} big stories: {names}"
    if len(text) > 120:
        text = text[:117].rstrip() + "…"
    return f"{text} [Stocks Review]"


def welcome(dashboard_url: str) -> Alert:
    return Alert("✅ Stocks Review is running",
                 "You'll get a message here only when big news breaks. Quiet days stay quiet.",
                 dashboard_url, "Checks the news every 15 minutes")


def test_alert(dashboard_url: str) -> Alert:
    return Alert("✅ Test notification", "Notifications from Stocks Review are working.",
                 dashboard_url, "Sent because you ran the workflow with 'send a test notification'")
