from momo.ticker_urls import (
    google_finance_url,
    simplywall_url,
    stockoracle_url,
    ticker_ext_links,
    tipranks_url,
    yahoo_quote_symbol,
    yahoo_quote_url,
)


def test_yahoo_quote_symbol_by_market():
    assert yahoo_quote_symbol("MY.1155") == "1155.KL"
    assert yahoo_quote_symbol("HK.01810") == "1810.HK"
    assert yahoo_quote_symbol("HK.09988") == "9988.HK"
    assert yahoo_quote_symbol("HK.00001") == "0001.HK"
    assert yahoo_quote_symbol("HK.00700") == "0700.HK"
    assert yahoo_quote_symbol("US.AAPL") == "AAPL"
    assert yahoo_quote_symbol("US.BRK.B") == "BRK-B"
    assert yahoo_quote_symbol("SG.D05") == "D05.SI"
    assert yahoo_quote_symbol("JP.7203") == "7203.T"


def test_yahoo_quote_url():
    assert yahoo_quote_url("MY.1155") == "https://finance.yahoo.com/quote/1155.KL"
    assert yahoo_quote_url("US.AAPL") == "https://finance.yahoo.com/quote/AAPL"
    assert yahoo_quote_url("") == ""


def test_simplywall_url():
    assert simplywall_url("MY.1155") == "https://simplywall.st/stock/klse/maybank"
    assert simplywall_url("MY.5176") == (
        "https://simplywall.st/stocks/my/real-estate/klse-sunreit/"
        "sunway-real-estate-investment-trust-shares"
    )
    assert simplywall_url("HK.01810") == "https://simplywall.st/stock/sehk/1810"
    assert simplywall_url("HK.00700") == "https://simplywall.st/stock/sehk/700"
    assert simplywall_url("US.AAPL") == "https://simplywall.st/stock/nasdaqgs/aapl"
    assert simplywall_url("SG.D05") == "https://simplywall.st/stock/sgx/d05"
    assert simplywall_url("") == ""


def test_google_finance_url():
    assert google_finance_url("US.AAPL") == "https://www.google.com/finance/quote/AAPL"
    assert google_finance_url("US.BABA") == "https://www.google.com/finance/quote/BABA"
    assert google_finance_url("HK.01810") == "https://www.google.com/finance/quote/1810:HKG"
    assert google_finance_url("MY.1155") == "https://www.google.com/finance/quote/1155:KLSE"
    assert google_finance_url("") == ""


def test_stockoracle_url_us_only():
    assert stockoracle_url("US.BABA") == (
        "https://app.stockoracle.com/stock-details/BABA/overview"
    )
    assert stockoracle_url("US.AAPL") == (
        "https://app.stockoracle.com/stock-details/AAPL/overview"
    )
    assert stockoracle_url("HK.09988") == ""
    assert stockoracle_url("MY.1155") == ""


def test_tipranks_url():
    assert tipranks_url("US.AAPL") == "https://www.tipranks.com/stocks/aapl"
    assert tipranks_url("US.BRK.B") == "https://www.tipranks.com/stocks/brk.b"
    assert tipranks_url("HK.00700") == "https://www.tipranks.com/stocks/hk:0700"
    assert tipranks_url("HK.01810") == "https://www.tipranks.com/stocks/hk:1810"
    assert tipranks_url("SG.D05") == "https://www.tipranks.com/stocks/sg:d05"
    assert tipranks_url("JP.7203") == "https://www.tipranks.com/stocks/jp:7203"
    assert tipranks_url("MY.1155") == ""
    assert tipranks_url("") == ""


def test_ticker_ext_links_order_and_skip():
    us = ticker_ext_links("US.BABA")
    assert [x["id"] for x in us] == [
        "yahoo",
        "simplywall",
        "google",
        "tipranks",
        "stockoracle",
    ]
    assert us[-1]["url"].endswith("/stock-details/BABA/overview")

    my = ticker_ext_links("MY.1155")
    assert [x["id"] for x in my] == ["yahoo", "simplywall", "google"]
    assert ticker_ext_links("") == []
