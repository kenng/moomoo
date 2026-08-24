from unittest.mock import patch

import pytest

from momo.adapters.stock_oracle import (
    LOGIN_URL,
    StockOracleError,
    get_oracle_valuation,
    login_stock_oracle,
    parse_oracle_overview,
)
from momo.cli import main
from momo.db.repo import list_stock_oracle_valuations_for_symbols
from momo.services import stock_oracle
from momo.services.price_targets import _oracle_hover_text, _upside_pct


SAMPLE_OVERVIEW = "Wide Moat OracleValue™ USD 208.69 20.56% Undervalued"


def test_parse_wide_moat_undervalued():
    parsed = parse_oracle_overview(SAMPLE_OVERVIEW)
    assert parsed == {
        "moat": "wide",
        "value": 208.69,
        "currency": "USD",
        "assess_pct": -20.56,
    }


def test_parse_html_and_overvalued():
    html = """
    <div class="MuiStack-root css-klawuc">
      Narrow Moat OracleValue&trade; USD 1,234.50 12.3% Overvalued
    </div>
    """
    parsed = parse_oracle_overview(html)
    assert parsed["moat"] == "narrow"
    assert parsed["value"] == 1234.5
    assert parsed["assess_pct"] == 12.3


def test_parse_no_moat_fair_value():
    parsed = parse_oracle_overview("No Moat OracleValue USD 10.00 0.00% Fairly Valued")
    assert parsed["moat"] == "none"
    assert parsed["assess_pct"] == 0.0


def test_get_oracle_valuation_skips_non_us():
    with patch("momo.adapters.stock_oracle.fetch_overview_html") as fetch:
        assert get_oracle_valuation("HK.09988") is None
        assert get_oracle_valuation("MY.1155") is None
        fetch.assert_not_called()


def test_get_oracle_valuation_fetches_us():
    html = f"<html>{SAMPLE_OVERVIEW}</html>"
    with patch("momo.adapters.stock_oracle.fetch_overview_html", return_value=html) as fetch:
        row = get_oracle_valuation("US.BABA")
    assert row["symbol"] == "US.BABA"
    assert row["value"] == 208.69
    assert row["assess_pct"] == -20.56
    fetch.assert_called_once()
    assert "BABA" in fetch.call_args.args[0]


class _FakeLocator:
    def __init__(self, page, key):
        self.page = page
        self.key = key

    def fill(self, value):
        self.page.filled[self.key] = value

    def click(self):
        self.page.clicked.append(self.key)
        if self.key == "Log in":
            self.page.url = self.page.after_login_url
        elif self.key == "Continue":
            self.page.url = "https://app.stockoracle.com/"


class _FakeLoginPage:
    def __init__(self, after_login_url="https://app.stockoracle.com/"):
        self.url = LOGIN_URL
        self.after_login_url = after_login_url
        self.filled = {}
        self.clicked = []
        self.gotos = []

    def goto(self, url, **_kwargs):
        self.gotos.append(url)
        self.url = url

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def get_by_role(self, _role, name=""):
        return _FakeLocator(self, name)

    def wait_for_function(self, *_args, **_kwargs):
        return None


def test_login_requires_credentials():
    with pytest.raises(StockOracleError, match="ACCOUNT_STOCK_ORACLE"):
        login_stock_oracle(_FakeLoginPage(), username="", password="x")


def test_login_fills_email_and_password():
    page = _FakeLoginPage()
    login_stock_oracle(page, username="user@example.com", password="secret")
    assert page.gotos == [LOGIN_URL]
    assert page.filled['input[placeholder="Email"]'] == "user@example.com"
    assert page.filled['input[placeholder="Password"]'] == "secret"
    assert page.clicked == ["Log in"]


def test_login_continues_past_device_limit():
    page = _FakeLoginPage(after_login_url="https://app.stockoracle.com/warning?mode=1")
    login_stock_oracle(page, username="user@example.com", password="secret")
    assert page.clicked == ["Log in", "Continue"]
    assert "/warning" not in page.url


class _FakeOverviewLocator:
    def filter(self, **_kwargs):
        return self

    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs):
        return None

    def inner_html(self, **_kwargs):
        return f'<div class="MuiStack-root css-klawuc">{SAMPLE_OVERVIEW}</div>'


def test_fetch_overview_html_uses_headless_page():
    from momo.adapters.stock_oracle import OVERVIEW_VALUE_SEL, fetch_overview_html

    class FakePage:
        def __init__(self):
            self.url = None
            self.selectors = []

        def goto(self, url, **_kwargs):
            self.url = url

        def locator(self, selector):
            self.selectors.append(selector)
            return _FakeOverviewLocator()

        def wait_for_function(self, *_args, **_kwargs):
            return None

    page = FakePage()
    html = fetch_overview_html(
        "https://app.stockoracle.com/stock-details/BABA/overview",
        page=page,
    )
    assert page.url.endswith("/BABA/overview")
    assert OVERVIEW_VALUE_SEL in page.selectors
    assert "css-klawuc" in html
    assert "OracleValue" in html
    assert "20.56% Undervalued" in html


def test_list_oracle_valuations_empty_symbols():
    assert list_stock_oracle_valuations_for_symbols(None, []) == {}


def test_oracle_upside_uses_value_vs_last():
    assert round(_upside_pct(208.69, 173.0), 1) == 20.6


def test_oracle_hover_text_includes_moat_and_assess():
    class Row:
        currency = "USD"
        moat = "wide"
        assess_pct = -20.56

    assert _oracle_hover_text(Row()) == "OracleValue · USD · wide moat · 20.6% undervalued"


def test_refresh_oracle_fetches_us_only():
    stocks = [
        {"symbol": "US.BABA", "code": "BABA", "name": "Alibaba", "market": "US"},
        {"symbol": "HK.09988", "code": "09988", "name": "Alibaba", "market": "HK"},
        {"symbol": "MY.1155", "code": "1155", "name": "Maybank", "market": "MY"},
    ]
    fetched: list[str] = []

    def fake_get(symbol: str, **_kwargs):
        fetched.append(symbol)
        return {
            "symbol": symbol,
            "moat": "wide",
            "value": 1.0,
            "currency": "USD",
            "assess_pct": -1.0,
        }

    with (
        patch(
            "momo.services.stock_oracle.load_watchlist",
            return_value=stocks,
        ),
        patch("momo.services.stock_oracle.get_oracle_valuation", side_effect=fake_get),
        patch("momo.services.stock_oracle.upsert_stock_oracle_valuation") as upsert,
        patch("momo.services.stock_oracle.get_session") as session,
        patch("momo.services.stock_oracle.oracle_browser_page") as browser,
    ):
        session.return_value.__enter__.return_value = object()
        browser.return_value.__enter__.return_value = object()
        result = stock_oracle.refresh_watchlist_oracle()

    assert fetched == ["US.BABA"]
    assert result["refreshed"] == 1
    assert result["skipped"] == 2
    assert result["symbols"] == 1
    assert result["ok"] is True
    assert result["source"] == "watchlist"
    upsert.assert_called_once()


def test_cli_targets_oracle_does_not_refresh_analyst_targets(capsys):
    oracle = {
        "ok": True,
        "refreshed": 1,
        "skipped": 2,
        "errors": [],
        "source": "watchlist",
        "symbols": 1,
    }

    def boom():
        raise AssertionError("analyst targets must not run")

    with (
        patch(
            "momo.services.stock_oracle.refresh_watchlist_oracle",
            return_value=oracle,
        ),
        patch(
            "momo.services.price_targets.refresh_watchlist_targets",
            side_effect=boom,
        ),
    ):
        assert main(["refresh", "--targets", "--oracle", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"refreshed": 1' in out


def test_cli_oracle_requires_targets(capsys):
    assert main(["refresh", "--oracle"]) == 2
    err = capsys.readouterr().err
    assert "momo refresh --targets --oracle" in err
