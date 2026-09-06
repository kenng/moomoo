"""Option-strategy timing: ticker resolve, indicators, and condition scoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable

from momo.config import KNOWN_MARKETS, bare_code, to_symbol

OPTION_MARKETS = ("US", "HK")
STRATEGY_LABELS = {
    "bull_put": "Bull put spread",
}
CONCLUSION_SUMMARY = {
    "Ideal": "Conditions line up for a bull put spread.",
    "Favorable": "Mostly supportive; size smaller or wait for a cleaner tape.",
    "Wait-and-see": "Mixed tape — wait for better IV, trend, or a quieter calendar.",
    "Avoid": "Too many conditions against entry.",
}

_VERDICT_MARK = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
_VERDICT_POINTS = {"pass": 0, "warn": 1, "fail": 2}


class StrategyTimingError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    key: str
    label: str
    threshold: str
    status: str
    verdict: str


@dataclass(frozen=True)
class StrategyCheck:
    strategy: str
    strategy_label: str
    symbol: str
    conditions: tuple[Condition, ...]
    penalty: int
    conclusion: str

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "strategy_label": self.strategy_label,
            "symbol": self.symbol,
            "conditions": [asdict(c) for c in self.conditions],
            "penalty": self.penalty,
            "conclusion": self.conclusion,
            "summary": CONCLUSION_SUMMARY.get(self.conclusion, ""),
        }


def resolve_option_underlying(code: str) -> dict:
    """Bare tickers become US.*; only US and HK option underlyings are allowed."""
    raw = (code or "").strip().upper()
    if not raw:
        raise StrategyTimingError("ticker is empty")

    if "." in raw:
        prefix, _rest = raw.split(".", 1)
        if prefix in KNOWN_MARKETS:
            if prefix not in OPTION_MARKETS:
                raise StrategyTimingError(
                    f"{raw} has no OpenD option stats (US and HK only)"
                )
            symbol = to_symbol(raw)
            return {
                "code": bare_code(symbol),
                "symbol": symbol,
                "market": prefix,
                "name": bare_code(symbol),
            }

    symbol = to_symbol(raw, market="US")
    return {
        "code": bare_code(symbol),
        "symbol": symbol,
        "market": "US",
        "name": bare_code(symbol),
    }


def sma(closes: list[float], period: int) -> float | None:
    if period <= 0 or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def rsi(closes: list[float], period: int = 6) -> float | None:
    """Wilder RSI on daily closes."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))


def metrics_from_closes(closes: list[float]) -> dict:
    last = closes[-1] if closes else None
    prev5 = closes[:-5] if len(closes) > 5 else []
    return {
        "last_price": last,
        "ma20": sma(closes, 20),
        "ma50": sma(closes, 50),
        "ma20_prev5": sma(prev5, 20) if prev5 else None,
        "rsi_6": rsi(closes, 6),
    }


def iv_rank_from_history(current: float | None, history: list[float]) -> float | None:
    """Classic IV rank: (current − 52w min) / (52w max − 52w min) × 100."""
    if current is None or not history:
        return None
    lo = min(history)
    hi = max(history)
    if hi <= lo:
        return 50.0
    return (current - lo) / (hi - lo) * 100.0


def conclusion_for_penalty(penalty: int) -> str:
    if penalty <= 1:
        return "Ideal"
    if penalty <= 3:
        return "Favorable"
    if penalty <= 7:
        return "Wait-and-see"
    return "Avoid"


def _marked(text: str, verdict: str) -> str:
    return f"{text} — {_VERDICT_MARK[verdict]}"


def _fmt_num(value: float, digits: int) -> str:
    rounded = round(value, digits)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.{digits}f}"


def _iv_condition(iv_rank: float | None) -> Condition:
    threshold = "≥ 40 preferred"
    if iv_rank is None:
        return Condition(
            "iv_rank", "IV Rank", threshold, _marked("Unknown", "warn"), "warn"
        )
    if iv_rank >= 40:
        verdict = "pass"
    elif iv_rank >= 30:
        verdict = "warn"
    else:
        verdict = "fail"
    shown = _fmt_num(iv_rank, 1)
    return Condition(
        "iv_rank", "IV Rank", threshold, _marked(shown, verdict), verdict
    )


def _rsi_condition(rsi_6: float | None) -> Condition:
    threshold = "Below 55"
    if rsi_6 is None:
        return Condition(
            "rsi_6", "RSI (6-day)", threshold, _marked("Unknown", "warn"), "warn"
        )
    if rsi_6 < 55:
        verdict = "pass"
    elif rsi_6 <= 60:
        verdict = "warn"
    else:
        verdict = "fail"
    return Condition(
        "rsi_6",
        "RSI (6-day)",
        threshold,
        _marked(_fmt_num(rsi_6, 1), verdict),
        verdict,
    )


def _price_condition(last: float | None, ma20: float | None) -> Condition:
    threshold = "Near or below 20-day MA"
    if last is None or ma20 is None or ma20 == 0:
        return Condition(
            "price_ma20", "Price", threshold, _marked("Unknown", "warn"), "warn"
        )
    gap = (last - ma20) / ma20
    if gap < 0:
        verdict, label = "pass", "Below"
    elif gap == 0:
        verdict, label = "pass", "At 20-day MA"
    elif gap <= 0.01:
        verdict, label = "pass", "Near 20-day MA"
    elif gap <= 0.03:
        verdict, label = "warn", "Above"
    else:
        verdict, label = "fail", "Above"
    return Condition(
        "price_ma20", "Price", threshold, _marked(label, verdict), verdict
    )


def _pcr_condition(put_call: float | None) -> Condition:
    threshold = "Above 0.7"
    if put_call is None:
        return Condition(
            "put_call",
            "Put/Call Ratio",
            threshold,
            _marked("Unknown", "warn"),
            "warn",
        )
    if put_call >= 0.70:
        verdict = "pass"
        extra = ""
    elif put_call >= 0.50:
        verdict = "warn"
        extra = ""
    else:
        verdict = "fail"
        extra = " (too bullish)"
    status = _marked(_fmt_num(put_call, 2), verdict) + extra
    return Condition("put_call", "Put/Call Ratio", threshold, status, verdict)


def _trend_condition(
    ma20: float | None, ma50: float | None, ma20_prev5: float | None
) -> Condition:
    threshold = "20-day MA above 50-day MA and rising"
    if ma20 is None or ma50 is None:
        return Condition(
            "trend",
            "Short-term trend",
            threshold,
            _marked("Unknown", "warn"),
            "warn",
        )
    if ma20 < ma50:
        return Condition(
            "trend",
            "Short-term trend",
            threshold,
            _marked("Downtrend", "fail"),
            "fail",
        )
    rising = ma20_prev5 is not None and ma20 > ma20_prev5
    if ma20 > ma50 and rising:
        return Condition(
            "trend",
            "Short-term trend",
            threshold,
            _marked("Uptrend", "pass"),
            "pass",
        )
    return Condition(
        "trend",
        "Short-term trend",
        threshold,
        _marked("Developing", "warn"),
        "warn",
    )


def _earnings_label(when: date) -> str:
    return f"Earnings {when.day} {when.strftime('%b')}"


def _catalyst_condition(
    next_earnings: date | None,
    *,
    today: date,
    cleared_14d: bool,
) -> Condition:
    threshold = "No earnings within 14 days"
    if next_earnings is not None:
        days = (next_earnings - today).days
        if 0 <= days <= 14:
            return Condition(
                "catalyst",
                "Fundamental catalyst",
                threshold,
                _marked(_earnings_label(next_earnings), "fail"),
                "fail",
            )
        if days > 14:
            return Condition(
                "catalyst",
                "Fundamental catalyst",
                threshold,
                _marked(_earnings_label(next_earnings), "pass"),
                "pass",
            )
    if cleared_14d:
        return Condition(
            "catalyst",
            "Fundamental catalyst",
            threshold,
            _marked("No earnings in 14 days", "pass"),
            "pass",
        )
    return Condition(
        "catalyst",
        "Fundamental catalyst",
        threshold,
        _marked("Earnings date unknown", "warn"),
        "warn",
    )


def evaluate_bull_put(
    metrics: dict, *, today: date | None = None
) -> tuple[Condition, ...]:
    today = today or date.today()
    return (
        _iv_condition(metrics.get("iv_rank")),
        _rsi_condition(metrics.get("rsi_6")),
        _price_condition(metrics.get("last_price"), metrics.get("ma20")),
        _pcr_condition(metrics.get("put_call")),
        _trend_condition(
            metrics.get("ma20"),
            metrics.get("ma50"),
            metrics.get("ma20_prev5"),
        ),
        _catalyst_condition(
            metrics.get("next_earnings"),
            today=today,
            cleared_14d=bool(metrics.get("earnings_in_14d_cleared")),
        ),
    )


STRATEGY_EVALUATORS: dict[str, Callable[..., tuple[Condition, ...]]] = {
    "bull_put": evaluate_bull_put,
}


def evaluate_strategy(
    strategy: str,
    symbol: str,
    metrics: dict,
    *,
    today: date | None = None,
) -> StrategyCheck:
    strategy = (strategy or "").strip()
    evaluator = STRATEGY_EVALUATORS.get(strategy)
    if evaluator is None:
        known = ", ".join(STRATEGY_LABELS)
        raise StrategyTimingError(f"unknown strategy {strategy!r} (try {known})")
    conditions = evaluator(metrics, today=today)
    penalty = sum(_VERDICT_POINTS[c.verdict] for c in conditions)
    return StrategyCheck(
        strategy=strategy,
        strategy_label=STRATEGY_LABELS[strategy],
        symbol=symbol,
        conditions=conditions,
        penalty=penalty,
        conclusion=conclusion_for_penalty(penalty),
    )
