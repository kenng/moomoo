from __future__ import annotations

import argparse
import json
import sys

from momo.config import get_settings, to_symbol
from momo.opend_client import OpenDError, smoke_test_snapshot
from momo.services import news_digest
from momo.watchlist import load_watchlist, resolve_stock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="momo", description="Moomoo helpers (MY/HK/…)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_smoke = sub.add_parser("smoke", help="Test OpenD quote connectivity")
    p_smoke.add_argument(
        "--code",
        default="1155",
        help="Stock code (1155, MY.1155, HK.1810, HK.01810)",
    )
    p_smoke.add_argument(
        "--market",
        default=None,
        help="Market for bare codes (MY, HK, …). Default: DEFAULT_MARKET from .env",
    )

    p_news = sub.add_parser("news", help="Show cached important news")
    p_news.add_argument("--code", help="Single stock code (e.g. 1155 or HK.1810)")
    p_news.add_argument("--market", default=None, help="Market for bare codes")
    p_news.add_argument("--watchlist", action="store_true", help="All watchlist stocks")
    p_news.add_argument("--json", action="store_true", help="JSON output")
    p_news.add_argument("--limit", type=int, default=None)

    p_refresh = sub.add_parser(
        "refresh", help="Fetch news or analyst price targets into cache"
    )
    p_refresh.add_argument("--code", help="Single stock code")
    p_refresh.add_argument("--market", default=None, help="Market for bare codes")
    p_refresh.add_argument("--news", action="store_true", help="Refresh watchlist news")
    p_refresh.add_argument(
        "--targets",
        action="store_true",
        help="Refresh consensus / institution price targets for open positions",
    )
    p_refresh.add_argument("--json", action="store_true")

    p_wl = sub.add_parser("watchlist", help="List configured watchlist")
    p_wl.add_argument("--json", action="store_true")

    p_publish = sub.add_parser(
        "publish",
        help="Export read-only digests to Cloudflare R2 for the Worker mirror",
    )
    p_publish.add_argument("--json", action="store_true", help="JSON output")
    p_publish.add_argument(
        "--dry-run",
        action="store_true",
        help="Build payloads only; do not upload",
    )

    args = parser.parse_args(argv)

    try:
        if args.command == "smoke":
            return _cmd_smoke(args)
        if args.command == "news":
            return _cmd_news(args)
        if args.command == "refresh":
            return _cmd_refresh(args)
        if args.command == "watchlist":
            return _cmd_watchlist(args)
        if args.command == "publish":
            return _cmd_publish(args)
    except OpenDError as exc:
        print(f"OpenD error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


def _cmd_smoke(args) -> int:
    symbol = to_symbol(args.code, market=args.market)
    result = smoke_test_snapshot(symbol)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _cmd_watchlist(args) -> int:
    stocks = load_watchlist()
    if args.json:
        print(json.dumps(stocks, indent=2))
    else:
        for s in stocks:
            print(f"{s['symbol']}\t{s['name']}")
    return 0


def _cmd_news(args) -> int:
    if args.code:
        data = news_digest.get_digest_for_code(
            args.code, limit=args.limit, market=args.market
        )
    else:
        # default to watchlist
        data = news_digest.get_watchlist_digest(limit_per_stock=args.limit)

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    if isinstance(data, dict):
        _print_digest(data)
    else:
        for item in data:
            _print_digest(item)
            print("-" * 60)
    return 0


def _cmd_refresh(args) -> int:
    if args.targets:
        return _cmd_refresh_targets(args)

    if args.code:
        stock = resolve_stock(args.code, market=args.market)
        results = [news_digest.refresh_stock_news(stock)]
    else:
        results = news_digest.refresh_watchlist()

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 0

    for r in results:
        print(
            f"{r['symbol']} ({r['name']}): fetched={r['fetched']} "
            f"top={r['stored_top']} inserted={r['inserted']}"
        )
        snap = r.get("snapshot")
        if snap:
            print(
                f"  price={snap.get('last_price')} "
                f"chg%={snap.get('change_rate')} vol={snap.get('volume')}"
            )
        for n in r.get("news", [])[:5]:
            print(f"  [{n['importance_score']}] {n['title'][:100]}")
    return 0


def _cmd_refresh_targets(args) -> int:
    from momo.services import price_targets

    result = price_targets.refresh_watchlist_targets()
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    print(
        f"targets: source={result.get('source')} "
        f"symbols={result.get('symbols')} "
        f"consensus={result.get('refreshed')} "
        f"institutions={result.get('institution_rows')}"
    )
    for err in result.get("errors") or []:
        print(f"  error: {err}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def _cmd_publish(args) -> int:
    from momo.adapters.r2 import R2ConfigError
    from momo.services import r2_export

    try:
        if args.dry_run:
            bundle = r2_export.build_export_bundle()
            result = {
                "synced_at": bundle["meta.json"]["synced_at"],
                "version": bundle["meta.json"]["version"],
                "keys": sorted(bundle.keys()),
                "count": len(bundle),
                "dry_run": True,
            }
        else:
            result = r2_export.publish_to_r2()
    except R2ConfigError as exc:
        print(f"R2 config error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    print(
        f"published {result['count']} objects "
        f"(synced_at={result['synced_at']})"
    )
    if args.dry_run:
        for key in result["keys"]:
            print(f"  {key}")
    else:
        for key in result.get("uploaded") or []:
            print(f"  {key}")
    return 0


def _print_digest(item: dict) -> None:
    symbol = item.get("symbol", "")
    name = item.get("name") or item.get("stock_name") or ""
    print(f"{symbol} {name}".strip())
    snap = item.get("snapshot")
    if snap:
        print(
            f"  price={snap.get('last_price')} "
            f"chg%={snap.get('change_rate')} vol={snap.get('volume')}"
        )
    news = item.get("news") or []
    if not news:
        print("  (no cached news — run: momo refresh --news)")
        return
    for n in news:
        print(
            f"  [{n.get('importance_score')}] "
            f"{n.get('news_sub_type')} | {n.get('publish_time')} | {n.get('title')}"
        )
        if n.get("url"):
            print(f"      {n['url']}")


if __name__ == "__main__":
    raise SystemExit(main())
