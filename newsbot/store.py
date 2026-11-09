"""Persist fetched headlines and alert history between runs (as JSON files on the news-data branch)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .text import tokens

log = logging.getLogger(__name__)

ITEMS_FILE = "items.json"
STATE_FILE = "state.json"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s (%s); starting fresh", path, exc)
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def load_items(data_dir: Path) -> list[dict]:
    data = _load(data_dir / ITEMS_FILE, [])
    return data if isinstance(data, list) else []


def load_state(data_dir: Path) -> dict:
    data = _load(data_dir / STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def merge_items(old: list[dict], new: list[dict], now: datetime, keep_days: int) -> list[dict]:
    """Union by id (and by title+source, since the same article can have different links); drop old news."""
    cutoff = now - timedelta(days=keep_days)
    by_id: dict[str, dict] = {}
    seen_titles: set[tuple[str, str]] = set()
    for item in [*old, *new]:  # old first, so the first-seen copy wins
        key = (item["title"].strip().lower(), item["source"].strip().lower())
        if item["id"] in by_id or key in seen_titles:
            continue
        try:
            if datetime.fromisoformat(item["published"]) < cutoff:
                continue
        except (KeyError, ValueError):
            continue
        by_id[item["id"]] = item
        seen_titles.add(key)
    return list(by_id.values())


def prune_state(state: dict, now: datetime, keep_days: int) -> dict:
    cutoff = now - timedelta(days=keep_days + 2)

    def fresh(stamp: str) -> bool:
        try:
            return datetime.fromisoformat(stamp) >= cutoff
        except ValueError:
            return False

    for key in ("notified_items", "muted_items"):
        state[key] = {k: v for k, v in state.get(key, {}).items() if fresh(v)}
    state["seen_ipos"] = {k: v for k, v in state.get("seen_ipos", {}).items()
                          if datetime.fromisoformat(v) >= now - timedelta(days=120)}
    state["alert_log"] = [e for e in state.get("alert_log", []) if fresh(e["at"])]
    return state


def record_alerts(state: dict, stories: list[dict], now: datetime) -> None:
    stamp = now.isoformat(timespec="seconds")
    notified = state.setdefault("notified_items", {})
    log_ = state.setdefault("alert_log", [])
    for s in stories:
        for item_id in s["_item_ids"]:
            notified[item_id] = stamp
        log_.append({
            "at": stamp, "title": s["title"], "link": s["link"], "companies": s["companies"],
            "categories": s["categories"], "lang": s["lang"], "toks": sorted(tokens(s["title"])),
        })


def mute(state: dict, stories: list[dict], now: datetime) -> None:
    """First run: everything already in the news counts as seen, so you aren't flooded on day one."""
    stamp = now.isoformat(timespec="seconds")
    muted = state.setdefault("muted_items", {})
    for s in stories:
        for item_id in s["_item_ids"]:
            muted[item_id] = stamp
