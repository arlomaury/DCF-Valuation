"""
SEC EDGAR data: download a company's XBRL "company facts" and turn them into
clean annual series plus a point-in-time balance sheet.

Everything here is pure parsing except the two fetch_* functions, so the
parsing is unit-tested against fixture data (tests/test_sec_data.py).

Design notes - each of these fixed a real mis-valuation:

* Fiscal years are labelled from the period END date minus 7 days. 52/53-week
  companies end their year on dates like 2023-12-30 and 2025-01-04; labelling
  by the raw end year gave two different fiscal years the same label and one
  was silently dropped.
* Line items are merged across XBRL tags year by year. Companies switch tags
  (e.g. SalesRevenueNet -> RevenueFromContractWithCustomer... in 2018), and
  picking a single tag lost the older years.
* Debt is built from its parts (current + noncurrent + finance leases) instead
  of picking one tag. Picking one either double-counted the current portion
  (LongTermDebt already includes it) or missed commercial paper / leases.
* Shares outstanding come from the most recent filing cover page (10-Q or
  10-K) and are summed across share classes (e.g. Alphabet's A, B and C).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A"}
BALANCE_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A"}
MAX_YEARS = 6          # 6 years of history -> 5 years of growth rates

# --------------------------------------------------------------------------
# Concept map.  key -> (list of tags in priority order, how to combine, kind)
#   combine: "first" = first tag (in order) that reports that year
#            "max"   = largest value reported across tags that year (used where
#                      tags are nested subsets of each other, e.g. revenue)
#   kind:    "duration" (income / cash-flow) or "instant" (balance sheet)
# Taxonomy is us-gaap unless a tag is written "ns:Tag".  "*:Tag" searches every
# company-specific namespace too (some large filers tag D&A with their own).
# --------------------------------------------------------------------------
CONCEPTS = {
    # Income statement
    "revenue": (["RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
                 "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"],
                "max", "duration"),
    "cogs": (["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
              "CostOfServices"], "first", "duration"),
    "grossProfit": (["GrossProfit"], "first", "duration"),
    "operatingIncome": (["OperatingIncomeLoss"], "first", "duration"),
    # InterestPaidNet (cash interest paid) is the last resort: some large
    # filers (NextEra, for one) tag their income-statement interest with their
    # own labels, and without any figure the credit rating has to be assumed.
    "interestExpense": (["InterestExpense", "InterestExpenseNonoperating",
                         "InterestExpenseDebt", "InterestAndDebtExpense",
                         "InterestPaidNet"],
                        "first", "duration"),
    # One-time write-downs inside operating income (goodwill, acquired
    # intangibles, other assets). Excluded when setting the default margin,
    # the way analysts normalize a base year. "max" because the tags overlap
    # (AssetImpairmentCharges often includes the others).
    "impairments": (["GoodwillImpairmentLoss", "ImpairmentOfIntangibleAssetsExcludingGoodwill",
                     "ImpairmentOfIntangibleAssetsFinitelived",
                     "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill",
                     "GoodwillAndIntangibleAssetImpairment", "AssetImpairmentCharges"], "max", "duration"),
    "preTaxIncome": (["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
                     "first", "duration"),
    "incomeTax": (["IncomeTaxExpenseBenefit"], "first", "duration"),
    "netIncome": (["NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
                  "first", "duration"),
    # Cash flow statement
    "dna": (["*:DepreciationDepletionAndAmortization",
             "*:DepreciationAmortizationAndAccretionNet",
             "*:DepreciationAndAmortization",
             "*:DepreciationAmortizationAndOther"], "max", "duration"),
    "_depreciation": (["Depreciation", "DepreciationNonproduction"], "first", "duration"),
    "_amortization": (["AmortizationOfIntangibleAssets"], "first", "duration"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment",
               "PaymentsToAcquireProductiveAssets",
               "PaymentsForCapitalImprovements",
               "PaymentsToAcquireOtherPropertyPlantAndEquipment",
               # Verizon's capital spending since 2019.
               "PaymentsToAcquireOtherProductiveAssets"], "first", "duration"),
    # Assets acquired with new finance leases: non-cash, but economically capex
    # (the matching liability is counted as debt below).
    "financeLeaseAdditions": (["RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability"],
                              "first", "duration"),
    "sbc": (["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
            "first", "duration"),
    "dilutedShares": (["WeightedAverageNumberOfDilutedSharesOutstanding"], "first", "duration"),
    "_bsShares": (["CommonStockSharesOutstanding"], "first", "instant"),
    "basicShares": (["WeightedAverageNumberOfSharesOutstandingBasic"], "first", "duration"),
    # Balance sheet
    "totalAssets": (["Assets"], "first", "instant"),
    "currentAssets": (["AssetsCurrent"], "first", "instant"),
    "currentLiabilities": (["LiabilitiesCurrent"], "first", "instant"),
    "cash": (["CashAndCashEquivalentsAtCarryingValue", "Cash",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
             "first", "instant"),
    "shortTermInvestments": (["ShortTermInvestments", "MarketableSecuritiesCurrent",
                              "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                              "AvailableForSaleSecuritiesCurrent",
                              # NVIDIA's tag since fiscal 2026.
                              "DebtSecuritiesCurrent"], "first", "instant"),
    "cashAndSTI": (["CashCashEquivalentsAndShortTermInvestments"], "first", "instant"),
    "longTermInvestments": (["MarketableSecuritiesNoncurrent",
                             "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
                             "LongTermInvestments", "DebtSecuritiesNoncurrent"], "first", "instant"),
    "_debtCurrent": (["DebtCurrent"], "first", "instant"),
    # Last resort: the debt-maturity table's "due within 12 months", which is
    # the current portion. Caterpillar tags its ~$7B current portion only there.
    "_ltDebtCurrent": (["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
                        "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"],
                       "first", "instant"),
    "_shortBorrowings": (["ShortTermBorrowings", "CommercialPaper"], "first", "instant"),
    "_ltDebtNoncurrent": (["LongTermDebtNoncurrent"], "first", "instant"),
    "_ltDebtAndLeasesNoncurrent": (["LongTermDebtAndCapitalLeaseObligations"], "first", "instant"),
    "_ltDebtTotal": (["LongTermDebt"], "first", "instant"),
    # Current and noncurrent debt and finance leases in one figure. General
    # Motors reports its debt only this way; without it GM showed no debt.
    "_debtAndLeasesInclCurrent": (["LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"],
                                  "first", "instant"),
    "_financeLease": (["FinanceLeaseLiability"], "first", "instant"),
    "_financeLeaseNoncurrent": (["FinanceLeaseLiabilityNoncurrent"], "first", "instant"),
    "_financeLeaseCurrent": (["FinanceLeaseLiabilityCurrent"], "first", "instant"),
    "operatingLeaseLiability": (["OperatingLeaseLiability"], "first", "instant"),
    "minorityInterest": (["MinorityInterest"], "first", "instant"),
    # Pension and retiree-medical plans: funded status (negative = deficit).
    # A deficit is debt owed to retirees and comes off equity value, after tax.
    "pensionFundedStatus": (["DefinedBenefitPlanFundedStatusOfPlan"], "first", "instant"),
    # Stakes in companies that are not consolidated (Coca-Cola's bottlers).
    # Their profits sit below operating income, so their value is added in
    # the equity bridge or it would be lost.
    "equityMethodInvestments": (["EquityMethodInvestments"], "first", "instant"),
    # Tax value of loss carryforwards, from the 10-K tax footnote (annual).
    # Kept as two keys because they are grossed up at different rates: the
    # domestic tag is the FEDERAL asset (losses x 21%), the total one mixes
    # federal, state and foreign.
    "nolDTADomestic": (["DeferredTaxAssetsOperatingLossCarryforwardsDomestic"], "first", "instant"),
    "nolDTA": (["DeferredTaxAssetsOperatingLossCarryforwards"], "first", "instant"),
    "preferredStock": (["PreferredStockValue"], "first", "instant"),
}

BALANCE_KEYS = ["_bsShares", "cash", "shortTermInvestments", "cashAndSTI", "longTermInvestments",
                "minorityInterest", "preferredStock", "operatingLeaseLiability",
                "pensionFundedStatus", "equityMethodInvestments",
                "currentAssets", "currentLiabilities", "totalAssets",
                "_debtCurrent", "_ltDebtCurrent", "_shortBorrowings", "_ltDebtNoncurrent",
                "_ltDebtAndLeasesNoncurrent", "_ltDebtTotal", "_financeLease",
                "_financeLeaseNoncurrent", "_financeLeaseCurrent", "_debtAndLeasesInclCurrent"]

KNOWN_STANDARD_NS = {"us-gaap", "dei", "srt", "ifrs-full", "invest"}


def _d(s):
    return date.fromisoformat(s)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _valid_date(s):
    """True for a YYYY-MM-DD string.  One malformed fact must be skipped,
    not crash the whole company."""
    if not isinstance(s, str):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def fiscal_label(end: str) -> int:
    """Fiscal-year label for a period ending on `end`.

    Subtracting a week maps 52/53-week year ends that spill into the first
    days of January (e.g. 2025-01-04) back onto the year they belong to,
    while leaving normal Dec/Jan month-ends alone (Walmart's FY2025 ends
    2025-01-31 and is still labelled 2025, matching its own naming)."""
    return (_d(end) - timedelta(days=7)).year


def _period_days(fact):
    if not fact.get("start") or not fact.get("end"):
        return None
    try:
        return (_d(fact["end"]) - _d(fact["start"])).days
    except ValueError:
        return None


def _tag_sources(facts, tag):
    """Yield (namespace, unit-list) for a tag spec like 'X', 'ns:X' or '*:X'."""
    if ":" in tag:
        ns, name = tag.split(":", 1)
    else:
        ns, name = "us-gaap", tag
    namespaces = [ns]
    if ns == "*":
        namespaces = ["us-gaap"] + [n for n in facts if n not in KNOWN_STANDARD_NS]
    for n in namespaces:
        concept = facts.get(n, {}).get(name)
        if not concept:
            continue
        units = concept.get("units", {})
        for unit_key in ("USD", "shares"):
            if unit_key in units:
                yield f"{n}:{name}" if n != "us-gaap" else name, units[unit_key]


def _annual_by_year(entries, kind, fy_ends=None):
    """{fiscal_year: fact} for annual facts, keeping the most recently filed
    (i.e. restated) value for each year.

    For balance-sheet (instant) facts, `fy_ends` is the set of fiscal year-end
    dates; values dated anything else - e.g. a debt figure "as of" a date
    after year end, disclosed in a note - are ignored so they cannot replace
    the year-end balance."""
    # A year-end balance can also come from the comparative column of a later
    # 10-Q (NVIDIA tagged its fiscal 2026 securities only there), but a 10-K
    # figure always wins over one.
    forms = BALANCE_FORMS if kind == "instant" and fy_ends is not None else ANNUAL_FORMS
    rank = lambda f: (f.get("form") in ANNUAL_FORMS, f.get("filed", ""), f.get("end", ""))  # noqa: E731
    out = {}
    for f in entries:
        if f.get("form") not in forms or not _is_num(f.get("val")) or not _valid_date(f.get("end")):
            continue
        days = _period_days(f)
        if kind == "duration":
            # A full fiscal year: 52 weeks = 364 days, 53 weeks = 371.
            if days is None or not (350 <= days <= 380):
                continue
        elif days is not None:
            continue                    # instants have no start date
        elif fy_ends is not None and f["end"] not in fy_ends:
            continue
        try:
            yr = fiscal_label(f["end"])
        except ValueError:
            continue
        prev = out.get(yr)
        if prev is None or rank(f) > rank(prev):
            out[yr] = f
    return out


def extract_annual(facts, key, fy_ends=None):
    """Return {'values': {year: val}, 'tags': {year: tag}, 'ends': {year: date}} or None."""
    tags, combine, kind = CONCEPTS[key]
    per_year = {}                      # year -> list of (priority, tag, val, end)
    ends = {}
    for prio, spec in enumerate(tags):
        for tag, entries in _tag_sources(facts, spec):
            for yr, f in _annual_by_year(entries, kind, fy_ends if kind == "instant" else None).items():
                per_year.setdefault(yr, []).append((prio, tag, f["val"]))
                ends.setdefault(yr, f["end"])
    if not per_year:
        return None
    values, used = {}, {}
    for yr, cands in per_year.items():
        if key == "revenue" and any(t == "RevenueFromContractWithCustomerExcludingAssessedTax" for _p, t, _v in cands):
            # Excise / sales taxes collected for the government are not the
            # company's revenue; never let the tax-inclusive figure win on size.
            cands = [c for c in cands if c[1] != "RevenueFromContractWithCustomerIncludingAssessedTax"]
        if combine == "max":
            prio, tag, val = max(cands, key=lambda c: (c[2], -c[0]))
        else:
            prio, tag, val = min(cands, key=lambda c: c[0])
        values[yr], used[yr] = val, tag
    return {"values": values, "tags": used, "ends": ends}


def extract_latest_instant(facts, key, as_of=None, max_age_days=400, not_before=None):
    """Most recent balance-sheet value from any 10-K or 10-Q.

    If `as_of` is given, prefer a value dated exactly then (so all balance
    sheet items come from the same report); otherwise fall back to the most
    recent value no older than `max_age_days` before `as_of`. A value dated
    before `not_before` (the last fiscal year-end) is ignored: the latest 10-K
    left it out, which almost always means it is now zero."""
    tags, _combine, _kind = CONCEPTS[key]
    best = None                         # (end, prio, filed, val, tag)
    for prio, spec in enumerate(tags):
        for tag, entries in _tag_sources(facts, spec):
            for f in entries:
                if f.get("form") not in BALANCE_FORMS or f.get("start") or not _is_num(f.get("val")):
                    continue
                end = f.get("end")
                if not _valid_date(end):
                    continue
                if as_of and end > as_of:
                    continue
                cand = (end, -prio, f.get("filed", ""), f["val"], tag)
                if best is None or cand[:3] > best[:3]:
                    best = cand
    if best is None:
        return None
    end, _p, _filed, val, tag = best
    if as_of and (_d(as_of) - _d(end)).days > max_age_days:
        return None
    if not_before and end < not_before:
        return None
    return {"value": val, "date": end, "tag": tag}


def latest_balance_date(facts):
    """Date of the most recent balance sheet (latest 10-Q or 10-K)."""
    for key in ("totalAssets", "currentAssets"):
        v = extract_latest_instant(facts, key)
        if v:
            return v["date"]
    return None


def shares_outstanding(facts):
    """Shares outstanding from the most recent filing cover page.

    Multi-class companies report one value per class in the same filing; those
    are summed.  Returns {'value', 'date', 'classes'} or None."""
    concept = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not concept:
        return None
    entries = [f for f in concept.get("units", {}).get("shares", [])
               if f.get("form") in BALANCE_FORMS and _is_num(f.get("val"))
               and f["val"] > 0 and _valid_date(f.get("end"))]
    if not entries:
        return None
    latest = max(entries, key=lambda f: (f.get("filed", ""), f.get("end", "")))
    same = [f for f in entries if f.get("accn") == latest.get("accn") and f.get("end") == latest.get("end")]
    return {"value": sum(f["val"] for f in same), "date": latest.get("end"), "classes": len(same)}


def _sum_present(*vals):
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def compose_debt(get, date_of=None):
    """Total debt from components.  `get(key)` returns a number or None;
    `date_of(key)` (optional) returns the date that value is from.
    Returns (total, parts, current_debt)."""
    parts = {}
    # Current: one total tag if reported, else current LTD + short-term borrowings.
    cur = get("_debtCurrent")
    if cur is not None and date_of:
        # A stale total (e.g. only in the last 10-K) must not override fresher
        # components reported in the latest 10-Q.
        newer = [date_of(k) for k in ("_ltDebtCurrent", "_shortBorrowings") if get(k) is not None]
        if newer and max(newer) > (date_of("_debtCurrent") or ""):
            cur = None
    if cur is not None:
        parts["Current debt"] = cur
    else:
        ltdc, stb = get("_ltDebtCurrent"), get("_shortBorrowings")
        if ltdc is not None:
            parts["Current portion of long-term debt"] = ltdc
        if stb is not None:
            parts["Short-term borrowings / commercial paper"] = stb
        cur = _sum_present(ltdc, stb)
    # Noncurrent: LongTermDebt includes the current portion, so it is only used
    # (net of that portion) when the noncurrent tag is missing.
    leases_inside = False
    nonc = get("_ltDebtNoncurrent")
    if nonc is None:
        nonc = get("_ltDebtAndLeasesNoncurrent")
        leases_inside = nonc is not None
    if nonc is None:
        total_ltd = get("_ltDebtTotal")
        if total_ltd is not None:
            # LongTermDebt includes its current portion, which is already
            # counted in `cur` above whenever it was reported.
            nonc = total_ltd - (get("_ltDebtCurrent") or 0)
    if nonc is None:
        incl = get("_debtAndLeasesInclCurrent")
        if incl is not None:
            nonc = incl - (cur or 0)
            leases_inside = True
    if nonc is not None:
        parts["Long-term debt" + (" (incl. finance leases)" if leases_inside else "")] = nonc
    # Finance leases are debt; operating leases are left in operating costs.
    fl = None
    if not leases_inside:
        fl = get("_financeLease")
        if fl is None:
            fl = _sum_present(get("_financeLeaseNoncurrent"), get("_financeLeaseCurrent"))
        if fl:
            parts["Finance lease liabilities"] = fl
    total = _sum_present(cur, nonc, fl)
    # Current debt for working-capital purposes also includes the current
    # part of finance leases (it sits in current liabilities too).
    cur_all = (cur or 0) + ((get("_financeLeaseCurrent") or 0) if not leases_inside else 0)
    return (total or 0), parts, cur_all


def parse_company_facts(raw):
    """Turn a companyfacts JSON document into the structure the UI uses."""
    facts = raw.get("facts", {})
    if not facts.get("us-gaap"):
        if facts.get("ifrs-full"):
            raise ValueError("This company files under IFRS (a foreign filer). "
                             "The model only reads US GAAP filings.")
        raise ValueError("No US GAAP financial data found for this company.")

    # Income-statement items first; their period end dates define the fiscal
    # year-ends that balance-sheet items must be dated at.
    series = {k: extract_annual(facts, k) for k, v in CONCEPTS.items() if v[2] == "duration"}
    fy_ends = set()
    for k in ("revenue", "operatingIncome", "netIncome", "preTaxIncome"):
        if series.get(k):
            fy_ends |= set(series[k]["ends"].values())
    for k, v in CONCEPTS.items():
        if v[2] == "instant":
            series[k] = extract_annual(facts, k, fy_ends or None)

    # D&A fallback: depreciation + amortization of intangibles.
    if not series.get("dna"):
        dep, amt = series.get("_depreciation"), series.get("_amortization")
        if dep or amt:
            yrs = set((dep or {}).get("values", {})) | set((amt or {}).get("values", {}))
            vals = {y: ((dep or {}).get("values", {}).get(y) or 0) + ((amt or {}).get("values", {}).get(y) or 0)
                    for y in yrs}
            series["dna"] = {"values": vals, "tags": {y: "Depreciation+AmortizationOfIntangibleAssets" for y in yrs}}

    # EBIT fallback: pre-tax income + interest expense, marked as derived. Per
    # year, not only when the tag is absent altogether: some companies stop
    # reporting an operating-income subtotal (Johnson & Johnson), which left
    # the latest years blank while older ones had it.
    derived = []
    if series.get("preTaxIncome"):
        pti = series["preTaxIncome"]["values"]
        ie = (series.get("interestExpense") or {}).get("values", {})
        ebit = series.get("operatingIncome") or {"values": {}, "tags": {}}
        # Only the years that will be shown (anchored on revenue, as below),
        # so a gap outside that window doesn't mark reported numbers derived.
        rev_years = (series.get("revenue") or {}).get("values")
        shown = set(sorted(rev_years, reverse=True)[:MAX_YEARS]) if rev_years else set(pti)
        gaps = [y for y in pti if y in shown and y not in ebit["values"]]
        if gaps:
            for y in gaps:
                ebit["values"][y] = pti[y] + (ie.get(y) or 0)
                ebit["tags"][y] = "derived: pre-tax income + interest"
            series["operatingIncome"] = ebit
            derived.append("operatingIncome")

    # Years: anchored on revenue (fall back to EBIT / net income).
    anchor = next((series[k] for k in ("revenue", "operatingIncome", "netIncome") if series.get(k)), None)
    if not anchor:
        raise ValueError("Could not find annual revenue or earnings in this company's 10-K filings. "
                         "It may be a recent listing or a newly formed holding company that has "
                         "not filed an annual report yet.")
    years = sorted(anchor["values"], reverse=True)[:MAX_YEARS]

    aligned, sources = {}, {}
    for key, s in series.items():
        if key.startswith("_") or not s:
            continue
        aligned[key] = [s["values"].get(y) for y in years]
        tags = {s["tags"][y] for y in years if y in s["tags"]}
        sources[key] = " / ".join(sorted(tags)) if tags else None

    # Annual total debt per year, from components.
    def annual_getter(y):
        return lambda k: (series.get(k) or {}).get("values", {}).get(y)
    debt_by_year = [compose_debt(annual_getter(y)) for y in years]
    aligned["totalDebt"] = [d[0] for d in debt_by_year]
    aligned["currentDebt"] = [d[2] for d in debt_by_year]
    sources["totalDebt"] = "built from current debt + long-term debt + finance leases"
    sources["currentDebt"] = "current debt, or current portion of long-term debt + short-term borrowings"

    # Point-in-time balance sheet from the most recent 10-Q/10-K.
    bs_date = latest_balance_date(facts)
    latest = {"date": bs_date, "values": {}, "sources": {}}
    if bs_date:
        dates = {}
        # Items carried over from an older report must be at least as recent
        # as the last 10-K: Microsoft's commercial paper from the 2025 10-K was
        # being added to its 2026 debt after the 2026 10-K no longer listed it.
        last_fye = max((e for e in fy_ends if e <= bs_date), default=None)
        for key in BALANCE_KEYS:
            v = extract_latest_instant(facts, key, as_of=bs_date, not_before=last_fye)
            if v:
                latest["values"][key] = v["value"]
                latest["sources"][key] = f'{v["tag"]} ({v["date"]})'
                dates[key] = v["date"]
        total_debt, parts, _cur = compose_debt(lambda k: latest["values"].get(k), dates.get)
        stale = sorted({d for k, d in dates.items() if d != bs_date and k != "_bsShares"
                        and (k.startswith("_") or k in ("cash", "shortTermInvestments"))})
        if stale:
            latest["staleNote"] = ("Some balance-sheet items were not in the latest filing and come from "
                                   f"an earlier one ({', '.join(stale)}).")
        latest["values"]["totalDebt"] = total_debt
        latest["debtParts"] = parts
        for k in [k for k in latest["values"] if k.startswith("_") and k != "_bsShares"]:
            latest["values"].pop(k)

    shares = shares_outstanding(facts)
    # Whether the latest year's EBIT already contains income from equity-method
    # stakes: only when it was derived from a pre-tax income figure that
    # includes that income (the second pre-tax tag excludes it). The engine
    # adds the stakes' book value only when it does not.
    y0 = years[0]
    ebit_tag = ((series.get("operatingIncome") or {}).get("tags") or {}).get(y0) or ""
    pti_tag = ((series.get("preTaxIncome") or {}).get("tags") or {}).get(y0) or ""
    ebit_has_equity_income = ebit_tag.startswith("derived") and "IncomeLossFromEquityMethodInvestments" not in pti_tag

    return {
        "years": years,
        "aligned": aligned,
        "sources": sources,
        "derived": derived,
        "ebitIncludesEquityIncome": ebit_has_equity_income,
        "fiscalYearEnd": (anchor.get("ends") or {}).get(years[0]),
        "latestBalance": latest,
        "shares": shares,
    }


def choose_share_count(parsed):
    """Pick the share count used for per-share value, with an explanation.

    Cover-page shares are the most current count, so they are the base when
    they agree (within 20%) with an independent count: weighted-average basic
    shares, or common shares outstanding on the balance sheet.  A mismatch
    usually means share classes - one class missing from the cover page, or
    classes with different economics - so an independent count is used and
    the result is flagged for the user to check.  A dilution factor (diluted
    / basic weighted shares) accounts for options and RSUs."""
    a = parsed["aligned"]
    pos = lambda v: _is_num(v) and v > 0  # noqa: E731 - a negative or zero count is a tagging error
    basic = next((v for v in a.get("basicShares", []) if pos(v)), None)
    diluted = next((v for v in a.get("dilutedShares", []) if pos(v)), None)
    bs = (parsed.get("latestBalance") or {}).get("values", {}).get("_bsShares")
    bs = bs if pos(bs) else None
    cover_info = parsed.get("shares") or {}
    cover = cover_info.get("value")
    refs = [r for r in (basic, bs) if r]
    agrees = lambda x: any(0.8 <= x / r <= 1.2 for r in refs)  # noqa: E731
    needs_check = False
    if cover and (not refs or agrees(cover)):
        base, note = cover, ["cover-page shares outstanding"
                             + (f" ({cover_info.get('classes')} share classes summed)" if cover_info.get("classes", 1) > 1 else "")]
    elif refs:
        base = basic or bs
        note = ["weighted-average basic shares" if basic else "balance-sheet shares outstanding"]
        needs_check = bool(cover)
        if cover:
            note.append("(the cover-page count did not match - please check, e.g. multiple share classes)")
    elif diluted:
        return {"value": diluted, "dilutionFactor": 1.0, "basis": "weighted-average diluted shares",
                "needsCheck": False}
    else:
        return None
    factor = 1.0
    if basic and diluted and diluted >= basic:
        factor = min(diluted / basic, 1.10)
    if factor > 1.0:
        note.append(f"× {factor:.3f} dilution (diluted ÷ basic weighted shares)")
    return {"value": base * factor, "dilutionFactor": factor, "basis": " ".join(note),
            "needsCheck": needs_check}


def load_json(path):
    with open(path) as fh:
        return json.load(fh)
