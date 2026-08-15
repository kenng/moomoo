from datetime import datetime, timedelta, timezone

from momo.domain.ranking import (
    format_publish_time,
    news_is_fresh,
    parse_publish_time,
    score_news_item,
)

_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def test_notice_related_scores_higher_than_generic():
    notice = score_news_item(
        news_sub_type="NOTICE",
        view_count=100,
        publish_time="",
        related_securities=["MY.1155"],
        symbol="MY.1155",
        stock_name="Maybank",
        title="Maybank quarterly results",
    )
    generic = score_news_item(
        news_sub_type="NEWS",
        view_count=0,
        publish_time="",
        related_securities=[],
        symbol="MY.1155",
        stock_name="Maybank",
        title="Market wraps up",
    )
    assert notice > generic


def test_month_day_future_calendar_uses_previous_year():
    parsed = parse_publish_time("9/30", now=_NOW)
    assert parsed is not None
    assert parsed.date().isoformat() == "2025-09-30"
    assert format_publish_time("9/30", now=_NOW) == "30-Sep-2025"


def test_month_day_already_passed_uses_current_year():
    parsed = parse_publish_time("8/14", now=_NOW)
    assert parsed is not None
    assert parsed.date().isoformat() == "2026-08-14"
    assert format_publish_time("8/14", now=_NOW) == "14-Aug-2026"


def test_old_month_day_news_is_not_fresh():
    cutoff = _NOW - timedelta(days=30)
    assert news_is_fresh("9/30", cutoff=cutoff, now=_NOW) is False
    assert news_is_fresh("8/14", cutoff=cutoff, now=_NOW) is True
    assert news_is_fresh("2026-08-01 12:00:00", cutoff=cutoff, now=_NOW) is True
