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


def test_interest_falls_back_to_cash_interest_paid():
    facts = {}
    add(facts, "InterestPaidNet", "USD", [_dur(2737e6, "2024-12-31", "2025-02-14")])
    assert sd.extract_annual(facts, "interestExpense")["values"] == {2024: 2737e6}
    add(facts, "InterestExpense", "USD", [_dur(2900e6, "2024-12-31", "2025-02-14")])
    assert sd.extract_annual(facts, "interestExpense")["values"] == {2024: 2900e6}   # expense preferred


def test_ebit_derived_for_years_without_an_operating_income_subtotal():
    facts = {}
    for y in range(2020, 2026):
        end, filed = f"{y}-12-31", f"{y + 1}-02-15"
        add(facts, "Revenues", "USD", [_dur(1000, end, filed)])
        add(facts, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "USD", [_dur(150, end, filed)])
        add(facts, "InterestExpense", "USD", [_dur(10, end, filed)])
        if y <= 2022:                                   # stopped reporting the subtotal
            add(facts, "OperatingIncomeLoss", "USD", [_dur(170, end, filed)])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["operatingIncome"] == [160, 160, 160, 170, 170, 170]
    assert "operatingIncome" in p["derived"]
    assert p["ebitIncludesEquityIncome"] is True        # latest EBIT came from pre-tax income


def test_equity_income_flag_follows_the_latest_year_and_the_pretax_tag():
    facts = demo_company()["facts"]
    facts["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"] = [
        f for f in facts["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"] if not f["end"].startswith("2020")]
    p = sd.parse_company_facts({"facts": facts})
    assert "operatingIncome" in p["derived"]            # 2020 was filled in
    assert p["ebitIncludesEquityIncome"] is False       # but 2025 is reported

    facts = {}
    for y in range(2020, 2026):
        end, filed = f"{y}-12-31", f"{y + 1}-02-15"
        add(facts, "Revenues", "USD", [_dur(1000, end, filed)])
        add(facts, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "USD", [_dur(150, end, filed)])
    p = sd.parse_company_facts({"facts": facts})
    assert p["ebitIncludesEquityIncome"] is False       # that pre-tax figure leaves equity income out


def test_gap_outside_the_shown_years_does_not_mark_ebit_derived():
    facts = demo_company()["facts"]
    for y in (2018, 2019):                         # pre-tax income only, older than shown
        add(facts, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "USD", [_dur(50e6, f"{y}-12-31", f"{y + 1}-02-15")])
    p = sd.parse_company_facts({"facts": facts})
    assert "operatingIncome" not in p["derived"]


def test_loss_carryforward_keeps_federal_and_total_apart():
    raw = demo_company()
    from demo_data import _inst
    facts = raw["facts"]
    add(facts, "DeferredTaxAssetsOperatingLossCarryforwards", "USD", [_inst(90e6, "2025-12-31", "2026-02-15")])
    add(facts, "DeferredTaxAssetsOperatingLossCarryforwardsDomestic", "USD", [_inst(60e6, "2025-12-31", "2026-02-15")])
    p = sd.parse_company_facts(raw)
    assert p["aligned"]["nolDTADomestic"][0] == pytest.approx(60e6)
    assert p["aligned"]["nolDTA"][0] == pytest.approx(90e6)


def test_loss_carryforward_falls_back_to_total():
    raw = demo_company()
    from demo_data import _inst
    add(raw["facts"], "DeferredTaxAssetsOperatingLossCarryforwards", "USD", [_inst(90e6, "2025-12-31", "2026-02-15")])
    p = sd.parse_company_facts(raw)
    assert p["aligned"]["nolDTA"][0] == pytest.approx(90e6)


def test_debt_reported_only_as_one_including_current_figure():
    """General Motors files its debt only as LongTermDebtAndCapitalLeaseObligations-
    IncludingCurrentMaturities; it used to come out as zero debt."""
    from demo_data import _inst
    facts = demo_company()["facts"]
    g = facts["us-gaap"]
    for k in [k for k in g if "Debt" in k or "Borrowings" in k or "CommercialPaper" in k or "FinanceLease" in k]:
        g.pop(k)
    add(facts, "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "USD",
        [_inst(131574e6, "2025-12-31", "2026-02-01")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["totalDebt"][0] == 131574e6
    assert p["latestBalance"]["values"]["totalDebt"] == 131574e6      # not zero on the latest balance sheet


def test_capex_tagged_as_other_productive_assets():
    """Verizon's capital spending since 2019."""
    facts = demo_company()["facts"]
    facts["us-gaap"].pop("PaymentsToAcquirePropertyPlantAndEquipment")
    add(facts, "PaymentsToAcquireOtherProductiveAssets", "USD", [_dur(17011e6, "2025-12-31", "2026-02-15")])
    assert sd.extract_annual(facts, "capex")["values"][2025] == 17011e6


def test_pension_status_stakes_impairments_and_year_end_are_read():
    from demo_data import _inst
    facts = demo_company()["facts"]
    add(facts, "DefinedBenefitPlanFundedStatusOfPlan", "USD", [_inst(-4e8, "2025-12-31", "2026-02-15")])
    add(facts, "EquityMethodInvestments", "USD", [_inst(9e8, "2025-12-31", "2026-02-15")])
    add(facts, "GoodwillImpairmentLoss", "USD", [_dur(5e7, "2025-12-31", "2026-02-15")])
    p = sd.parse_company_facts({"facts": facts})
    lb = p["latestBalance"]["values"]
    assert lb["pensionFundedStatus"] == -4e8 and lb["equityMethodInvestments"] == 9e8
    assert p["aligned"]["impairments"][0] == 5e7
    assert p["fiscalYearEnd"] == "2025-12-31"


def test_balance_item_dropped_from_latest_10k_is_not_carried_over():
    # Commercial paper was in the 2025 10-K but not the 2026 one (Microsoft):
    # it must not be added to the 2026 debt. A 10-K item still counts in a
    # later 10-Q that does not repeat it.
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-06-30", "2025-08-01"), _dur(110, "2026-06-30", "2026-08-01")])
    add(facts, "Assets", "USD", [_inst(900, "2025-06-30", "2025-08-01"), _inst(1000, "2026-06-30", "2026-08-01")])
    add(facts, "LongTermDebtNoncurrent", "USD", [_inst(400, "2025-06-30", "2025-08-01"), _inst(380, "2026-06-30", "2026-08-01")])
    add(facts, "CommercialPaper", "USD", [_inst(50, "2025-06-30", "2025-08-01")])
    add(facts, "DefinedBenefitPlanFundedStatusOfPlan", "USD", [_inst(-30, "2026-06-30", "2026-08-01")])
    lb = sd.parse_company_facts({"facts": facts})["latestBalance"]
    assert lb["values"]["totalDebt"] == 380
    add(facts, "Assets", "USD", [_inst(1010, "2026-09-30", "2026-10-30", form="10-Q")])
    add(facts, "LongTermDebtNoncurrent", "USD", [_inst(370, "2026-09-30", "2026-10-30", form="10-Q")])
    lb = sd.parse_company_facts({"facts": facts})["latestBalance"]
    assert lb["date"] == "2026-09-30" and lb["values"]["totalDebt"] == 370
    assert lb["values"]["pensionFundedStatus"] == -30


def test_share_count_ignores_negative_counts():
    parsed = {"aligned": {"basicShares": [-5e7, 1.0e9], "dilutedShares": [1.02e9]},
              "latestBalance": {"values": {"_bsShares": -1}}, "shares": None}
    s = sd.choose_share_count(parsed)
    assert s["value"] == pytest.approx(1.02e9)


def test_year_end_securities_tagged_only_in_a_later_10q_still_count():
    # NVIDIA: MarketableSecuritiesCurrent until fiscal 2025, then only
    # DebtSecuritiesCurrent, and the fiscal-2026 year-end figure appears only
    # as the comparative column of the next 10-Q.
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-01-26", "2025-02-26", start="2024-01-29"),
                                   _dur(150, "2026-01-25", "2026-02-25", start="2025-01-27")])
    add(facts, "Assets", "USD", [_inst(500, "2026-01-25", "2026-02-25"), _inst(520, "2026-04-26", "2026-05-20", form="10-Q")])
    add(facts, "MarketableSecuritiesCurrent", "USD", [_inst(34, "2025-01-26", "2025-02-26")])
    add(facts, "DebtSecuritiesCurrent", "USD", [_inst(39, "2026-01-25", "2026-05-20", form="10-Q"),
                                                _inst(37, "2026-04-26", "2026-05-20", form="10-Q")])
    # A 10-K figure beats a 10-Q comparative for the same date.
    add(facts, "CashAndCashEquivalentsAtCarryingValue", "USD", [_inst(10, "2026-01-25", "2026-02-25"),
                                                                _inst(11, "2026-01-25", "2026-05-20", form="10-Q")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["shortTermInvestments"] == [39, 34]
    assert p["aligned"]["cash"][0] == 10
    assert p["latestBalance"]["values"]["shortTermInvestments"] == 37


def test_old_balance_sheet_share_count_does_not_raise_stale_note():
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-12-31", "2026-02-01")])
    add(facts, "Assets", "USD", [_inst(500, "2025-12-31", "2026-02-01"), _inst(520, "2026-06-30", "2026-08-01", form="10-Q")])
    add(facts, "CommonStockSharesOutstanding", "shares", [_inst(1e9, "2025-12-31", "2026-02-01")])
    lb = sd.parse_company_facts({"facts": facts})["latestBalance"]
    assert "staleNote" not in lb


def test_current_portion_from_maturity_table_when_not_tagged_on_balance_sheet():
    # Caterpillar: no LongTermDebtCurrent; the current portion is only in the
    # maturity schedule.
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-12-31", "2026-02-13")])
    add(facts, "Assets", "USD", [_inst(900, "2025-12-31", "2026-02-13")])
    add(facts, "LongTermDebtNoncurrent", "USD", [_inst(30696, "2025-12-31", "2026-02-13")])
    add(facts, "ShortTermBorrowings", "USD", [_inst(5514, "2025-12-31", "2026-02-13")])
    add(facts, "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", "USD", [_inst(7120, "2025-12-31", "2026-02-13")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["totalDebt"][0] == 30696 + 5514 + 7120
    assert p["latestBalance"]["values"]["totalDebt"] == 30696 + 5514 + 7120
    # A balance-sheet tag, when present, wins over the maturity table.
    add(facts, "LongTermDebtCurrent", "USD", [_inst(7000, "2025-12-31", "2026-02-13")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["totalDebt"][0] == 30696 + 5514 + 7000


def test_item_blank_in_latest_10q_current_column_counts_as_zero():
    # The latest 10-Q shows commercial paper only in its year-end comparative
    # column: it has been repaid, so it must not be carried into today's debt.
    # An item the 10-Q doesn't show at all (a 10-K note item) still carries.
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2026-02-01", "2026-03-18")])
    add(facts, "Assets", "USD", [_inst(900, "2026-02-01", "2026-03-18"),
                                 _inst(950, "2026-08-02", "2026-08-26", form="10-Q")])
    add(facts, "LongTermDebtNoncurrent", "USD", [_inst(400, "2026-02-01", "2026-03-18"),
                                                 _inst(390, "2026-08-02", "2026-08-26", form="10-Q")])
    add(facts, "CommercialPaper", "USD", [_inst(44, "2026-02-01", "2026-03-18"),
                                          _inst(44, "2026-02-01", "2026-08-26", form="10-Q")])
    add(facts, "FinanceLeaseLiability", "USD", [_inst(12, "2026-02-01", "2026-03-18")])
    lb = sd.parse_company_facts({"facts": facts})["latestBalance"]
    assert lb["values"]["totalDebt"] == 390 + 12


def test_dna_fallback_fills_missing_years_from_da_and_impairment_tag():
    # Tesla: DepreciationDepletionAndAmortization until 2017, then only
    # "Depreciation, amortization and impairment" (company namespace).
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2016-12-31", "2017-02-01"), _dur(200, "2025-12-31", "2026-01-29")])
    add(facts, "DepreciationDepletionAndAmortization", "USD", [_dur(5, "2016-12-31", "2017-02-01")])
    add(facts, "DepreciationAmortizationAndImpairment", "USD", [_dur(12, "2025-12-31", "2026-01-29")], ns="tsla")
    add(facts, "AssetImpairmentCharges", "USD", [_dur(2, "2025-12-31", "2026-01-29")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["dna"] == [10, 5]


def test_negative_interest_expense_is_read_as_a_cost():
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-09-27", "2025-11-13")])
    add(facts, "InterestExpense", "USD", [_dur(-18, "2025-09-27", "2025-11-13")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["interestExpense"] == [18]


def test_noncurrent_notes_payable_count_as_long_term_debt():
    # Oracle: no LongTermDebtNoncurrent; bonds are LongTermNotesPayable.
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2026-05-31", "2026-06-22")])
    add(facts, "Assets", "USD", [_inst(900, "2026-05-31", "2026-06-22")])
    add(facts, "DebtCurrent", "USD", [_inst(7, "2026-05-31", "2026-06-22")])
    add(facts, "LongTermNotesPayable", "USD", [_inst(122, "2026-05-31", "2026-06-22")])
    p = sd.parse_company_facts({"facts": facts})
    assert p["aligned"]["totalDebt"][0] == 129


def test_debt_parts_never_negative():
    vals = {"_ltDebtTotal": 5, "_ltDebtCurrent": 6}
    total, parts, _cur = sd.compose_debt(vals.get)
    assert total == 6 and min(parts.values()) >= 0


def test_share_counts_tagged_in_millions_are_rescaled():
    # McDonald's tags weighted shares as 713.4 (millions).
    parsed = {"aligned": {"basicShares": [713.4], "dilutedShares": [716.4]},
              "shares": {"value": 707641531, "classes": 1}}
    s = sd.choose_share_count(parsed)
    assert not s["needsCheck"] and s["value"] == pytest.approx(707641531 * 716.4 / 713.4)
    parsed = {"aligned": {"basicShares": [713.4], "dilutedShares": [716.4]}, "shares": None}
    assert sd.choose_share_count(parsed)["value"] == pytest.approx(716.4e6)


def test_retiree_plan_liability_from_balance_sheet_when_no_total_funded_status():
    # Lockheed / Procter & Gamble: no total funded-status tag, but the
    # underfunded plans sit on the balance sheet as noncurrent liabilities.
    from demo_data import _inst
    facts = {}
    add(facts, "Revenues", "USD", [_dur(100, "2025-12-31", "2026-02-01")])
    add(facts, "Assets", "USD", [_inst(900, "2025-12-31", "2026-02-01"), _inst(950, "2026-06-28", "2026-07-25", form="10-Q")])
    add(facts, "DefinedBenefitPensionPlanLiabilitiesNoncurrent", "USD", [_inst(39, "2025-12-31", "2026-02-01")])
    add(facts, "OtherPostretirementDefinedBenefitPlanLiabilitiesNoncurrent", "USD", [_inst(6, "2025-12-31", "2026-02-01")])
    lb = sd.parse_company_facts({"facts": facts})["latestBalance"]
    assert lb["values"]["retireeLiability"] == 45
    assert "staleNote" not in lb                  # annual-only plan figures are expected
    # The combined tag already includes retiree medical: not added twice.
    add(facts, "PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent", "USD",
        [_inst(44, "2025-12-31", "2026-02-01")])
    assert sd.parse_company_facts({"facts": facts})["latestBalance"]["values"]["retireeLiability"] == 44
