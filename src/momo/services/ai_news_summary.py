from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from momo.adapters.cursor_agent import CursorAgentAdapterError, run_prompt
from momo.config import get_settings
from momo.db.repo import (
    get_ai_summary,
    list_ai_summaries_for_symbols,
    list_news_for_symbol,
    upsert_ai_summary,
)
from momo.db.session import get_session
from momo.services import news_digest
from momo.watchlist import resolve_stock

logger = logging.getLogger(__name__)

MAX_NEWS_ITEMS = 8
MAX_CHARS_PER_ARTICLE = 3500
MAX_TOTAL_NEWS_CHARS = 14000
SUMMARY_PREVIEW_CHARS = 280
FETCH_TIMEOUT = 12.0

SYSTEM_PROMPT = (
    "You are an elite, conservative value investor in the tradition of "
    "Benjamin Graham, Warren Buffett, and Mohnish Pabrai. Ignore short-term "
    "price moves, sentiment, analyst consensus, and macro speculation. Focus "
    "on economics, moats, cash flows, and fundamentals."
)

USER_PROMPT_TEMPLATE = """Analyze the following news for {company} ({ticker}).

NEWS:
{news_text}

Format into exactly these 6 sections. Every section MUST start with a markdown H3
header in this exact shape (emoji + short status label after the pipe):

### N. SECTION TITLE | <emoji> <STATUS LABEL>

Status badge rules (pick exactly one emoji per section):
- 🟢 GREEN: Positive operational impact, moat widening, owner-friendly capital allocation, low risk, or strong BUY signal. Example labels: POSITIVE, MOAT WIDENING, BUY.
- 🟡 YELLOW: Neutral impact, unchanged moat, minor temporary noise, moderate risk, or HOLD / RE-EVALUATE signal. Example labels: NEUTRAL, NEUTRAL / UNVERIFIED NOISE, HOLD, RE-EVALUATE.
- 🔴 RED: Negative impact, moat erosion, poor capital allocation, severe red flags, or SELL / IRRELEVANT NOISE signal. Example labels: NEGATIVE, MOAT EROSION, SELL, RED FLAG.

Example headers (enforce this layout for every section):
### 1. EXECUTIVE SUMMARY | 🟡 NEUTRAL / UNVERIFIED NOISE
### 2. INTRINSIC VALUE & CASH FLOW IMPACT | 🟡 NEUTRAL

Sections:

### 1. EXECUTIVE SUMMARY | <badge>
- Core operational/financial fact in 2-3 sentences.
- What actually happened vs what management claims?

### 2. INTRINSIC VALUE & CASH FLOW IMPACT | <badge>
- Long-term FCF capacity: Positive / Negative / Neutral.
- Temporary/one-off or structural? Why?

### 3. COMPETITIVE MOAT CHECK | <badge>
- Impact on Pricing Power, Network Effects, Cost Advantage, Switching Costs, Barriers.
- Strengthen / dilute / unchanged?

### 4. CAPITAL ALLOCATION & MANAGEMENT EVALUATION | <badge>
- What does this reveal about capital allocation (buybacks, debt, M&A, capex)?
- Aligned with long-term owners or empire-building?

### 5. KEY RISKS & MARGIN OF SAFETY RED FLAGS | <badge>
- Accounting gimmicks, aggressive recognition, rising debt if mentioned/implied.
- Worst-case from this news; margin of safety needed.

### 6. THE VERDICT | <badge>
- Signal: Catalyst for Buying / Cause for Re-evaluation / Irrelevant Market Noise / Red Flag
- Key Metric/Filing to Verify Next: exact line item on BS / IS / CFS to check next quarter.
"""

_SECTION_HEADER_RE = re.compile(
    r"^(#{1,3}\s*)?"
    r"(?P<num>\d+)\.\s+"
    r"(?P<title>[^|\n]+?)"
    r"\s*\|\s*"
    r"(?P<status>(?P<emoji>[🟢🟡🔴])\s*(?P<label>.+?))"
    r"\s*$",
    re.MULTILINE,
)

_TONE_BY_EMOJI = {
    "🟢": "green",
    "🟡": "yellow",
    "🔴": "red",
}


class AiSummaryError(Exception):
    """User-facing AI summary failure."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def _extract_text_from_html(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return parser.text()


def _fetch_article_text(url: str) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        response = httpx.get(
            url,
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "momo-news-summary/0.1"},
        )
    except httpx.HTTPError as exc:
        logger.info("article fetch failed for %s: %s", url, exc)
        return ""
    if response.status_code != 200:
        return ""
    ctype = (response.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text" not in ctype and ctype:
        return ""
    text = _extract_text_from_html(response.text)
    return text[:MAX_CHARS_PER_ARTICLE]


def _news_field(item, key: str) -> str:
    if isinstance(item, dict):
        return str(item.get(key) or "").strip()
    return str(getattr(item, key, "") or "").strip()


def _build_news_corpus(news_items: list) -> tuple[str, list[str]]:
    chunks: list[str] = []
    urls: list[str] = []
    total = 0
    for idx, item in enumerate(news_items[:MAX_NEWS_ITEMS], start=1):
        title = _news_field(item, "title")
        url = _news_field(item, "url")
        source = _news_field(item, "source")
        published = _news_field(item, "publish_time")
        body = _fetch_article_text(url) if url else ""
        block = (
            f"[{idx}] {title}\n"
            f"Source: {source or 'unknown'} | Published: {published or 'n/a'}\n"
            f"URL: {url or 'n/a'}\n"
            f"Text: {body or '(headline/metadata only; article body unavailable)'}"
        )
        if total + len(block) > MAX_TOTAL_NEWS_CHARS:
            remain = MAX_TOTAL_NEWS_CHARS - total
            if remain < 200:
                break
            block = block[:remain] + "…"
        chunks.append(block)
        if url:
            urls.append(url)
        total += len(block)
        if total >= MAX_TOTAL_NEWS_CHARS:
            break
    return "\n\n".join(chunks), urls


def _parse_section_headers(summary: str) -> list[dict]:
    """Extract color-coded section headers for collapsed preview."""
    headers: list[dict] = []
    seen: set[int] = set()
    for match in _SECTION_HEADER_RE.finditer(summary or ""):
        num = int(match.group("num"))
        if num in seen or num < 1 or num > 6:
            continue
        seen.add(num)
        title = re.sub(r"\s+", " ", match.group("title")).strip().rstrip(":")
        emoji = match.group("emoji")
        label = re.sub(r"\s+", " ", match.group("label")).strip()
        status = f"{emoji} {label}".strip()
        headers.append(
            {
                "num": num,
                "title": title,
                "emoji": emoji,
                "label": label,
                "status": status,
                "tone": _TONE_BY_EMOJI.get(emoji, "yellow"),
                "line": f"### {num}. {title} | {status}",
            }
        )
    headers.sort(key=lambda h: h["num"])
    return headers


def _summary_dict(row) -> dict:
    text = row.summary or ""
    preview = text
    if len(preview) > SUMMARY_PREVIEW_CHARS:
        preview = preview[: SUMMARY_PREVIEW_CHARS - 1].rstrip() + "…"
    section_headers = _parse_section_headers(text)
    return {
        "symbol": row.symbol,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "summary": text,
        "preview": preview,
        "truncated": len(text) > SUMMARY_PREVIEW_CHARS,
        "section_headers": section_headers,
        "source_urls": [
            u for u in (row.source_urls or "").split("\n") if u.strip()
        ],
        "model": row.model,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_summary_for_symbol(symbol: str) -> dict | None:
    session = get_session()
    try:
        row = get_ai_summary(session, symbol)
        return _summary_dict(row) if row else None
    finally:
        session.close()


def get_summaries_for_symbols(symbols: list[str]) -> dict[str, dict]:
    session = get_session()
    try:
        rows = list_ai_summaries_for_symbols(session, symbols)
        return {sym: _summary_dict(row) for sym, row in rows.items()}
    finally:
        session.close()


def summarize_stock(
    code: str,
    *,
    market: str | None = None,
    force: bool = False,
    refresh_if_empty: bool = True,
) -> dict:
    """Build/save an AI value-investor summary from cached news links for a stock."""
    settings = get_settings()
    if not settings.cursor_api_key:
        raise AiSummaryError("CURSOR_API_KEY is not set")

    stock = resolve_stock(code, market=market)
    symbol = stock["symbol"]
    name = stock.get("name") or stock["code"]

    session = get_session()
    try:
        if not force:
            existing = get_ai_summary(session, symbol)
            if existing and existing.summary:
                return _summary_dict(existing)

        news = list_news_for_symbol(session, symbol, limit=MAX_NEWS_ITEMS)
    finally:
        session.close()

    if not news and refresh_if_empty:
        try:
            news_digest.refresh_stock_news(stock)
        except Exception as exc:
            logger.info("news refresh before AI summary failed for %s: %s", symbol, exc)
        session = get_session()
        try:
            news = list_news_for_symbol(session, symbol, limit=MAX_NEWS_ITEMS)
        finally:
            session.close()

    if not news:
        raise AiSummaryError(
            f"No cached news links for {symbol}. Refresh news first."
        )

    corpus, urls = _build_news_corpus(news)
    host_hint = ""
    if urls:
        host = urlparse(urls[0]).netloc
        if host:
            host_hint = f" (e.g. {host})"

    user_prompt = USER_PROMPT_TEMPLATE.format(
        company=name,
        ticker=symbol,
        news_text=corpus or f"(No article bodies{host_hint}; use headlines only.)",
    )

    try:
        summary = run_prompt(system=SYSTEM_PROMPT, user=user_prompt)
    except CursorAgentAdapterError as exc:
        raise AiSummaryError(str(exc)) from exc

    session = get_session()
    try:
        row = upsert_ai_summary(
            session,
            {
                "symbol": symbol,
                "stock_code": stock["code"],
                "stock_name": name,
                "summary": summary,
                "source_urls": "\n".join(urls),
                "model": settings.cursor_model,
            },
        )
        return _summary_dict(row)
    finally:
        session.close()
