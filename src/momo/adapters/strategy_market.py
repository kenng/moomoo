"""OpenD market data for option-strategy timing checks."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from momo.config import bare_code
from momo.domain.strategy_timing import iv_rank_from_history, metrics_from_closes
from momo.opend_client import OpenDError, quote_context

logger = logging.getLogger(__name__)

_KLINE_BARS = 80
_VOL_LOOKBACK_DAYS = 364


def _float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _closes_from_kline(data) -> list[float]:
    if data is None or getattr(data, "empty", True):
        return []
    closes: list[float] = []
    for _, row in data.iterrows():
        close = _float(row.get("close"))
        if close is not None:
            closes.append(close)
    return closes


def _daily_closes(ctx, ft, symbol: str) -> list[float]:
    ret, msg = ctx.subscribe([symbol], [ft.SubType.K_DAY])
    if ret == ft.RET_OK:
        ret, data = ctx.get_cur_kline(
            symbol, _KLINE_BARS, ft.KLType.K_DAY, ft.AuType.QFQ
        )
        if ret == ft.RET_OK:
            return _closes_from_kline(data)
        logger.warning("get_cur_kline failed for %s: %s", symbol, data)
    else:
        logger.warning("subscribe K_DAY failed for %s: %s", symbol, msg)

    ret, data, _page = ctx.request_history_kline(
        symbol,
        ktype=ft.KLType.K_DAY,
        autype=ft.AuType.QFQ,
        max_count=_KLINE_BARS,
    )
    if ret != ft.RET_OK:
        raise OpenDError(f"request_history_kline failed for {symbol}: {data}")
    return _closes_from_kline(data)


def _overview_metrics(ctx, ft, symbol: str) -> dict:
    ret, data = ctx.get_option_underlying_overview([symbol])
    if ret != ft.RET_OK:
        raise OpenDError(f"get_option_underlying_overview failed: {data}")
    if data is None or getattr(data, "empty", True):
        return {}
    row = data.iloc[0]
    put_vol = _float(row.get("put_volume"))
    call_vol = _float(row.get("call_volume"))
    put_call = None
    if put_vol is not None and call_vol not in (None, 0):
        put_call = put_vol / call_vol
    name = str(row.get("name") or "").strip()
    return {
        "name": name,
        "iv_rank": _float(row.get("iv_rank")),
        "iv": _float(row.get("iv")),
        "put_call": put_call,
    }


def _iv_rank_from_series(ctx, ft, symbol: str, current: float | None) -> float | None:
    end = date.today()
    begin = end - timedelta(days=_VOL_LOOKBACK_DAYS)
    rows = []
    page_req_key = None
    while True:
        ret, data, page_req_key = ctx.get_option_underlying_his_volatility(
            symbol,
            begin_time=begin.isoformat(),
            end_time=end.isoformat(),
            page_req_key=page_req_key,
        )
        if ret != ft.RET_OK:
            raise OpenDError(
                f"get_option_underlying_his_volatility failed: {data}"
            )
        if data is not None and not getattr(data, "empty", True):
            rows.append(data)
        if page_req_key is None:
            break
    if not rows:
        return None
    import pandas as pd

    frame = pd.concat(rows, ignore_index=True)
    history = [
        iv
        for iv in (_float(v) for v in frame.get("iv", []))
        if iv is not None
    ]
    latest = current if current is not None else (history[-1] if history else None)
    return iv_rank_from_history(latest, history)


def _latest_put_call(ctx, ft, symbol: str) -> float | None:
    end = date.today()
    begin = end - timedelta(days=14)
    ret, data, _page = ctx.get_option_underlying_his_statistic(
        symbol,
        begin_time=begin.isoformat(),
        end_time=end.isoformat(),
    )
    if ret != ft.RET_OK:
        raise OpenDError(f"get_option_underlying_his_statistic failed: {data}")
    if data is None or getattr(data, "empty", True):
        return None
    row = data.iloc[-1]
    ratio = _float(row.get("put_call_volume_ratio"))
    if ratio is not None:
        return ratio
    put_vol = _float(row.get("put_volume"))
    call_vol = _float(row.get("call_volume"))
    if put_vol is not None and call_vol not in (None, 0):
        return put_vol / call_vol
    return None


def _parse_day(value) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("-", "None", "nan"):
        return None
    if " " in text:
        text = text.split(" ", 1)[0]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _future_earnings_from_history(ctx, ft, symbol: str, today: date) -> date | None:
    api = getattr(ctx, "get_financials_earnings_price_history", None)
    if not callable(api):
        return None
    ret, data = api(symbol)
    if ret != ft.RET_OK:
        raise OpenDError(f"get_financials_earnings_price_history failed: {data}")
    if data is None or getattr(data, "empty", True):
        return None
    seen: set[date] = set()
    for _, row in data.iterrows():
        when = _parse_day(row.get("pub_trading_day_str") or row.get("pub_trading_day"))
        if when is not None and when >= today:
            seen.add(when)
    return min(seen) if seen else None


def _symbol_matches(row_code: str, symbol: str) -> bool:
    left = (row_code or "").strip().upper()
    right = symbol.strip().upper()
    if not left or not right:
        return False
    return left == right or bare_code(left) == bare_code(right)


# OpenD calendar windows are short; scan ~90 days so next earnings can surface
# even when the 14-day catalyst window is clear.
_CALENDAR_LOOKAHEAD_WEEKS = 13


def _calendar_hits(
    ctx, ft, symbol: str, today: date, *, weeks: int = _CALENDAR_LOOKAHEAD_WEEKS
) -> tuple[list[date], bool]:
    market_name = symbol.split(".", 1)[0]
    market = getattr(ft.Market, market_name, None)
    if market is None:
        return [], False
    hits: list[date] = []
    start = today
    for _ in range(max(1, weeks)):
        end = start + timedelta(days=6)
        ret, data = ctx.get_earnings_calendar(
            market,
            begin_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        if ret != ft.RET_OK:
            logger.warning("get_earnings_calendar failed: %s", data)
            return hits, False
        if data is not None and not getattr(data, "empty", True):
            for _, row in data.iterrows():
                code = str(row.get("security") or "")
                if not _symbol_matches(code, symbol):
                    continue
                when = _parse_day(row.get("earnings_date"))
                if when is not None:
                    hits.append(when)
        start = end + timedelta(days=1)
    return hits, True


def _earnings_metrics(ctx, ft, symbol: str) -> dict:
    today = date.today()
    next_earnings = None
    try:
        next_earnings = _future_earnings_from_history(ctx, ft, symbol, today)
    except OpenDError as exc:
        logger.warning("%s", exc)
    hits, calendar_ok = _calendar_hits(ctx, ft, symbol, today)
    if hits:
        soonest = min(hits)
        if next_earnings is None or soonest < next_earnings:
            next_earnings = soonest
    cleared = calendar_ok and not any(
        0 <= (when - today).days <= 14 for when in hits
    )
    if next_earnings is not None and 0 <= (next_earnings - today).days <= 14:
        cleared = False
    return {
        "next_earnings": next_earnings,
        # Cleared means no report inside 14 days; still keep next_earnings when
        # the calendar found a later date so the UI can show it.
        "earnings_in_14d_cleared": cleared,
    }


def fetch_strategy_market(symbol: str) -> dict:
    """Live OpenD snapshot used to score a strategy check."""
    import moomoo as ft

    metrics: dict = {
        "name": "",
        "iv_rank": None,
        "put_call": None,
        "last_price": None,
        "ma20": None,
        "ma50": None,
        "ma20_prev5": None,
        "rsi_6": None,
        "next_earnings": None,
        "earnings_in_14d_cleared": False,
    }
    try:
        with quote_context() as ctx:
            try:
                metrics.update(metrics_from_closes(_daily_closes(ctx, ft, symbol)))
            except OpenDError as exc:
                logger.warning("%s", exc)
            try:
                overview = _overview_metrics(ctx, ft, symbol)
                metrics["name"] = overview.get("name") or metrics["name"]
                metrics["iv_rank"] = overview.get("iv_rank")
                metrics["put_call"] = overview.get("put_call")
                if metrics["iv_rank"] is None:
                    metrics["iv_rank"] = _iv_rank_from_series(
                        ctx, ft, symbol, overview.get("iv")
                    )
            except OpenDError as exc:
                logger.warning("%s", exc)
            if metrics["put_call"] is None:
                try:
                    metrics["put_call"] = _latest_put_call(ctx, ft, symbol)
                except OpenDError as exc:
                    logger.warning("%s", exc)
            try:
                metrics.update(_earnings_metrics(ctx, ft, symbol))
            except OpenDError as exc:
                logger.warning("%s", exc)
    except OpenDError:
        raise
    except Exception as exc:
        raise OpenDError(f"OpenD request failed: {exc}") from exc
    return metrics
