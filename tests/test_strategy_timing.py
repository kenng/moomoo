from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from momo.api.main import app
from momo.config import Settings
from momo.db.models import Base
from momo.db.repo import list_recent_strategy_checks
from momo.domain.strategy_timing import (
    StrategyTimingError,
    evaluate_strategy,
    iv_rank_from_history,
    metrics_from_closes,
    resolve_option_underlying,
    sma,
)
from momo.services import strategy_timing


def _metrics(**overrides) -> dict:
    base = {
        "iv_rank": 45.0,
        "rsi_6": 50.0,
        "last_price": 99.0,
        "ma20": 100.0,
        "ma50": 98.0,
        "ma20_prev5": 97.0,
        "put_call": 0.80,
        "next_earnings": date(2026, 11, 1),
        "earnings_in_14d_cleared": False,
    }
    base.update(overrides)
    return base


def test_qcom_resolves_to_us():
    stock = resolve_option_underlying("qcom")
    assert stock["symbol"] == "US.QCOM"
    assert stock["market"] == "US"
    assert resolve_option_underlying("US.AAPL")["symbol"] == "US.AAPL"
    assert resolve_option_underlying("HK.00700")["symbol"] == "HK.00700"


def test_my_ticker_is_rejected():
    try:
        resolve_option_underlying("MY.1155")
    except StrategyTimingError as exc:
        assert "US and HK only" in str(exc)
        return
    raise AssertionError("expected StrategyTimingError")


def test_empty_ticker_is_rejected():
    try:
        resolve_option_underlying("  ")
    except StrategyTimingError as exc:
        assert "empty" in str(exc)
        return
    raise AssertionError("expected StrategyTimingError")


def test_conclusion_bands():
    today = date(2026, 9, 6)
    ideal = evaluate_strategy("bull_put", "US.QCOM", _metrics(), today=today)
    assert ideal.conclusion == "Ideal"
    assert ideal.penalty == 0

    favorable = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(iv_rank=35.0, rsi_6=58.0),
        today=today,
    )
    assert favorable.penalty == 2
    assert favorable.conclusion == "Favorable"

    waiting = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(iv_rank=20.0, put_call=0.33),
        today=today,
    )
    assert waiting.penalty == 4
    assert waiting.conclusion == "Wait-and-see"


def test_sample_tape_is_avoid():
    today = date(2026, 9, 6)
    check = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(
            iv_rank=23.0,
            rsi_6=64.7,
            last_price=102.0,
            ma20=100.0,
            ma50=99.0,
            ma20_prev5=100.5,
            put_call=0.33,
            next_earnings=date(2026, 9, 12),
        ),
        today=today,
    )
    by_key = {c.key: c for c in check.conditions}
    assert by_key["iv_rank"].status == "23 — ❌"
    assert by_key["rsi_6"].status == "64.7 — ❌"
    assert by_key["price_ma20"].status == "Above — ⚠️"
    assert by_key["put_call"].status == "0.33 — ❌ (too bullish)"
    assert by_key["trend"].status == "Developing — ⚠️"
    assert by_key["catalyst"].status == "Earnings 12 Sep — ❌"
    assert check.penalty == 10
    assert check.conclusion == "Avoid"


def test_condition_edges():
    today = date(2026, 9, 6)
    check = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(
            iv_rank=40.0,
            rsi_6=54.9,
            last_price=101.0,
            ma20=100.0,
            put_call=0.70,
            ma20_prev5=99.0,
            next_earnings=date(2026, 9, 21),
        ),
        today=today,
    )
    assert all(c.verdict == "pass" for c in check.conditions)
    catalyst = [c for c in check.conditions if c.key == "catalyst"][0]
    assert catalyst.status == "No earnings in 14 days · next 21 Sep — ✅"

    unknown = evaluate_strategy("bull_put", "US.QCOM", {}, today=today)
    assert unknown.penalty == 6
    assert all(c.verdict == "warn" for c in unknown.conditions)
    assert unknown.conclusion == "Wait-and-see"


def test_catalyst_unknown_and_cleared():
    today = date(2026, 9, 6)
    unknown = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(next_earnings=None, earnings_in_14d_cleared=False),
        today=today,
    )
    catalyst = [c for c in unknown.conditions if c.key == "catalyst"][0]
    assert catalyst.verdict == "warn"

    cleared = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(next_earnings=None, earnings_in_14d_cleared=True),
        today=today,
    )
    catalyst = [c for c in cleared.conditions if c.key == "catalyst"][0]
    assert catalyst.verdict == "pass"
    assert "No earnings in 14 days" in catalyst.status


def test_trend_down_and_up():
    today = date(2026, 9, 6)
    down = evaluate_strategy(
        "bull_put", "US.QCOM", _metrics(ma20=90.0, ma50=100.0), today=today
    )
    assert [c.verdict for c in down.conditions if c.key == "trend"] == ["fail"]

    up = evaluate_strategy(
        "bull_put",
        "US.QCOM",
        _metrics(ma20=110.0, ma50=100.0, ma20_prev5=108.0),
        today=today,
    )
    assert [c.verdict for c in up.conditions if c.key == "trend"] == ["pass"]


def test_closes_feed_rsi_and_mas():
    closes = [float(i) for i in range(1, 61)]
    metrics = metrics_from_closes(closes)
    assert metrics["last_price"] == 60.0
    assert metrics["ma20"] == sma(closes, 20)
    assert metrics["ma50"] == sma(closes, 50)
    assert metrics["ma20_prev5"] == sma(closes[:-5], 20)
    assert metrics["rsi_6"] == 100.0


def test_iv_rank_from_history():
    assert iv_rank_from_history(30.0, [10.0, 20.0, 50.0]) == 50.0
    assert iv_rank_from_history(None, [10.0]) is None
    assert iv_rank_from_history(12.0, [12.0, 12.0]) == 50.0


def test_analyze_appends_history(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _session():
        return factory()

    market = _metrics()
    market["name"] = "QUALCOMM"
    with (
        patch(
            "momo.services.strategy_timing.fetch_strategy_market",
            return_value=market,
        ),
        patch("momo.services.strategy_timing.get_session", _session),
    ):
        first = strategy_timing.analyze("QCOM")
        second = strategy_timing.analyze("QCOM")

    assert first["stock"]["symbol"] == "US.QCOM"
    assert first["check"]["conclusion"] == "Ideal"
    with factory() as session:
        rows = list_recent_strategy_checks(session)
    assert len(rows) == 2
    assert rows[0].id != rows[1].id
    assert {rows[0].symbol, rows[1].symbol} == {"US.QCOM"}
    assert second["check"]["symbol"] == "US.QCOM"


def test_delete_check_removes_row(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _session():
        return factory()

    market = _metrics()
    market["name"] = "QUALCOMM"
    with (
        patch(
            "momo.services.strategy_timing.fetch_strategy_market",
            return_value=market,
        ),
        patch("momo.services.strategy_timing.get_session", _session),
    ):
        strategy_timing.analyze("QCOM")
        strategy_timing.analyze("QCOM")
        with factory() as session:
            rows = list_recent_strategy_checks(session)
        assert len(rows) == 2
        removed_id = rows[0].id
        assert strategy_timing.delete_check(removed_id) is True
        assert strategy_timing.delete_check(removed_id) is False
        with factory() as session:
            left = list_recent_strategy_checks(session)
        assert len(left) == 1
        assert left[0].id != removed_id


def _plain_settings(**overrides) -> Settings:
    values = dict(
        auth_username="",
        auth_password="",
        session_secret="test-session-secret",
        momo_ui_mode="local",
    )
    values.update(overrides)
    return Settings(**values)


def test_strategies_form_renders():
    settings = _plain_settings()
    empty = {
        "symbol": "",
        "strategy": "bull_put",
        "strategies": [{"id": "bull_put", "label": "Bull put spread"}],
        "stock": None,
        "check": None,
        "recent": [],
        "error": None,
    }
    with (
        patch("momo.api.auth.get_settings", return_value=settings),
        patch("momo.api.main.get_settings", return_value=settings),
        patch("momo.api.main.strategy_timing.get_page", return_value=empty),
    ):
        res = TestClient(app).get("/strategies")
    assert res.status_code == 200
    assert "Option timing" in res.text
    assert "Bull put spread" in res.text
    assert "Analyze" in res.text


def test_strategies_page_shows_check_table():
    settings = _plain_settings()
    page = {
        "symbol": "US.QCOM",
        "strategy": "bull_put",
        "strategies": [{"id": "bull_put", "label": "Bull put spread"}],
        "stock": {
            "symbol": "US.QCOM",
            "code": "QCOM",
            "name": "QUALCOMM",
            "market": "US",
        },
        "check": {
            "strategy": "bull_put",
            "strategy_label": "Bull put spread",
            "symbol": "US.QCOM",
            "conditions": [
                {
                    "key": "iv_rank",
                    "label": "IV Rank",
                    "threshold": "≥ 40 preferred",
                    "status": "23 — ❌",
                    "verdict": "fail",
                }
            ],
            "penalty": 10,
            "conclusion": "Avoid",
            "summary": "Too many conditions against entry.",
            "fetched_at": None,
        },
        "recent": [],
        "error": None,
    }
    with (
        patch("momo.api.auth.get_settings", return_value=settings),
        patch("momo.api.main.get_settings", return_value=settings),
        patch("momo.api.main.strategy_timing.get_page", return_value=page),
    ):
        res = TestClient(app).get("/strategies?symbol=QCOM")
    assert res.status_code == 200
    assert "Avoid" in res.text
    assert "IV Rank" in res.text
    assert "23 — ❌" in res.text
    assert "Too many conditions against entry." in res.text


def test_strategies_page_shows_delete_controls():
    settings = _plain_settings()
    page = {
        "symbol": "",
        "strategy": "bull_put",
        "strategies": [{"id": "bull_put", "label": "Bull put spread"}],
        "stock": None,
        "check": None,
        "recent": [
            {
                "id": 7,
                "symbol": "US.QCOM",
                "stock_code": "QCOM",
                "stock_name": "QUALCOMM",
                "strategy": "bull_put",
                "strategy_label": "Bull put spread",
                "conclusion": "Avoid",
                "penalty": 10,
                "fetched_at": None,
            }
        ],
        "error": None,
    }
    with (
        patch("momo.api.auth.get_settings", return_value=settings),
        patch("momo.api.main.get_settings", return_value=settings),
        patch("momo.api.main.strategy_timing.get_page", return_value=page),
    ):
        res = TestClient(app).get("/strategies")
    assert res.status_code == 200
    assert 'id="toggle-check-delete"' in res.text
    assert 'action="/strategies/checks/7/delete"' in res.text
    assert "data-confirm=" in res.text


def test_delete_strategy_check_route():
    settings = _plain_settings()
    with (
        patch("momo.api.auth.get_settings", return_value=settings),
        patch("momo.api.main.get_settings", return_value=settings),
        patch("momo.api.main.strategy_timing.delete_check", return_value=True) as delete,
    ):
        res = TestClient(app).post(
            "/strategies/checks/7/delete", follow_redirects=False
        )
    assert res.status_code == 303
    assert res.headers["location"] == "/strategies"
    delete.assert_called_once_with(7)


def test_strategies_nav_hidden_when_read_only():
    settings = _plain_settings(momo_ui_mode="cloudflare")
    with (
        patch("momo.api.auth.get_settings", return_value=settings),
        patch("momo.api.main.get_settings", return_value=settings),
        patch(
            "momo.api.main.price_targets.get_watchlist_price_targets",
            return_value={
                "items": [],
                "items_by_diff": [],
                "groups": [],
                "last_updated": None,
                "source": None,
                "trd_env": "REAL",
                "error": None,
            },
        ),
    ):
        res = TestClient(app).get("/")
    assert res.status_code == 200
    assert "/strategies" not in res.text
