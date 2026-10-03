"""Synthetic SEC "companyfacts" data in the real format, used by the tests
and by `python3 dcf_model.py --demo` (runs with no network access).

Only the fields the parser reads are filled in: val, start, end, fy, fp,
form, filed, accn.
"""


def _dur(val, fy_end, filed, form="10-K", start=None):
    y, m, d = fy_end.split("-")
    start = start or f"{int(y) - 1}-{m}-{d}"
    return {"val": val, "start": start, "end": fy_end, "form": form, "filed": filed,
            "fy": int(y), "fp": "FY", "accn": f"0000-{filed}"}


def _inst(val, end, filed, form="10-K"):
    return {"val": val, "end": end, "form": form, "filed": filed, "fy": int(end[:4]), "fp": "FY",
            "accn": f"0000-{filed}"}


def add(facts, tag, unit, entries, ns="us-gaap"):
    facts.setdefault(ns, {}).setdefault(tag, {"units": {}})["units"].setdefault(unit, []).extend(entries)


def demo_company():
    """A tidy, plausible mid-cap industrial ("Acme Corp") with December
    year-ends, 2020-2025.  Numbers in USD."""
    facts = {}
    years = [2020, 2021, 2022, 2023, 2024, 2025]
    rev = [800e6, 880e6, 968e6, 1064.8e6, 1171.3e6, 1288.4e6]   # 10% growth
    ebit_m = [0.14, 0.145, 0.15, 0.15, 0.155, 0.16]
    for y, r, m in zip(years, rev, ebit_m):
        end, filed = f"{y}-12-31", f"{y + 1}-02-15"
        add(facts, "Revenues", "USD", [_dur(r, end, filed)])
        add(facts, "OperatingIncomeLoss", "USD", [_dur(r * m, end, filed)])
        add(facts, "InterestExpense", "USD", [_dur(12e6, end, filed)])
        pti = r * m - 12e6
        add(facts, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "USD", [_dur(pti, end, filed)])
        add(facts, "IncomeTaxExpenseBenefit", "USD", [_dur(pti * 0.21, end, filed)])
        add(facts, "NetIncomeLoss", "USD", [_dur(pti * 0.79, end, filed)])
        add(facts, "DepreciationDepletionAndAmortization", "USD", [_dur(r * 0.04, end, filed)])
        add(facts, "PaymentsToAcquirePropertyPlantAndEquipment", "USD", [_dur(r * 0.05, end, filed)])
        add(facts, "ShareBasedCompensation", "USD", [_dur(r * 0.01, end, filed)])
        add(facts, "WeightedAverageNumberOfSharesOutstandingBasic", "shares", [_dur(100e6, end, filed)])
        add(facts, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", [_dur(102e6, end, filed)])
        add(facts, "Assets", "USD", [_inst(r * 1.2, end, filed)])
        add(facts, "AssetsCurrent", "USD", [_inst(r * 0.40, end, filed)])
        add(facts, "LiabilitiesCurrent", "USD", [_inst(r * 0.25, end, filed)])
        add(facts, "CashAndCashEquivalentsAtCarryingValue", "USD", [_inst(r * 0.10, end, filed)])
        add(facts, "LongTermDebtNoncurrent", "USD", [_inst(250e6, end, filed)])
        add(facts, "LongTermDebtCurrent", "USD", [_inst(25e6, end, filed)])
    # Latest 10-Q balance sheet (Q2 2026) - should drive the equity bridge.
    q = ("2026-06-30", "2026-08-01")
    add(facts, "Assets", "USD", [_inst(1.6e9, *q, form="10-Q")])
    add(facts, "CashAndCashEquivalentsAtCarryingValue", "USD", [_inst(150e6, *q, form="10-Q")])
    add(facts, "LongTermDebtNoncurrent", "USD", [_inst(240e6, *q, form="10-Q")])
    add(facts, "LongTermDebtCurrent", "USD", [_inst(25e6, *q, form="10-Q")])
    add(facts, "EntityCommonStockSharesOutstanding", "shares",
        [{"val": 99.5e6, "end": "2026-07-25", "form": "10-Q", "filed": "2026-08-01", "accn": "q2"}], ns="dei")
    return {"cik": 1, "entityName": "Acme Corp", "facts": facts}
