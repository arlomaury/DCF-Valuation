/*
 * DCF valuation engine - pure functions, no UI.
 *
 * Loaded by the browser as a plain script (window.DCF) and by Node for the
 * tests in tests/engine.test.js.  Every number the app shows comes from here.
 *
 * Conventions
 *   - All money in US dollars, rates as decimals (0.08 = 8%).
 *   - Historical arrays are newest-first (index 0 = most recent fiscal year).
 *   - Projection arrays are oldest-first (index 0 = first projected year).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.DCF = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const PROJECTION_YEARS = 10;

  const isNum = (x) => typeof x === 'number' && isFinite(x);
  const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  const nums = (arr) => (arr || []).filter(isNum);
  const mean = (arr) => { const v = nums(arr); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const median = (arr) => {
    const v = nums(arr).sort((a, b) => a - b);
    if (!v.length) return null;
    const m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };
  const lerp = (a, b, t) => a + (b - a) * t;
  const round = (x, dp = 4) => (isNum(x) ? Math.round(x * 10 ** dp) / 10 ** dp : x);

  // ─── History ────────────────────────────────────────────────────────────
  /** Per-year metrics from the parsed SEC data (newest first). */
  function historicalMetrics(fin, opts = {}) {
    if (!fin || !fin.years) return [];
    const a = fin.aligned || {};
    const get = (k, i) => (a[k] ? a[k][i] : null);
    const includeLeaseCapex = opts.includeFinanceLeaseCapex !== false;
    const rows = fin.years.map((year, i) => {
      const rev = get('revenue', i);
      const ebit = get('operatingIncome', i);
      const da = get('dna', i);
      const cx = get('capex', i);
      const fla = get('financeLeaseAdditions', i);
      const capex = isNum(cx) ? cx + (includeLeaseCapex && isNum(fla) ? fla : 0) : null;
      const ptx = get('preTaxIncome', i), tax = get('incomeTax', i);
      const cash = cashLike(get('cash', i), get('shortTermInvestments', i), get('cashAndSTI', i));
      const ca = get('currentAssets', i), cl = get('currentLiabilities', i);
      const curDebt = currentDebtOf(a, i);
      // Operating working capital: current assets less cash & securities,
      // minus current liabilities less the debt inside them.
      const nwc = isNum(ca) && isNum(cl) ? (ca - cash) - (cl - curDebt) : null;
      let taxRate = null;
      if (isNum(ptx) && isNum(tax) && ptx > 0) {
        const r = tax / ptx;
        if (r >= 0 && r <= 0.5) taxRate = r;
      }
      const prevRev = get('revenue', i + 1);
      const ta = get('totalAssets', i);
      const ltInv = get('longTermInvestments', i) || 0;
      const investedCapital = isNum(ta) && isNum(cl) ? ta - cash - ltInv - (cl - curDebt) : null;
      return {
        year, revenue: rev, ebit, da, capex, nwc, taxRate, investedCapital,
        netIncome: get('netIncome', i), sbc: get('sbc', i), interest: get('interestExpense', i),
        ebitda: isNum(ebit) && isNum(da) ? ebit + da : null,
        revGrowth: isNum(rev) && isNum(prevRev) && prevRev > 0 ? rev / prevRev - 1 : null,
        ebitMargin: isNum(rev) && rev > 0 && isNum(ebit) ? ebit / rev : null,
        daPct: isNum(rev) && rev > 0 && isNum(da) ? da / rev : null,
        capexPct: isNum(rev) && rev > 0 && isNum(capex) ? capex / rev : null,
        nwcPct: isNum(rev) && rev > 0 && isNum(nwc) ? nwc / rev : null,
        sbcPct: isNum(rev) && rev > 0 && isNum(get('sbc', i)) ? get('sbc', i) / rev : null,
      };
    });
    // Return on invested capital, on average capital, at that year's tax rate.
    rows.forEach((r, i) => {
      const prev = rows[i + 1];
      const avgIC = prev && isNum(prev.investedCapital) && isNum(r.investedCapital)
        ? (r.investedCapital + prev.investedCapital) / 2 : r.investedCapital;
      const t = isNum(r.taxRate) ? r.taxRate : 0.25;
      r.roic = isNum(r.ebit) && isNum(avgIC) && avgIC > 0 ? r.ebit * (1 - t) / avgIC : null;
    });
    return rows;
  }

  function cashLike(cash, sti, combined) {
    const split = (isNum(cash) ? cash : 0) + (isNum(sti) ? sti : 0);
    return Math.max(split, isNum(combined) ? combined : 0);
  }

  // Debt inside current liabilities - taken out of working capital because it
  // is financing, not operations (it is counted in total debt instead).
  function currentDebtOf(a, i) {
    return a.currentDebt && isNum(a.currentDebt[i]) ? a.currentDebt[i] : 0;
  }

  // ─── Cost of capital ────────────────────────────────────────────────────
  function syntheticRating(ebit, interest, marketCap, market, debt) {
    if (!isNum(interest) || interest <= 0) {
      // No interest reported.  With little or no debt that means AAA; with
      // real debt it means the tag is missing, so assume investment grade (BBB)
      // rather than flattering the company.
      if (isNum(debt) && debt > 0 && (!isNum(marketCap) || debt > 0.05 * marketCap)) {
        return { rating: 'BBB', spread: 0.0111, coverage: null, assumed: true };
      }
      return { rating: 'AAA', spread: 0.004, coverage: null };
    }
    const coverage = (isNum(ebit) ? ebit : 0) / interest;
    const table = (marketCap || 0) >= market.largeFirmCutoff ? market.ratingsLarge : market.ratingsSmall;
    for (const [lower, rating, spread] of table) if (coverage >= lower) return { rating, spread, coverage };
    return { rating: 'D', spread: 0.19, coverage };
  }

  /**
   * WACC with a bottom-up beta.
   *   levered beta  βL = βU × (1 + (1 − t) × D/E)
   *   cost of equity Ke = Rf + βL × ERP
   *   cost of debt  Kd = Rf + default spread (synthetic rating)
   *   WACC = E/V × Ke + D/V × Kd × (1 − t)
   */
  function costOfCapital(inp) {
    const E = Math.max(0, inp.marketCap || 0);
    const D = Math.max(0, inp.debt || 0);
    // Without a market value of equity the market weights are meaningless, so
    // fall back to an all-equity structure (the UI warns about the missing price).
    let wD = isNum(inp.targetDebtWeight) ? clamp(inp.targetDebtWeight, 0, 0.9)
      : (E > 0 ? D / (E + D) : 0);
    const wE = 1 - wD;
    const de = wE > 0 ? wD / wE : 0;
    const taxShield = inp.ebitPositive === false ? 0 : inp.marginalTax;
    const leveredBeta = isNum(inp.betaOverride) ? inp.betaOverride
      : inp.unleveredBeta * (1 + (1 - inp.marginalTax) * de);
    const ke = inp.riskFree + leveredBeta * inp.erp;
    const kdPre = isNum(inp.costOfDebtOverride) ? inp.costOfDebtOverride : inp.riskFree + inp.spread;
    const kdAfter = kdPre * (1 - taxShield);
    return { wE, wD, de, leveredBeta, ke, kdPre, kdAfter, taxShield, wacc: wE * ke + wD * kdAfter };
  }

  // ─── Default assumptions ────────────────────────────────────────────────
  /**
   * Starting assumptions from the company's own history plus market data.
   * Every choice is returned with a one-line reason so the UI can show it.
   */
  function defaultAssumptions(data, overrides = {}) {
    const fin = data.financials;
    const market = data.market;
    const n = PROJECTION_YEARS;
    const h = historicalMetrics(fin);
    const why = {};
    const latest = h[0] || {};
    const industry = (market.industries || []).find((x) => x.name === data.industry) || {};
    const rf = market.riskFree.rate;
    const tMarg = market.marginalTaxRate;

    // Revenue growth: blend last year's growth with the 3-year CAGR, then fade
    // in a straight line to the terminal rate by the final year.
    const revs = h.map((r) => r.revenue);
    const yoy = latest.revGrowth;
    const k = Math.min(3, nums(revs).length - 1);
    const cagr = k >= 1 && revs[k] > 0 && revs[0] > 0 ? Math.pow(revs[0] / revs[k], 1 / k) - 1 : null;
    let g1 = isNum(yoy) && isNum(cagr) ? 0.5 * yoy + 0.5 * cagr : (isNum(yoy) ? yoy : (isNum(cagr) ? cagr : 0.05));
    const g1Raw = g1;
    g1 = clamp(g1, -0.10, 0.40);
    const gT = isNum(overrides.terminalGrowth) ? overrides.terminalGrowth : Math.min(0.025, rf);
    why.revenueGrowth = `Year 1 = average of last year's growth (${pct(yoy)}) and the ${k}-year CAGR (${pct(cagr)})`
      + (g1 !== g1Raw ? `, capped at ${pct(g1)}` : '') + `, fading in a straight line to the ${pct(gT)} terminal rate by year ${n}.`;

    // Operating margin: start from the latest year; hold it if positive, else
    // converge to the industry's average margin.
    const m3 = mean(h.slice(0, 3).map((r) => r.ebitMargin));
    let m0 = isNum(latest.ebitMargin) ? latest.ebitMargin : (isNum(m3) ? m3 : 0.1);
    if (isNum(latest.ebitMargin) && isNum(m3) && m3 > 0 && latest.ebitMargin > 0 && latest.ebitMargin < 0.5 * m3) {
      m0 = m3;
      why.ebitMargin = `Last year's margin (${pct(latest.ebitMargin)}) is under half the 3-year average, which usually means a one-off charge, so the 3-year average (${pct(m3)}) is used and held flat.`;
    }
    let mT = m0;
    if (m0 <= 0) {
      mT = isNum(industry.operatingMargin) && industry.operatingMargin > 0 ? industry.operatingMargin : 0.10;
      why.ebitMargin = `Operating margin is negative (${pct(m0)}), so it converges to the ${industry.name || 'market'} average of ${pct(mT)} by year ${n}.`;
    }
    if (!why.ebitMargin && !isNum(latest.ebitMargin)) {
      why.ebitMargin = isNum(m3)
        ? `Last year's operating margin is not reported, so the 3-year average (${pct(m3)}) is used and held flat.`
        : `No operating margin is reported, so a placeholder of ${pct(m0)} is used - replace it with your own estimate.`;
    }
    if (!why.ebitMargin) {
      why.ebitMargin = `Latest operating margin (${pct(m0)}) held flat - no expansion assumed.`
        + (isNum(industry.operatingMargin) ? ` Industry average for reference: ${pct(industry.operatingMargin)}.` : '');
    }

    // Tax: today's effective rate converging to the marginal rate.
    const tEff0 = median(h.slice(0, 3).map((r) => r.taxRate));
    const tEff = isNum(tEff0) ? clamp(tEff0, 0, 0.4) : tMarg;
    why.taxRate = `Median effective rate of the last 3 profitable years (${pct(tEff)}), converging to the ${pct(tMarg)} marginal rate by year ${n}: low effective rates (credits, deferrals) rarely last forever.`;

    const daPct = isNum(latest.daPct) ? latest.daPct : (mean(h.map((r) => r.daPct)) || 0.03);
    const capexPct = mean(h.slice(0, 3).map((r) => r.capexPct));
    const nwcPct0 = median(h.slice(0, 3).map((r) => r.nwcPct));
    const nwcPct = isNum(nwcPct0) ? clamp(nwcPct0, -0.3, 0.4) : 0.05;
    why.daPct = `Latest year's D&A / revenue.`;
    why.capexPct = `Average of the last 3 years' capex / revenue (including assets bought with finance leases).`;
    why.nwcPct = `Median working capital / revenue over 3 years (${pct(nwcPct)}); working capital grows with revenue.`;

    // Capital structure and cost of capital.
    const lb = fin.latestBalance && fin.latestBalance.values ? fin.latestBalance.values : {};
    const price = data.quote && data.quote.price > 0 ? data.quote.price : 0;
    const shares = data.shares ? data.shares.value : 0;
    const marketCap = price * shares;
    const debt = lb.totalDebt != null ? lb.totalDebt : (fin.aligned.totalDebt || [0])[0] || 0;
    const rating = syntheticRating(latest.ebit, latest.interest, marketCap, market, debt);

    const a = {
      years: n,
      revenueGrowth: Array.from({ length: n }, (_, i) => round(lerp(g1, gT, n > 1 ? i / (n - 1) : 1))),
      ebitMargin: Array.from({ length: n }, (_, i) => round(lerp(m0, mT, n > 1 ? i / (n - 1) : 1))),
      taxRate: Array.from({ length: n }, (_, i) => round(lerp(tEff, tMarg, n > 1 ? i / (n - 1) : 1))),
      daPct: round(daPct), capexPct: round(isNum(capexPct) ? capexPct : daPct), nwcPct: round(nwcPct),
      sbcPct: round(isNum(latest.sbcPct) ? latest.sbcPct : 0),
      terminalGrowth: gT, terminalTax: tMarg,
      ronic: null, terminalMethod: 'gordon', exitMultiple: null,
      midYear: true, addBackSBC: false, includeLongTermInvestments: true,
      // Cost of capital inputs
      riskFree: rf, erp: market.erp, marginalTax: tMarg,
      industry: data.industry, unleveredBeta: industry.unleveredBeta || 0.9,
      betaOverride: null, spread: rating.spread, rating: rating.rating, coverage: rating.coverage,
      // Kept so the rating is re-derived whenever price or shares change (the
      // large/small-firm table depends on market cap), until the user types
      // their own spread.
      autoSpread: true, ratingEbit: latest.ebit, ratingInterest: latest.interest,
      ratingTables: { largeFirmCutoff: market.largeFirmCutoff, ratingsLarge: market.ratingsLarge, ratingsSmall: market.ratingsSmall },
      costOfDebtOverride: null, targetDebtWeight: null,
      // Equity bridge (latest balance sheet) and per-share inputs
      price, shares, debt,
      cash: cashLike(lb.cash, lb.shortTermInvestments, lb.cashAndSTI),
      longTermInvestments: lb.longTermInvestments || 0,
      minorityInterest: lb.minorityInterest || 0,
      preferredStock: lb.preferredStock || 0,
    };
    why.terminalGrowth = `At most 2.5% and never above the risk-free rate (${pct(rf)}): no company can outgrow the economy forever.`;
    why.beta = `${industry.name || 'Market'} unlevered beta (${(a.unleveredBeta).toFixed(2)}, Damodaran ${market.dataDate}), re-levered at this company's market debt/equity.`;
    why.costOfDebt = rating.coverage == null
      ? (rating.assumed ? 'The company has debt but no interest expense was found in its filings, so an investment-grade BBB rating is assumed - check it.' : 'No interest expense and little or no debt, so rated AAA.')
      : `Interest coverage ${rating.coverage.toFixed(1)}× → synthetic rating ${rating.rating}, spread ${pct(rating.spread)} over the risk-free rate.`;

    // Return on new investment in the terminal period: half-way between this
    // company's ROIC and the cost of capital - competitive advantages fade.
    const coc = costOfCapital(cocInputs({ ...a, ...overrides }, latest));
    const roic = mean(h.slice(0, 3).map((r) => r.roic));
    if (isNum(roic) && roic > coc.wacc) {
      a.ronic = round(coc.wacc + 0.5 * (Math.min(roic, 0.4) - coc.wacc));
      why.ronic = `Half-way between the 3-year ROIC (${pct(roic)}) and the WACC (${pct(coc.wacc)}): excess returns on new investment fade as competitors catch up.`;
    } else {
      a.ronic = round(coc.wacc);
      why.ronic = `Equal to the WACC - new investment in the long run earns its cost of capital${isNum(roic) ? ` (3-year ROIC is ${pct(roic)})` : ''}.`;
    }
    Object.assign(a, overrides);
    // Exit multiple: the EV/EBITDA the perpetuity method implies, as a starting
    // point for the cross-check.
    if (!isNum(a.exitMultiple)) {
      const v = value(fin, { ...a, terminalMethod: 'gordon' }, 'unlevered');
      a.exitMultiple = v && isNum(v.impliedExitMultiple) && v.impliedExitMultiple > 0
        ? Math.round(v.impliedExitMultiple * 2) / 2 : 12;
      why.exitMultiple = 'Set to the EV/EBITDA multiple implied by the perpetuity-growth value, as a cross-check.';
    }
    return { assumptions: a, why, history: h };
  }

  /** The synthetic rating for the current inputs (re-derived live while the
   *  spread is on automatic). */
  function currentRating(a) {
    if (!a.autoSpread || !a.ratingTables) return { rating: a.rating, spread: a.spread, coverage: a.coverage };
    return syntheticRating(a.ratingEbit, a.ratingInterest, (a.price || 0) * (a.shares || 0), a.ratingTables, a.debt);
  }

  function cocInputs(a, latest) {
    return {
      riskFree: a.riskFree, erp: a.erp, unleveredBeta: a.unleveredBeta, betaOverride: a.betaOverride,
      marginalTax: a.marginalTax, spread: currentRating(a).spread, costOfDebtOverride: a.costOfDebtOverride,
      marketCap: (a.price || 0) * (a.shares || 0), debt: a.debt, targetDebtWeight: a.targetDebtWeight,
      ebitPositive: latest ? !(isNum(latest.ebit) && latest.ebit <= 0) : true,
    };
  }

  // ─── Projection ─────────────────────────────────────────────────────────
  function project(fin, a, mode, coc) {
    const h = historicalMetrics(fin);
    const base = h[0];
    if (!base || !isNum(base.revenue) || base.revenue <= 0) return null;
    const n = a.years || PROJECTION_YEARS;
    let rev = base.revenue;
    let nwc = rev * a.nwcPct;               // normalised starting working capital
    let debt = a.debt || 0;
    const debtToRev = debt / base.revenue;
    const rows = [];
    for (let i = 0; i < n; i++) {
      const g = a.revenueGrowth[i] ?? 0;
      const newRev = rev * (1 + g);
      const ebit = newRev * (a.ebitMargin[i] ?? 0);
      const t = a.taxRate[i] ?? a.marginalTax;
      const taxes = Math.max(ebit, 0) * t;  // no tax credit assumed on losses
      const nopat = ebit - taxes;
      const da = newRev * a.daPct;
      const capex = newRev * a.capexPct;
      const newNwc = newRev * a.nwcPct;
      const dNwc = newNwc - nwc;
      const sbc = a.addBackSBC ? newRev * (a.sbcPct || 0) : 0;
      const row = { year: (fin.years[0] || 0) + i + 1, t: i + 1, revenue: newRev, growth: g, ebit,
        ebitMargin: a.ebitMargin[i], taxRate: t, taxes, nopat, da, capex, dNwc,
        ebitda: ebit + da, reinvestment: capex - da + dNwc };
      if (mode === 'levered') {
        const newDebt = newRev * debtToRev;
        const interest = debt * coc.kdPre;
        const pretax = ebit - interest;
        const netIncome = pretax - Math.max(pretax, 0) * t;
        const netBorrowing = newDebt - debt;
        Object.assign(row, { interest, netIncome, netBorrowing, debt: newDebt,
          fcf: netIncome + da - capex - dNwc + netBorrowing + sbc });
        debt = newDebt;
      } else {
        row.fcf = nopat + da - capex - dNwc + sbc;
      }
      rows.push(row);
      rev = newRev;
      nwc = newNwc;
    }
    return rows;
  }

  // ─── Valuation ──────────────────────────────────────────────────────────
  /**
   * Full valuation.  mode: 'unlevered' (FCFF at WACC, the default) or
   * 'levered' (FCFE at cost of equity).  `discountOverride` is used by the
   * sensitivity tables.
   */
  function value(fin, a, mode = 'unlevered', discountOverride = null) {
    const h = historicalMetrics(fin);
    const coc = costOfCapital(cocInputs(a, h[0]));
    const r = isNum(discountOverride) ? discountOverride : (mode === 'levered' ? coc.ke : coc.wacc);
    const proj = project(fin, a, mode, coc);
    if (!proj) return null;
    const n = proj.length;
    const last = proj[n - 1];
    const g = a.terminalGrowth;
    const tT = isNum(a.terminalTax) ? a.terminalTax : a.marginalTax;
    const warnings = [];

    // Terminal value, perpetuity growth with value-driver reinvestment:
    //   FCF(n+1) = NOPAT(n+1) × (1 − g / RONIC)
    // so growth is paid for by reinvestment instead of appearing for free.
    const ronic = isNum(a.ronic) && a.ronic > 0 ? a.ronic : r;
    const reinvestRate = clamp(g / ronic, 0, 1);
    const nopatNext = last.ebit * (1 + g) * (1 - tT);
    let fcfNext;
    if (mode === 'levered') {
      const interestNext = last.debt * coc.kdPre;
      const niNext = (last.ebit * (1 + g) - interestNext) * (1 - tT);
      fcfNext = niNext - reinvestRate * nopatNext + g * last.debt;
    } else {
      fcfNext = nopatNext * (1 - reinvestRate);
    }
    let tvGordon = null;
    if (r - g < 0.005) warnings.push(`Discount rate (${pct(r)}) must be clearly above terminal growth (${pct(g)}).`);
    else if (fcfNext <= 0) warnings.push('Terminal-year cash flow is negative, so the perpetuity value is not meaningful. Revisit the margin path.');
    else tvGordon = fcfNext / (r - g);

    const ebitdaN = last.ebitda;
    let tvExit = null;
    if (isNum(a.exitMultiple) && a.exitMultiple > 0 && ebitdaN > 0) {
      tvExit = ebitdaN * a.exitMultiple - (mode === 'levered' ? last.debt : 0);
    }
    // Discounting: mid-year convention puts each year's cash flow in the
    // middle of the year.  The perpetuity is a stream of such flows, so it is
    // discounted from n − 0.5; an exit multiple is a sale at the end of year n.
    const dfAt = (tt) => 1 / Math.pow(1 + r, tt);
    const pvRows = proj.map((p, i) => {
      const tt = a.midYear ? i + 0.5 : i + 1;
      return { ...p, period: tt, df: dfAt(tt), pv: p.fcf * dfAt(tt) };
    });
    const sumPV = pvRows.reduce((s, p) => s + p.pv, 0);
    const pvGordon = tvGordon != null ? tvGordon * dfAt(a.midYear ? n - 0.5 : n) : null;
    const pvExit = tvExit != null ? tvExit * dfAt(n) : null;
    let pvTV;
    const method = a.terminalMethod || 'gordon';
    if (method === 'exit') pvTV = pvExit;
    else if (method === 'average') pvTV = pvGordon != null && pvExit != null ? (pvGordon + pvExit) / 2 : (pvGordon ?? pvExit);
    else pvTV = pvGordon;
    if (pvTV == null) {
      return { error: warnings[0] || 'Terminal value could not be computed.', warnings, proj: pvRows, coc, discountRate: r };
    }

    const nonOperating = (a.cash || 0) + (a.includeLongTermInvestments ? (a.longTermInvestments || 0) : 0);
    const claims = (a.minorityInterest || 0) + (a.preferredStock || 0);
    let ev, equity;
    if (mode === 'levered') {
      equity = sumPV + pvTV + nonOperating - claims;
      ev = equity + (a.debt || 0) - nonOperating + claims;
    } else {
      ev = sumPV + pvTV;
      equity = ev - (a.debt || 0) - claims + nonOperating;
    }
    const perShare = a.shares > 0 ? equity / a.shares : null;
    const upside = perShare != null && a.price > 0 ? perShare / a.price - 1 : null;
    const tvShare = (sumPV + pvTV) !== 0 ? pvTV / (sumPV + pvTV) : null;
    // Cross-checks on a like-for-like present-value basis: the exit multiple
    // that gives the same PV as the perpetuity, and the perpetual growth rate
    // that gives the same PV as the exit multiple (solved by bisection, since
    // the year-11 cash flow itself depends on g through reinvestment).
    const impliedExitMultiple = pvGordon != null && ebitdaN > 0
      ? (pvGordon / dfAt(n) + (mode === 'levered' ? last.debt : 0)) / ebitdaN : null;
    const gordonPV = (gg) => {
      const rr = clamp(gg / ronic, 0, 1);
      const nopat = last.ebit * (1 + gg) * (1 - tT);
      const f = mode === 'levered'
        ? (last.ebit * (1 + gg) - last.debt * coc.kdPre) * (1 - tT) - rr * nopat + gg * last.debt
        : nopat * (1 - rr);
      return f / (r - gg) * dfAt(a.midYear ? n - 0.5 : n);
    };
    let impliedGrowthFromExit = null;
    if (pvExit != null && pvExit > 0) {
      let lo = -0.05, hi = r - 0.001;
      if (gordonPV(lo) <= pvExit && gordonPV(hi) >= pvExit) {
        for (let k = 0; k < 80; k++) { const mid = (lo + hi) / 2; if (gordonPV(mid) < pvExit) lo = mid; else hi = mid; }
        impliedGrowthFromExit = (lo + hi) / 2;
      }
    }

    if (tvShare != null && tvShare > 0.85) warnings.push(`Terminal value is ${pct(tvShare)} of the total - the result depends mostly on the long-run assumptions.`);
    if (g > a.riskFree) warnings.push('Terminal growth is above the risk-free rate, which implies the company eventually outgrows the economy.');
    if (isNum(a.ronic) && a.ronic < r && g > 0) warnings.push('Return on new investment is below the cost of capital, so growth destroys value in the terminal period.');
    if (!(a.shares > 0)) warnings.push('Shares outstanding are missing - enter them on the Company page.');
    if (!(a.price > 0)) warnings.push('No share price was found - enter it on the Company page so the market capital weights and upside can be computed.');

    return {
      mode, discountRate: r, coc, proj: pvRows, sumPV,
      tvGordon, tvExit, pvGordon, pvExit, pvTV, terminalMethod: method,
      fcfNext, nopatNext, reinvestRate, ronic,
      enterpriseValue: ev, equityValue: equity, perShare, upside,
      nonOperating, claims, debt: a.debt || 0, tvShare,
      impliedExitMultiple, impliedGrowthFromExit, warnings,
    };
  }

  /** Per-share value across discount rates (columns) and a second input (rows). */
  function sensitivity(fin, a, mode = 'unlevered') {
    const baseV = value(fin, a, mode);
    if (!baseV || baseV.error) return null;
    const r0 = baseV.discountRate;
    const rates = [-0.015, -0.01, -0.005, 0, 0.005, 0.01, 0.015].map((d) => round(r0 + d, 6)).filter((x) => x > 0.02);
    const g0 = a.terminalGrowth;
    const growths = [-0.01, -0.005, 0, 0.005, 0.01].map((d) => round(g0 + d, 6)).filter((x) => x >= -0.01);
    const m0 = a.exitMultiple || 10;
    const mults = [-4, -2, 0, 2, 4].map((d) => m0 + d).filter((x) => x > 0);
    const cell = (over, rr) => {
      const v = value(fin, { ...a, ...over }, mode, rr);
      return v && !v.error ? v.perShare : null;
    };
    return {
      rates,
      growthTable: growths.map((g) => ({ label: g, values: rates.map((rr) => cell({ terminalGrowth: g, terminalMethod: 'gordon' }, rr)) })),
      multipleTable: mults.map((m) => ({ label: m, values: rates.map((rr) => cell({ exitMultiple: m, terminalMethod: 'exit' }, rr)) })),
      base: { rate: r0, growth: g0, multiple: m0 },
    };
  }

  function pct(x, dp = 1) { return isNum(x) ? (x * 100).toFixed(dp) + '%' : 'n/a'; }

  return { PROJECTION_YEARS, historicalMetrics, costOfCapital, syntheticRating, defaultAssumptions,
    project, value, sensitivity, cocInputs, currentRating, _util: { median, mean, clamp, lerp, cashLike } };
});
