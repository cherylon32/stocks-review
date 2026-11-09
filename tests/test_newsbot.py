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
    assert len(sent) == 1 and sent[0][0].startswith("✅ Stocks Review is running") and sent[0][0].endswith("[Stocks Review]")
    assert cli.main(args) == 0                                  # second run: big NVIDIA story
    title, alerts = sent[-1]
    assert "NVIDIA" in title and len(alerts) == 1
    data = json.loads((tmp_path / "news.json").read_text(encoding="utf-8"))
    nvda = next(s for s in data["stories"] if "NVIDIA" in s["companies"])
    assert nvda["alerted"] and nvda["level"] == "big"
    assert not any(k.startswith("_") for s in data["stories"] for k in s)


# ── rules tuned on real headlines ─────────────────────────────────────

@pytest.mark.parametrize("title, expected", [
    ("Nvidia CEO Jensen Huang calls US chip restrictions on China a mistake", False),
    ("Intel CEO resigns after board clash", True),
    ("Apple names John Ternus as new CEO", True),
    ("小米集团总裁卸任", True),
])
def test_leadership_means_a_change_at_the_top(title, expected):
    assert ("leadership" in analyze.categories_for(title)) is expected


def test_questions_and_round_ups_score_lower(cfg):
    news = analyze.tag_item(item("Nvidia shares tumble 7% after warning", "Reuters"), cfg)["_score"]
    question = analyze.tag_item(item("Is Nvidia stock a buy after its 7% tumble?", "Reuters"), cfg)["_score"]
    explainer = analyze.tag_item(item("Why Nvidia shares tumbled 7% today", "Reuters"), cfg)["_score"]
    round_up = analyze.tag_item(item("Stock futures steady but Nvidia shares tumble 7%", "Reuters"), cfg)["_score"]
    assert question < news and explainer < news and round_up < news


def test_quote_pages_are_noise():
    assert analyze.is_noise("腾讯控股(00700)股票股价_股价行情_讨论_资讯", "某网站")
    assert analyze.is_noise("Amkor Technology xStock Price, Live Charts, and Marketcap", "Coinbase")


def test_one_outlet_is_never_big(cfg):
    [story] = analyze.build_stories([item("Nvidia shares plunge 12% as US bans chip sales to China", "Reuters")], cfg)
    assert story["score"] >= cfg.alerts["big_news_score"] and story["level"] == "notable"


def test_differently_worded_headlines_about_one_event_are_merged(cfg):
    stories = analyze.build_stories([
        item("Nvidia board launches probe into Jensen Huang after alleged affair with Chinese spy", "Reuters", 90),
        item("Nvidia board launches independent probe into CEO Jensen Huang over spy affair", "CNBC", 80),
        item("Jensen Huang affair: Nvidia board probe into alleged Chinese spy link", "Bloomberg", 70),
        item("Nvidia shares fall as board probes Jensen Huang affair with alleged spy", "WSJ", 60),
        item("Nvidia probes Jensen Huang's affair with alleged Chinese spy, shares slide", "Barron's", 50),
        item("Microsoft unveils new AI model for Office", "The Verge", 40),
    ], cfg)
    nvda = [s for s in stories if "NVIDIA" in s["companies"]]
    assert len(nvda) == 1 and nvda[0]["outlets"] == 5


def test_story_categories_ignore_stray_words(cfg):
    items = [item(f"Nvidia board probes Jensen Huang over spy claims {n}", src, 60 - n)
             for n, src in enumerate(["Reuters", "CNBC", "Bloomberg", "WSJ", "AP News", "Axios"])]
    items.append(item("Nvidia board probes Jensen Huang over spy claims, may launch new chip", "Blog", 1))
    [story] = analyze.build_stories(items, cfg)
    assert "regulation" in story["categories"] and "product" not in story["categories"]


def test_broken_xml_is_repaired():
    feed = config.Feed(url="x", source="36氪", lang="zh", region="CN", label="x")
    xml = ("<rss><channel><item><title>寒武纪 & 芯片\x0b 大涨</title><link>https://36kr.com/p/1?a=1&b=2</link>"
           "<pubDate>Mon, 09 Nov 2026 08:00:00 GMT</pubDate></item></channel></rss>").encode()
    [entry] = sources.parse_feed(xml, feed, NOW)
    assert entry["title"] == "寒武纪 & 芯片 大涨" and entry["link"] == "https://36kr.com/p/1?a=1&b=2"


def test_single_outlet_ipo_story_does_not_alert(cfg):
    eager = config.Config(**{**cfg.__dict__, "alerts": {**cfg.alerts, "ipo_score": 5}})
    one = analyze.build_stories([item("Stripe files confidentially for IPO", "Reuters")], eager)
    assert one[0]["score"] >= 5 and analyze.select_alerts(one, {}, eager, NOW) == []
    two = analyze.build_stories([item("Stripe files confidentially for IPO", "Reuters", 40),
                                 item("Stripe files confidentially for US IPO", "Bloomberg", 30)], eager)
    assert len(analyze.select_alerts(two, {}, eager, NOW)) == 1


def test_badly_broken_rss_still_yields_headlines():
    feed = config.Feed(url="x", source="36氪", lang="zh", region="CN", label="x")
    xml = ("<rss><channel><title>36氪</title><item><title><![CDATA[寒武纪股价大涨 <b>AI芯片</b>]]></title>"
           "<link>https://36kr.com/p/1?a=1&amp;b=2</link><pubDate>Mon, 09 Nov 2026 08:00:00 GMT</pubDate>"
           "<description><p>unclosed <img src=x></description></item><broken attr=1 2></channel></rss>").encode()
    [entry] = sources.parse_feed(xml, feed, NOW)
    assert entry["title"] == "寒武纪股价大涨 AI芯片" and entry["link"] == "https://36kr.com/p/1?a=1&b=2"


def test_alert_says_when_the_news_was_posted(cfg):
    stories = analyze.build_stories(big_story_items(), cfg)
    nvda = next(s for s in stories if "NVIDIA" in s["companies"])
    assert "posted 30 min ago" in notify.from_story(nvda, NOW).detail


def test_story_points_to_its_newest_report(cfg):
    [story] = analyze.build_stories([
        item("Nvidia shares tumble 7% after Jensen Huang warns on China sales", "Reuters", 120),
        item("Nvidia shares tumble 7% after Jensen Huang warns on China sales, deepening losses", "Some Blog", 5),
    ], cfg)
    assert story["source"] == "Reuters"
    assert story["newest"]["source"] == "Some Blog"
    [single] = analyze.build_stories([item("Stripe files confidentially for IPO", "Reuters")], cfg)
    assert single["newest"] is None


def test_email_subject_carries_the_headline(cfg):
    stories = analyze.build_stories(big_story_items(), cfg)
    alert = notify.from_story(next(s for s in stories if "NVIDIA" in s["companies"]), NOW)
    subject = notify.title_for([alert])
    assert subject.startswith("🔥 NVIDIA: Nvidia") and subject.endswith("[Stocks Review]") and len(subject) <= 140
    two = notify.title_for([alert, notify.Alert("x", "y", "z", "w", tag="🔥 Anthropic")])
    assert two == "🔥 2 big stories: NVIDIA, Anthropic [Stocks Review]"


def test_email_message_is_high_priority_with_read_button(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, user, password): sent["login"] = (user, password)
        def send_message(self, msg): sent["msg"] = msg

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    ch = notify.Channels({"SMTP_USER": "me@example.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop"}, "https://dash")
    alert = notify.Alert("🔥 NVIDIA · Big price move", "Nvidia sinks <5%>", "https://news/1", "Reuters · posted 5 min ago", "🔥 NVIDIA")
    assert ch.names == ["email"]
    assert ch.send([alert], notify.title_for([alert]))
    msg = sent["msg"]
    assert sent["login"] == ("me@example.com", "abcdefghijklmnop")
    assert msg["X-Priority"] == "1" and msg["To"] == "me@example.com" and "Stocks Review" in msg["From"]
    html_part = msg.get_body(("html",)).get_content()
    assert "Read the article" in html_part and "Nvidia sinks &lt;5%&gt;" in html_part and "https://news/1" in html_part
