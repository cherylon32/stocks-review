import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from newsbot import __main__ as cli
from newsbot import analyze, config, notify, sources, store

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return config.load(ROOT / "config.yml", ROOT / "companies.yml")


def item(title, source="Reuters", minutes_ago=30, lang="en", link=None, summary=""):
    published = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    link = link or f"https://example.com/{abs(hash((title, source)))}"
    return {"id": sources.item_id(link, title), "title": title, "link": link, "source": source,
            "published": published, "fetched": published, "lang": lang, "region": "US", "summary": summary}


# ── config ────────────────────────────────────────────────────────────

def test_config_loads_watchlist_and_languages(cfg):
    watched = {c.name: c for c in cfg.watched}
    assert set(watched) == {"NVIDIA", "TSMC", "Tencent"}
    assert watched["Tencent"].lang == "zh"
    assert watched["TSMC"].lang == "both"
    assert watched["NVIDIA"].lang == "en"


def test_unknown_watchlist_entry_is_tracked_as_typed(tmp_path):
    (tmp_path / "c.yml").write_text("watchlist: [Acme Robotics]\n", encoding="utf-8")
    loaded = config.load(tmp_path / "c.yml", ROOT / "companies.yml")
    acme = [c for c in loaded.watched if c.name == "Acme Robotics"]
    assert acme and acme[0].found_in("Acme Robotics jumps on debut")


@pytest.mark.parametrize("text, name, expected", [
    ("Nvidia shares fall", "NVIDIA", True),
    ("英伟达股价大跌", "NVIDIA", True),
    ("TSMC lifts forecast", "TSMC", True),
    ("tsmc in lowercase", "TSMC", False),          # ALL-CAPS aliases must match exactly
    ("AI arms race heats up", "Arm Holdings", False),
    ("Roadblock for chip exports", "Block", False),
    ("H-1B visa fee changes", "Visa", False),
    ("xAI raises money", "xAI", True),
    ("騰訊業績勝預期", "Tencent", True),
])
def test_company_matching(cfg, text, name, expected):
    company = next(c for c in cfg.companies if c.name == name)
    assert company.found_in(text) is expected


# ── parsing ───────────────────────────────────────────────────────────

def test_parse_google_news_feed():
    feed = sources.google_news_feed("Nvidia", "en-US")
    items = sources.parse_feed((FIX / "google_news.xml").read_bytes(), feed, NOW)
    titles = [i["title"] for i in items]
    assert "Nvidia shares tumble 7% after Jensen Huang warns on China sales" in titles
    assert all(" - " not in t for t in titles)              # source suffix removed
    assert not any("Old news" in t for t in titles)         # older than 3 days skipped
    assert {i["source"] for i in items} == {"Reuters", "CNBC", "The Motley Fool"}
    assert all(i["summary"] == "" and i["lang"] == "en" for i in items)


def test_parse_atom_feed():
    feed = config.Feed(url="https://example.com/feed", source="Example", lang="en", region="US", label="x")
    [entry] = sources.parse_feed((FIX / "atom.xml").read_bytes(), feed, NOW)
    assert entry["link"] == "https://example.com/stripe-ipo"
    assert entry["source"] == "Example"
    assert entry["summary"] == "The payments company Stripe has filed."


def test_google_news_url_and_watchlist_searches(cfg):
    feeds = sources.build_feeds(cfg)
    urls = [f.url for f in feeds]
    assert any("when%3A1d" in u and "hl=zh-CN" in u for u in urls)
    labels = [f.label for f in feeds]
    assert any('"NVIDIA" (stock OR shares)' in label for label in labels)
    assert any("(腾讯 OR 騰訊 OR 微信支付) 股价" in label for label in labels)  # Chinese stock → Chinese search
    assert any("(台积电 OR 台積電) 股價" in label and "hl=zh-TW" in f.url
               for f in feeds for label in [f.label])  # Taiwan → traditional Chinese edition
    assert len(urls) == len(set(urls))


# ── classification ────────────────────────────────────────────────────

@pytest.mark.parametrize("title, cats", [
    ("Stripe files confidentially for IPO", {"ipo"}),
    ("小米集团港股上市首日大涨", {"ipo"}),
    ("A股上市公司三季报出炉", {"earnings"}),          # 上市公司 = "listed company", not an IPO
    ("Fed cuts interest rates by a quarter point", {"macro"}),
    ("US tightens export controls on AI chips", {"regulation"}),
    ("Researchers fine-tune their language model", set()),
    ("Nvidia beats estimates, raises guidance", {"earnings"}),
])
def test_categories(title, cats):
    assert analyze.categories_for(title) == cats


@pytest.mark.parametrize("title, points", [
    ("Nvidia shares tumble 7% after warning", 3),
    ("Nvidia shares fall 6%", 2),
    ("Tesla stock rises 12% on deliveries", 3),
    ("Apple shares edge up 1%", 0),
    ("腾讯股价暴跌", 2),
])
def test_move_points(title, points):
    assert analyze.move_points(title) == points


def test_noise_detection():
    assert analyze.is_noise("3 AI Stocks to Buy Now Before They Soar", "The Motley Fool")
    assert analyze.is_noise("Is Nvidia stock a buy?", "InvestorPlace")
    assert not analyze.is_noise("Nvidia shares tumble 7%", "Reuters")


# ── stories and scoring ───────────────────────────────────────────────

def test_same_story_from_two_outlets_is_grouped(cfg):
    stories = analyze.build_stories([
        item("Nvidia shares tumble 7% after Jensen Huang warns on China sales", "Reuters", 50),
        item("Nvidia stock tumbles as Huang warns on China sales outlook", "CNBC", 30),
        item("Microsoft unveils new AI model for Office", "The Verge", 20),
    ], cfg)
    assert len(stories) == 2
    nvda = next(s for s in stories if "NVIDIA" in s["companies"])
    assert nvda["outlets"] == 2 and len(nvda["more"]) == 1
    assert nvda["level"] == "big"
    assert "NVIDIA is on your watchlist" in nvda["reasons"]
    msft = next(s for s in stories if "Microsoft" in s["companies"])
    assert msft["level"] != "big"                     # product news, single outlet


def test_out_of_focus_news_is_dropped(cfg):
    assert analyze.build_stories([item("Local bakery wins award", "Daily News")], cfg) == []


def test_clickbait_never_big(cfg):
    stories = analyze.build_stories([item("3 AI Stocks to Buy Now Before They Soar", "The Motley Fool")], cfg)
    assert stories and stories[0]["noise"] and stories[0]["level"] == "normal"


def test_chinese_coverage_of_us_stock_is_marked_duplicate(cfg):
    stories = analyze.build_stories([
        item("Nvidia shares tumble 7% after Jensen Huang warns on China sales", "Reuters", 40),
        item("英伟达股价暴跌7% 黄仁勋警告中国销售", "新浪财经", 35, lang="zh"),
    ], cfg)
    zh = next(s for s in stories if s["lang"] == "zh")
    assert zh["alt"] and zh["alt_dup"]


# ── alerts ────────────────────────────────────────────────────────────

def big_story_items():
    return [
        item("Nvidia shares tumble 7% after Jensen Huang warns on China sales", "Reuters", 50),
        item("Nvidia stock tumbles as Huang warns on China sales outlook", "CNBC", 30),
    ]


def test_select_alerts_basic_and_dedupe(cfg):
    stories = analyze.build_stories(big_story_items(), cfg)
    state = {"first_run_at": "x"}
    chosen = analyze.select_alerts(stories, state, cfg, NOW)
    assert [s["companies"][0] for s in chosen] == ["NVIDIA"]
    store.record_alerts(state, chosen, NOW)
    assert analyze.select_alerts(stories, state, cfg, NOW) == []   # never twice


def test_old_news_does_not_alert(cfg):
    stories = analyze.build_stories(big_story_items(), cfg)
    later = NOW + timedelta(hours=cfg.alerts["max_age_hours"] + 1)
    assert analyze.select_alerts(stories, {}, cfg, later) == []


def test_follow_up_story_about_same_event_is_not_alerted_again(cfg):
    state = {}
    first = analyze.build_stories(big_story_items(), cfg)
    store.record_alerts(state, analyze.select_alerts(first, state, cfg, NOW), NOW)
    follow_up = analyze.build_stories([
        item("Nvidia plunges 8% as China sales warning rattles investors", "Bloomberg", 10),
        item("Nvidia sinks 8%, wiping out $300 billion", "WSJ", 5),
    ], cfg)
    assert analyze.select_alerts(follow_up, state, cfg, NOW) == []


def test_per_run_cap(cfg):
    titles = [
        ("Tesla shares plunge 12% after deliveries miss estimates", "Tesla stock plunges 12% as deliveries miss estimates"),
        ("Apple shares soar 9% on record iPhone results", "Apple stock soars 9% after record iPhone results"),
        ("Microsoft stock sinks 8% on cloud revenue miss", "Microsoft shares sink 8% after cloud revenue miss"),
    ]
    items = [item(a, "Reuters", 30) for a, _ in titles] + [item(b, "CNBC", 20) for _, b in titles]
    stories = analyze.build_stories(items, cfg)
    capped = {**cfg.alerts, "max_per_run": 2}
    small = config.Config(**{**cfg.__dict__, "alerts": capped})
    assert len(analyze.select_alerts(stories, {}, small, NOW)) == 2


def test_ipo_calendar_parse_and_alerts(cfg):
    calendar = sources.parse_ipo_calendar(json.loads((FIX / "nasdaq_ipo.json").read_text()))
    assert [r["company"] for r in calendar["upcoming"]] == ["Example AI Labs Inc.", "Tasty Burgers Holdings"]
    assert calendar["upcoming"][0]["offer_usd"] == 300_000_000
    quiet_state: dict = {}
    assert analyze.ipo_calendar_alerts(calendar, quiet_state, cfg, NOW, quiet=True) == []
    assert set(quiet_state["seen_ipos"]) == {"222-2", "333-3"}
    alerts = analyze.ipo_calendar_alerts(calendar, {}, cfg, NOW, quiet=False)
    assert [a["company"] for a in alerts] == ["Example AI Labs Inc."]
    assert "raising about $300M" in notify.from_ipo(alerts[0]).headline


# ── storage ───────────────────────────────────────────────────────────

def test_merge_items_dedupes_and_prunes():
    a = item("Same headline", "Reuters", link="https://x/1")
    b = item("Same headline", "Reuters", link="https://x/2")     # same article, different link
    old = item("Ancient", "Reuters", minutes_ago=60 * 24 * 30)
    merged = store.merge_items([a, old], [a, b], NOW, keep_days=14)
    assert merged == [a]


# ── end to end ────────────────────────────────────────────────────────

def test_full_run_first_quiet_then_alerts(tmp_path, monkeypatch):
    sent = []
    batches = iter([
        [item("Microsoft unveils new AI model for Office", "The Verge", 60)],
        big_story_items(),
    ])
    monkeypatch.setattr(sources, "fetch_all", lambda feeds, now: (next(batches), {"feeds_total": 1, "feeds_ok": 1, "failures": []}))
    monkeypatch.setattr(sources, "fetch_ipo_calendar", lambda now: None)
    monkeypatch.setattr(cli, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    monkeypatch.setattr(notify.Channels, "send", lambda self, alerts, title: sent.append((title, alerts)) or True)
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    args = ["--data-dir", str(tmp_path), "--config", str(ROOT / "config.yml"), "--companies", str(ROOT / "companies.yml")]

    assert cli.main(args) == 0                                  # first run: welcome only
    assert [t for t, _ in sent] == ["Stocks Review is running"]
    assert cli.main(args) == 0                                  # second run: big NVIDIA story
    title, alerts = sent[-1]
    assert "NVIDIA" in title and len(alerts) == 1
    data = json.loads((tmp_path / "news.json").read_text(encoding="utf-8"))
    nvda = next(s for s in data["stories"] if "NVIDIA" in s["companies"])
    assert nvda["alerted"] and nvda["level"] == "big"
    assert not any(k.startswith("_") for s in data["stories"] for k in s)
