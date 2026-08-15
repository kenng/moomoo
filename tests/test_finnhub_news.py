from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from momo.adapters import finnhub as finnhub_adapter
from momo.config import get_settings
from momo.services import news_digest


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_normalize_news_provider():
    assert news_digest.normalize_news_provider(None) == "all"
    assert news_digest.normalize_news_provider("Finnhub") == "finnhub"
    assert news_digest.normalize_news_provider("nope") == "all"


def test_to_finnhub_symbol_us_and_hk():
    assert finnhub_adapter.to_finnhub_symbol(
        {"code": "QCOM", "market": "US", "symbol": "US.QCOM"}
    ) == "QCOM"
    assert finnhub_adapter.to_finnhub_symbol(
        {"code": "09988", "market": "HK", "symbol": "HK.09988"}
    ) == "09988.HK"
    assert finnhub_adapter.to_finnhub_symbol(
        {"code": "1155", "market": "MY", "symbol": "MY.1155"}
    ) == "1155.MY"


def test_normalize_item_maps_finnhub_fields():
    item = finnhub_adapter._normalize_item(
        {
            "headline": "Qualcomm beats estimates",
            "source": "Reuters",
            "url": "https://example.com/qcom",
            "datetime": 1_700_000_000,
            "related": "QCOM,AAPL",
        }
    )
    assert item is not None
    assert item["title"] == "Qualcomm beats estimates"
    assert item["source"] == "Reuters"
    assert item["url"] == "https://example.com/qcom"
    assert item["news_sub_type"] == "NEWS"
    assert item["view_count"] == 0
    assert item["related_securities"] == ["QCOM", "AAPL"]
    assert item["publish_time"] == "2023-11-14 22:13:20"


def test_company_news_parses_http_response(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    get_settings.cache_clear()

    payload = [
        {
            "headline": "Hello",
            "source": "Finnhub",
            "url": "https://example.com/a",
            "datetime": 1_700_000_000,
            "related": "QCOM",
        }
    ]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = payload
    mock_response.text = "[]"

    with patch("momo.adapters.finnhub.httpx.get", return_value=mock_response) as get:
        items = finnhub_adapter.company_news(
            "QCOM", from_date="2026-08-01", to_date="2026-08-08"
        )

    assert len(items) == 1
    assert items[0]["title"] == "Hello"
    get.assert_called_once()
    _, kwargs = get.call_args
    assert kwargs["params"]["symbol"] == "QCOM"
    assert kwargs["headers"]["X-Finnhub-Token"] == "test-token"


def test_company_news_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    get_settings.cache_clear()

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "unauthorized"

    with patch("momo.adapters.finnhub.httpx.get", return_value=mock_response):
        with pytest.raises(finnhub_adapter.FinnhubError, match="HTTP 401"):
            finnhub_adapter.company_news(
                "QCOM", from_date="2026-08-01", to_date="2026-08-08"
            )


def test_refresh_skips_finnhub_when_api_key_empty(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    get_settings.cache_clear()

    stock = {
        "code": "QCOM",
        "market": "US",
        "symbol": "US.QCOM",
        "name": "QUALCOMM",
    }
    opend_item = {
        "title": "OpenD only",
        "news_sub_type": "NEWS",
        "source": "OpenD",
        "publish_time": "2026-08-01 12:00:00",
        "view_count": 0,
        "related_securities": ["US.QCOM"],
        "url": "https://example.com/opend",
    }

    with (
        patch.object(news_digest.news_adapter, "search_news", return_value=[opend_item]),
        patch.object(news_digest.finnhub_adapter, "company_news") as fh_news,
        patch.object(news_digest.quote_adapter, "get_snapshots", return_value=[]),
        patch.object(news_digest, "upsert_news_items", return_value=1),
        patch.object(news_digest, "delete_news_older_than", return_value=0),
        patch.object(news_digest, "get_session") as session_factory,
    ):
        session_factory.return_value = MagicMock()
        result = news_digest.refresh_stock_news(stock)

    fh_news.assert_not_called()
    assert result["fetched"] == 1
    assert result["news"][0]["url"] == "https://example.com/opend"


def test_refresh_skips_old_opend_month_day_dates(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    get_settings.cache_clear()

    fixed_now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(news_digest, "datetime", FrozenDateTime)
    monkeypatch.setattr("momo.domain.ranking.datetime", FrozenDateTime)

    stock = {
        "code": "09988",
        "market": "HK",
        "symbol": "HK.09988",
        "name": "BABA-W",
    }
    old_item = {
        "title": "Alibaba Group Target Price Raised to HK$135.00",
        "news_sub_type": "RATING",
        "source": "Dow Jones",
        "publish_time": "9/30",
        "view_count": 0,
        "related_securities": ["HK.09988"],
        "url": "https://www.moomoo.com/news/post/44042687",
    }
    recent_item = {
        "title": "Recent rating",
        "news_sub_type": "RATING",
        "source": "Dow Jones",
        "publish_time": "8/14",
        "view_count": 0,
        "related_securities": ["HK.09988"],
        "url": "https://www.moomoo.com/news/post/recent",
    }

    with (
        patch.object(
            news_digest.news_adapter,
            "search_news",
            return_value=[old_item, recent_item],
        ),
        patch.object(news_digest.quote_adapter, "get_snapshots", return_value=[]),
        patch.object(news_digest, "upsert_news_items", return_value=1) as upsert,
        patch.object(news_digest, "delete_news_older_than", return_value=0),
        patch.object(news_digest, "get_session") as session_factory,
    ):
        session_factory.return_value = MagicMock()
        result = news_digest.refresh_stock_news(stock)

    stored = upsert.call_args.args[1]
    assert [n["url"] for n in stored] == [recent_item["url"]]
    assert result["fetched"] == 1
    assert result["news"][0]["url"] == recent_item["url"]


def test_refresh_merges_and_dedupes_by_url(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    monkeypatch.setenv("FINNHUB_NEWS_LOOKBACK_DAYS", "7")
    get_settings.cache_clear()

    stock = {
        "code": "QCOM",
        "market": "US",
        "symbol": "US.QCOM",
        "name": "QUALCOMM",
    }
    shared_url = "https://example.com/shared"
    opend_item = {
        "title": "Shared from OpenD",
        "news_sub_type": "NEWS",
        "source": "OpenD",
        "publish_time": "2026-08-01 12:00:00",
        "view_count": 10,
        "related_securities": ["US.QCOM"],
        "url": shared_url,
    }
    finnhub_items = [
        {
            "title": "Shared from Finnhub",
            "news_sub_type": "NEWS",
            "source": "Finnhub",
            "publish_time": "2026-08-01 13:00:00",
            "view_count": 0,
            "related_securities": ["QCOM"],
            "url": shared_url,
        },
        {
            "title": "Finnhub only",
            "news_sub_type": "NEWS",
            "source": "Reuters",
            "publish_time": "2026-08-02 09:00:00",
            "view_count": 0,
            "related_securities": ["QCOM"],
            "url": "https://example.com/fh-only",
        },
    ]

    with (
        patch.object(news_digest.news_adapter, "search_news", return_value=[opend_item]),
        patch.object(
            news_digest.finnhub_adapter, "company_news", return_value=finnhub_items
        ) as fh_news,
        patch.object(news_digest.quote_adapter, "get_snapshots", return_value=[]),
        patch.object(news_digest, "upsert_news_items", return_value=2) as upsert,
        patch.object(news_digest, "delete_news_older_than", return_value=0) as purge,
        patch.object(news_digest, "get_session") as session_factory,
    ):
        session_factory.return_value = MagicMock()
        result = news_digest.refresh_stock_news(stock)

    fh_news.assert_called_once()
    assert result["fetched"] == 2
    urls = {n["url"] for n in result["news"]}
    assert urls == {shared_url, "https://example.com/fh-only"}
    upsert.assert_called_once()
    stored_urls = {n["url"] for n in upsert.call_args.args[1]}
    assert stored_urls == {shared_url, "https://example.com/fh-only"}
    purge.assert_called_once()


def test_refresh_soft_fails_finnhub_errors(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    get_settings.cache_clear()

    stock = {
        "code": "QCOM",
        "market": "US",
        "symbol": "US.QCOM",
        "name": "QUALCOMM",
    }
    opend_item = {
        "title": "OpenD survives",
        "news_sub_type": "NEWS",
        "source": "OpenD",
        "publish_time": "2026-08-01 12:00:00",
        "view_count": 0,
        "related_securities": [],
        "url": "https://example.com/opend-ok",
    }

    with (
        patch.object(news_digest.news_adapter, "search_news", return_value=[opend_item]),
        patch.object(
            news_digest.finnhub_adapter,
            "company_news",
            side_effect=finnhub_adapter.FinnhubError("boom"),
        ),
        patch.object(news_digest.quote_adapter, "get_snapshots", return_value=[]),
        patch.object(news_digest, "upsert_news_items", return_value=1),
        patch.object(news_digest, "delete_news_older_than", return_value=0),
        patch.object(news_digest, "get_session") as session_factory,
    ):
        session_factory.return_value = MagicMock()
        result = news_digest.refresh_stock_news(stock)

    assert result["fetched"] == 1
    assert result["news"][0]["title"] == "OpenD survives"


def test_refresh_stores_all_finnhub_not_just_top_n(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    monkeypatch.setenv("NEWS_TOP_N", "2")
    get_settings.cache_clear()

    stock = {
        "code": "QCOM",
        "market": "US",
        "symbol": "US.QCOM",
        "name": "QUALCOMM",
    }
    # High-scoring OpenD noise — would crowd out Finnhub under old top-N store.
    opend_items = [
        {
            "title": f"OpenD notice {i}",
            "news_sub_type": "NOTICE",
            "source": "SEC",
            "publish_time": "2026-08-08 12:00:00",
            "view_count": 1000,
            "related_securities": ["US.QCOM"],
            "url": f"https://example.com/opend-{i}",
        }
        for i in range(5)
    ]
    finnhub_items = [
        {
            "title": f"Finnhub story {i}",
            "news_sub_type": "NEWS",
            "source": "Yahoo",
            "publish_time": "2026-08-07 12:00:00",
            "view_count": 0,
            "related_securities": ["QCOM"],
            "url": f"https://example.com/fh-{i}",
        }
        for i in range(5)
    ]

    with (
        patch.object(news_digest.news_adapter, "search_news", return_value=opend_items),
        patch.object(
            news_digest.finnhub_adapter, "company_news", return_value=finnhub_items
        ),
        patch.object(news_digest.quote_adapter, "get_snapshots", return_value=[]),
        patch.object(news_digest, "upsert_news_items", return_value=7) as upsert,
        patch.object(news_digest, "delete_news_older_than", return_value=0),
        patch.object(news_digest, "get_session") as session_factory,
    ):
        session_factory.return_value = MagicMock()
        result = news_digest.refresh_stock_news(stock)

    stored = upsert.call_args.args[1]
    stored_urls = {n["url"] for n in stored}
    assert all(f"https://example.com/fh-{i}" in stored_urls for i in range(5))
    assert result["stored_top"] == len(stored)
    assert result["stored_top"] >= 5
    fh_stored = [n for n in stored if n["url"].startswith("https://example.com/fh-")]
    assert all(n["provider"] == "finnhub" for n in fh_stored)
    opend_stored = [n for n in stored if n["url"].startswith("https://example.com/opend-")]
    assert opend_stored
    assert all(n["provider"] == "opend" for n in opend_stored)


def test_delete_news_older_than_uses_publish_time():
    from datetime import datetime, timezone

    from momo.db.repo import delete_news_older_than

    old = MagicMock()
    old.publish_time = "2026-06-01 12:00:00"
    old.fetched_at = datetime(2026, 8, 1, 12, 0, 0)

    recent = MagicMock()
    recent.publish_time = "2026-08-01 12:00:00"
    recent.fetched_at = datetime(2026, 8, 1, 12, 0, 0)

    unparsed_old_fetch = MagicMock()
    unparsed_old_fetch.publish_time = ""
    unparsed_old_fetch.fetched_at = datetime(2026, 6, 1, 12, 0, 0)

    session = MagicMock()
    session.scalars.return_value = [old, recent, unparsed_old_fetch]

    cutoff = datetime(2026, 7, 8, tzinfo=timezone.utc)
    deleted = delete_news_older_than(session, cutoff=cutoff)

    assert deleted == 2
    session.delete.assert_any_call(old)
    session.delete.assert_any_call(unparsed_old_fetch)
    assert recent not in [c.args[0] for c in session.delete.call_args_list]
    session.commit.assert_called_once()


def test_company_news_network_error(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")
    get_settings.cache_clear()

    with patch(
        "momo.adapters.finnhub.httpx.get",
        side_effect=httpx.ConnectError("offline"),
    ):
        with pytest.raises(finnhub_adapter.FinnhubError, match="request failed"):
            finnhub_adapter.company_news(
                "QCOM", from_date="2026-08-01", to_date="2026-08-08"
            )
