"""Load config.yml and companies.yml into plain objects."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ZH_MARKETS = {"CN", "HK"}
BOTH_MARKETS = {"TW", "MY"}  # Malaysia: English and Chinese press both matter

DEFAULT_ALERTS = {
    "big_news_score": 9,
    "notable_score": 6,
    "ipo_score": 7,
    "max_per_run": 4,
    "max_per_day": 10,
    "max_per_company_per_day": 2,
    "max_age_hours": 12,
    "ipo_calendar_min_offer_usd": 100_000_000,
}


@dataclass
class Company:
    name: str
    aliases: list[str]
    tier: int = 2
    market: str = "US"
    sector: str = ""
    ticker: str | None = None
    private: bool = False
    lang: str = "en"
    watch: bool = False
    search: str | None = None  # custom news search used when the company is on your watchlist
    ignore: list[str] = field(default_factory=list)  # headline patterns where the name isn't the subject
    _ci: re.Pattern | None = field(default=None, repr=False)
    _cs: re.Pattern | None = field(default=None, repr=False)
    _cjk: tuple[str, ...] = field(default=(), repr=False)
    _ignore: re.Pattern | None = field(default=None, repr=False)

    def compile(self) -> "Company":
        ci, cs, cjk = [], [], []
        for alias in dict.fromkeys(self.aliases):  # de-duplicate, keep order
            if not alias:
                continue
            if not alias.isascii():
                cjk.append(alias)
            elif alias.isupper() or alias[0].islower():
                cs.append(re.escape(alias))
            else:
                ci.append(re.escape(alias))
        wrap = r"(?<![A-Za-z0-9])(?:{})(?![A-Za-z0-9])"
        self._ci = re.compile(wrap.format("|".join(ci)), re.I) if ci else None
        self._cs = re.compile(wrap.format("|".join(cs))) if cs else None
        self._cjk = tuple(cjk)
        self._ignore = re.compile("|".join(f"(?:{p})" for p in self.ignore), re.I) if self.ignore else None
        return self

    def found_in(self, text: str) -> bool:
        found = bool((self._ci and self._ci.search(text)) or (self._cs and self._cs.search(text))
                     or any(alias in text for alias in self._cjk))
        # e.g. "JPMorgan analysts see Nvidia rising" is news about Nvidia, not about JPMorgan.
        return found and not (self._ignore and self._ignore.search(text))

    def keys(self) -> set[str]:
        """Lower-cased names a watchlist entry may use for this company."""
        out = {self.name.lower(), *(a.lower() for a in self.aliases)}
        if self.ticker:
            out.add(self.ticker.lower())
        return out


@dataclass
class Feed:
    url: str
    source: str | None  # fixed source name, or None when the feed names each item's source
    lang: str           # "en" or "zh"
    region: str
    label: str


@dataclass
class Config:
    companies: list[Company]
    watchlist: list[str]
    alerts: dict
    keep_days: int
    searches: list[dict]
    feeds: list[dict]
    ipo_calendar: bool

    @property
    def watched(self) -> list[Company]:
        return [c for c in self.companies if c.watch]


def default_lang(market: str) -> str:
    if market in ZH_MARKETS:
        return "zh"
    if market in BOTH_MARKETS:
        return "both"
    return "en"


def load(config_path: str | Path = "config.yml", companies_path: str | Path = "companies.yml") -> Config:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    company_rows = yaml.safe_load(Path(companies_path).read_text(encoding="utf-8")) or []

    companies = []
    for row in company_rows:
        market = str(row.get("market", "US")).upper()
        aliases = [str(a) for a in row.get("aliases", [])] or [str(row["name"])]
        companies.append(
            Company(
                name=str(row["name"]),
                aliases=aliases,
                tier=int(row.get("tier", 2)),
                market=market,
                sector=str(row.get("sector", "")),
                ticker=str(row["ticker"]) if row.get("ticker") else None,
                private=bool(row.get("private", False)),
                lang=str(row.get("lang") or default_lang(market)),
                search=str(row["search"]) if row.get("search") else None,
                ignore=[str(p) for p in row.get("ignore") or []],
            )
        )

    watchlist = [str(w).strip() for w in (raw.get("watchlist") or []) if str(w).strip()]
    for entry in watchlist:
        key = entry.lower()
        match = next((c for c in companies if key in c.keys()), None)
        if match is None:  # not in companies.yml: track it exactly as typed
            match = Company(name=entry, aliases=[entry], market="CN" if not entry.isascii() else "US",
                            lang="zh" if not entry.isascii() else "en")
            companies.append(match)
        match.watch = True

    for company in companies:
        company.compile()

    alerts = {**DEFAULT_ALERTS, **(raw.get("alerts") or {})}
    return Config(
        companies=companies,
        watchlist=watchlist,
        alerts=alerts,
        keep_days=int(raw.get("keep_days", 14)),
        searches=list(raw.get("searches") or []),
        feeds=list(raw.get("feeds") or []),
        ipo_calendar=bool(raw.get("ipo_calendar", True)),
    )
