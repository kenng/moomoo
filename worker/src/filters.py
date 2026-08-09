from __future__ import annotations

import re
from datetime import datetime, timezone

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


def parse_publish_time(publish_time: str) -> datetime | None:
    if not publish_time:
        return None
    now = datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d"):
        try:
            parsed = datetime.strptime(publish_time, fmt)
            if fmt == "%m/%d":
                parsed = parsed.replace(year=now.year)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
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
