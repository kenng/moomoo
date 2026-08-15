from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import markdown as markdown_lib
from markupsafe import Markup

_MD_EXTENSIONS = ["sane_lists", "nl2br", "fenced_code", "tables"]
_UNSAFE_HTML_RE = re.compile(
    r"</?(script|style|iframe|object|embed|link|meta|base)[^>]*>",
    re.IGNORECASE,
)
_MD_H3_RE = re.compile(r"<h3>(.*?)</h3>", re.IGNORECASE | re.DOTALL)
_MD_TONE_MARKERS = (
    ("🟢", "tone-green"),
    ("🟡", "tone-yellow"),
    ("🔴", "tone-red"),
)
NEWS_RETENTION_DAYS = 30


def parse_publish_time(
    publish_time: str, *, now: datetime | None = None
) -> datetime | None:
    if not publish_time:
        return None
    now = now or datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(publish_time, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    parsed = _parse_month_day(publish_time, now)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def news_is_fresh(
    publish_time: str,
    *,
    cutoff: datetime,
    fetched_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    published = parse_publish_time(publish_time, now=now)
    if published is not None:
        age_ref = published.replace(tzinfo=None)
    elif fetched_at is None:
        return True
    else:
        age_ref = fetched_at.replace(tzinfo=None) if fetched_at.tzinfo else fetched_at
    cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
    return age_ref >= cutoff_naive


def _parse_month_day(publish_time: str, now: datetime) -> datetime | None:
    parts = publish_time.split("/")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    now_cmp = now.replace(tzinfo=now.tzinfo)
    for year in (now.year, now.year - 1):
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            continue
        if year == now.year and parsed.replace(tzinfo=now.tzinfo) > now_cmp:
            continue
        return parsed
    return None


def format_publish_time(publish_time: str) -> str:
    parsed = parse_publish_time(publish_time or "")
    if parsed is None:
        return publish_time or ""
    return parsed.strftime("%d-%b-%Y")


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_updated_at(value: datetime | str | None) -> str:
    dt = _as_datetime(value)
    if dt is None:
        return "" if value is None or value == "" else str(value)
    return dt.strftime("%d-%b-%Y %H:%M")


def format_short_date(value: datetime | str | None) -> str:
    """dd-mm-yy for table cells."""
    dt = _as_datetime(value)
    if dt is None:
        return ""
    return dt.strftime("%d-%m-%y")


def format_sort_key(value: datetime | str | None) -> str:
    """Compact sortable timestamp for data-* attributes."""
    dt = _as_datetime(value)
    if dt is None:
        return ""
    return dt.strftime("%Y%m%d%H%M%S")


def _tone_class_for_heading(inner_html: str) -> str:
    for marker, tone in _MD_TONE_MARKERS:
        if marker in inner_html:
            return tone
    return ""


def _style_markdown_headings(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        tone = _tone_class_for_heading(inner)
        cls = f' class="ai-summary-section {tone}"' if tone else ""
        return f"<h3{cls}>{inner}</h3>"

    return _MD_H3_RE.sub(repl, html)


def render_markdown(value: str | None) -> Markup:
    text = (value or "").strip()
    if not text:
        return Markup("")
    html = markdown_lib.markdown(text, extensions=_MD_EXTENSIONS)
    html = _UNSAFE_HTML_RE.sub("", html)
    html = _style_markdown_headings(html)
    return Markup(html)
