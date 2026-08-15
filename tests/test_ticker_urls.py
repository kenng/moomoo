from momo.ticker_urls import simplywall_url, yahoo_quote_symbol, yahoo_quote_url


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
    assert simplywall_url("MY.1155") == "https://simplywall.st/stock/klse/1155"
    assert simplywall_url("HK.01810") == "https://simplywall.st/stock/sehk/1810"
    assert simplywall_url("HK.00700") == "https://simplywall.st/stock/sehk/700"
    assert simplywall_url("US.AAPL") == "https://simplywall.st/stock/nasdaqgs/aapl"
    assert simplywall_url("SG.D05") == "https://simplywall.st/stock/sgx/d05"
    assert simplywall_url("") == ""
