from __future__ import annotations

from datetime import datetime, timezone


TYPE_WEIGHTS = {
    "NOTICE": 35.0,
    "RATING": 30.0,
    "NEWS": 15.0,
    "ALL": 10.0,
}

NEWS_RETENTION_DAYS = 30


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


def parse_publish_time(
    publish_time: str, *, now: datetime | None = None
) -> datetime | None:
    if not publish_time:
        return None
    # Moomoo often returns short forms like "5/13" with no year.
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
    """True if publish_time (else fetched_at) is on/after cutoff."""
    published = parse_publish_time(publish_time, now=now)
    if published is not None:
        age_ref = published.replace(tzinfo=None)
    elif fetched_at is None:
        return True
    else:
        age_ref = fetched_at.replace(tzinfo=None) if fetched_at.tzinfo else fetched_at
    cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
    return age_ref >= cutoff_naive


def format_publish_time(
    publish_time: str, *, now: datetime | None = None
) -> str:
    """Display as dd-mmm-YYYY (e.g. 15-Aug-2026); fall back to raw string."""
    parsed = parse_publish_time(publish_time, now=now)
    if parsed is None:
        return publish_time or ""
    return parsed.strftime("%d-%b-%Y")


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


def _recency_boost(publish_time: str) -> float:
    now = datetime.now(timezone.utc)
    parsed = parse_publish_time(publish_time, now=now)
    if parsed is None or parsed > now:
        return 0.0
    hours = (now - parsed).total_seconds() / 3600.0
    if hours <= 24:
        return 20.0
    if hours <= 72:
        return 12.0
    if hours <= 168:
        return 6.0
    return 0.0
