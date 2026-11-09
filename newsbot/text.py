"""Small text helpers: cleaning, language detection and tokens for story matching."""
from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
_WORD = re.compile(r"[a-z0-9]+(?:[.'&][a-z0-9]+)*")

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "from",
    "as", "is", "are", "was", "were", "be", "been", "its", "it", "this", "that", "after",
    "amid", "over", "into", "about", "says", "said", "say", "new", "s", "up", "down", "out",
    "more", "than", "how", "why", "what", "who", "will", "could", "may", "might", "can",
    "his", "her", "their", "has", "have", "had", "not", "but", "per", "vs", "via", "report",
    "reports", "news", "stock", "stocks", "shares", "share", "today", "live", "update", "updates",
}


def clean_html(value: str | None, limit: int | None = None) -> str:
    """Strip tags and entities, collapse whitespace, optionally truncate on a word boundary."""
    if not value:
        return ""
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value))).strip()
    if limit and len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
        text = cut.rstrip(" ,.;:-") + "…"
    return text


def strip_source_suffix(title: str, source: str | None) -> str:
    """Google News titles end with ' - Source'; drop it so titles from different outlets compare."""
    title = title.strip()
    if source:
        for sep in (" - ", " – ", " — ", " | "):
            suffix = f"{sep}{source}"
            if title.endswith(suffix):
                return title[: -len(suffix)].strip()
    return title


def has_cjk(text: str) -> bool:
    return bool(_CJK_RUN.search(text or ""))


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> set[str]:
    """Words (English) plus character pairs (Chinese), used to spot the same story across outlets."""
    lowered = (text or "").lower()
    out = {_stem(w) for w in _WORD.findall(lowered) if w not in _STOPWORDS and (len(w) > 1 or w.isdigit())}
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
