"""Tag headlines, group the same story across outlets, score it and decide what deserves an alert.

Scoring is deliberately simple and explainable (no AI, no cost). A story's score is the score of
its strongest headline plus a bonus for how many different outlets are reporting it:

  event type     IPO / earnings / regulation / deal +3, leadership / economy +2, analyst / product +1
  price move     "plunges", "soars", "暴跌", or a move of 5%+ ............................ +2 to +4
  company        market heavyweight +3, other tracked company +2, on your watchlist +2 more
  source         well-known outlet (Reuters, Bloomberg, 财新 …) .............................. +1
  coverage       2 outlets +1, 3-4 +2, 5-7 +3, 8+ +4
  clickbait      "3 AI stocks to buy now" ................................................. -4
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from .config import Company, Config
from .text import jaccard, tokens

CATEGORY_WEIGHTS = {
    "ipo": 3, "earnings": 3, "regulation": 3, "deal": 3,
    "leadership": 2, "macro": 2, "analyst": 1, "product": 1,
}
# Events that can change what a company is worth. Product launches and analyst notes alone never alert.
MATERIAL = {"ipo", "earnings", "regulation", "deal", "leadership", "macro", "move"}

CATEGORY_LABELS = {
    "ipo": "IPO / listing", "earnings": "Earnings / guidance", "regulation": "Regulation / legal",
    "deal": "Deal / investment", "leadership": "Leadership change", "macro": "Economy / rates",
    "analyst": "Analyst view", "product": "Product news", "move": "Big price move",
}

_EN = {
    "ipo": r"\bIPOs?\b|initial public offering|\bgo(?:es|ing)? public\b|\bwent public\b"
           r"|\b(?:market|trading|stock market|Nasdaq|NYSE|Hong Kong) debut\b|\bdirect listing\b"
           r"|\bS-1\b|\bF-1\b|\bSPAC\b|\blisting (?:plan|application|debut)\b"
           r"|\bfiles? (?:confidentially )?(?:for|to) (?:an? )?(?:IPO|list)",
    "earnings": r"\bearnings\b|\bresults\b|\brevenue\b|\bprofits?\b|\bEPS\b|\bguidance\b|\boutlook\b"
                r"|\bforecasts?\b|\b(?:beats?|miss(?:es|ed)?) (?:estimates|expectations|forecasts)\b"
                r"|\bquarterly\b|\bQ[1-4]\b",
    "regulation": r"\bexport (?:bans?|controls?|curbs?|restrictions?|licen[cs]es?|rules?)\b"
                  r"|\bsanction(?:s|ed)?\b|\bantitrust\b|\bprobe[sd]?\b|\binvestigat(?:e|es|ed|ion|ing)\b"
                  r"|\bbann?(?:ed|s)?\b|\blawsuits?\b|\bsue[sd]?\b|\bfined\b|\bfines\b|\bregulators?\b"
                  r"|\bcrackdown\b|\bDOJ\b|\bFTC\b|\bentity list\b",
    "deal": r"\bacqui(?:re[sd]?|ring|sitions?)\b|\bmergers?\b|\btakeover\b|\bbuyout\b|\bstake\b"
            r"|\bto buy\b|\bdeal (?:to|for|with)\b|\bfunding round\b|\braises? \$|\bvaluation\b",
    "leadership": r"\bCEO\b|\bchief executive\b|\bCFO\b|\bresign(?:s|ed|ation)?\b|\bsteps? down\b"
                  r"|\bstepping down\b|\bappoint(?:s|ed|ment)?\b|\bsuccessor\b|\bousted\b",
    "analyst": r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|\bprice target\b|\boverweight\b|\bunderweight\b"
               r"|\boutperform\b|\bunderperform\b",
    "product": r"\blaunch(?:es|ed)?\b|\bunveil(?:s|ed)?\b|\breleases?d?\b|\bintroduce[sd]?\b"
               r"|\bnew (?:chip|model|product)s?\b",
    "macro": r"\bFed\b|\bFederal Reserve\b|\bFOMC\b|\brate (?:cut|hike|decision)s?\b|\binterest rates?\b"
             r"|\binflation\b|\bCPI\b|\bPCE\b|\bjobs report\b|\bpayrolls\b|\bunemployment\b|\brecession\b"
             r"|\btariffs?\b|\btrade (?:war|deal|talks)\b|\bGDP\b|\b(?:Treasury|bond) yields?\b|\bECB\b"
             r"|\bBank of Japan\b|\bBOJ\b|\bPBOC\b|\bPeople's Bank of China\b|\bstimulus\b"
             r"|\bgovernment shutdown\b",
}
_ZH = {
    "ipo": r"IPO|招股|申购|申購|递表|遞表|上市申请|上市申請|聆讯|聆訊|挂牌|掛牌|首发|首發|暗盘|暗盤|上市(?!公司)",
    "earnings": r"财报|財報|业绩|業績|营收|營收|净利|淨利|季报|季報|年报|年報|指引|盈利|亏损|虧損|预告|預告",
    "regulation": r"出口管制|制裁|反垄断|反壟斷|调查|調查|禁令|监管|監管|罚款|罰款|诉讼|訴訟|起诉|起訴|约谈|約談"
                  r"|立案|实体清单|實體清單",
    "deal": r"收购|收購|并购|併購|合并|合併|入股|融资|融資|估值",
    "leadership": r"首席执行官|首席執行官|行政总裁|行政總裁|辞职|辭職|卸任|接任|董事长|董事長",
    "analyst": r"评级|評級|目标价|目標價|调升|調升|调降|調降",
    "product": r"发布会|發布會|新品|推出|亮相",
    "macro": r"美联储|美聯儲|联储|聯準會|降息|加息|升息|利率|通胀|通膨|通脹|非农|非農|关税|關稅|贸易战|貿易戰"
             r"|央行|人民银行|降准|降準|国务院|國務院|证监会|證監會|经济数据|經濟數據|衰退",
}
CATEGORY_RULES = {cat: (re.compile(_EN[cat], re.I), re.compile(_ZH[cat])) for cat in CATEGORY_WEIGHTS}

STRONG_MOVE = re.compile(
    r"\bplunge[sd]?\b|\bplummet(?:s|ed)?\b|\btumble[sd]?\b|\bsinks?\b|\bsank\b|\bslump(?:s|ed)?\b"
    r"|\bcrash(?:es|ed)?\b|\bsoar(?:s|ed|ing)?\b|\bsurge[sd]?\b|\bjump(?:s|ed)?\b|\bsky-?rocket(?:s|ed)?\b"
    r"|\brall(?:y|ies|ied)\b|\brecord high\b|\ball-time high\b|\bsell-?off\b|\bwipe[sd]? out\b"
    r"|\bslide[sd]?\b|\bslid\b|\bdive[sd]?\b|\bnosedive[sd]?\b|\btank(?:s|ed)?\b|\btrading halt"
    r"|\brout\b|\bbiggest (?:drop|fall|gain|jump|one-day)\b"
    r"|暴跌|大跌|跳水|重挫|急跌|崩跌|暴涨|暴漲|大涨|大漲|飙升|飆升|涨停|漲停|跌停|创新高|創新高|历史新高|歷史新高"
    r"|市值蒸发|市值蒸發|闪崩|閃崩|熔断|停牌", re.I)
MILD_MOVE = re.compile(
    r"\bfall(?:s|ing)?\b|\bfell\b|\bdrop(?:s|ped)?\b|\bris(?:e|es|ing)\b|\brose\b|\bgain(?:s|ed)?\b"
    r"|\bclimb(?:s|ed)?\b|\bslip(?:s|ped)?\b|\bdown\b|\bup\b|\blower\b|\bhigher\b|涨|漲|跌", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?[%％]")

NOISE = re.compile(
    r"\bstocks? to (?:buy|watch|own|hold)\b|\bshould you (?:buy|sell)\b|\bis it (?:a |too late to )?buy\b"
    r"|\b(?:buy|sell) (?:now|right now|today)\b|\bmillionaire\b|\bpredictions?\b|\bwhere will\b"
    r"|\bmy (?:favorite|top)\b|\bbetter (?:buy|stock)\b|\bhere's why\b|\bforever\b|\bno-brainer\b"
    r"|\bunstoppable\b|\bsmartest\b|\bsurefire\b|\bdon't miss\b|\bbillionaires? (?:are )?(?:buying|selling)\b"
    r"|\b\d+ (?:top |best |great |magnificent |growth |dividend |ai |tech )?stocks?\b"
    r"|牛股|必看|速看|揭秘|收评|午评|早评|盘前必读|龙虎榜|个股异动", re.I)
NOISE_SOURCES = ("motley fool", "fool.com", "investorplace", "24/7 wall st", "zacks", "marketbeat",
                 "simply wall st", "ainvest", "gurufocus", "seeking alpha", "tipranks", "insider monkey")
TRUSTED_SOURCES = (
    "reuters", "bloomberg", "cnbc", "wall street journal", "wsj", "financial times", "ft.com",
    "nikkei", "south china morning post", "scmp", "barron", "marketwatch", "associated press",
    "ap news", "the information", "techcrunch", "yahoo finance", "fortune", "the economist", "axios",
    "caixin", "财新", "財新", "财联社", "第一财经", "21世纪经济报道", "证券时报", "中国证券报", "上海证券报",
    "经济观察", "华尔街见闻", "澎湃", "新浪财经", "东方财富", "经济日报", "經濟日報", "信報", "明報",
    "工商時報", "鉅亨", "中央社", "联合早报", "聯合早報", "36氪", "the edge", "straits times",
)

SECTOR = re.compile(
    r"\bAI\b|artificial intelligence|\bchips?\b|chipmakers?|semiconductors?|data cent(?:er|re)s?|\bcloud\b"
    r"|\bsoftware\b|fintech|\bpayments?\b|digital bank|neobank|stablecoins?|crypto exchange"
    r"|\btech (?:stocks?|shares|sector|giants?|IPOs?)\b|\bNasdaq\b|Magnificent Seven|Hang Seng Tech"
    r"|robotics|quantum computing|\bGPUs?\b|\bLLMs?\b"
    r"|人工智能|芯片|晶片|半导体|半導體|算力|大模型|数据中心|數據中心|云计算|雲端|金融科技|支付|数字银行|數字銀行"
    r"|虚拟银行|虛擬銀行|稳定币|穩定幣|科技股|恒生科技|恒生科指|科创板|科創板|纳斯达克|納斯達克|纳指|机器人|機器人|量子",
    re.I)
TECH_IPO_NAME = re.compile(
    r"\bAI\b|\.ai\b|Intelligen|Tech|Semi|Silicon|Chip|Micro|Data|Cloud|Software|Digital|Robot|Quantum"
    r"|Cyber|Network|Compute|Pay|Fintech|Crypto|Blockchain|Labs\b", re.I)


# ── Tagging ───────────────────────────────────────────────────────────

def _companies(text: str, companies: list[Company]) -> list[Company]:
    return [c for c in companies if c.found_in(text)]


def move_points(title: str) -> int:
    strong = bool(STRONG_MOVE.search(title))
    mild = strong or bool(MILD_MOVE.search(title))
    pct = max((float(m) for m in PERCENT.findall(title)), default=0.0)
    points = 2 if strong else 0
    if mild and pct >= 5:
        points = 3 if strong else 2
        if pct >= 10:
            points += 1
    return points


def categories_for(title: str) -> set[str]:
    text = title.replace("上市公司", "")
    return {cat for cat, (en, zh) in CATEGORY_RULES.items() if en.search(text) or zh.search(text)}


def is_noise(title: str, source: str) -> bool:
    src = (source or "").lower()
    return bool(NOISE.search(title)) or any(s in src for s in NOISE_SOURCES)


def is_trusted(source: str) -> bool:
    src = (source or "").lower()
    return any(s in src for s in TRUSTED_SOURCES)


def tag_item(item: dict, cfg: Config) -> dict | None:
    """Add analysis fields (prefixed with _) to an item; return None if it's outside our focus."""
    title = item["title"]
    title_cos = _companies(title, cfg.companies)
    other_cos = [c for c in _companies(item.get("summary", ""), cfg.companies) if c not in title_cos]
    cats = categories_for(title)
    sector = bool(SECTOR.search(title) or SECTOR.search(item.get("summary", "")))
    if not (title_cos or other_cos or sector or "macro" in cats):
        return None

    moves = move_points(title)
    if moves:
        cats.add("move")
    noise = is_noise(title, item["source"])
    trusted = is_trusted(item["source"])

    event = [CATEGORY_WEIGHTS[c] for c in cats if c in CATEGORY_WEIGHTS]
    score = (max(event) + (0.5 if len(event) > 1 else 0)) if event else 0
    if title_cos:
        score += max(3 if c.tier == 1 else 2 for c in title_cos) + (2 if any(c.watch for c in title_cos) else 0)
    elif other_cos:
        score += 1 + (1 if any(c.watch for c in other_cos) else 0)
    elif sector or "macro" in cats:
        score += 1
    score += moves + (1 if trusted else 0) - (4 if noise else 0)

    return {
        **item,
        "_time": datetime.fromisoformat(item["published"]),
        "_companies": title_cos + other_cos,
        "_title_companies": title_cos,
        "_cats": cats,
        "_moves": moves,
        "_noise": noise,
        "_trusted": trusted,
        "_score": score,
        "_toks": tokens(title),
    }


# ── Grouping the same story ───────────────────────────────────────────

def cluster(tagged: list[dict], window: timedelta = timedelta(hours=36)) -> list[list[dict]]:
    """Greedy grouping of headlines about the same event (same language, similar words, close in time)."""
    groups: list[dict] = []
    index: dict[str, set[int]] = {}
    for item in sorted(tagged, key=lambda i: i["_time"]):
        names = {c.name for c in item["_title_companies"]}
        best, best_sim = None, 0.0
        candidates = set().union(*(index.get(t, set()) for t in item["_toks"])) if item["_toks"] else set()
        for gi in candidates:
            g = groups[gi]
            if g["lang"] != item["lang"] or item["_time"] - g["last"] > window:
                continue
            sim = max(jaccard(item["_toks"], other["_toks"]) for other in g["items"][-15:])
            threshold = 0.34 if names & g["names"] else 0.5
            if sim >= threshold and sim > best_sim:
                best, best_sim = gi, sim
        if best is None:
            groups.append({"items": [item], "names": set(names), "lang": item["lang"], "last": item["_time"]})
            best = len(groups) - 1
        else:
            g = groups[best]
            g["items"].append(item)
            g["names"] |= names
            g["last"] = max(g["last"], item["_time"])
        for t in item["_toks"]:
            index.setdefault(t, set()).add(best)
    return [g["items"] for g in groups]


def coverage_points(outlets: int) -> int:
    if outlets >= 8:
        return 4
    if outlets >= 5:
        return 3
    if outlets >= 3:
        return 2
    return 1 if outlets == 2 else 0


def _accepts(company: Company, lang: str) -> bool:
    return company.lang == "both" or company.lang == lang


def build_story(items: list[dict], cfg: Config) -> dict:
    lead = max(items, key=lambda i: (i["_trusted"], i["_score"], -i["_time"].timestamp()))
    outlets = len({i["source"].lower() for i in items})
    companies: dict[str, Company] = {}
    for i in sorted(items, key=lambda i: -i["_score"]):
        for c in i["_companies"]:
            companies.setdefault(c.name, c)
    title_companies = {c.name: c for i in items for c in i["_title_companies"]}
    cats = set().union(*(i["_cats"] for i in items))
    best = max(i["_score"] for i in items)
    cover = coverage_points(outlets)
    score = round(best + cover, 1)
    a = cfg.alerts
    level = "big" if score >= a["big_news_score"] else "notable" if score >= a["notable_score"] else "normal"
    if lead["_noise"]:
        level = "normal"

    reasons = []
    if outlets >= 2:
        reasons.append(f"Reported by {outlets} outlets")
    if max(i["_moves"] for i in items) >= 2:
        reasons.append("Big price move")
    for c in title_companies.values():
        if c.watch:
            reasons.append(f"{c.name} is on your watchlist")
        elif c.tier == 1:
            reasons.append(f"{c.name} is a market heavyweight")
        if c.private and "ipo" in cats:
            reasons.append(f"{c.name} is not listed yet (IPO candidate)")
    reasons += [CATEGORY_LABELS[c] for c in sorted(cats) if c != "move"]
    if lead["_trusted"]:
        reasons.append(f"Trusted source: {lead['source']}")

    # News about a company in a language you didn't choose for it (e.g. Chinese coverage of NVIDIA).
    alt = bool(title_companies) and not any(_accepts(c, lead["lang"]) for c in title_companies.values())
    times = [i["_time"] for i in items]
    others = sorted((i for i in items if i is not lead), key=lambda i: i["_time"], reverse=True)
    return {
        "id": min(items, key=lambda i: i["_time"])["id"],
        "title": lead["title"],
        "link": lead["link"],
        "source": lead["source"],
        "summary": lead.get("summary", ""),
        "lang": lead["lang"],
        "region": lead["region"],
        "published": lead["published"],
        "first_seen": min(times).isoformat(timespec="seconds"),
        "latest": max(times).isoformat(timespec="seconds"),
        "companies": list(companies),
        "markets": sorted({c.market for c in companies.values()}),
        "watch": any(c.watch for c in companies.values()),
        "categories": sorted(cats),
        "score": score,
        "level": level,
        "outlets": outlets,
        "reasons": reasons,
        "noise": lead["_noise"],
        "alt": alt,
        "more": [{"title": i["title"], "link": i["link"], "source": i["source"], "published": i["published"]}
                 for i in others[:8]],
        "_item_ids": [i["id"] for i in items],
        "_title_companies": set(title_companies),
        "_material": bool(cats & MATERIAL),
        "_toks": lead["_toks"],
    }


def build_stories(items: list[dict], cfg: Config) -> list[dict]:
    tagged = [t for t in (tag_item(i, cfg) for i in items) if t is not None]
    stories = [build_story(group, cfg) for group in cluster(tagged)]
    _mark_language_duplicates(stories)
    stories.sort(key=lambda s: s["latest"], reverse=True)
    return stories


def _mark_language_duplicates(stories: list[dict]) -> None:
    """An 'alt' story is a duplicate when the same company has news in your preferred language that day."""
    preferred = [s for s in stories if s["_title_companies"] and not s["alt"]]
    for s in stories:
        s["alt_dup"] = False
        if not s["alt"]:
            continue
        t = datetime.fromisoformat(s["latest"])
        for p in preferred:
            if p["lang"] == s["lang"] or not (p["_title_companies"] & s["_title_companies"]):
                continue
            if abs(datetime.fromisoformat(p["latest"]) - t) <= timedelta(hours=24):
                s["alt_dup"] = True
                break


# ── Alerts ────────────────────────────────────────────────────────────

def _same_event(story: dict, other: dict) -> bool:
    """Two alerts are about the same event if they share a company and an event type (any language)."""
    shared = set(story["companies"]) & set(other["companies"])
    cats = set(story["categories"]) & set(other["categories"])
    if shared:
        return bool(cats) or not (story["categories"] or other["categories"])
    if story["companies"] or other["companies"]:
        return False
    return story["lang"] == other.get("lang") and jaccard(story["_toks"], set(other.get("toks", []))) >= 0.3


def select_alerts(stories: list[dict], state: dict, cfg: Config, now: datetime) -> list[dict]:
    a = cfg.alerts
    notified = state.get("notified_items", {})
    muted = state.get("muted_items", {})
    day_ago = now - timedelta(hours=24)
    recent = [e for e in state.get("alert_log", []) if datetime.fromisoformat(e["at"]) >= day_ago]
    per_company = Counter(c for e in recent for c in e.get("companies", []))
    sent_today = len(recent)

    eligible = []
    for s in stories:
        if s["noise"] or s["alt_dup"] or not s["_material"]:
            continue
        if any(i in notified or i in muted for i in s["_item_ids"]):
            continue
        if now - datetime.fromisoformat(s["latest"]) > timedelta(hours=a["max_age_hours"]):
            continue
        threshold = a["ipo_score"] if "ipo" in s["categories"] else a["big_news_score"]
        if s["score"] >= threshold:
            eligible.append(s)
    eligible.sort(key=lambda s: (s["score"], s["latest"]), reverse=True)

    chosen: list[dict] = []
    for s in eligible:
        if len(chosen) >= a["max_per_run"] or sent_today >= a["max_per_day"]:
            break
        if any(per_company[c] >= a["max_per_company_per_day"] for c in s["companies"]):
            continue
        if any(_same_event(s, c) for c in chosen) or any(_same_event(s, e) for e in recent):
            continue
        chosen.append(s)
        sent_today += 1
        per_company.update(s["companies"])
    return chosen


def is_tech_ipo(row: dict, cfg: Config) -> bool:
    return bool(TECH_IPO_NAME.search(row["company"])) or any(c.found_in(row["company"]) for c in cfg.companies)


def ipo_calendar_alerts(calendar: dict | None, state: dict, cfg: Config, now: datetime, quiet: bool) -> list[dict]:
    """New upcoming US IPOs of tech/fintech/AI companies above the size threshold."""
    if not calendar:
        return []
    seen = state.setdefault("seen_ipos", {})
    alerts = []
    for row in calendar.get("upcoming", []):
        row["tech"] = is_tech_ipo(row, cfg)
        if row["deal_id"] in seen:
            continue
        seen[row["deal_id"]] = now.isoformat(timespec="seconds")
        big = (row.get("offer_usd") or 0) >= cfg.alerts["ipo_calendar_min_offer_usd"]
        if row["tech"] and big and not quiet:
            alerts.append(row)
    for section in ("priced", "filed"):
        for row in calendar.get(section, []):
            row["tech"] = is_tech_ipo(row, cfg)
    return alerts
