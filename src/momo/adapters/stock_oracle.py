from __future__ import annotations

import re
from html import unescape

import httpx

from momo.ticker_urls import stockoracle_url

_MOAT_RE = re.compile(
    r"\b(?P<moat>very\s+wide|wide|narrow|none|no)\s+moat\b",
    re.IGNORECASE,
)
_VALUE_RE = re.compile(
    r"OracleValue[^\w]*(?P<ccy>[A-Z]{3})?\s*(?P<value>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_ASSESS_RE = re.compile(
    r"(?P<sign>[+-])?\s*(?P<pct>\d+(?:\.\d+)?)\s*%\s*"
    r"(?P<label>undervalued|overvalued|fair(?:ly)?(?:\s*-?\s*valued)?|fair\s+value)",
    re.IGNORECASE,
)
_MOAT_NORMALIZE = {
    "very wide": "wide",
    "wide": "wide",
    "narrow": "narrow",
    "none": "none",
    "no": "none",
}


class StockOracleError(Exception):
    """Stock Oracle fetch or parse error."""


def parse_oracle_overview(text: str) -> dict | None:
    """Parse moat / OracleValue / mispricing from overview page text or HTML."""
    visible = _visible_text(text or "")
    if "OracleValue" not in visible.replace(" ", ""):
        # Keep a looser check: OracleValue™ may lose the word after tag stripping.
        if not _VALUE_RE.search(visible):
            return None

    moat = None
    moat_match = _MOAT_RE.search(visible)
    if moat_match:
        moat = _MOAT_NORMALIZE.get(moat_match.group("moat").lower())

    value = None
    currency = ""
    value_match = _VALUE_RE.search(visible)
    if value_match:
        currency = (value_match.group("ccy") or "").upper()
        value = _float(value_match.group("value"))

    assess_pct = None
    assess_match = _ASSESS_RE.search(visible)
    if assess_match:
        pct = abs(_float(assess_match.group("pct")) or 0.0)
        label = re.sub(r"[\s-]+", " ", assess_match.group("label").lower())
        sign_char = assess_match.group("sign")
        if "undervalued" in label:
            assess_pct = -pct
        elif "overvalued" in label:
            assess_pct = pct
        elif sign_char == "-":
            assess_pct = -pct
        elif sign_char == "+":
            assess_pct = pct
        else:
            assess_pct = 0.0

    if value is None and assess_pct is None and not moat:
        return None
    return {
        "moat": moat or "",
        "value": value,
        "currency": currency,
        "assess_pct": assess_pct,
    }


def get_oracle_valuation(symbol: str, *, timeout: float = 20.0) -> dict | None:
    """Fetch Stock Oracle overview for a US symbol. Non-US names are skipped."""
    url = stockoracle_url(symbol)
    if not url:
        return None
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise StockOracleError(f"Stock Oracle request failed for {symbol}: {exc}") from exc

    parsed = parse_oracle_overview(response.text)
    if not parsed:
        raise StockOracleError(
            f"Stock Oracle overview for {symbol} had no OracleValue "
            "(login wall or layout change)"
        )
    return {
        "symbol": symbol,
        **parsed,
        "url": url,
    }


def _visible_text(raw: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\u2122", "").replace("™", "")
    return re.sub(r"\s+", " ", text).strip()


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
