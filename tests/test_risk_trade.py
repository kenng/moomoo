from momo.adapters.trade import OrderRequest, TradeAdapter
from momo.domain.risk import RiskGuard
from momo.services.paper_stop_loss import PaperStopLossService, StopLossRule


def test_risk_blocks_non_watchlist_symbol():
    risk = RiskGuard()
    decision = risk.check_order(
        symbol="MY.9999",
        side="SELL",
        quantity=100,
        price=1.0,
    )
    assert decision.allowed is False
    assert decision.reason == "symbol_not_in_watchlist"


def test_paper_stop_loss_triggers_sell():
    trade = TradeAdapter(risk=RiskGuard())
    svc = PaperStopLossService(trade=trade)
    svc.add_rule(StopLossRule(symbol="MY.1155", quantity=100, stop_price=10.0))
    actions = svc.evaluate({"MY.1155": {"last_price": 9.5}})
    assert len(actions) == 1
    assert actions[0]["accepted"] is True
    assert actions[0]["mode"] == "paper"
    assert len(trade.list_paper_orders()) == 1


def test_paper_order_request_shape():
    trade = TradeAdapter(risk=RiskGuard())
    result = trade.place_order(
        OrderRequest(symbol="MY.1155", side="SELL", quantity=10, price=9.0)
    )
    assert result.accepted is True
    assert result.message == "paper_order_recorded"
