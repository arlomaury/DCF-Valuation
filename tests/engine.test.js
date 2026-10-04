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

test('missing share price falls back to all-equity weights instead of 100% debt', () => {
  const c = DCF.costOfCapital({ riskFree: 0.04, erp: 0.05, unleveredBeta: 1, marginalTax: 0.25, spread: 0.01, marketCap: 0, debt: 500 });
  assert.equal(c.wD, 0);
  close(c.wacc, c.ke);
});

test('debt with no reported interest is not rated AAA', () => {
  const market = { largeFirmCutoff: 5e9, ratingsLarge: [[8.5, 'AAA', 0.004], [-1e9, 'D', 0.19]], ratingsSmall: [[-1e9, 'D', 0.19]] };
  assert.equal(DCF.syntheticRating(100, null, 10e9, market, 3e9).rating, 'BBB');
  assert.equal(DCF.syntheticRating(100, null, 10e9, market, 0).rating, 'AAA');
});

test('synthetic rating follows a price typed in later (large vs small firm table)', () => {
  const tables = { largeFirmCutoff: 5e9, ratingsLarge: [[8.5, 'AAA', 0.004], [6.5, 'AA', 0.0055], [-1e9, 'D', 0.19]],
    ratingsSmall: [[12.5, 'AAA', 0.004], [9.5, 'AA', 0.0055], [-1e9, 'D', 0.19]] };
  const a = { autoSpread: true, ratingEbit: 100, ratingInterest: 10, ratingTables: tables, debt: 50, shares: 1e8, price: 0 };
  assert.equal(DCF.currentRating(a).rating, 'AA');                       // no price -> small-firm table
  assert.equal(DCF.currentRating({ ...a, price: 100 }).rating, 'AAA');  // $10B cap -> large-firm table
  assert.equal(DCF.currentRating({ ...a, autoSpread: false, spread: 0.02, rating: 'x' }).spread, 0.02);
});

test('RONIC equal to WACC (after rounding) does not trigger the value-destruction warning', () => {
  const a = simpleAssumptions();
  const v0 = DCF.value(simpleFin(), a, 'unlevered');
  const v = DCF.value(simpleFin(), { ...a, ronic: Math.round(v0.discountRate * 1e4) / 1e4 - 0.00004 }, 'unlevered');
  assert.ok(!v.warnings.some((w) => /destroys value/.test(w)), v.warnings.join('; '));
});

test('capex fade: year-n reinvestment matches what the terminal value assumes', () => {
  // Heavy builder: capex 40% of revenue against D&A 5%.
  const a = simpleAssumptions({ capexPct: 0.40, capexFade: true, terminalGrowth: 0.02, revenueGrowth: Array(5).fill(0.02) });
  const rows = DCF.project(simpleFin(), a, 'unlevered');
  const last = rows[4];
  // Steady state: capex - D&A + dNWC = (g / RONIC) x NOPAT in the final year.
  close(last.capex - last.da + last.dNwc, (0.02 / 0.10) * last.nopat, 1e-9);
  close(rows[0].capex / rows[0].revenue, 0.40 + (0.05 + 0.2 * 0.75 * 0.2 - 0.1 * 0.02 / 1.02 - 0.40) / 5);
  // Off (and absent, as in older saved inputs): held flat.
  const flat = DCF.project(simpleFin(), simpleAssumptions({ capexPct: 0.40 }), 'unlevered');
  for (const r of flat) close(r.capex / r.revenue, 0.40);
});

test('capex fade is on by default', () => {
  const data = { financials: simpleFin(), market: { riskFree: { rate: 0.04 }, erp: 0.05, marginalTaxRate: 0.25, industries: [] },
    quote: { price: 10 }, shares: { value: 100 } };
  assert.strictEqual(DCF.defaultAssumptions(data).assumptions.capexFade, true);
});

test('missing capex is said plainly, not described as a 3-year average', () => {
  const fin = simpleFin(); delete fin.aligned.capex;
  const data = { financials: fin, market: { riskFree: { rate: 0.04 }, erp: 0.05, marginalTaxRate: 0.25, industries: [] },
    quote: { price: 10 }, shares: { value: 100 } };
  const { assumptions, why } = DCF.defaultAssumptions(data);
  assert.strictEqual(assumptions.capexPct, assumptions.daPct);
  assert.match(why.capexPct, /not found/);
});

test('capex fade: with no RONIC it falls back to the same rate as the terminal value (levered too)', () => {
  const a = simpleAssumptions({ capexPct: 0.40, capexFade: true, ronic: 0, revenueGrowth: Array(5).fill(0.02) });
  const v = DCF.value(simpleFin(), a, 'levered');
  const last = v.proj[v.proj.length - 1];
  close(last.capex - last.da + last.dNwc, (0.02 / v.coc.ke) * last.nopat, 1e-9);
});

// ─── Loss carryforwards ────────────────────────────────────────────────────
test('tax losses: a loss year builds a carryforward that shelters up to 80% of later profit', () => {
  // Margins: -10%, then +20%. Revenue 1050 then 1102.5.
  const a = simpleAssumptions({ ebitMargin: [-0.1, 0.2, 0.2, 0.2, 0.2] });
  const rows = DCF.project(simpleFin(), a, 'unlevered');
  close(rows[0].taxes, 0);
  close(rows[0].nolEnd, 105);                       // the year-1 loss
  const ebit2 = 1102.5 * 0.2;                       // 220.5
  const used = Math.min(105, 0.8 * ebit2);          // 105 (all of it)
  close(rows[1].nolUsed, used);
  close(rows[1].taxes, (ebit2 - used) * 0.25);
  close(rows[2].nolUsed, 0);
  close(rows[2].taxes, rows[2].ebit * 0.25);
});

test('tax losses: the 80% limit leaves some income taxed every profitable year', () => {
  const a = simpleAssumptions({ startingNol: 1e6 });
  const rows = DCF.project(simpleFin(), a, 'unlevered');
  for (const r of rows) {
    close(r.nolUsed, 0.8 * r.ebit);
    close(r.taxes, 0.2 * r.ebit * 0.25);
  }
});

test('tax losses: FCFE shelters income after interest, not operating income', () => {
  const a = simpleAssumptions({ debt: 2000, startingNol: 50 });
  const rows = DCF.project(simpleFin(), a, 'levered', DCF.costOfCapital(DCF.cocInputs(a, null)));
  const r = rows[0];
  const pretax = r.ebit - r.interest;
  close(r.nolUsed, Math.min(50, 0.8 * pretax));
  close(r.netIncome, pretax - (pretax - r.nolUsed) * 0.25);
});

test('tax losses: defaults count filed carryforwards only for a loss-making company', () => {
  const market = { riskFree: { rate: 0.04 }, erp: 0.05, marginalTaxRate: 0.25, industries: [],
    largeFirmCutoff: 5e9, ratingsLarge: [[-1e9, 'BBB', 0.01]], ratingsSmall: [[-1e9, 'BBB', 0.01]] };
  const data = (ebit) => {
    const fin = simpleFin();
    fin.aligned.operatingIncome = [ebit, 180];
    fin.aligned.nolDTA = [25, 20];
    fin.latestBalance = { values: {} };
    return { financials: fin, market, quote: { price: 10 }, shares: { value: 100 } };
  };
  close(DCF.defaultAssumptions(data(-50)).assumptions.startingNol, 100);   // 25 / 25%
  close(DCF.defaultAssumptions(data(200)).assumptions.startingNol, 0);
  // A federal-only asset is grossed up at the federal 21%.
  const fed = data(-50); fed.financials.aligned.nolDTADomestic = [21, null];
  close(DCF.defaultAssumptions(fed).assumptions.startingNol, 100);
});

test('tax losses: losses left after year 10 are valued, not dropped', () => {
  const a = simpleAssumptions({ startingNol: 5000 });
  const v = DCF.value(simpleFin(), a, 'unlevered');
  const last = v.proj[v.proj.length - 1];
  assert.ok(last.nolEnd > 0);
  // Recompute by hand: shelter 80% of taxable income each year until gone.
  let left = last.nolEnd, pv = 0;
  for (let k = 1; left > 0; k++) {
    const used = Math.min(left, 0.8 * last.ebit * Math.pow(1.02, k));
    pv += used * 0.25 / Math.pow(1 + v.terminalRate, k); left -= used;
  }
  close(v.pvNolLeft, pv / Math.pow(1 + v.discountRate, 5));
  const v0 = DCF.value(simpleFin(), simpleAssumptions(), 'unlevered');
  close(v.enterpriseValue - v.pvNolLeft - v.sumPV - v.pvTV, 0);
  assert.ok(v.perShare > v0.perShare);
});

// ─── Stable-period beta ────────────────────────────────────────────────────
test('terminal rate: a high beta is capped at 1.2 for the terminal value only', () => {
  const a = simpleAssumptions({ unleveredBeta: 1.6, terminalBetaCap: 1.2 });
  const v = DCF.value(simpleFin(), a, 'unlevered');
  close(v.discountRate, 0.04 + 1.6 * 0.05);
  close(v.terminalRate, 0.04 + 1.2 * 0.05);
  const last = v.proj[v.proj.length - 1];
  const fcf11 = last.ebit * 1.02 * 0.75 * (1 - 0.02 / 0.10);
  close(v.tvGordon, fcf11 / (0.10 - 0.02));
  close(v.pvGordon, v.tvGordon / Math.pow(1.12, 5));          // discounted at today's rate
  // No cap: the terminal rate is today's rate.
  const v0 = DCF.value(simpleFin(), { ...a, terminalBetaCap: null }, 'unlevered');
  close(v0.terminalRate, v0.discountRate);
  assert.ok(v.perShare > v0.perShare);
});

test('terminal rate: a beta below the cap is left alone', () => {
  const v = DCF.value(simpleFin(), simpleAssumptions({ unleveredBeta: 0.7, terminalBetaCap: 1.2 }), 'unlevered');
  close(v.terminalRate, v.discountRate);
});

test('terminal rate: sensitivity shifts keep the same terminal spread', () => {
  const a = simpleAssumptions({ unleveredBeta: 1.6, terminalBetaCap: 1.2 });
  const v = DCF.value(simpleFin(), a, 'unlevered', 0.15);
  close(v.terminalRate, 0.15 - 0.02);
});

test('reverse DCF: the implied growth and rate reproduce the price', () => {
  const a = simpleAssumptions();                      // constant 5% growth
  const v = DCF.value(simpleFin(), a, 'unlevered');
  const m = DCF.marketImplied(simpleFin(), { ...a, price: v.perShare }, 'unlevered');
  close(m.growth, 0.05, 1e-6);
  close(m.rate, v.discountRate, 1e-6);
  // A price no input range can reach gives null, not a made-up number.
  const far = DCF.marketImplied(simpleFin(), { ...a, price: v.perShare * 1e6 }, 'unlevered');
  assert.strictEqual(far.growth, null);
  assert.strictEqual(DCF.marketImplied(simpleFin(), { ...a, price: 0 }, 'unlevered'), null);
});
