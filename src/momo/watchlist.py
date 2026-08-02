from pathlib import Path

import yaml

from momo.config import get_settings, to_my_code


def load_watchlist(path: Path | None = None) -> list[dict]:
    settings = get_settings()
    watchlist_path = path or settings.watchlist_path
    with open(watchlist_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    stocks = []
    for item in raw.get("stocks", []):
        code = str(item["code"])
        stocks.append(
            {
                "code": code,
                "symbol": to_my_code(code),
                "name": item.get("name") or code,
            }
        )
    return stocks
