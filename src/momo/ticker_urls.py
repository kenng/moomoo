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

# KLSE pages use the Bursa ticker (SUNREIT), not the numeric stock code (5176).
_SWS_MY_TICKER = {
    "1066": "rhbbank",
    "1155": "maybank",
    "1295": "pbbank",
    "4065": "ppb",
    "5123": "sentral",
    "5176": "sunreit",
    "5180": "clmt",
    "5318": "dxn",
    "6139": "takaful",
}

# Canonical analysis URLs when the short /stock/{exchange}/{ticker} path is wrong.
_SWS_URL_OVERRIDE = {
    "MY.5176": (
        "https://simplywall.st/stocks/my/real-estate/klse-sunreit/"
        "sunway-real-estate-investment-trust-shares"
    ),
}

# Google Finance: TICKER:EXCHANGE (US can omit exchange)
_GOOGLE_EXCHANGE = {
    "HK": "HKG",
    "MY": "KLSE",
    "SG": "SGX",
    "JP": "TYO",
    "SH": "SHA",
    "SZ": "SHE",
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


def _hk_share_code(code: str, *, padded: bool) -> str:
    if not code.isdigit():
        return code
    if padded:
        return code[-4:].zfill(4) if len(code) >= 4 else code.zfill(4)
    return str(int(code))


def yahoo_quote_symbol(symbol: str) -> str | None:
    market, code = _split_symbol(symbol)
    if not code:
        return None
    if market == "US":
        return code.replace(".", "-")
    if market == "HK":
        return f"{_hk_share_code(code, padded=True)}.HK"
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


def _q(value: str, safe: str = ".-") -> str:
    return quote(value, safe=safe)


def yahoo_quote_url(symbol: str) -> str:
    ysym = yahoo_quote_symbol(symbol)
    if not ysym:
        return ""
    return f"https://finance.yahoo.com/quote/{_q(ysym)}"


def simplywall_url(symbol: str) -> str:
    market, code = _split_symbol(symbol)
    if not code:
        return ""
    override = _SWS_URL_OVERRIDE.get(f"{market}.{code}" if market else code)
    if override:
        return override
    exchange = _SWS_EXCHANGE.get(market)
    if not exchange:
        return ""
    if market == "HK":
        ticker = _hk_share_code(code, padded=False)
    elif market == "US":
        ticker = code.replace(".", "-").lower()
    elif market == "MY":
        ticker = _SWS_MY_TICKER.get(code, code).lower()
    else:
        ticker = code.lower()
    return f"https://simplywall.st/stock/{exchange}/{_q(ticker)}"


def google_finance_url(symbol: str) -> str:
    market, code = _split_symbol(symbol)
    if not code:
        return ""
    if market == "US":
        ticker = code.replace(".", "-")
        return f"https://www.google.com/finance/quote/{_q(ticker)}"
    if market == "HK":
        ticker = _hk_share_code(code, padded=True)
    else:
        ticker = code
    suffix = _GOOGLE_EXCHANGE.get(market)
    if not suffix:
        return ""
    return f"https://www.google.com/finance/quote/{_q(f'{ticker}:{suffix}', safe=':.-')}"


def stockoracle_url(symbol: str) -> str:
    """Stock Oracle covers US-listed names (e.g. BABA overview)."""
    market, code = _split_symbol(symbol)
    if market != "US" or not code:
        return ""
    ticker = code.replace(".", "-")
    return f"https://app.stockoracle.com/stock-details/{_q(ticker)}/overview"


def tipranks_url(symbol: str) -> str:
    """TipRanks stock pages: US ticker, or {cc}:{code} for HK/SG/JP."""
    market, code = _split_symbol(symbol)
    if not code:
        return ""
    if market == "US":
        ticker = code.lower()
    elif market == "HK":
        ticker = f"hk:{_hk_share_code(code, padded=True)}"
    elif market == "SG":
        ticker = f"sg:{code.lower()}"
    elif market == "JP":
        ticker = f"jp:{code.lower()}"
    else:
        return ""
    return f"https://www.tipranks.com/stocks/{_q(ticker, safe=':.-')}"


def ticker_ext_links(symbol: str) -> list[dict[str, str]]:
    """Icon-row sources for a ticker. Skip a source when its URL cannot be built."""
    specs = (
        ("yahoo", "Yahoo Finance", "finance.yahoo.com", yahoo_quote_url),
        ("simplywall", "Simply Wall St", "simplywall.st", simplywall_url),
        ("google", "Google Finance", "www.google.com", google_finance_url),
        ("tipranks", "TipRanks", "www.tipranks.com", tipranks_url),
        ("stockoracle", "Stock Oracle", "app.stockoracle.com", stockoracle_url),
    )
    links: list[dict[str, str]] = []
    for sid, label, domain, builder in specs:
        url = builder(symbol)
        if url:
            links.append({"id": sid, "label": label, "domain": domain, "url": url})
    return links
