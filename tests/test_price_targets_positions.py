from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from momo.db.models import Base, QuoteSnapshot
from momo.db.repo import latest_snapshot, save_snapshot
from momo.services.price_targets import (
    _attach_weights,
    _group_by_weight,
    aggregate_positions_for_targets,
    refresh_watchlist_targets,
)


def test_aggregate_includes_option_underlyings():
    rows = aggregate_positions_for_targets(
        [
            {
                "code": "US.QCOM",
                "name": "QUALCOMM",
                "qty": 10,
                "market_val": 1500,
                "average_cost": 120,
                "nominal_price": 150,
                "unrealized_pl": 300,
                "pl_ratio_avg_cost": 25,
            },
            {
                # Already held as stock — annotate, do not duplicate.
                "code": "US.QCOM260911P140000",
                "name": "QCOM 09/11/26 140 Put",
                "qty": 1,
                "market_val": 500,
            },
            {
                "code": "US.QCOM260918C150000",
                "name": "QCOM 09/18/26 150 Call",
                "qty": 2,
                "market_val": 800,
            },
            {
                # Option-only underlying.
                "code": "US.AAPL260911C200000",
                "name": "AAPL call",
                "qty": 1,
            },
            {
                # Flat option — ignore.
                "code": "US.MSFT260911C400000",
                "name": "MSFT call",
                "qty": 0,
            },
        ]
    )
    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {"US.QCOM", "US.AAPL"}
    assert not any("260911" in s or "260918" in s for s in by_symbol)

    qcom = by_symbol["US.QCOM"]
    assert qcom["qty"] == 10
    assert qcom["name"] == "QUALCOMM"
    assert qcom["option_contracts"] == 2
    assert qcom["market_val"] == 1500

    aapl = by_symbol["US.AAPL"]
    assert aapl["qty"] is None
    assert aapl["option_contracts"] == 1
    assert aapl["name"] == "AAPL"
    assert aapl["market_val"] is None


def test_aggregate_uses_resolved_stock_owner_for_hk_options():
    """HK option roots (MIU) must map to stock ticker (HK.01810), not HK.MIU."""
    rows = aggregate_positions_for_targets(
        [
            {
                "code": "HK.01810",
                "name": "XIAOMI-W",
                "qty": 3000,
                "market_val": 80000,
            },
            {
                "code": "HK.MIU260929P22000",
                "name": "MIU 260929 22.00 P",
                "qty": -1,
            },
            {
                "code": "HK.MIU260828P21000",
                "name": "MIU 260828 21.00 P",
                "qty": -1,
            },
        ],
        underlying_map={
            "HK.MIU260929P22000": "HK.01810",
            "HK.MIU260828P21000": "HK.01810",
        },
    )
    by_symbol = {r["symbol"]: r for r in rows}
    assert set(by_symbol) == {"HK.01810"}
    assert "HK.MIU" not in by_symbol
    assert by_symbol["HK.01810"]["option_contracts"] == 2
    assert by_symbol["HK.01810"]["name"] == "XIAOMI-W"
    assert by_symbol["HK.01810"]["qty"] == 3000


def test_option_only_weight_band():
    rows = aggregate_positions_for_targets(
        [
            {
                "code": "US.MSFT",
                "name": "MSFT",
                "qty": 10,
                "market_val": 4000,
                "average_cost": 300,
                "nominal_price": 400,
            },
            {
                "code": "US.QCOM260911P140000",
                "qty": 1,
            },
        ]
    )
    weighted = _attach_weights(rows)
    by_symbol = {r["symbol"]: r for r in weighted}
    assert by_symbol["US.MSFT"]["weight_band"] == "20%+ of portfolio"
    assert by_symbol["US.QCOM"]["weight_band"] == "Options only"
    assert by_symbol["US.QCOM"]["weight_pct"] is None

    groups = _group_by_weight(weighted)
    labels = [g["label"] for g in groups]
    assert "Options only" in labels
    opt_group = next(g for g in groups if g["label"] == "Options only")
    assert [i["symbol"] for i in opt_group["items"]] == ["US.QCOM"]


def test_refresh_targets_updates_quote_snapshot_last_price():
    stocks = [
        {
            "symbol": "HK.09988",
            "code": "09988",
            "name": "BABA-W",
            "market": "HK",
        }
    ]
    snap = {
        "symbol": "HK.09988",
        "stock_code": "09988",
        "last_price": 122.4,
        "change_rate": -1.5,
        "volume": 1.0,
        "turnover": 2.0,
    }

    with (
        patch(
            "momo.services.price_targets._stocks_for_targets",
            return_value=(stocks, {"source": "watchlist"}),
        ),
        patch(
            "momo.services.price_targets.quote_adapter.get_snapshots",
            return_value=[snap],
        ) as get_snaps,
        patch("momo.services.price_targets.save_snapshot") as save,
        patch(
            "momo.services.price_targets.research_adapter.get_analyst_consensus",
            return_value=None,
        ),
        patch(
            "momo.services.price_targets.research_adapter.get_institution_targets",
            return_value=[],
        ),
        patch(
            "momo.services.price_targets.replace_institution_targets",
            return_value=0,
        ),
        patch("momo.services.price_targets.get_session") as session,
    ):
        session.return_value.__enter__.return_value = object()
        result = refresh_watchlist_targets()

    get_snaps.assert_called_once_with(["HK.09988"])
    save.assert_called_once()
    stored = save.call_args.args[1]
    assert stored["symbol"] == "HK.09988"
    assert stored["last_price"] == 122.4
    assert result["ok"] is True


def test_save_snapshot_updates_existing_last_price():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    row = {
        "symbol": "HK.09988",
        "stock_code": "09988",
        "last_price": 119.9,
        "change_rate": 0.0,
        "volume": 1.0,
        "turnover": 1.0,
    }
    save_snapshot(session, row)
    save_snapshot(
        session,
        {**row, "last_price": 122.4, "change_rate": -1.5},
    )
    rows = list(session.scalars(select(QuoteSnapshot)))
    assert len(rows) == 1
    snap = latest_snapshot(session, "HK.09988")
    assert snap is not None
    assert snap.last_price == 122.4
    assert snap.change_rate == -1.5
    session.close()
