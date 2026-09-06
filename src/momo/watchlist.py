from pathlib import Path
from tempfile import NamedTemporaryFile

import yaml

from momo.config import KNOWN_MARKETS, bare_code, get_settings, to_symbol

_WATCHLIST_HEADER = (
    "# Synced from live moomoo positions (stocks/ETFs only; options excluded)\n"
)
_MARKET_ORDER = {"HK": 0, "US": 1, "MY": 2, "SG": 3, "SH": 4, "SZ": 5, "JP": 6}


class _Quoted(str):
    """Force double-quoted YAML scalars (preserve leading zeros on HK codes)."""


class _WatchlistDumper(yaml.SafeDumper):
    pass


def _represent_quoted(dumper, data: _Quoted):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_WatchlistDumper.add_representer(_Quoted, _represent_quoted)


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


def stock_etf_holdings(stocks: list[dict]) -> list[dict]:
    """Keep open stock/ETF rows only (qty > 0); drop option-only underlyings."""
    holdings: list[dict] = []
    for stock in stocks:
        if (stock.get("qty") or 0) <= 0:
            continue
        symbol = (stock.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        market = (stock.get("market") or "").upper()
        if not market and "." in symbol:
            market = symbol.split(".", 1)[0]
        code = stock.get("code") or bare_code(symbol)
        holdings.append(
            {
                "code": str(code),
                "symbol": symbol,
                "name": stock.get("name") or str(code),
                "market": market or symbol.split(".", 1)[0],
            }
        )
    holdings.sort(
        key=lambda s: (
            _MARKET_ORDER.get(s["market"], 99),
            s["code"],
        )
    )
    return holdings


def save_watchlist(stocks: list[dict], path: Path | None = None) -> Path:
    """Overwrite watchlist YAML with the given stock/ETF entries."""
    settings = get_settings()
    watchlist_path = Path(path or settings.watchlist_path)
    entries = [
        {
            "code": _Quoted(str(s["code"])),
            "market": _Quoted(str(s.get("market") or "").upper()),
            "name": _Quoted(str(s.get("name") or s["code"])),
        }
        for s in stocks
    ]
    body = yaml.dump(
        {"stocks": entries},
        Dumper=_WatchlistDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        indent=2,
    )
    text = _WATCHLIST_HEADER + body
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=watchlist_path.parent,
        prefix=f".{watchlist_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(text)
        tmp_path = Path(tmp.name)
    tmp_path.replace(watchlist_path)
    return watchlist_path


def sync_watchlist_from_positions(
    stocks: list[dict],
    path: Path | None = None,
) -> dict:
    """
    Write stock/ETF holdings to watchlist.yaml.

    Skips the write when there are no open stock/ETF positions so a temporary
    empty book cannot wipe the file.
    """
    holdings = stock_etf_holdings(stocks)
    if not holdings:
        return {
            "ok": True,
            "synced": False,
            "symbols": 0,
            "skipped": "no_stock_holdings",
        }
    save_watchlist(holdings, path=path)
    return {
        "ok": True,
        "synced": True,
        "symbols": len(holdings),
        "skipped": None,
    }


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
