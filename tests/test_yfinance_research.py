from types import SimpleNamespace

import pandas as pd

from momo.adapters import yfinance_research as yf_research


def test_to_yahoo_symbol_my():
    assert yf_research.to_yahoo_symbol("MY.1155") == "1155.KL"
    assert yf_research.to_yahoo_symbol("my.5296") == "5296.KL"


def test_to_yahoo_symbol_rejects_non_my():
    try:
        yf_research.to_yahoo_symbol("US.AAPL")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "MY" in str(exc)


def test_rating_from_yahoo_key_and_mean():
    assert yf_research._rating_from_yahoo("buy", None) == 4
    assert yf_research._rating_from_yahoo("strong_buy", 1.2) == 5
    assert yf_research._rating_from_yahoo(None, 2.2) == 4  # 6-2.2 → 4
    assert yf_research._rating_from_yahoo(None, 4.8) == 1


def test_get_analyst_consensus_maps_yahoo(monkeypatch):
    summary = pd.DataFrame(
        [
            {
                "period": "0m",
                "strongBuy": 1,
                "buy": 7,
                "hold": 9,
                "sell": 0,
                "strongSell": 1,
            }
        ]
    )

    class FakeTicker:
        analyst_price_targets = {
            "current": 10.68,
            "high": 15.0,
            "low": 11.0,
            "mean": 11.84,
            "median": 11.5,
        }
        info = {
            "numberOfAnalystOpinions": 19,
            "recommendationKey": "buy",
            "recommendationMean": 2.22,
        }
        recommendations_summary = summary

    monkeypatch.setattr(
        yf_research,
        "to_yahoo_symbol",
        lambda symbol: "1155.KL",
    )

    import sys

    fake_yf = SimpleNamespace(Ticker=lambda _sym: FakeTicker())
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    # Force re-import path uses sys.modules["yfinance"]
    result = yf_research.get_analyst_consensus("MY.1155")
    assert result is not None
    assert result["symbol"] == "MY.1155"
    assert result["highest"] == 15.0
    assert result["average"] == 11.84
    assert result["lowest"] == 11.0
    assert result["total_analysts"] == 19
    assert result["rating"] == 4
    assert result["rating_label"] == "Buy"
    # 1+7+9+0+1 = 18 → percentages of recommendation summary
    assert result["strong_buy"] == round(1 / 18 * 100, 2)
    assert result["buy"] == round(7 / 18 * 100, 2)
    assert result["hold"] == round(9 / 18 * 100, 2)
    assert result["underperform"] == 0.0
    assert result["sell"] == round(1 / 18 * 100, 2)


def test_get_institution_targets_empty_for_my():
    assert yf_research.get_institution_targets("MY.1155") == []
