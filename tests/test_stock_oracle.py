from unittest.mock import patch

import httpx

from momo.adapters.stock_oracle import get_oracle_valuation, parse_oracle_overview
from momo.cli import main
from momo.services import stock_oracle


SAMPLE_OVERVIEW = "Wide Moat OracleValue™ USD 208.69 20.56% Undervalued"


def test_parse_wide_moat_undervalued():
    parsed = parse_oracle_overview(SAMPLE_OVERVIEW)
    assert parsed == {
        "moat": "wide",
        "value": 208.69,
        "currency": "USD",
        "assess_pct": -20.56,
    }


def test_parse_html_and_overvalued():
    html = """
    <div class="MuiStack-root css-klawuc">
      Narrow Moat OracleValue&trade; USD 1,234.50 12.3% Overvalued
    </div>
    """
    parsed = parse_oracle_overview(html)
    assert parsed["moat"] == "narrow"
    assert parsed["value"] == 1234.5
    assert parsed["assess_pct"] == 12.3


def test_parse_no_moat_fair_value():
    parsed = parse_oracle_overview("No Moat OracleValue USD 10.00 0.00% Fairly Valued")
    assert parsed["moat"] == "none"
    assert parsed["assess_pct"] == 0.0


def test_get_oracle_valuation_skips_non_us():
    with patch("momo.adapters.stock_oracle.httpx.get") as get:
        assert get_oracle_valuation("HK.09988") is None
        assert get_oracle_valuation("MY.1155") is None
        get.assert_not_called()


def test_get_oracle_valuation_fetches_us():
    request = httpx.Request("GET", "https://app.stockoracle.com/stock-details/BABA/overview")
    response = httpx.Response(200, text=f"<html>{SAMPLE_OVERVIEW}</html>", request=request)
    with patch("momo.adapters.stock_oracle.httpx.get", return_value=response) as get:
        row = get_oracle_valuation("US.BABA")
    assert row["symbol"] == "US.BABA"
    assert row["value"] == 208.69
    assert row["assess_pct"] == -20.56
    get.assert_called_once()
    assert "BABA" in get.call_args.args[0]


def test_refresh_oracle_fetches_us_only():
    stocks = [
        {"symbol": "US.BABA", "code": "BABA", "name": "Alibaba", "market": "US"},
        {"symbol": "HK.09988", "code": "09988", "name": "Alibaba", "market": "HK"},
        {"symbol": "MY.1155", "code": "1155", "name": "Maybank", "market": "MY"},
    ]
    fetched: list[str] = []

    def fake_get(symbol: str):
        fetched.append(symbol)
        return {
            "symbol": symbol,
            "moat": "wide",
            "value": 1.0,
            "currency": "USD",
            "assess_pct": -1.0,
        }

    with (
        patch(
            "momo.services.stock_oracle._stocks_for_targets",
            return_value=(stocks, {"source": "positions"}),
        ),
        patch("momo.services.stock_oracle.get_oracle_valuation", side_effect=fake_get),
        patch("momo.services.stock_oracle.upsert_stock_oracle_valuation") as upsert,
        patch("momo.services.stock_oracle.get_session") as session,
    ):
        session.return_value.__enter__.return_value = object()
        result = stock_oracle.refresh_watchlist_oracle()

    assert fetched == ["US.BABA"]
    assert result["refreshed"] == 1
    assert result["skipped"] == 2
    assert result["symbols"] == 1
    assert result["ok"] is True
    upsert.assert_called_once()


def test_cli_targets_oracle_does_not_refresh_analyst_targets(capsys):
    oracle = {
        "ok": True,
        "refreshed": 1,
        "skipped": 2,
        "errors": [],
        "source": "positions",
        "symbols": 1,
    }

    def boom():
        raise AssertionError("analyst targets must not run")

    with (
        patch(
            "momo.services.stock_oracle.refresh_watchlist_oracle",
            return_value=oracle,
        ),
        patch(
            "momo.services.price_targets.refresh_watchlist_targets",
            side_effect=boom,
        ),
    ):
        assert main(["refresh", "--targets", "--oracle", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"refreshed": 1' in out


def test_cli_oracle_requires_targets(capsys):
    assert main(["refresh", "--oracle"]) == 2
    err = capsys.readouterr().err
    assert "momo refresh --targets --oracle" in err
