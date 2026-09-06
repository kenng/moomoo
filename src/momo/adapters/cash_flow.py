"""OpenD account cash-flow queries (securities: per clearing date)."""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

from momo.adapters import orders as orders_adapter
from momo.config import get_settings
from momo.domain.options import parse_option_code
from momo.opend_client import OpenDError, trade_context

# OpenD: max 20 get_acc_cash_flow calls per 30s.
_MAX_BURST = 18
_BURST_PAUSE_SEC = 31.0

_DIVIDEND_TYPE_HINTS = ("dividend", "股息", "红利", "分派")
_REMARK_STOCK = re.compile(
    r"<([A-Z]+)\s+(\S+)\s+([^>]+)>", re.IGNORECASE
)
# e.g. "DXN (5318)" — Bursa-style name + stock code
_REMARK_STOCK_PAREN = re.compile(
    r"([A-Za-z][A-Za-z0-9.&+\-]*)\s*\((\d{3,5})\)"
)
# e.g. "… per CLMT Unit" / "per MAYBANK share"
_REMARK_PER_TICKER = re.compile(
    r"\bper\s+([A-Z]{2,12})\s+"
    r"(?:Units?|shares?|ordinary\s+shares?)\b",
    re.IGNORECASE,
)
# e.g. "CRESNDO - Second Interim…"
_REMARK_LEADING_TICKER = re.compile(r"^([A-Z]{2,12})\s*[-–—]\s+")
_REMARK_SHARES = re.compile(r"(\d+(?:\.\d+)?)\s*shares?", re.IGNORECASE)
# e.g. "of 0.60 sen per ordinary share" / "of RM0.33 per ordinary share"
_REMARK_RATE_PER = re.compile(
    r"(?:^|[\s\-])(?:of\s+)?(?P<ccy>RM|MYR)?"
    r"\s*(?P<amt>\d+(?:\.\d+)?)\s*(?P<sen>sen)?\s+per\b",
    re.IGNORECASE,
)
_CCY_MARKET = {
    "MYR": "MY",
    "HKD": "HK",
    "USD": "US",
    "SGD": "SG",
}
_TICKER_STOPWORDS = frozenset(
    {
        "ORDINARY",
        "SHARE",
        "SHARES",
        "UNIT",
        "UNITS",
        "STOCK",
        "THE",
        "AND",
        "INTERIM",
        "FINAL",
        "SINGLE",
        "TIER",
        "SPECIAL",
        "GROSS",
        "NET",
        "CASH",
        "FUND",
        "INCOME",
        "FIRST",
        "SECOND",
        "THIRD",
        "FOURTH",
    }
)


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
            out[day] = _cash_flow_from_ctx(ctx, ft, env, acc_id, day, retries=1)
    return out


def account_supports_cash_flow(exc: Exception) -> bool:
    """False when OpenD rejects cash-flow queries for this account type."""
    text = str(exc).lower()
    return "does not support" not in text


def _cash_flow_from_ctx(ctx, ft, env, acc_id: int, clearing_date: str, retries: int = 0) -> list[dict]:
    attempt = 0
    while True:
        ret, data = ctx.get_acc_cash_flow(
            clearing_date=clearing_date, trd_env=env, acc_id=acc_id
        )
        if ret == ft.RET_OK:
            break
        msg = str(data)
        if retries and attempt < retries and "high frequency" in msg.lower():
            attempt += 1
            time.sleep(_BURST_PAUSE_SEC)
            continue
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
    else:
        pm = _REMARK_STOCK_PAREN.search(remark)
        if pm:
            stock_name = pm.group(1).strip()
            local = pm.group(2).strip()
            exchange = "MY"
            stock_code = f"MY.{local}"
        else:
            tm = _REMARK_PER_TICKER.search(remark) or _REMARK_LEADING_TICKER.search(
                remark
            )
            if tm:
                name = tm.group(1).upper()
                if name not in _TICKER_STOPWORDS:
                    stock_name = name
                    exchange = "MY"
                    stock_code = _resolve_my_symbol(stock_name)

    shares = None
    sm = _REMARK_SHARES.search(remark)
    if sm:
        try:
            shares = float(sm.group(1))
        except ValueError:
            shares = None
    if shares is None:
        shares = _shares_from_rate(remark, row.get("cashflow_amount"))

    return {
        **row,
        "exchange": exchange,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "shares": shares,
    }


def attach_holding(row: dict, positions: list[dict]) -> dict:
    """Fill stock from a unique open holding when the remark omitted the ticker."""
    out = dict(row)
    if out.get("stock_code") or out.get("stock_name"):
        return out
    remark = str(out.get("cashflow_remark") or "")
    if not remark or "fund cash dividend" in remark.lower():
        return out

    rate = _dividend_rate(remark)
    shares = out.get("shares")
    amount = out.get("cashflow_amount")
    if shares is None and rate is None:
        return out

    matches = [
        p
        for p in positions
        if _holding_matches(out, p, shares=shares, rate=rate, amount=amount)
    ]
    if len(matches) != 1:
        return out

    pos = matches[0]
    code = str(pos.get("code") or "").strip()
    name = str(pos.get("name") or "").strip()
    if not code and not name:
        return out
    out["stock_code"] = code
    out["stock_name"] = name
    qty = _float(pos.get("qty"))
    if out.get("shares") is None and qty:
        out["shares"] = qty
    return out


def _dividend_rate(remark: str) -> float | None:
    hits = list(_REMARK_RATE_PER.finditer(remark or ""))
    if len(hits) != 1:
        return None
    try:
        amt = float(hits[0].group("amt"))
    except (TypeError, ValueError):
        return None
    if amt <= 0:
        return None
    if hits[0].group("sen"):
        return amt / 100.0
    return amt


def _shares_from_rate(remark: str, amount) -> float | None:
    rate = _dividend_rate(remark)
    cash = _float(amount)
    if not rate or cash is None or cash <= 0:
        return None
    shares = cash / rate
    rounded = round(shares)
    if rounded > 0 and abs(shares - rounded) <= 0.02:
        return float(rounded)
    return None


def _holding_matches(
    row: dict,
    pos: dict,
    *,
    shares: float | None,
    rate: float | None,
    amount,
) -> bool:
    code = str(pos.get("code") or "").strip()
    if not code or parse_option_code(code):
        return False
    qty = _float(pos.get("qty")) or 0.0
    if qty <= 0:
        return False

    row_acc = str(row.get("acc_id") or "")
    pos_acc = str(pos.get("acc_id") or "")
    if row_acc and pos_acc and row_acc != pos_acc:
        return False

    market = _CCY_MARKET.get(str(row.get("currency") or "").upper())
    if market and not code.upper().startswith(f"{market}."):
        return False

    if shares is not None and abs(qty - shares) <= 0.51:
        return True
    cash = _float(amount)
    if rate and cash is not None and abs(qty * rate - cash) <= 0.05:
        return True
    return False


def _resolve_my_symbol(ticker: str) -> str:
    """Map a Bursa short name to MY.<code> via watchlist when possible."""
    name = (ticker or "").strip().upper()
    if not name:
        return ""
    try:
        from momo.watchlist import load_watchlist

        for stock in load_watchlist():
            if stock.get("market") == "MY" and str(stock.get("name") or "").upper() == name:
                return stock["symbol"]
    except Exception:
        pass
    return f"MY.{name}"


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


def parse_month_start(value: str | None) -> date | None:
    """First day of YYYY-MM (or YYYY-MM-DD month)."""
    month = _parse_year_month(value)
    if not month:
        return None
    year, mon = month
    return date(year, mon, 1)


def parse_month_end(value: str | None) -> date | None:
    """Last day of YYYY-MM (or YYYY-MM-DD month)."""
    month = _parse_year_month(value)
    if not month:
        return None
    year, mon = month
    if mon == 12:
        return date(year, 12, 31)
    return date(year, mon + 1, 1) - timedelta(days=1)


def _parse_year_month(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    text = value.strip()
    for fmt, size in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            dt = datetime.strptime(text[:size], fmt)
            return dt.year, dt.month
        except ValueError:
            continue
    return None


def list_accounts(trd_env: str | None = None) -> list[dict]:
    return orders_adapter.list_accounts(trd_env=trd_env)


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
