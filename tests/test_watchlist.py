from pathlib import Path

from momo.watchlist import (
    load_watchlist,
    save_watchlist,
    stock_etf_holdings,
    sync_watchlist_from_positions,
)


def test_stock_etf_holdings_drops_option_only():
    rows = stock_etf_holdings(
        [
            {
                "symbol": "US.QCOM",
                "code": "QCOM",
                "name": "QUALCOMM",
                "market": "US",
                "qty": 10,
            },
            {
                "symbol": "US.AAPL",
                "code": "AAPL",
                "name": "AAPL",
                "market": "US",
                "qty": None,
                "option_contracts": 1,
            },
            {
                "symbol": "HK.09988",
                "code": "09988",
                "name": "BABA-W",
                "market": "HK",
                "qty": 100,
            },
        ]
    )
    assert [r["symbol"] for r in rows] == ["HK.09988", "US.QCOM"]


def test_save_and_load_preserves_hk_leading_zeros(tmp_path: Path):
    path = tmp_path / "watchlist.yaml"
    save_watchlist(
        [
            {
                "code": "09988",
                "market": "HK",
                "name": "BABA-W",
            },
            {
                "code": "QCOM",
                "market": "US",
                "name": "QUALCOMM",
            },
        ],
        path=path,
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Synced from live moomoo positions")
    assert 'code: "09988"' in text

    loaded = load_watchlist(path)
    assert loaded[0]["symbol"] == "HK.09988"
    assert loaded[0]["code"] == "09988"
    assert loaded[1]["symbol"] == "US.QCOM"


def test_sync_skips_empty_holdings(tmp_path: Path):
    path = tmp_path / "watchlist.yaml"
    path.write_text(
        'stocks:\n  - code: "QCOM"\n    market: US\n    name: QUALCOMM\n',
        encoding="utf-8",
    )
    result = sync_watchlist_from_positions(
        [
            {
                "symbol": "US.AAPL",
                "code": "AAPL",
                "qty": None,
                "option_contracts": 1,
            }
        ],
        path=path,
    )
    assert result["synced"] is False
    assert result["skipped"] == "no_stock_holdings"
    assert "QCOM" in path.read_text(encoding="utf-8")


def test_sync_writes_stock_holdings(tmp_path: Path):
    path = tmp_path / "watchlist.yaml"
    result = sync_watchlist_from_positions(
        [
            {
                "symbol": "US.AVGO",
                "code": "AVGO",
                "name": "Broadcom",
                "market": "US",
                "qty": 5,
            },
            {
                "symbol": "US.AAPL",
                "code": "AAPL",
                "qty": None,
                "option_contracts": 2,
            },
        ],
        path=path,
    )
    assert result == {
        "ok": True,
        "synced": True,
        "symbols": 1,
        "skipped": None,
    }
    loaded = load_watchlist(path)
    assert [s["symbol"] for s in loaded] == ["US.AVGO"]
