from __future__ import annotations

import json

from momo.adapters.strategy_market import fetch_strategy_market
from momo.config import get_settings
from momo.db.repo import (
    delete_strategy_check,
    list_recent_strategy_checks,
    save_strategy_check,
)
from momo.db.session import get_session
from momo.domain.strategy_timing import (
    STRATEGY_LABELS,
    StrategyTimingError,
    evaluate_strategy,
    resolve_option_underlying,
)
from momo.opend_client import OpenDError


def listed_strategies() -> list[dict]:
    return [
        {"id": key, "label": label} for key, label in STRATEGY_LABELS.items()
    ]


def _row_to_recent(row) -> dict:
    try:
        conditions = json.loads(row.conditions_json or "[]")
    except json.JSONDecodeError:
        conditions = []
    return {
        "id": row.id,
        "symbol": row.symbol,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "strategy": row.strategy,
        "strategy_label": STRATEGY_LABELS.get(row.strategy, row.strategy),
        "conclusion": row.conclusion,
        "penalty": row.penalty,
        "conditions": conditions,
        "fetched_at": row.fetched_at,
    }


def recent_checks(limit: int = 20) -> list[dict]:
    with get_session() as session:
        return [_row_to_recent(row) for row in list_recent_strategy_checks(session, limit)]


def delete_check(check_id: int) -> bool:
    with get_session() as session:
        return delete_strategy_check(session, check_id)


def analyze(symbol_input: str, strategy: str = "bull_put") -> dict:
    stock = resolve_option_underlying(symbol_input)
    metrics = fetch_strategy_market(stock["symbol"])
    if metrics.get("name"):
        stock = {**stock, "name": metrics["name"]}
    check = evaluate_strategy(strategy, stock["symbol"], metrics)
    payload = check.as_dict()
    with get_session() as session:
        row = save_strategy_check(
            session,
            {
                "symbol": stock["symbol"],
                "stock_code": stock["code"],
                "stock_name": stock.get("name") or stock["code"],
                "strategy": check.strategy,
                "conclusion": check.conclusion,
                "penalty": check.penalty,
                "conditions_json": json.dumps(payload["conditions"]),
            },
        )
    payload["fetched_at"] = row.fetched_at
    return {"stock": stock, "check": payload}


def get_page(symbol: str | None = None, strategy: str = "bull_put") -> dict:
    strategy = (strategy or "bull_put").strip() or "bull_put"
    ticker = (symbol or "").strip()
    page = {
        "symbol": ticker,
        "strategy": strategy,
        "strategies": listed_strategies(),
        "stock": None,
        "check": None,
        "recent": recent_checks(),
        "error": None,
    }
    if not ticker:
        return page
    if get_settings().read_only_ui:
        page["error"] = "Analyze needs a local OpenD session"
        return page
    try:
        result = analyze(ticker, strategy)
    except StrategyTimingError as exc:
        page["error"] = str(exc)
        return page
    except OpenDError as exc:
        page["error"] = str(exc)
        return page
    page["stock"] = result["stock"]
    page["check"] = result["check"]
    page["symbol"] = result["stock"]["symbol"]
    page["recent"] = recent_checks()
    return page
