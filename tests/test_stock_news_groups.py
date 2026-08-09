from momo.api.main import _group_position_stocks_by_market


def test_group_position_stocks_hk_us_my_order():
    groups = [
        {"code": "MY.6139", "name": "TAKAFUL", "position": {"qty": 100}},
        {"code": "US.QCOM", "name": "QUALCOMM", "position": {"qty": 10}},
        {"code": "HK.09988", "name": "BABA-W", "position": {"qty": 200}},
        {"code": "SG.D05", "name": "DBS", "position": {"qty": 50}},
    ]
    sections = _group_position_stocks_by_market(groups)
    assert [s["market"] for s in sections] == ["HK", "US", "MY", "OTHER"]
    assert [s["code"] for s in sections[0]["stocks"]] == ["09988"]
    assert [s["code"] for s in sections[1]["stocks"]] == ["QCOM"]
    assert sections[2]["stocks"][0]["klse_news_url"] == (
        "https://www.klsescreener.com/v2/news/stock/6139"
    )
    assert sections[0]["stocks"][0]["klse_news_url"] is None
    assert sections[3]["stocks"][0]["market"] == "SG"


def test_group_skips_empty_and_sorts_within_market():
    groups = [
        {"code": "MY.6139", "name": "TAKAFUL", "position": {"qty": 1}},
        {"code": "", "name": "x", "position": {"qty": 1}},
        {"code": "MY.1155", "name": "MAYBANK", "position": {"qty": 2}},
    ]
    sections = _group_position_stocks_by_market(groups)
    assert len(sections) == 1
    assert [s["code"] for s in sections[0]["stocks"]] == ["1155", "6139"]
