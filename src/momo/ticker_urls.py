"""Map Moomoo symbols (MY.1155, HK.01810, US.AAPL) to public quote pages."""
from __future__ import annotations

from urllib.parse import quote

_KNOWN_MARKETS = ("MY", "HK", "US", "SG", "SH", "SZ", "JP")

# Simply Wall St short quote URLs: /stock/{exchange}/{ticker}
_SWS_EXCHANGE = {
    "MY": "klse",
    "HK": "sehk",
    "US": "nasdaqgs",
    "SG": "sgx",
    "JP": "tse",
    "SH": "shse",
    "SZ": "szse",
}


def _split_symbol(symbol: str) -> tuple[str, str]:
    text = (symbol or "").strip().upper()
    if not text:
        return "", ""
    if "." in text:
        market, rest = text.split(".", 1)
        if market in _KNOWN_MARKETS:
            return market, rest
    return "", text


def yahoo_quote_symbol(symbol: str) -> str | None:
    market, code = _split_symbol(symbol)
    if not code:
        return None
    if market == "US":
        return code.replace(".", "-")
    if market == "HK":
        if code.isdigit():
            code = code[-4:].zfill(4) if len(code) >= 4 else code.zfill(4)
        return f"{code}.HK"
    if market == "MY":
        return f"{code}.KL"
    if market == "SG":
        return f"{code}.SI"
    if market == "SH":
        return f"{code}.SS"
    if market == "SZ":
        return f"{code}.SZ"
    if market == "JP":
        return f"{code}.T"
    return code


def yahoo_quote_url(symbol: str) -> str:
    ysym = yahoo_quote_symbol(symbol)
    if not ysym:
        return ""
    return f"https://finance.yahoo.com/quote/{quote(ysym, safe='.-')}"


def simplywall_url(symbol: str) -> str:
    market, code = _split_symbol(symbol)
    if not code:
        return ""
    exchange = _SWS_EXCHANGE.get(market)
    if not exchange:
        return ""
    ticker = code
    if market == "HK" and code.isdigit():
        ticker = str(int(code))
    elif market == "US":
        ticker = code.replace(".", "-").lower()
    else:
        ticker = code.lower()
    return f"https://simplywall.st/stock/{exchange}/{quote(ticker, safe='.-')}"
