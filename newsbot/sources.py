"""Fetch news from Google News searches and RSS/Atom feeds, plus the Nasdaq US IPO calendar."""
from __future__ import annotations

import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from .config import Config, Feed
from .text import clean_html, has_cjk, strip_source_suffix

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; stocks-review-bot/1.0; +https://github.com/cherylon32/stocks-review)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Google News editions: hl, gl, ceid, item language, region
EDITIONS = {
    "en-US": ("en-US", "US", "US:en", "en", "US"),
    "en-SG": ("en-SG", "SG", "SG:en", "en", "Asia"),
    "zh-CN": ("zh-CN", "CN", "CN:zh-Hans", "zh", "CN"),
    "zh-HK": ("zh-HK", "HK", "HK:zh-Hant", "zh", "HK"),
    "zh-TW": ("zh-TW", "TW", "TW:zh-Hant", "zh", "TW"),
}

MAX_AGE_ON_FETCH = timedelta(days=3)
ATOM = "{http://www.w3.org/2005/Atom}"


def google_news_feed(query: str, lang: str) -> Feed:
    hl, gl, ceid, item_lang, region = EDITIONS.get(lang, EDITIONS["en-US"])
    url = (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:1d')}"
           f"&hl={hl}&gl={gl}&ceid={ceid}")
    return Feed(url=url, source=None, lang=item_lang, region=region, label=f"Google News [{lang}] {query}")


def build_feeds(cfg: Config) -> list[Feed]:
    feeds = [google_news_feed(str(s["q"]), str(s.get("lang", "en-US"))) for s in cfg.searches if s.get("q")]

    # Every watchlist company gets its own searches, in the language(s) you prefer for it.
    for company in cfg.watched:
        english = next((a for a in [company.name, *company.aliases] if a.isascii()), None)
        chinese = [a for a in company.aliases if not a.isascii()][:3]  # simplified and traditional forms
        if company.lang in ("en", "both") and english:
            feeds.append(google_news_feed(f'"{english}" (stock OR shares)', "en-US"))
        if company.lang in ("zh", "both") and chinese:
            edition = "zh-TW" if company.market == "TW" else "zh-CN"
            word = "股價" if edition == "zh-TW" else "股价"
            names = chinese[0] if len(chinese) == 1 else "(" + " OR ".join(chinese) + ")"
            feeds.append(google_news_feed(f"{names} {word}", edition))

    for f in cfg.feeds:
        if f.get("url"):
            feeds.append(Feed(url=str(f["url"]), source=str(f.get("name") or "") or None,
                              lang=str(f.get("lang", "en")), region=str(f.get("region", "Global")),
                              label=str(f.get("name") or f["url"])))

    unique: dict[str, Feed] = {}
    for feed in feeds:
        unique.setdefault(feed.url, feed)
    return list(unique.values())


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def item_id(link: str, title: str) -> str:
    return hashlib.sha1((link or title).encode("utf-8")).hexdigest()[:16]


_BAD_CHARS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_BARE_AMP = re.compile(rb"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#[0-9]{1,7}|#x[0-9a-fA-F]{1,6});)")


def _xml_root(xml_bytes: bytes) -> ET.Element:
    """Parse XML, repairing the two most common feed mistakes (control characters and bare '&')."""
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ET.fromstring(_BARE_AMP.sub(b"&amp;", _BAD_CHARS.sub(b"", xml_bytes)))


def parse_feed(xml_bytes: bytes, feed: Feed, now: datetime) -> list[dict]:
    """Parse RSS 2.0 or Atom into item dicts. Raises ET.ParseError on XML that can't be repaired."""
    root = _xml_root(xml_bytes)
    entries = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry"):
            link_el = e.find(f"{ATOM}link[@rel='alternate']")
            if link_el is None:  # (Elements without children are falsy, so no `or` here)
                link_el = e.find(f"{ATOM}link")
            entries.append({
                "title": e.findtext(f"{ATOM}title"),
                "link": link_el.get("href") if link_el is not None else None,
                "date": e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"),
                "summary": e.findtext(f"{ATOM}summary") or e.findtext(f"{ATOM}content"),
                "source": None,
            })
    else:
        for e in root.iter("item"):
            entries.append({
                "title": e.findtext("title"),
                "link": e.findtext("link"),
                "date": e.findtext("pubDate") or e.findtext("{http://purl.org/dc/elements/1.1/}date"),
                "summary": e.findtext("description"),
                "source": e.findtext("source"),
            })

    items = []
    for entry in entries:
        title = clean_html(entry["title"])
        link = (entry["link"] or "").strip()
        if not title or not link:
            continue
        source = (feed.source or (entry["source"] or "").strip()
                  or _source_from_title(title) or "Unknown")
        title = strip_source_suffix(title, source)
        published = _parse_date(entry["date"]) or now
        if published > now + timedelta(hours=1):  # bad clocks in some feeds
            published = now
        if now - published > MAX_AGE_ON_FETCH:
            continue
        # Google News summaries just repeat the headline; keep summaries from real feeds only.
        summary = "" if feed.source is None else clean_html(entry["summary"], limit=280)
        items.append({
            "id": item_id(link, title),
            "title": title,
            "link": link,
            "source": source,
            "published": published.isoformat(timespec="seconds"),
            "fetched": now.isoformat(timespec="seconds"),
            "lang": "zh" if has_cjk(title) else ("en" if feed.lang == "zh" else feed.lang),
            "region": feed.region,
            "summary": summary,
        })
    return items


def _source_from_title(title: str) -> str | None:
    match = re.search(r"\s[-–—]\s([^-–—]{2,40})$", title)
    return match.group(1).strip() if match else None


def fetch_feed(feed: Feed, now: datetime, session: requests.Session) -> list[dict]:
    resp = session.get(feed.url, timeout=20, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return parse_feed(resp.content, feed, now)


def fetch_all(feeds: list[Feed], now: datetime, workers: int = 4) -> tuple[list[dict], dict]:
    session = requests.Session()
    results: list[dict] = []
    failures: list[str] = []

    def run(feed: Feed):
        for attempt in range(2):
            try:
                return feed, fetch_feed(feed, now, session), None
            except Exception as exc:  # network errors, HTTP errors, broken XML
                err = exc
                time.sleep(2 * (attempt + 1))
        return feed, [], err

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for feed, items, err in pool.map(run, feeds):
            if err is not None:
                failures.append(f"{feed.label}: {type(err).__name__}: {err}"[:300])
                log.warning("Feed failed: %s (%s)", feed.label, err)
            else:
                log.info("%3d items  %s", len(items), feed.label)
                results.extend(items)

    report = {"feeds_total": len(feeds), "feeds_ok": len(feeds) - len(failures), "failures": failures}
    return results, report


# ── Nasdaq US IPO calendar ────────────────────────────────────────────

_MONEY = re.compile(r"[^0-9.]")


def _money(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(_MONEY.sub("", str(value)) or "nan")
    except ValueError:
        return None


def _rows(data: dict, *path: str) -> list[dict]:
    node = data
    for key in path:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def parse_ipo_calendar(payload: dict) -> dict[str, list[dict]]:
    data = (payload or {}).get("data") or {}
    sections = {
        "upcoming": _rows(data, "upcoming", "upcomingTable", "rows"),
        "priced": _rows(data, "priced", "rows"),
        "filed": _rows(data, "filed", "rows"),
    }
    out: dict[str, list[dict]] = {}
    for name, rows in sections.items():
        parsed = []
        for row in rows:
            company = clean_html(row.get("companyName"))
            if not company:
                continue
            deal = str(row.get("dealID") or f"{name}:{company}")
            parsed.append({
                "deal_id": deal,
                "company": company,
                "ticker": row.get("proposedTickerSymbol") or "",
                "exchange": row.get("proposedExchange") or "",
                "price": row.get("proposedSharePrice") or "",
                "shares": row.get("sharesOffered") or "",
                "offer_usd": _money(row.get("dollarValueOfSharesOffered")),
                "date": row.get("expectedPriceDate") or row.get("pricedDate") or row.get("filedDate") or "",
                "link": f"https://www.nasdaq.com/market-activity/ipos/overview?dealId={deal}",
            })
        out[name] = parsed
    return out


def fetch_ipo_calendar(now: datetime) -> dict[str, list[dict]] | None:
    months = {now.strftime("%Y-%m"), (now.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")}
    merged: dict[str, dict[str, dict]] = {"upcoming": {}, "priced": {}, "filed": {}}
    ok = False
    for month in sorted(months):
        try:
            resp = requests.get(
                f"https://api.nasdaq.com/api/ipo/calendar?date={month}", timeout=20,
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*"},
            )
            resp.raise_for_status()
            parsed = parse_ipo_calendar(resp.json())
            ok = True
        except Exception as exc:
            log.warning("IPO calendar %s failed: %s", month, exc)
            continue
        for section, rows in parsed.items():
            for row in rows:
                merged[section].setdefault(row["deal_id"], row)
    if not ok:
        return None
    return {k: list(v.values()) for k, v in merged.items()}
