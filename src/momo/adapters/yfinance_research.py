from __future__ import annotations

from datetime import datetime, timezone

from momo.adapters.research import rating_label
from momo.config import bare_code

# Yahoo recommendationKey → Moomoo rating scale (5=Strong Buy … 1=Sell).
_KEY_TO_RATING = {
    "strong_buy": 5,
    "buy": 4,
    "hold": 3,
    "underperform": 2,
    "sell": 1,
}


class YFinanceError(Exception):
    """Yahoo Finance / yfinance fetch or parse error."""


def to_yahoo_symbol(symbol: str) -> str:
    """Map Moomoo MY symbol (MY.1155) to Yahoo Bursa ticker (1155.KL)."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("stock code is empty")
    market = symbol.split(".", 1)[0] if "." in symbol else ""
    if market != "MY":
        raise ValueError(f"yfinance price targets only support MY symbols, got {symbol}")
    code = bare_code(symbol)
    if not code:
        raise ValueError("stock code is empty")
    return f"{code}.KL"


def get_analyst_consensus(symbol: str) -> dict | None:
    """Fetch Yahoo consensus target / rating for a Malaysia (MY.*) symbol."""
    import yfinance as yf

    yahoo = to_yahoo_symbol(symbol)
    try:
        ticker = yf.Ticker(yahoo)
        targets = ticker.analyst_price_targets or {}
        info = ticker.info or {}
        breakdown = _recommendation_pcts(ticker)
    except Exception as exc:  # noqa: BLE001 — yfinance raises varied errors
        raise YFinanceError(f"yfinance failed for {yahoo}: {exc}") from exc

    highest = _float(targets.get("high") if targets else None)
    average = _float(targets.get("mean") if targets else None)
    lowest = _float(targets.get("low") if targets else None)
    if highest is None:
        highest = _float(info.get("targetHighPrice"))
    if average is None:
        average = _float(info.get("targetMeanPrice"))
    if lowest is None:
        lowest = _float(info.get("targetLowPrice"))

    total = _int(info.get("numberOfAnalystOpinions"))
    if total is None and breakdown:
        total = breakdown.get("total")

    rating = _rating_from_yahoo(
        info.get("recommendationKey"),
        info.get("recommendationMean"),
    )

    if all(v is None for v in (highest, average, lowest, total, rating)):
        return None

    return {
        "symbol": symbol,
        "highest": highest,
        "average": average,
        "lowest": lowest,
        "rating": rating,
        "rating_label": rating_label(rating),
        "total_analysts": total,
        "update_time_str": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "buy": breakdown.get("buy"),
        "hold": breakdown.get("hold"),
        "sell": breakdown.get("sell"),
        "strong_buy": breakdown.get("strong_buy"),
        "underperform": breakdown.get("underperform"),
    }


def get_institution_targets(symbol: str) -> list[dict]:
    """Per-institution targets are not available via yfinance for MY."""
    to_yahoo_symbol(symbol)  # validate
    return []


def _recommendation_pcts(ticker) -> dict:
    """Convert Yahoo recommendations_summary counts (latest period) to %."""
    try:
        summary = ticker.recommendations_summary
    except Exception:  # noqa: BLE001
        return {}
    if summary is None or getattr(summary, "empty", True):
        return {}

    row = None
    if "period" in summary.columns:
        zero = summary[summary["period"].astype(str) == "0m"]
        row = zero.iloc[0] if not zero.empty else summary.iloc[0]
    else:
        row = summary.iloc[0]

    def count(col: str) -> int:
        if col not in summary.columns:
            return 0
        try:
            return int(row[col] or 0)
        except (TypeError, ValueError):
            return 0

    strong_buy = count("strongBuy")
    buy = count("buy")
    hold = count("hold")
    # Yahoo sell ≈ Moomoo underperform; strongSell ≈ sell.
    underperform = count("sell")
    sell = count("strongSell")
    total = strong_buy + buy + hold + underperform + sell
    if total <= 0:
        return {}

    def pct(n: int) -> float:
        return round((n / total) * 100.0, 2)

    return {
        "strong_buy": pct(strong_buy),
        "buy": pct(buy),
        "hold": pct(hold),
        "underperform": pct(underperform),
        "sell": pct(sell),
        "total": total,
    }


def _rating_from_yahoo(key, mean) -> int | None:
    if isinstance(key, str):
        mapped = _KEY_TO_RATING.get(key.strip().lower())
        if mapped is not None:
            return mapped
    mean_f = _float(mean)
    if mean_f is None:
        return None
    # Yahoo mean: 1=Strong Buy … 5=Sell → Moomoo: 5=Strong Buy … 1=Sell.
    return max(1, min(5, int(round(6.0 - mean_f))))


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
