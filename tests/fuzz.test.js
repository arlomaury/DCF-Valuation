const DCF = require('../web/engine.js');
const test = require('node:test');
const assert = require('node:assert');
let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const pick = (a) => a[Math.floor(rnd() * a.length)];
const market = { riskFree: { rate: 0.045 }, erp: 0.0423, marginalTaxRate: 0.25, dataDate: 'x', largeFirmCutoff: 5e9,
  industries: [{ name: 'Machinery', unleveredBeta: 0.89, operatingMargin: 0.168 }, { name: 'Banks (Regional)', unleveredBeta: 0.37, operatingMargin: null }],
  ratingsLarge: [[8.5,'AAA',0.004],[3,'A-',0.0089],[1.25,'B-',0.0509],[-1e9,'D',0.19]], ratingsSmall: [[12.5,'AAA',0.004],[4.5,'A-',0.0089],[1.5,'B-',0.0509],[-1e9,'D',0.19]] };
const maybe = (v, p = 0.15) => (rnd() < p ? pick([null, undefined, 0]) : v);
let problems = [], runs = 0;
test('engine stays finite, monotone and consistent on random companies', () => {
for (let it = 0; it < 600; it++) {
  const ny = 1 + Math.floor(rnd() * 6);
  const rev0 = Math.exp(rnd() * 12 + 14);
  const years = Array.from({ length: ny }, (_, i) => 2025 - i);
  const revs = years.map((_, i) => rev0 * Math.pow(1 / (1 + (rnd() * 0.6 - 0.2)), i));
  const m = rnd() * 0.7 - 0.25;
  const ali = {
    revenue: revs.map((r) => maybe(r, 0.05)),
    operatingIncome: revs.map((r) => maybe(r * (m + rnd() * 0.05))),
    dna: revs.map((r) => maybe(r * rnd() * 0.1)), capex: revs.map((r) => maybe(r * rnd() * 0.15)),
    preTaxIncome: revs.map((r) => maybe(r * m * 0.9)), incomeTax: revs.map((r) => maybe(r * m * 0.9 * (rnd() * 0.6 - 0.1))),
    interestExpense: revs.map((r) => maybe(r * rnd() * 0.03, 0.3)),
    currentAssets: revs.map((r) => maybe(r * rnd() * 0.8)), currentLiabilities: revs.map((r) => maybe(r * rnd() * 0.6)),
    cash: revs.map((r) => maybe(r * rnd() * 0.3)), totalAssets: revs.map((r) => maybe(r * (0.5 + rnd() * 2))),
    totalDebt: revs.map((r) => r * rnd() * 0.8), currentDebt: revs.map((r) => r * rnd() * 0.05), sbc: revs.map((r) => maybe(r * 0.02)),
    nolDTA: revs.map((r) => maybe(r * rnd() * 0.5, 0.5)), nolDTADomestic: revs.map((r) => maybe(r * rnd() * 0.4, 0.6)),
  };
  const data = { financials: { years, aligned: ali, latestBalance: { values: { totalDebt: ali.totalDebt[0], cash: ali.cash[0] || 0 } } },
    market, industry: pick(['Machinery', 'Banks (Regional)', 'Unknown']),
    quote: { price: rnd() < 0.1 ? 0 : rnd() * 300 + 1 }, shares: rnd() < 0.05 ? null : { value: rev0 / (rnd() * 50 + 5) } };
  let d;
  try { d = DCF.defaultAssumptions(data); } catch (e) { problems.push(['defaults threw', e.message, it]); continue; }
  const a = d.assumptions;
  // Exercise loss carryforwards and the terminal beta cap on every kind of company.
  if (rnd() < 0.4) a.startingNol = rev0 * rnd() * 3;
  a.terminalBetaCap = pick([1.2, 1.2, null, 0.6, 2.0]);
  if (rnd() < 0.3) a.unleveredBeta = 0.5 + rnd() * 1.5;
  // Every user-facing override and toggle, not just the defaults.
  if (rnd() < 0.2) a.targetDebtWeight = rnd() * 0.8;
  if (rnd() < 0.15) a.costOfDebtOverride = 0.02 + rnd() * 0.12;
  if (rnd() < 0.15) a.betaOverride = 0.3 + rnd() * 2;
  if (rnd() < 0.3) a.midYear = false;
  if (rnd() < 0.3) a.capexFade = false;
  if (rnd() < 0.2) a.addBackSBC = true;
  if (rnd() < 0.2) a.includeLongTermInvestments = false;
  if (rnd() < 0.15) { a.autoSpread = false; a.spread = rnd() * 0.1; }
  if (rnd() < 0.1) a.terminalGrowth = -0.01 + rnd() * 0.04;
  if (!Number.isFinite(a.startingNol) || a.startingNol < 0) problems.push(['bad startingNol', a.startingNol, it]);
  for (const k of ['revenueGrowth', 'ebitMargin', 'taxRate']) if (a[k].some((x) => !Number.isFinite(x))) problems.push(['non-finite default', k, it]);
  for (const k of ['daPct', 'capexPct', 'nwcPct', 'terminalGrowth', 'ronic', 'exitMultiple', 'unleveredBeta', 'spread', 'debt', 'cash'])
    if (!Number.isFinite(a[k])) problems.push(['non-finite default', k, a[k], it]);
  if (Object.values(d.why).some((w) => /NaN|undefined|null/.test(w))) problems.push(['bad why text', JSON.stringify(d.why).slice(0, 200), it]);
  for (const mode of ['unlevered', 'levered']) {
    for (const method of ['gordon', 'exit', 'average']) {
      runs++;
      let v;
      try { v = DCF.value(data.financials, { ...a, terminalMethod: method }, mode); } catch (e) { problems.push(['value threw', e.message, it]); continue; }
      if (!v) { if (Number.isFinite(ali.revenue[0]) && ali.revenue[0] > 0) problems.push(['null value with revenue', it]); continue; }
      if (v.error) { (global.errs = global.errs || {})[v.error.slice(0,40)] = ((global.errs||{})[v.error.slice(0,40)]||0)+1; continue; } global.ok=(global.ok||0)+1;
      if (!(v.pvNolLeft >= 0) || !Number.isFinite(v.pvNolLeft)) problems.push(['pvNolLeft', v.pvNolLeft, it]);
      if (v.terminalRate > v.discountRate + 1e-12 && a.terminalBetaCap != null) problems.push(['terminal rate above today', it]);
      if (v.proj.some((p) => !(p.nolEnd >= 0) || !(p.nolUsed >= 0) || p.taxes < -1e-9)) problems.push(['nol/taxes', mode, it]);
      for (const k of ['enterpriseValue', 'equityValue', 'sumPV', 'pvTV', 'discountRate', 'terminalRate'])
        if (!Number.isFinite(v[k])) problems.push(['non-finite', mode, method, k, it]);
      if (a.shares > 0 && !Number.isFinite(v.perShare)) problems.push(['perShare', mode, it]);
      if (v.proj.some((p) => !Number.isFinite(p.fcf) || !Number.isFinite(p.pv))) problems.push(['proj NaN', mode, it]);
      if (v.discountRate <= 0 || v.discountRate > 0.5) problems.push(['odd rate', v.discountRate, it]);
      // Monotonicity: with positive cash flows, a higher discount rate must not raise value.
      if (mode === 'unlevered' && method === 'gordon' && v.proj.every((p) => p.fcf > 0)) {
        const hi = DCF.value(data.financials, { ...a, terminalMethod: method }, mode, v.discountRate + 0.01);
        if (hi && !hi.error && hi.enterpriseValue > v.enterpriseValue + 1e-6 * Math.abs(v.enterpriseValue)) problems.push(['not monotone in rate', it]);
      }
      // Bridge identity
      if (mode === 'unlevered' && Math.abs(v.equityValue - (v.enterpriseValue - a.debt - v.claims + v.nonOperating)) > 1e-6 * Math.abs(v.enterpriseValue) + 1) problems.push(['bridge', it]);
    }
  }
  const s = DCF.sensitivity(data.financials, a, 'unlevered');
  if (s) { for (const row of [...s.growthTable, ...s.multipleTable]) for (const x of row.values) if (x !== null && !Number.isFinite(x)) problems.push(['sens NaN', it]); }
}
assert.deepStrictEqual(problems, []);
assert.ok(global.ok > 2000);
});
