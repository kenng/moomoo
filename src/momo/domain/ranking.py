from __future__ import annotations

from datetime import datetime, timezone


TYPE_WEIGHTS = {
    "NOTICE": 35.0,
    "RATING": 30.0,
    "NEWS": 15.0,
    "ALL": 10.0,
}


def score_news_item(
    *,
    news_sub_type: str,
    view_count: int,
    publish_time: str,
    related_securities: list[str],
    symbol: str,
    stock_name: str,
    title: str,
) -> float:
    type_key = (news_sub_type or "").upper()
    score = TYPE_WEIGHTS.get(type_key, 10.0)

    # Attention signal (log-ish compression)
    score += min(25.0, (max(view_count, 0) ** 0.5))

    # Related security match
    related_upper = {s.upper() for s in related_securities}
    if symbol.upper() in related_upper:
        score += 25.0

    # Title relevance
    title_l = title.lower()
    if stock_name and stock_name.lower() in title_l:
        score += 15.0
    bare = symbol.removeprefix("MY.").lower()
    if bare and bare in title_l:
        score += 10.0

    # Recency boost if parseable
    score += _recency_boost(publish_time)
    return round(score, 2)


def parse_publish_time(publish_time: str) -> datetime | None:
    if not publish_time:
        return None
    # Moomoo often returns short forms like "5/13" — weak signal only
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
    """Display as dd-mmm-YYYY (e.g. 03-Nov-2026); fall back to raw string."""
    parsed = parse_publish_time(publish_time)
    if parsed is None:
        return publish_time or ""
    return parsed.strftime("%d-%b-%Y")


def _recency_boost(publish_time: str) -> float:
    parsed = parse_publish_time(publish_time)
    if parsed is None:
        return 0.0
    now = datetime.now(timezone.utc)
    hours = max(0.0, (now - parsed).total_seconds() / 3600.0)
    if hours <= 24:
        return 20.0
    if hours <= 72:
        return 12.0
    if hours <= 168:
        return 6.0
    return 0.0
