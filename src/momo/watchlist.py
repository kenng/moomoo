from pathlib import Path

import yaml

from momo.config import bare_code, get_settings, to_symbol


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
