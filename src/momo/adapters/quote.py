from __future__ import annotations

import logging
import re

from momo.config import bare_code
from momo.domain.options import parse_option_code
from momo.opend_client import OpenDError, quote_context

logger = logging.getLogger(__name__)

_NO_QUOTE_PERM = re.compile(
    r"No permission to get quotes for ([A-Za-z]{2}\.[A-Za-z0-9]+)",
    re.I,
)


def _permission_denied_symbol(message: str) -> str | None:
    match = _NO_QUOTE_PERM.search(message or "")
    if not match:
        return None
    return match.group(1).strip().upper()


def _snapshot_rows(data) -> list[dict]:
    if data is None or getattr(data, "empty", True):
        return []
    rows = []
    for _, row in data.iterrows():
        code = str(row.get("code") or "")
        rows.append(
            {
                "symbol": code,
                "stock_code": bare_code(code),
                "last_price": _float(row.get("last_price")),
                "change_rate": _float(row.get("change_rate")),
                "volume": _float(row.get("volume")),
                "turnover": _float(row.get("turnover")),
            }
        )
    return rows


def fetch_market_snapshots(ctx, symbols: list[str], *, ret_ok) -> list[dict]:
    """Fetch snapshots, dropping symbols OpenD has no quote permission for."""
    remaining = list(dict.fromkeys(s for s in symbols if s))
    while remaining:
        ret, data = ctx.get_market_snapshot(remaining)
        if ret == ret_ok:
            return _snapshot_rows(data)
        denied = _permission_denied_symbol(str(data))
        next_remaining = (
            [s for s in remaining if s.upper() != denied] if denied else remaining
        )
        if denied and len(next_remaining) < len(remaining):
            logger.warning("skipping snapshot for %s: no quote permission", denied)
            remaining = next_remaining
            continue
        raise OpenDError(f"get_market_snapshot failed: {data}")
    return []


def get_snapshots(symbols: list[str]) -> list[dict]:
    remaining = list(dict.fromkeys(s for s in symbols if s))
    if not remaining:
        return []

    import moomoo as ft

    with quote_context() as ctx:
        return fetch_market_snapshots(ctx, remaining, ret_ok=ft.RET_OK)


def resolve_option_underlyings(option_codes: list[str]) -> dict[str, str]:
    """Map option contract codes → underlying stock symbols via stock_owner."""
    if not option_codes:
        return {}

    import moomoo as ft

    market_enum = {
        "HK": ft.Market.HK,
        "US": ft.Market.US,
        "SG": ft.Market.SG,
        "JP": ft.Market.JP,
    }
    by_market: dict[str, list[str]] = {}
    for raw in option_codes:
        code = (raw or "").strip().upper()
        info = parse_option_code(code)
        if info is None:
            continue
        by_market.setdefault(info.market, []).append(code)

    out: dict[str, str] = {}
    with quote_context() as ctx:
        for market, codes in by_market.items():
            mkt = market_enum.get(market)
            if mkt is None:
                continue
            # Dedupe while preserving order.
            unique = list(dict.fromkeys(codes))
            ret, data = ctx.get_stock_basicinfo(
                mkt, ft.SecurityType.DRVT, code_list=unique
            )
            if ret != ft.RET_OK:
                raise OpenDError(f"get_stock_basicinfo failed: {data}")
            if data is None or getattr(data, "empty", True):
                continue
            for _, row in data.iterrows():
                code = str(row.get("code") or "").strip().upper()
                owner = str(row.get("stock_owner") or "").strip().upper()
                if code and owner:
                    out[code] = owner
    return out


def _float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
