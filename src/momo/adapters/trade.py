from __future__ import annotations

from dataclasses import dataclass

from momo.config import get_settings
from momo.domain.risk import RiskGuard


class TradingDisabledError(RuntimeError):
    pass


@dataclass
class OrderRequest:
    symbol: str
    side: str  # BUY / SELL
    quantity: float
    price: float | None = None
    order_type: str = "MARKET"
    stop_price: float | None = None


@dataclass
class OrderResult:
    mode: str
    accepted: bool
    message: str
    request: OrderRequest


class TradeAdapter:
    """Paper-first trade adapter. Live OpenD trade calls are hard-gated."""

    def __init__(self, risk: RiskGuard | None = None):
        self.settings = get_settings()
        self.risk = risk or RiskGuard(self.settings)
        self._paper_orders: list[OrderResult] = []

    def place_order(self, request: OrderRequest) -> OrderResult:
        decision = self.risk.check_order(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
        )
        if not decision.allowed:
            return OrderResult(
                mode=self.settings.trading_mode,
                accepted=False,
                message=decision.reason,
                request=request,
            )

        if self.settings.live_trading_enabled:
            raise TradingDisabledError(
                "Live trading path is stubbed; enable only after explicit implementation."
            )

        # Paper mode: record only
        result = OrderResult(
            mode="paper",
            accepted=True,
            message="paper_order_recorded",
            request=request,
        )
        self._paper_orders.append(result)
        self.risk.record_order()
        return result

    def list_paper_orders(self) -> list[OrderResult]:
        return list(self._paper_orders)
