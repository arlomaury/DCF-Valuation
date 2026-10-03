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
    "interestExpense": (["InterestExpense", "InterestExpenseNonoperating",
                         "InterestExpenseDebt", "InterestAndDebtExpense"],
                        "first", "duration"),
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
               "PaymentsToAcquireOtherPropertyPlantAndEquipment"], "first", "duration"),
    # Assets acquired with new finance leases: non-cash, but economically capex
    # (the matching liability is counted as debt below).
    "financeLeaseAdditions": (["RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability"],
                              "first", "duration"),
    "sbc": (["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
            "first", "duration"),
    "dilutedShares": (["WeightedAverageNumberOfDilutedSharesOutstanding"], "first", "duration"),
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
                              "AvailableForSaleSecuritiesCurrent"], "first", "instant"),
    "cashAndSTI": (["CashCashEquivalentsAndShortTermInvestments"], "first", "instant"),
    "longTermInvestments": (["MarketableSecuritiesNoncurrent",
                             "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
                             "LongTermInvestments"], "first", "instant"),
    "_debtCurrent": (["DebtCurrent"], "first", "instant"),
    "_ltDebtCurrent": (["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
                       "first", "instant"),
    "_shortBorrowings": (["ShortTermBorrowings", "CommercialPaper"], "first", "instant"),
    "_ltDebtNoncurrent": (["LongTermDebtNoncurrent"], "first", "instant"),
    "_ltDebtAndLeasesNoncurrent": (["LongTermDebtAndCapitalLeaseObligations"], "first", "instant"),
    "_ltDebtTotal": (["LongTermDebt"], "first", "instant"),
    "_financeLease": (["FinanceLeaseLiability"], "first", "instant"),
    "_financeLeaseNoncurrent": (["FinanceLeaseLiabilityNoncurrent"], "first", "instant"),
    "_financeLeaseCurrent": (["FinanceLeaseLiabilityCurrent"], "first", "instant"),
    "operatingLeaseLiability": (["OperatingLeaseLiability"], "first", "instant"),
    "minorityInterest": (["MinorityInterest"], "first", "instant"),
    "preferredStock": (["PreferredStockValue"], "first", "instant"),
}

BALANCE_KEYS = ["cash", "shortTermInvestments", "cashAndSTI", "longTermInvestments",
                "minorityInterest", "preferredStock", "operatingLeaseLiability",
                "currentAssets", "currentLiabilities", "totalAssets",
                "_debtCurrent", "_ltDebtCurrent", "_shortBorrowings", "_ltDebtNoncurrent",
                "_ltDebtAndLeasesNoncurrent", "_ltDebtTotal", "_financeLease",
                "_financeLeaseNoncurrent", "_financeLeaseCurrent"]

KNOWN_STANDARD_NS = {"us-gaap", "dei", "srt", "ifrs-full", "invest"}


def _d(s):
    return date.fromisoformat(s)


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


def _annual_by_year(entries, kind):
    """{fiscal_year: fact} for annual facts, keeping the most recently filed
    (i.e. restated) value for each year."""
    out = {}
    for f in entries:
        if f.get("form") not in ANNUAL_FORMS or f.get("val") is None or not f.get("end"):
            continue
        days = _period_days(f)
        if kind == "duration":
            # A full fiscal year: 52 weeks = 364 days, 53 weeks = 371.
            if days is None or not (350 <= days <= 380):
                continue
        elif days is not None:
            continue                    # instants have no start date
        try:
            yr = fiscal_label(f["end"])
        except ValueError:
            continue
        prev = out.get(yr)
        if prev is None or (f.get("filed", ""), f.get("end", "")) > (prev.get("filed", ""), prev.get("end", "")):
            out[yr] = f
    return out


def extract_annual(facts, key):
    """Return {'values': {year: val}, 'tags': {year: tag}} or None."""
    tags, combine, kind = CONCEPTS[key]
    per_year = {}                      # year -> list of (priority, tag, val)
    for prio, spec in enumerate(tags):
        for tag, entries in _tag_sources(facts, spec):
            for yr, f in _annual_by_year(entries, kind).items():
                per_year.setdefault(yr, []).append((prio, tag, f["val"]))
    if not per_year:
        return None
    values, used = {}, {}
    for yr, cands in per_year.items():
        if combine == "max":
            prio, tag, val = max(cands, key=lambda c: (c[2], -c[0]))
        else:
            prio, tag, val = min(cands, key=lambda c: c[0])
        values[yr], used[yr] = val, tag
    return {"values": values, "tags": used}


def extract_latest_instant(facts, key, as_of=None, max_age_days=400):
    """Most recent balance-sheet value from any 10-K or 10-Q.

    If `as_of` is given, prefer a value dated exactly then (so all balance
    sheet items come from the same report); otherwise fall back to the most
    recent value no older than `max_age_days` before `as_of`."""
    tags, _combine, _kind = CONCEPTS[key]
    best = None                         # (end, prio, filed, val, tag)
    for prio, spec in enumerate(tags):
        for tag, entries in _tag_sources(facts, spec):
            for f in entries:
                if f.get("form") not in BALANCE_FORMS or f.get("start") or f.get("val") is None:
                    continue
                end = f.get("end")
                if not end:
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
               if f.get("form") in BALANCE_FORMS and f.get("val")]
    if not entries:
        return None
    latest = max(entries, key=lambda f: (f.get("filed", ""), f.get("end", "")))
    same = [f for f in entries if f.get("accn") == latest.get("accn") and f.get("end") == latest.get("end")]
    return {"value": sum(f["val"] for f in same), "date": latest.get("end"), "classes": len(same)}


def _sum_present(*vals):
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def compose_debt(get):
    """Total debt from components.  `get(key)` returns a number or None.
    Returns (total, parts) where parts explains what was used."""
    parts = {}
    # Current: one total tag if reported, else current LTD + short-term borrowings.
    cur = get("_debtCurrent")
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
    return (total or 0), parts, (cur or 0)


def parse_company_facts(raw):
    """Turn a companyfacts JSON document into the structure the UI uses."""
    facts = raw.get("facts", {})
    if not facts.get("us-gaap"):
        if facts.get("ifrs-full"):
            raise ValueError("This company files under IFRS (a foreign filer). "
                             "The model only reads US GAAP filings.")
        raise ValueError("No US GAAP financial data found for this company.")

    series = {k: extract_annual(facts, k) for k in CONCEPTS}

    # D&A fallback: depreciation + amortization of intangibles.
    if not series.get("dna"):
        dep, amt = series.get("_depreciation"), series.get("_amortization")
        if dep or amt:
            yrs = set((dep or {}).get("values", {})) | set((amt or {}).get("values", {}))
            vals = {y: ((dep or {}).get("values", {}).get(y) or 0) + ((amt or {}).get("values", {}).get(y) or 0)
                    for y in yrs}
            series["dna"] = {"values": vals, "tags": {y: "Depreciation+AmortizationOfIntangibleAssets" for y in yrs}}

    # EBIT fallback: pre-tax income + interest expense, marked as derived.
    derived = []
    if not series.get("operatingIncome") and series.get("preTaxIncome"):
        pti = series["preTaxIncome"]["values"]
        ie = (series.get("interestExpense") or {}).get("values", {})
        series["operatingIncome"] = {"values": {y: v + (ie.get(y) or 0) for y, v in pti.items()},
                                     "tags": {y: "derived: pre-tax income + interest" for y in pti}}
        derived.append("operatingIncome")

    # Years: anchored on revenue (fall back to EBIT / net income).
    anchor = next((series[k] for k in ("revenue", "operatingIncome", "netIncome") if series.get(k)), None)
    if not anchor:
        raise ValueError("Could not find annual revenue or earnings in this company's 10-K filings.")
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
        for key in BALANCE_KEYS:
            v = extract_latest_instant(facts, key, as_of=bs_date)
            if v:
                latest["values"][key] = v["value"]
                latest["sources"][key] = f'{v["tag"]} ({v["date"]})'
        total_debt, parts, _cur = compose_debt(lambda k: latest["values"].get(k))
        latest["values"]["totalDebt"] = total_debt
        latest["debtParts"] = parts
        for k in [k for k in latest["values"] if k.startswith("_")]:
            latest["values"].pop(k)

    shares = shares_outstanding(facts)
    return {
        "years": years,
        "aligned": aligned,
        "sources": sources,
        "derived": derived,
        "latestBalance": latest,
        "shares": shares,
    }


def choose_share_count(parsed):
    """Pick the share count used for per-share value, with an explanation.

    Cover-page shares are the most current count, so they are the base. They
    are cross-checked against the latest weighted-average basic count: a big
    mismatch usually means classes with different economics (one class worth
    many of another) and the weighted count is safer.  A dilution factor from
    diluted vs basic weighted shares accounts for options and RSUs."""
    a = parsed["aligned"]
    basic = next((v for v in a.get("basicShares", []) if v), None)
    diluted = next((v for v in a.get("dilutedShares", []) if v), None)
    cover = (parsed.get("shares") or {}).get("value")
    if cover and basic and 0.8 <= cover / basic <= 1.2:
        base, note = cover, ["cover-page shares outstanding"]
    elif cover and basic:
        base, note = basic, ["weighted-average basic shares (the cover-page count looked "
                             "inconsistent, e.g. share classes with different economics)"]
    elif cover:
        base, note = cover, ["cover-page shares outstanding"]
    elif basic:
        base, note = basic, ["weighted-average basic shares"]
    elif diluted:
        return {"value": diluted, "dilutionFactor": 1.0, "basis": "weighted-average diluted shares"}
    else:
        return None
    factor = 1.0
    if basic and diluted and diluted >= basic:
        factor = min(diluted / basic, 1.10)
    if factor > 1.0:
        note.append(f"× {factor:.3f} dilution (diluted / basic weighted shares)")
    return {"value": base * factor, "dilutionFactor": factor, "basis": " ".join(note)}


def load_json(path):
    with open(path) as fh:
        return json.load(fh)
