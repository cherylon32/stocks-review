"""Run one update: fetch news, score it, alert on big stories and write the dashboard data.

    python -m newsbot --data-dir public/data
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import analyze, config, notify, sources, store

log = logging.getLogger("newsbot")

MAX_STORIES = 800


def dashboard_payload(stories, calendar, cfg, report, now, env) -> dict:
    visible = [s for s in stories if not s["noise"]]
    visible.sort(key=lambda s: (s["level"] != "normal", s["latest"]), reverse=True)
    visible = sorted(visible[:MAX_STORIES], key=lambda s: s["latest"], reverse=True)
    repo = env.get("GITHUB_REPOSITORY", "")
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "repo": repo,
        "branch": env.get("GITHUB_REF_NAME", ""),
        "watchlist": [c.name for c in cfg.watched],
        "thresholds": {"big": cfg.alerts["big_news_score"], "notable": cfg.alerts["notable_score"],
                       "ipo": cfg.alerts["ipo_score"]},
        "keep_days": cfg.keep_days,
        "report": report,
        "stories": [{k: v for k, v in s.items() if not k.startswith("_")} for s in visible],
        "ipo_calendar": calendar or {"upcoming": [], "priced": [], "filed": []},
        "ipo_calendar_ok": calendar is not None,
    }


def run(args) -> int:
    now = datetime.now(timezone.utc)
    env = dict(os.environ)
    cfg = config.load(args.config, args.companies)
    data_dir = Path(args.data_dir)
    channels = notify.Channels(env, env.get("DASHBOARD_URL", ""))
    log.info("Notification channels: %s", ", ".join(channels.names) or "none configured")

    if args.test_notification:
        if not channels.names:
            log.error("No notification channel is configured, so there is nothing to test.")
        elif not channels.send([notify.test_alert(channels.dashboard_url)], "Stocks Review: test"):
            log.error("Test notification failed on every channel.")

    state = store.load_state(data_dir)
    first_run = "first_run_at" not in state

    feeds = sources.build_feeds(cfg)
    fetched, report = sources.fetch_all(feeds, now)
    log.info("Fetched %d headlines from %d/%d feeds", len(fetched), report["feeds_ok"], report["feeds_total"])
    calendar = sources.fetch_ipo_calendar(now) if cfg.ipo_calendar else None

    items = store.merge_items(store.load_items(data_dir), fetched, now, cfg.keep_days)
    stories = analyze.build_stories(items, cfg)
    alerted = set(state.get("notified_items", {}))
    for s in stories:
        s["alerted"] = any(i in alerted for i in s["_item_ids"])

    news_alerts = [] if first_run else analyze.select_alerts(stories, state, cfg, now)
    ipo_rows = analyze.ipo_calendar_alerts(calendar, state, cfg, now, quiet=first_run)
    alerts = [notify.from_story(s) for s in news_alerts] + [notify.from_ipo(r) for r in ipo_rows]
    for s in news_alerts:
        log.info("ALERT %.1f  %s", s["score"], s["title"])

    if first_run and not fetched:
        pass  # nothing fetched yet: stay in first-run mode so the next run sets the baseline
    elif first_run:
        store.mute(state, stories, now)
        state["first_run_at"] = now.isoformat(timespec="seconds")
        if channels.names and not args.no_notify:
            channels.send([notify.welcome(channels.dashboard_url)], "Stocks Review is running")
    elif alerts and not args.no_notify:
        delivered = channels.send(alerts, notify.title_for(alerts)) if channels.names else False
        # Mark as alerted if delivered, or if no channel is set up yet (so you aren't flooded later).
        if delivered or not channels.names:
            store.record_alerts(state, news_alerts, now)
            for s in news_alerts:
                s["alerted"] = True

    state["last_run_at"] = now.isoformat(timespec="seconds")
    store.prune_state(state, now, cfg.keep_days)
    store.save_json(data_dir / store.ITEMS_FILE, items)
    store.save_json(data_dir / store.STATE_FILE, state)
    store.save_json(data_dir / "news.json", dashboard_payload(stories, calendar, cfg, report, now, env))
    big = sum(s["level"] == "big" for s in stories)
    log.info("Saved %d headlines as %d stories (%d big). Alerts sent: %d", len(items), len(stories), big, len(alerts))
    if fetched == [] and report["feeds_total"]:
        log.error("Every feed failed; kept the previous news.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newsbot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="public/data", help="where news and state JSON live")
    parser.add_argument("--config", default="config.yml")
    parser.add_argument("--companies", default="companies.yml")
    parser.add_argument("--test-notification", action="store_true", help="send a test message first")
    parser.add_argument("--no-notify", action="store_true", help="don't send any alerts")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
