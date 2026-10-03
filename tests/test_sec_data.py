import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import market_data as md  # noqa: E402
import sec_data as sd  # noqa: E402
from demo_data import _dur, add, demo_company  # noqa: E402


def test_demo_company_parses_six_years_newest_first():
    p = sd.parse_company_facts(demo_company())
    assert p["years"] == [2025, 2024, 2023, 2022, 2021, 2020]
    assert p["aligned"]["revenue"][0] == pytest.approx(1288.4e6)
    assert p["aligned"]["operatingIncome"][0] == pytest.approx(1288.4e6 * 0.16)


def test_fiscal_label_handles_52_53_week_years():
    # Two different fiscal years that both "end" in calendar 2025 on raw dates.
    assert sd.fiscal_label("2024-12-28") == 2024
    assert sd.fiscal_label("2025-01-03") == 2024     # 53-week FY2024 spilling into January
    assert sd.fiscal_label("2026-01-02") == 2025
    assert sd.fiscal_label("2025-01-31") == 2025     # Walmart-style Jan 31 year end stays
    facts = {}
    for end, start, val in [("2023-12-30", "2023-01-01", 100), ("2025-01-04", "2023-12-31", 110),
                            ("2026-01-03", "2025-01-05", 121)]:
        add(facts, "Revenues", "USD", [_dur(val, end, end[:4] + "-03-01", start=start)])
    s = sd.extract_annual(facts, "revenue")
    assert s["values"] == {2023: 100, 2024: 110, 2025: 121}


def test_tag_switch_keeps_full_history():
    facts = {}
    add(facts, "SalesRevenueNet", "USD", [_dur(90, "2016-12-31", "2017-02-01"),
                                          _dur(95, "2017-12-31", "2018-02-01")])
    add(facts, "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
        [_dur(100, "2018-12-31", "2019-02-01"), _dur(105, "2019-12-31", "2020-02-01")])
    s = sd.extract_annual(facts, "revenue")
    assert s["values"] == {2016: 90, 2017: 95, 2018: 100, 2019: 105}


def test_restated_value_wins():
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2022-12-31", "2023-02-01"),
                                   _dur(98, "2022-12-31", "2024-02-01")])   # restated next year
    assert sd.extract_annual(facts, "revenue")["values"][2022] == 98


def test_quarterly_and_ytd_facts_ignored():
    facts = {}
    add(facts, "Revenues", "USD", [_dur(25, "2022-03-31", "2022-05-01", form="10-Q", start="2022-01-01"),
                                   _dur(60, "2022-06-30", "2023-02-01", start="2022-01-01"),   # 6 months inside a 10-K
                                   _dur(100, "2022-12-31", "2023-02-01")])
    assert sd.extract_annual(facts, "revenue")["values"] == {2022: 100}


def test_revenue_takes_total_when_tags_overlap():
    facts = {}
    add(facts, "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", [_dur(90, "2022-12-31", "2023-02-01")])
    add(facts, "Revenues", "USD", [_dur(100, "2022-12-31", "2023-02-01")])  # includes lease income
    assert sd.extract_annual(facts, "revenue")["values"][2022] == 100


def test_dna_found_in_company_namespace():
    facts = {"us-gaap": {}}
    add(facts, "DepreciationAmortizationAndOther", "USD", [_dur(42, "2022-06-30", "2022-08-01")], ns="msft")
    add(facts, "DepreciationAndAmortization", "USD", [_dur(30, "2022-06-30", "2022-08-01")])
    assert sd.extract_annual(facts, "dna")["values"][2022] == 42


def test_debt_does_not_double_count_current_portion():
    vals = {"_ltDebtTotal": 300, "_ltDebtCurrent": 50, "_shortBorrowings": 20}
    total, parts, cur = sd.compose_debt(vals.get)
    assert total == 320          # 300 total LTD (incl. 50 current) + 20 CP
    assert cur == 70


def test_debt_includes_finance_leases_and_commercial_paper():
    vals = {"_ltDebtNoncurrent": 1000, "_ltDebtCurrent": 100, "_shortBorrowings": 50, "_financeLease": 30}
    total, parts, cur = sd.compose_debt(vals.get)
    assert total == 1180
    assert "Finance lease liabilities" in parts


def test_debt_current_total_tag_preferred():
    vals = {"_debtCurrent": 150, "_ltDebtCurrent": 100, "_shortBorrowings": 50, "_ltDebtNoncurrent": 900}
    total, _parts, cur = sd.compose_debt(vals.get)
    assert (total, cur) == (1050, 150)


def test_leases_not_added_twice_when_inside_debt_tag():
    vals = {"_ltDebtAndLeasesNoncurrent": 500, "_financeLease": 40}
    total, _parts, _cur = sd.compose_debt(vals.get)
    assert total == 500


def test_latest_balance_sheet_comes_from_latest_10q():
    p = sd.parse_company_facts(demo_company())
    lb = p["latestBalance"]
    assert lb["date"] == "2026-06-30"
    assert lb["values"]["cash"] == 150e6
    assert lb["values"]["totalDebt"] == 265e6


def test_multiclass_shares_are_summed():
    facts = {"us-gaap": {}}
    add(facts, "EntityCommonStockSharesOutstanding", "shares", [
        {"val": 5.8e9, "end": "2026-07-20", "form": "10-Q", "filed": "2026-07-25", "accn": "A"},
        {"val": 0.86e9, "end": "2026-07-20", "form": "10-Q", "filed": "2026-07-25", "accn": "A"},
        {"val": 5.4e9, "end": "2026-07-20", "form": "10-Q", "filed": "2026-07-25", "accn": "A"},
        {"val": 12.5e9, "end": "2026-04-20", "form": "10-Q", "filed": "2026-04-25", "accn": "B"},
    ], ns="dei")
    s = sd.shares_outstanding(facts)
    assert s["value"] == pytest.approx(12.06e9) and s["classes"] == 3


def test_share_count_falls_back_when_classes_have_unequal_economics():
    parsed = {"aligned": {"basicShares": [1.44e6], "dilutedShares": [1.44e6]},
              "shares": {"value": 1.3e9}}          # class B count summed with class A
    s = sd.choose_share_count(parsed)
    assert s["value"] == pytest.approx(1.44e6)


def test_share_count_applies_dilution():
    s = sd.choose_share_count(sd.parse_company_facts(demo_company()))
    assert s["value"] == pytest.approx(99.5e6 * 1.02)


def test_ifrs_filer_gets_clear_error():
    with pytest.raises(ValueError, match="IFRS"):
        sd.parse_company_facts({"facts": {"ifrs-full": {"Revenue": {}}}})


def test_ebit_derived_when_missing():
    facts = {}
    add(facts, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "USD", [_dur(80, "2022-12-31", "2023-02-01")])
    add(facts, "InterestExpense", "USD", [_dur(20, "2022-12-31", "2023-02-01")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["operatingIncome"][0] == 100
    assert "operatingIncome" in p["derived"]


def test_every_sic_mapping_points_at_a_real_industry():
    for _lo, _hi, name in md._SIC_RANGES:
        assert name in md.INDUSTRIES, name
    assert md.industry_for_sic("3674") == "Semiconductor"
    assert md.industry_for_sic("7372") == "Software (System & Application)"
    assert md.industry_for_sic("2834") == "Drugs (Pharmaceutical)"
    assert md.industry_for_sic(None) == md.DEFAULT_INDUSTRY
    # Spot checks against companies whose filing code is easy to misread.
    assert md.industry_for_sic("2080") == "Beverage (Soft)"         # Coca-Cola, PepsiCo
    assert md.industry_for_sic("2082") == "Beverage (Alcoholic)"    # brewers
    assert md.industry_for_sic("3021") == "Shoe"                    # Nike
    assert md.industry_for_sic("2670") == "Diversified"             # 3M
    assert md.industry_for_sic("4210") == "Transportation"          # UPS
    assert md.industry_for_sic("4213") == "Trucking"


def test_synthetic_rating():
    assert md.synthetic_rating(100, 10, 10e9)[:2] == ("AAA", 0.004)     # 10x, large
    assert md.synthetic_rating(100, 10, 1e9)[:2] == ("AA", 0.0055)      # 10x, small firm table
    assert md.synthetic_rating(100, 40, 10e9)[0] == "BBB"               # 2.5x is the BBB floor
    assert md.synthetic_rating(100, 0, 10e9)[0] == "AAA"
    assert md.synthetic_rating(-5, 10, 10e9)[0] == "D"


def test_excise_taxes_are_not_revenue():
    facts = {}
    add(facts, "RevenueFromContractWithCustomerIncludingAssessedTax", "USD", [_dur(125, "2022-12-31", "2023-02-01")])
    add(facts, "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", [_dur(100, "2022-12-31", "2023-02-01")])
    assert sd.extract_annual(facts, "revenue")["values"][2022] == 100


def test_stale_current_debt_total_does_not_override_fresh_components():
    vals = {"_debtCurrent": 500, "_ltDebtCurrent": 80, "_shortBorrowings": 20, "_ltDebtNoncurrent": 1000}
    dates = {"_debtCurrent": "2025-12-31", "_ltDebtCurrent": "2026-06-30", "_shortBorrowings": "2026-06-30",
             "_ltDebtNoncurrent": "2026-06-30"}
    total, _parts, cur = sd.compose_debt(vals.get, dates.get)
    assert (total, cur) == (1100, 100)


def test_current_finance_lease_counts_as_current_debt_for_working_capital():
    vals = {"_ltDebtCurrent": 50, "_ltDebtNoncurrent": 500, "_financeLeaseCurrent": 5, "_financeLeaseNoncurrent": 40}
    total, _parts, cur = sd.compose_debt(vals.get)
    assert total == 595 and cur == 55


def test_share_count_flags_a_missing_share_class():
    parsed = {"aligned": {"basicShares": [12.2e9], "dilutedShares": [12.3e9]},
              "shares": {"value": 5.8e9, "classes": 1}}       # only class A on the cover
    s = sd.choose_share_count(parsed)
    assert s["needsCheck"] and s["value"] == pytest.approx(12.3e9)


def test_share_count_uses_balance_sheet_when_weighted_missing():
    parsed = {"aligned": {}, "latestBalance": {"values": {"_bsShares": 1.0e9}},
              "shares": {"value": 1.01e9, "classes": 1}}
    s = sd.choose_share_count(parsed)
    assert not s["needsCheck"] and s["value"] == pytest.approx(1.01e9)


def test_subsequent_event_balance_does_not_replace_year_end():
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-06-30", "2025-08-20")])
    # Year-end debt, and a later "as of" figure disclosed in the same 10-K.
    from demo_data import _inst
    add(facts, "LongTermDebtNoncurrent", "USD", [_inst(500, "2025-06-30", "2025-08-20"),
                                                 _inst(900, "2025-08-15", "2025-08-20")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["totalDebt"][0] == 500
