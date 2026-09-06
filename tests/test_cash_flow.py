from momo.adapters.cash_flow import attach_holding, enrich_dividend


def test_enrich_infers_shares_from_sen_rate_without_inventing_ticker():
    row = enrich_dividend(
        {
            "cashflow_remark": "First Interim Dividend of 0.60 sen per ordinary share",
            "cashflow_amount": 77.40,
            "currency": "MYR",
        }
    )
    assert row["stock_code"] == ""
    assert row["stock_name"] == ""
    assert row["shares"] == 12900.0


def test_enrich_keeps_bursa_code_from_remark():
    row = enrich_dividend(
        {
            "cashflow_remark": "DXN (5318) - Fourth Interim Dividend of 0.70 sen per ordinary share",
            "cashflow_amount": 29.40,
            "currency": "MYR",
        }
    )
    assert row["stock_code"] == "MY.5318"
    assert row["stock_name"] == "DXN"
    assert row["shares"] == 4200.0


def test_enrich_keeps_sehk_code_and_explicit_shares():
    row = enrich_dividend(
        {
            "cashflow_remark": (
                "25 F/D-RMB0.25/SH(-10%), PAY IN HKD 0.25857/SH (NET) "
                "<SEHK 857 PETROCHINA> 2000 shares"
            ),
            "cashflow_amount": 517.14,
            "currency": "HKD",
        }
    )
    assert row["stock_code"] == "HK.00857"
    assert row["stock_name"] == "PETROCHINA"
    assert row["shares"] == 2000.0


def test_enrich_skips_share_guess_when_remark_has_two_rates():
    row = enrich_dividend(
        {
            "cashflow_remark": (
                "CRESNDO - Second Interim single tier dividend of 1 sen per share "
                "and Second special single tier dividend of 3 sen per share"
            ),
            "cashflow_amount": 32.00,
            "currency": "MYR",
        }
    )
    assert row["stock_name"] == "CRESNDO"
    assert row["shares"] is None


def test_attach_holding_fills_unique_my_position():
    row = enrich_dividend(
        {
            "acc_id": "1001",
            "cashflow_remark": "First Interim Dividend of 0.60 sen per ordinary share",
            "cashflow_amount": 77.40,
            "currency": "MYR",
        }
    )
    filled = attach_holding(
        row,
        [
            {"acc_id": 1001, "code": "HK.00857", "name": "PETROCHINA", "qty": 2000},
            {"acc_id": 1001, "code": "MY.5318", "name": "DXN", "qty": 12900},
        ],
    )
    assert filled["stock_code"] == "MY.5318"
    assert filled["stock_name"] == "DXN"
    assert filled["shares"] == 12900.0


def test_attach_holding_skips_ambiguous_qty():
    row = enrich_dividend(
        {
            "acc_id": "1001",
            "cashflow_remark": "First Interim Dividend of 0.60 sen per ordinary share",
            "cashflow_amount": 77.40,
            "currency": "MYR",
        }
    )
    filled = attach_holding(
        row,
        [
            {"acc_id": 1001, "code": "MY.5318", "name": "DXN", "qty": 12900},
            {"acc_id": 1001, "code": "MY.1155", "name": "MAYBANK", "qty": 12900},
        ],
    )
    assert filled["stock_code"] == ""
    assert filled["stock_name"] == ""


def test_enrich_skips_share_guess_when_amount_does_not_divide_evenly():
    row = enrich_dividend(
        {
            "cashflow_remark": "Advanced income distribution of 0.47 sen per CLMT Unit",
            "cashflow_amount": 11.42,
            "currency": "MYR",
        }
    )
    assert row["stock_name"] == "CLMT"
    assert row["shares"] is None


def test_attach_holding_skips_fund_and_options():
    row = enrich_dividend(
        {
            "acc_id": "1001",
            "cashflow_remark": "Fund Cash Dividend",
            "cashflow_amount": 0.44,
            "currency": "MYR",
        }
    )
    filled = attach_holding(
        row,
        [
            {"acc_id": 1001, "code": "MY.5318", "name": "DXN", "qty": 12900},
            {
                "acc_id": 1001,
                "code": "US.QCOM260911P140000",
                "name": "QCOM Put",
                "qty": 1,
            },
        ],
    )
    assert filled["stock_code"] == ""
    assert filled["shares"] is None
