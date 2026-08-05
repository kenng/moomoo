"""OpenD account cash-flow queries (securities: per clearing date)."""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from momo.adapters import orders as orders_adapter
from momo.config import get_settings
from momo.opend_client import OpenDError, trade_context

# OpenD: max 20 get_acc_cash_flow calls per 30s.
_MAX_BURST = 18
_BURST_PAUSE_SEC = 31.0

_DIVIDEND_TYPE_HINTS = ("dividend", "股息", "红利", "分派")
_REMARK_STOCK = re.compile(
    r"<([A-Z]+)\s+(\S+)\s+([^>]+)>", re.IGNORECASE
)
_REMARK_SHARES = re.compile(r"(\d+(?:\.\d+)?)\s*shares?", re.IGNORECASE)


def fetch_cash_flow_day(
    *,
    clearing_date: str,
    acc_id: int,
    trd_env: str | None = None,
) -> list[dict]:
    """All cash-flow rows for one account on one clearing date."""
    import moomoo as ft

    settings = get_settings()
    env = _trd_env(ft, trd_env or settings.orders_trd_env)
    with trade_context() as ctx:
        return _cash_flow_from_ctx(ctx, ft, env, acc_id, clearing_date)


def fetch_cash_flow_days(
    *,
    clearing_dates: list[str],
    acc_id: int,
    trd_env: str | None = None,
) -> dict[str, list[dict]]:
    """Fetch many clearing dates under the OpenD rate limit. Returns date → rows."""
    import moomoo as ft

    settings = get_settings()
    env = _trd_env(ft, trd_env or settings.orders_trd_env)
    out: dict[str, list[dict]] = {}
    if not clearing_dates:
        return out

    with trade_context() as ctx:
        for i, day in enumerate(clearing_dates):
            if i and i % _MAX_BURST == 0:
                time.sleep(_BURST_PAUSE_SEC)
            out[day] = _cash_flow_from_ctx(ctx, ft, env, acc_id, day)
    return out


def is_dividend_row(row: dict) -> bool:
    blob = f"{row.get('cashflow_type') or ''} {row.get('cashflow_remark') or ''}".lower()
    return any(h in blob for h in _DIVIDEND_TYPE_HINTS)


def enrich_dividend(row: dict) -> dict:
    """Parse stock code / name / shares from cashflow_remark when present."""
    remark = str(row.get("cashflow_remark") or "")
    stock_code = ""
    stock_name = ""
    exchange = ""
    m = _REMARK_STOCK.search(remark)
    if m:
        exchange = m.group(1).upper()
        local = m.group(2).strip()
        stock_name = m.group(3).strip()
        # Map common exchange tags to OpenD market prefixes.
        market = {
            "SEHK": "HK",
            "HK": "HK",
            "NYSE": "US",
            "NASDAQ": "US",
            "US": "US",
            "SGX": "SG",
            "BURSA": "MY",
            "MY": "MY",
            "KLSE": "MY",
        }.get(exchange, exchange)
        if market == "HK" and local.isdigit():
            local = local.zfill(5)
        stock_code = f"{market}.{local}" if market else local

    shares = None
    sm = _REMARK_SHARES.search(remark)
    if sm:
        try:
            shares = float(sm.group(1))
        except ValueError:
            shares = None

    return {
        **row,
        "exchange": exchange,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "shares": shares,
    }


def weekday_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def list_accounts(trd_env: str | None = None) -> list[dict]:
    return orders_adapter.list_accounts(trd_env=trd_env)


def _cash_flow_from_ctx(ctx, ft, env, acc_id: int, clearing_date: str) -> list[dict]:
    ret, data = ctx.get_acc_cash_flow(
        clearing_date=clearing_date, trd_env=env, acc_id=acc_id
    )
    if ret != ft.RET_OK:
        raise OpenDError(f"get_acc_cash_flow failed ({clearing_date}): {data}")
    if data is None or getattr(data, "empty", True):
        return []

    rows = []
    for _, row in data.iterrows():
        cashflow_id = str(row.get("cashflow_id") or "")
        if not cashflow_id:
            continue
        rows.append(
            {
                "acc_id": acc_id,
                "cashflow_id": cashflow_id,
                "clearing_date": str(row.get("clearing_date") or clearing_date),
                "settlement_date": _na_str(row.get("settlement_date")),
                "currency": str(row.get("currency") or ""),
                "cashflow_type": _enum_name(row.get("cashflow_type")),
                "cashflow_direction": _enum_name(row.get("cashflow_direction")),
                "cashflow_amount": _float(row.get("cashflow_amount")),
                "cashflow_remark": str(row.get("cashflow_remark") or ""),
                "create_time": _na_str(row.get("create_time")),
            }
        )
    return rows


def _trd_env(ft, name: str):
    key = (name or "SIMULATE").strip().upper()
    return getattr(ft.TrdEnv, key, ft.TrdEnv.SIMULATE)


def _enum_name(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "name"):
        return str(value.name)
    text = str(value).strip()
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in ("", "N/A", "NONE", "NAN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _na_str(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.upper() in ("N/A", "NONE", "NAN"):
        return ""
    return text
