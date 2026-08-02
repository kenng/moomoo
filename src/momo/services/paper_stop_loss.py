from __future__ import annotations

from dataclasses import dataclass

from momo.adapters.trade import OrderRequest, TradeAdapter


@dataclass
class StopLossRule:
    symbol: str
    quantity: float
    stop_price: float


class PaperStopLossService:
    """Future job: when last price <= stop, submit a paper sell.

    Not wired to a scheduler yet — call evaluate() from a job when ready.
    """

    def __init__(self, trade: TradeAdapter | None = None):
        self.trade = trade or TradeAdapter()
        self.rules: list[StopLossRule] = []

    def add_rule(self, rule: StopLossRule) -> None:
        self.rules.append(rule)

    def evaluate(self, snapshots_by_symbol: dict[str, dict]) -> list[dict]:
        actions = []
        for rule in self.rules:
            snap = snapshots_by_symbol.get(rule.symbol) or {}
            last = snap.get("last_price")
            if last is None:
                continue
            if float(last) > rule.stop_price:
                continue
            result = self.trade.place_order(
                OrderRequest(
                    symbol=rule.symbol,
                    side="SELL",
                    quantity=rule.quantity,
                    price=float(last),
                    order_type="MARKET",
                    stop_price=rule.stop_price,
                )
            )
            actions.append(
                {
                    "symbol": rule.symbol,
                    "last_price": last,
                    "stop_price": rule.stop_price,
                    "accepted": result.accepted,
                    "message": result.message,
                    "mode": result.mode,
                }
            )
        return actions
