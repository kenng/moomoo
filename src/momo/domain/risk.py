from __future__ import annotations

from dataclasses import dataclass

from momo.config import Settings, get_settings
from momo.watchlist import load_watchlist


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class RiskGuard:
    """Hard guardrails for future order placement (paper/live)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        kill_switch: bool = False,
        max_order_value: float = 5000.0,
        max_daily_orders: int = 20,
    ):
        self.settings = settings or get_settings()
        self.kill_switch = kill_switch
        self.max_order_value = max_order_value
        self.max_daily_orders = max_daily_orders
        self._orders_today = 0

    def whitelist(self) -> set[str]:
        return {s["symbol"] for s in load_watchlist()}

    def check_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None,
    ) -> RiskDecision:
        if self.kill_switch:
            return RiskDecision(False, "kill_switch_enabled")

        if self.settings.trading_mode == "live" and not self.settings.allow_live_trading:
            return RiskDecision(False, "live_trading_not_allowed")

        if symbol not in self.whitelist():
            return RiskDecision(False, "symbol_not_in_watchlist")

        if quantity <= 0:
            return RiskDecision(False, "invalid_quantity")

        if price is not None and price * quantity > self.max_order_value:
            return RiskDecision(False, "max_order_value_exceeded")

        if self._orders_today >= self.max_daily_orders:
            return RiskDecision(False, "max_daily_orders_exceeded")

        return RiskDecision(True, "ok")

    def record_order(self) -> None:
        self._orders_today += 1
