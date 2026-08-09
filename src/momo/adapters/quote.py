from __future__ import annotations

from momo.config import bare_code
from momo.domain.options import parse_option_code
from momo.opend_client import OpenDError, quote_context


def get_snapshots(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []

    import moomoo as ft

    with quote_context() as ctx:
        ret, data = ctx.get_market_snapshot(symbols)
        if ret != ft.RET_OK:
            raise OpenDError(f"get_market_snapshot failed: {data}")
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
