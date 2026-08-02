from __future__ import annotations

import argparse
import json

from momo.services.news_digest import refresh_stock_news, refresh_watchlist
from momo.watchlist import load_watchlist


def run(code: str | None = None) -> list[dict]:
    if code:
        stock = next(
            (s for s in load_watchlist() if s["code"] == code or s["symbol"].endswith(code)),
            None,
        )
        if stock is None:
            raise SystemExit(f"Stock not in watchlist: {code}")
        return [refresh_stock_news(stock)]
    return refresh_watchlist()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh watchlist news from OpenD")
    parser.add_argument("--code", help="Optional single stock code")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = run(args.code)
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print(f"{r['symbol']}: fetched={r['fetched']} top={r['stored_top']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
