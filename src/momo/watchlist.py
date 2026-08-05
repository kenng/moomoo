from pathlib import Path

import yaml

from momo.config import KNOWN_MARKETS, bare_code, get_settings, to_symbol


def load_watchlist(path: Path | None = None) -> list[dict]:
    settings = get_settings()
    watchlist_path = path or settings.watchlist_path
    with open(watchlist_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    stocks = []
    for item in raw.get("stocks", []):
        raw_code = str(item["code"])
        market = item.get("market")
        symbol = to_symbol(raw_code, market=market)
        stocks.append(
            {
                "code": bare_code(symbol),
                "symbol": symbol,
                "name": item.get("name") or bare_code(symbol),
                "market": symbol.split(".", 1)[0],
            }
        )
    return stocks


def resolve_stock(code: str, market: str | None = None) -> dict:
    """Resolve a code to a stock dict, preferring watchlist market over default."""
    code = code.strip().upper()
    watchlist = load_watchlist()

    if market is not None:
        symbol = to_symbol(code, market=market)
        for s in watchlist:
            if s["symbol"] == symbol:
                return s
        return {
            "code": bare_code(symbol),
            "symbol": symbol,
            "name": bare_code(symbol),
            "market": symbol.split(".", 1)[0],
        }

    # Prefixed code (US.QCOM) — trust the prefix
    if "." in code:
        prefix, _ = code.split(".", 1)
        if prefix in KNOWN_MARKETS:
            symbol = to_symbol(code)
            for s in watchlist:
                if s["symbol"] == symbol:
                    return s
            return {
                "code": bare_code(symbol),
                "symbol": symbol,
                "name": bare_code(symbol),
                "market": prefix,
            }

    # Bare code — prefer watchlist entry so US.QCOM isn't misread as HK.QCOM
    bare = bare_code(code)
    for s in watchlist:
        if s["code"] == bare:
            return s

    symbol = to_symbol(code)
    return {
        "code": bare_code(symbol),
        "symbol": symbol,
        "name": bare_code(symbol),
        "market": symbol.split(".", 1)[0],
    }
