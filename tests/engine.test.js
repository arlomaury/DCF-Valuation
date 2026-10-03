// Run with:  node --test tests/
// Checks the valuation engine against hand-computed numbers.
const test = require('node:test');
const assert = require('node:assert');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const DCF = require('../web/engine.js');

const close = (a, b, tol = 1e-6) => assert.ok(Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)), `${a} != ${b}`);

// A one-line company: revenue 1000, nothing on the balance sheet.
function simpleFin() {
  return {
    years: [2025, 2024],
    aligned: { revenue: [1000, 900], operatingIncome: [200, 180], dna: [50, 45], capex: [60, 55],
      currentAssets: [100, 90], currentLiabilities: [100, 90], totalDebt: [0, 0], currentDebt: [0, 0] },
  };
}
function simpleAssumptions(over = {}) {
  const n = 5;
  return Object.assign({
    years: n, revenueGrowth: Array(n).fill(0.05), ebitMargin: Array(n).fill(0.2), taxRate: Array(n).fill(0.25),
    daPct: 0.05, capexPct: 0.06, nwcPct: 0.10, terminalGrowth: 0.02, terminalTax: 0.25, ronic: 0.10,
    terminalMethod: 'gordon', exitMultiple: 10, midYear: false, includeLongTermInvestments: true,
    riskFree: 0.04, erp: 0.05, marginalTax: 0.25, unleveredBeta: 1.0, spread: 0.01,
    price: 10, shares: 100, debt: 0, cash: 0, longTermInvestments: 0, minorityInterest: 0, preferredStock: 0,
  }, over);
}

test('cost of capital: unlevered firm WACC equals cost of equity', () => {
  const c = DCF.costOfCapital({ riskFree: 0.04, erp: 0.05, unleveredBeta: 1.2, marginalTax: 0.25,
    spread: 0.01, marketCap: 1000, debt: 0 });
  close(c.ke, 0.04 + 1.2 * 0.05);
  close(c.wacc, c.ke);
});

test('cost of capital: beta re-levered with Hamada and debt weighted after tax', () => {
  const c = DCF.costOfCapital({ riskFree: 0.04, erp: 0.05, unleveredBeta: 1.0, marginalTax: 0.25,
    spread: 0.02, marketCap: 750, debt: 250 });
  close(c.wD, 0.25);
  close(c.leveredBeta, 1.0 * (1 + 0.75 * (250 / 750)));       // 1.25
  close(c.ke, 0.04 + 1.25 * 0.05);                             // 10.25%
  close(c.kdAfter, 0.06 * 0.75);                               // 4.5%
  close(c.wacc, 0.75 * 0.1025 + 0.25 * 0.045);
});

test('projection: FCFF = NOPAT + D&A - capex - change in working capital', () => {
  const a = simpleAssumptions();
  const rows = DCF.project(simpleFin(), a, 'unlevered');
  const r1 = rows[0];
  close(r1.revenue, 1050);
  close(r1.ebit, 210);
  close(r1.nopat, 157.5);
  close(r1.dNwc, 0.10 * (1050 - 1000));
  close(r1.fcf, 157.5 + 52.5 - 63 - 5);
});

test('valuation by hand: end-of-year discounting, perpetuity with value-driver reinvestment', () => {
  const a = simpleAssumptions();
  const v = DCF.value(simpleFin(), a, 'unlevered');
  const r = 0.04 + 1.0 * 0.05;                                 // 9%, no debt
  close(v.discountRate, r);
  // Re-derive independently.
  let rev = 1000, nwc = 100, pv = 0, ebit;
  for (let t = 1; t <= 5; t++) {
    rev *= 1.05; ebit = rev * 0.2;
    const fcf = ebit * 0.75 + rev * 0.05 - rev * 0.06 - (rev * 0.1 - nwc);
    nwc = rev * 0.1;
    pv += fcf / Math.pow(1 + r, t);
  }
  const fcfNext = ebit * 1.02 * 0.75 * (1 - 0.02 / 0.10);
  const tv = fcfNext / (r - 0.02);
  const ev = pv + tv / Math.pow(1 + r, 5);
  close(v.enterpriseValue, ev);
  close(v.perShare, ev / 100);
});

test('mid-year convention: flows at t-0.5 and the perpetuity from n-0.5', () => {
  const a = simpleAssumptions({ midYear: true });
  const end = DCF.value(simpleFin(), { ...a, midYear: false }, 'unlevered');
  const mid = DCF.value(simpleFin(), a, 'unlevered');
  // Every discount factor moves by exactly (1+r)^0.5 under the perpetuity method.
  close(mid.enterpriseValue, end.enterpriseValue * Math.sqrt(1 + end.discountRate));
});

test('exit multiple is discounted from the end of the final year, not mid-year', () => {
  const a = simpleAssumptions({ midYear: true, terminalMethod: 'exit', exitMultiple: 12 });
  const v = DCF.value(simpleFin(), a, 'unlevered');
  const last = v.proj[v.proj.length - 1];
  close(v.pvTV, last.ebitda * 12 / Math.pow(1 + v.discountRate, 5));
});

test('equity bridge: minus debt, minority interest and preferred; plus cash and investments', () => {
  const a = simpleAssumptions({ debt: 300, cash: 120, longTermInvestments: 30, minorityInterest: 20, preferredStock: 10 });
  const v = DCF.value(simpleFin(), a, 'unlevered');
  close(v.equityValue, v.enterpriseValue - 300 - 20 - 10 + 120 + 30);
  const noInv = DCF.value(simpleFin(), { ...a, includeLongTermInvestments: false }, 'unlevered');
  close(v.equityValue - noInv.equityValue, 30);
});

test('levered DCF does not subtract debt a second time', () => {
  const a = simpleAssumptions({ debt: 300, cash: 50 });
  const v = DCF.value(simpleFin(), a, 'levered');
  close(v.equityValue, v.sumPV + v.pvTV + 50);
  // Interest is charged on the opening debt balance at the pre-tax cost of debt.
  close(v.proj[0].interest, 300 * v.coc.kdPre);
});

test('growth at RONIC = WACC adds no value in the terminal period', () => {
  const a = simpleAssumptions();
  const r = 0.09;
  const lo = DCF.value(simpleFin(), { ...a, ronic: r, terminalGrowth: 0.01 }, 'unlevered');
  const hi = DCF.value(simpleFin(), { ...a, ronic: r, terminalGrowth: 0.03 }, 'unlevered');
  // TV = NOPAT(n+1) / WACC: growth only moves the first year's NOPAT.
  close(hi.pvTV / lo.pvTV, 1.03 / 1.01, 1e-9);
});

test('perpetuity refused when discount rate is not above growth', () => {
  const v = DCF.value(simpleFin(), simpleAssumptions({ terminalGrowth: 0.09 }), 'unlevered');
  assert.ok(v.error);
});

test('losses are not taxed', () => {
  const a = simpleAssumptions({ ebitMargin: Array(5).fill(-0.1) });
  const rows = DCF.project(simpleFin(), a, 'unlevered');
  assert.equal(rows[0].taxes, 0);
});

test('synthetic rating uses the small-firm table below $5B', () => {
  const market = { largeFirmCutoff: 5e9, ratingsLarge: [[8.5, 'AAA', 0.004], [-1e9, 'D', 0.19]],
    ratingsSmall: [[12.5, 'AAA', 0.004], [9.5, 'AA', 0.0055], [-1e9, 'D', 0.19]] };
  assert.equal(DCF.syntheticRating(100, 10, 10e9, market).rating, 'AAA');
  assert.equal(DCF.syntheticRating(100, 10, 1e9, market).rating, 'AA');
});

test('sensitivity table centre equals the base case', () => {
  const a = simpleAssumptions();
  const s = DCF.sensitivity(simpleFin(), a, 'unlevered');
  const base = DCF.value(simpleFin(), a, 'unlevered');
  const mid = s.growthTable.find((r) => Math.abs(r.label - a.terminalGrowth) < 1e-9);
  const col = s.rates.findIndex((x) => Math.abs(x - base.discountRate) < 1e-9);
  close(mid.values[col], base.perShare);
});

// End-to-end through the Python parser on the demo company.
function demoData() {
  const root = path.join(__dirname, '..');
  const out = execFileSync('python3', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(root)})
import sec_data, market_data as md
from demo_data import demo_company
p = sec_data.parse_company_facts(demo_company())
print(json.dumps({"financials": p, "shares": sec_data.choose_share_count(p), "industry": "Machinery",
  "quote": {"price": 40.0}, "market": {"riskFree": {"rate": 0.045}, "erp": md.IMPLIED_ERP,
  "marginalTaxRate": md.MARGINAL_TAX_RATE, "dataDate": md.DATA_DATE, "industries": md.industry_table(),
  "ratingsLarge": [[max(b, -1e9), r, s] for b, r, s in md.RATINGS_LARGE],
  "ratingsSmall": [[max(b, -1e9), r, s] for b, r, s in md.RATINGS_SMALL], "largeFirmCutoff": md.LARGE_FIRM_CUTOFF}}))
`], { cwd: root });
  return JSON.parse(out);
}

test('defaults for the demo company are sensible and explained', () => {
  const data = demoData();
  const { assumptions: a, why } = DCF.defaultAssumptions(data);
  close(a.revenueGrowth[0], 0.10, 1e-3);                // steady 10% grower
  close(a.revenueGrowth[9], 0.025, 1e-9);                // fades to terminal growth
  close(a.ebitMargin[0], 0.16, 1e-9);                    // latest margin, held flat
  close(a.taxRate[0], 0.21, 1e-3);                       // effective
  close(a.taxRate[9], 0.25, 1e-9);                       // converges to marginal
  close(a.debt, 265e6);                                  // latest 10-Q, not the 10-K
  close(a.cash, 150e6);
  close(a.shares, 99.5e6 * 1.02);
  assert.equal(a.rating, 'AAA');                         // 206M / 12M = 17x coverage
  for (const k of ['revenueGrowth', 'ebitMargin', 'taxRate', 'beta', 'costOfDebt', 'ronic', 'terminalGrowth']) {
    assert.ok(why[k], `missing explanation for ${k}`);
  }
  const v = DCF.value(data.financials, a, 'unlevered');
  assert.ok(!v.error, v.error);
  assert.ok(v.perShare > 0 && isFinite(v.perShare));
  assert.ok(v.coc.wacc > 0.05 && v.coc.wacc < 0.12, `wacc ${v.coc.wacc}`);
});

test('implied exit multiple reproduces the perpetuity value, and implied growth inverts it', () => {
  const a = simpleAssumptions({ midYear: true });
  const g = DCF.value(simpleFin(), a, 'unlevered');
  const e = DCF.value(simpleFin(), { ...a, terminalMethod: 'exit', exitMultiple: g.impliedExitMultiple }, 'unlevered');
  close(e.pvTV, g.pvTV, 1e-9);
  close(e.impliedGrowthFromExit, a.terminalGrowth, 1e-6);
});
