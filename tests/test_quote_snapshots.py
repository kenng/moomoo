from unittest.mock import MagicMock

import pytest

from momo.adapters.quote import fetch_market_snapshots
from momo.opend_client import OpenDError


class _Rows:
    def __init__(self, rows, empty=False):
        self._rows = rows
        self.empty = empty

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


def test_fetch_skips_symbol_without_quote_permission():
    ctx = MagicMock()
    ctx.get_market_snapshot.side_effect = [
        (
            -1,
            "No permission to get quotes for MY.6139. Please check MY Security MarketStocks quote permissions.",
        ),
        (
            0,
            _Rows(
                [
                    {
                        "code": "MY.1155",
                        "last_price": 10.5,
                        "change_rate": 0.2,
                        "volume": 1,
                        "turnover": 2,
                    }
                ]
            ),
        ),
    ]

    rows = fetch_market_snapshots(ctx, ["MY.1155", "MY.6139"], ret_ok=0)

    assert [r["symbol"] for r in rows] == ["MY.1155"]
    assert rows[0]["last_price"] == 10.5
    assert ctx.get_market_snapshot.call_args_list[0].args[0] == [
        "MY.1155",
        "MY.6139",
    ]
    assert ctx.get_market_snapshot.call_args_list[1].args[0] == ["MY.1155"]


def test_fetch_raises_other_opend_errors():
    ctx = MagicMock()
    ctx.get_market_snapshot.return_value = (-1, "timeout")
    with pytest.raises(OpenDError, match="timeout"):
        fetch_market_snapshots(ctx, ["MY.1155"], ret_ok=0)
