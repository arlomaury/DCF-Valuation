"""Randomised filings, including malformed dates and junk values: the parser
must never crash, and must always return aligned, finite data."""
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sec_data as sd  # noqa: E402
from sec_data import CONCEPTS  # noqa: E402

tags = sorted({t.split(":")[-1] for v in CONCEPTS.values() for t in v[0]})
forms = ["10-K", "10-K/A", "10-Q", "8-K", "S-1", "10-KT", None]


def rdate(y):
    return f"{y}-{random.choice(['01-03','03-31','06-30','09-28','12-31','12-30','01-31'])}"


def test_parser_survives_random_filings():
    random.seed(7)
    problems = []; ok = 0
    for it in range(600):
        facts = {"us-gaap": {}, "dei": {}}
        if random.random() < 0.3: facts["abc"] = {}
        for tag in random.sample(tags, random.randint(1, len(tags))):
            ns = "abc" if (random.random() < 0.05 and "abc" in facts) else "us-gaap"
            unit = "shares" if "Shares" in tag else random.choice(["USD", "USD", "USD", "EUR"])
            ents = []
            for _ in range(random.randint(0, 25)):
                y = random.randint(2015, 2026)
                end = rdate(y)
                e = {"val": random.choice([random.uniform(-1e9, 1e11), 0, None, 1e15, "12", True, float("nan"), float("inf")]), "end": end,
                     "form": random.choice(forms), "filed": f"{y+random.randint(0,2)}-0{random.randint(1,9)}-15",
                     "accn": str(random.randint(1, 5)), "fy": y, "fp": random.choice(["FY", "Q1", "Q3"])}
                if random.random() < 0.6:
                    e["start"] = random.choice([f"{y-1}{end[4:]}", f"{y}-01-01", "bad-date", None])
                if random.random() < 0.02: e["end"] = "garbage"
                ents.append({k: v for k, v in e.items() if v is not None or k == "val"})
            facts[ns].setdefault(tag, {"units": {}})["units"][unit] = ents
        if random.random() < 0.5:
            facts["dei"]["EntityCommonStockSharesOutstanding"] = {"units": {"shares": [
                {"val": random.uniform(1e6, 1e10), "end": rdate(2026), "form": random.choice(["10-Q", "10-K"]), "filed": "2026-05-01", "accn": "z"}
                for _ in range(random.randint(1, 3))]}}
        try:
            p = sd.parse_company_facts({"facts": facts})
            sh = sd.choose_share_count(p)
            json.dumps(p, allow_nan=False); json.dumps(sh, allow_nan=False)
            ys = p["years"]
            if ys != sorted(ys, reverse=True) or len(set(ys)) != len(ys) or len(ys) > 6: problems.append(("years", it, ys))
            for k, arr in p["aligned"].items():
                if len(arr) != len(ys): problems.append(("misaligned", k, it))
            if sh and not (sh["value"] > 0 and math.isfinite(sh["value"])): problems.append(("shares", it, sh))
            ok += 1
        except ValueError as e:
            msg = str(e)
            if not any(s in msg for s in ("US GAAP", "IFRS", "annual revenue")): problems.append(("unexpected ValueError", msg, it))
        except Exception as e:
            problems.append(("CRASH", type(e).__name__, str(e)[:120], it))
    assert ok > 300
    assert problems == []
