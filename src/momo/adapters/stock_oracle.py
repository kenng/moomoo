from __future__ import annotations

import re
from contextlib import contextmanager
from html import unescape

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from momo.config import get_settings
from momo.ticker_urls import stockoracle_url

LOGIN_URL = "https://app.stockoracle.com/login"

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
OVERVIEW_VALUE_SEL = "div.MuiStack-root.css-klawuc"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


def login_stock_oracle(page, *, username: str, password: str, timeout: float = 30.0) -> None:
    """Sign in at /login. Continues past the device-limit warning when shown."""
    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        raise StockOracleError(
            "Stock Oracle login requires ACCOUNT_STOCK_ORACLE_USERNAME "
            "and ACCOUNT_STOCK_ORACLE_PASSWORD"
        )
    ms = max(int(timeout * 1000), 1)
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=ms)
    page.locator('input[placeholder="Email"]').fill(username)
    page.locator('input[placeholder="Password"]').fill(password)
    page.get_by_role("button", name="Log in").click()
    try:
        page.wait_for_function(
            "() => !location.pathname.includes('/login')",
            timeout=ms,
        )
    except PlaywrightTimeout as exc:
        raise StockOracleError("Stock Oracle login failed") from exc
    if "/warning" in (page.url or ""):
        page.get_by_role("button", name="Continue").click()
        try:
            page.wait_for_function(
                "() => !location.pathname.includes('/warning') "
                "&& !location.pathname.includes('/login')",
                timeout=ms,
            )
        except PlaywrightTimeout as exc:
            raise StockOracleError("Stock Oracle login stalled on device-limit warning") from exc


@contextmanager
def oracle_browser_page(*, timeout: float = 30.0):
    """One logged-in Chromium page for a batch of Stock Oracle fetches."""
    settings = get_settings()
    username = settings.account_stock_oracle_username
    password = settings.account_stock_oracle_password
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=_USER_AGENT,
                locale="en-US",
            )
            page = context.new_page()
            login_stock_oracle(page, username=username, password=password, timeout=timeout)
            yield page
        finally:
            browser.close()


def fetch_overview_html(url: str, *, timeout: float = 20.0, page=None) -> str:
    """Load a Stock Oracle overview URL in headless Chromium and return HTML."""
    if page is not None:
        return _goto_overview(page, url, timeout)
    with oracle_browser_page() as owned:
        return _goto_overview(owned, url, timeout)


def get_oracle_valuation(symbol: str, *, timeout: float = 20.0, page=None) -> dict | None:
    """Fetch Stock Oracle overview for a US symbol. Non-US names are skipped."""
    url = stockoracle_url(symbol)
    if not url:
        return None
    try:
        html = fetch_overview_html(url, timeout=timeout, page=page)
    except Exception as exc:
        if isinstance(exc, StockOracleError):
            raise
        raise StockOracleError(f"Stock Oracle request failed for {symbol}: {exc}") from exc

    parsed = parse_oracle_overview(html)
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


def _goto_overview(page, url: str, timeout: float) -> str:
    ms = max(int(timeout * 1000), 1)
    page.goto(url, wait_until="domcontentloaded", timeout=ms)
    stack = page.locator(OVERVIEW_VALUE_SEL).filter(has_text="OracleValue")
    try:
        stack.first.wait_for(state="visible", timeout=ms)
        page.wait_for_function(
            """() => {
                const el = document.querySelector('div.MuiStack-root.css-klawuc');
                const t = (el && el.innerText) || '';
                return /moat/i.test(t) && t.includes('OracleValue') && /\\d/.test(t);
            }""",
            timeout=ms,
        )
    except PlaywrightTimeout as exc:
        raise StockOracleError(
            "Stock Oracle overview value stack did not load "
            "(div.MuiStack-root.css-klawuc)"
        ) from exc
    return stack.first.inner_html(timeout=ms)


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
