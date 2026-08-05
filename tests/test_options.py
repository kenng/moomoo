from datetime import date

from momo.domain.options import parse_option_code


def test_parse_us_put():
    info = parse_option_code("US.QCOM260911P140000")
    assert info is not None
    assert info.underlying_symbol == "US.QCOM"
    assert info.expiry == date(2026, 9, 11)
    assert info.option_type == "PUT"
    assert info.strike == 140.0
    assert info.multiplier == 100


def test_parse_us_call():
    info = parse_option_code("US.AAPL261218C200000")
    assert info is not None
    assert info.option_type == "CALL"
    assert info.strike == 200.0
    assert info.expiry == date(2026, 12, 18)


def test_stock_code_is_not_option():
    assert parse_option_code("HK.01810") is None
    assert parse_option_code("US.QCOM") is None
