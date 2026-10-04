/* DCF Valuation Model - browser UI.
 *
 * Source file.  The browser loads the compiled web/app.js; rebuild after
 * editing with ./scripts/build_web.sh.  All valuation math lives in
 * web/engine.js (window.DCF) so it can be tested on its own.
 */
const { useState, useMemo, useEffect, useRef, useCallback } = React;
const E = window.DCF;

// ─── Formatting ──────────────────────────────────────────────────────────
const isNum = (x) => typeof x === 'number' && isFinite(x);
const money = (n) => {
  if (!isNum(n)) return '—';
  const a = Math.abs(n), s = n < 0 ? '−' : '';
  if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}K`;
  return `${s}$${a.toFixed(2)}`;
};
const count = (n) => (!isNum(n) ? '—' : n >= 1e9 ? `${(n / 1e9).toFixed(3)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n.toLocaleString('en-US'));
const pct = (n, dp = 1) => (isNum(n) ? `${(n * 100).toFixed(dp)}%` : '—');
const price = (n) => (isNum(n) ? `$${n.toFixed(2)}` : '—');
const mult = (n) => (isNum(n) ? `${n.toFixed(1)}×` : '—');

// ─── API ─────────────────────────────────────────────────────────────────
async function api(path) {
  const res = await fetch(path);
  let body;
  try { body = await res.json(); } catch { throw new Error(`Server error (${res.status}).`); }
  if (!res.ok || body.error) throw new Error(body.error || `Server error (${res.status}).`);
  return body;
}

// ─── Small components ────────────────────────────────────────────────────
function Card({ title, subtitle, children, className = '' }) {
  return (
    <section className={`bg-slate-800/70 border border-slate-700/60 rounded-xl p-5 ${className}`}>
      {title && <h3 className="text-base font-semibold text-white">{title}</h3>}
      {subtitle && <p className="text-xs text-slate-400 mt-0.5 mb-3">{subtitle}</p>}
      {!subtitle && title && <div className="mb-3" />}
      {children}
    </section>
  );
}

function Why({ children }) {
  if (!children) return null;
  return <p className="text-[11px] leading-snug text-slate-400 mt-1.5">{children}</p>;
}

/** Number input that lets you type freely and commits on blur / Enter.
 *  kind: 'pct' shows and edits percentages, 'num' plain numbers. */
function NumInput({ label, value, onChange, kind = 'pct', dp = 2, suffix, prefix, hint, small, disabled, optional }) {
  const shown = (v) => (isNum(v) ? (kind === 'pct' ? (v * 100).toFixed(dp) : String(+v.toFixed(6))) : '');
  const [text, setText] = useState(shown(value));
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setText(shown(value)); }, [value, focused]);
  const commit = () => {
    setFocused(false);
    if (optional && String(text).trim() === '') { onChange(null); return; }
    const v = parseFloat(String(text).replace(/[,$%×x]/g, ''));
    if (!isFinite(v)) { setText(shown(value)); return; }
    onChange(kind === 'pct' ? v / 100 : v);
  };
  return (
    <label className="block">
      {label && <span className="block text-[11px] font-medium text-slate-400 mb-1">{label}</span>}
      <span className={`flex items-center bg-slate-900/80 border border-slate-600/50 rounded-lg focus-within:border-blue-500/60 ${disabled ? 'opacity-50' : ''}`}>
        {prefix && <span className="pl-2.5 text-slate-500 text-sm">{prefix}</span>}
        <input inputMode="decimal" value={text} disabled={disabled}
          onFocus={() => setFocused(true)} onBlur={commit}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
          className={`w-full bg-transparent ${small ? 'px-1.5 py-1 text-xs' : 'px-2.5 py-1.5 text-sm'} text-white outline-none font-mono text-right`} />
        {/* The small per-year boxes leave the unit to the larger box above
            them: on a phone the "%" took the room the decimal needed. */}
        {small ? <span className="pr-1" /> : <span className="pr-2.5 text-slate-500 text-sm">{suffix ?? (kind === 'pct' ? '%' : '')}</span>}
      </span>
      {hint && <Why>{hint}</Why>}
    </label>
  );
}

function Toggle({ value, onChange, options }) {
  return (
    <div className="flex gap-1 bg-slate-900/70 p-1 rounded-lg border border-slate-700/60">
      {options.map(([v, l]) => (
        <button key={v} onClick={() => onChange(v)}
          className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${value === v ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40' : 'text-slate-400 hover:text-slate-200 border border-transparent'}`}>
          {l}
        </button>
      ))}
    </div>
  );
}

function Check({ label, value, onChange, hint }) {
  return (
    <label className="flex items-start gap-2 text-xs text-slate-300 cursor-pointer">
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 accent-blue-500" />
      <span>{label}{hint && <Why>{hint}</Why>}</span>
    </label>
  );
}

function Stat({ label, value, sub, tone = 'blue' }) {
  const c = { blue: 'text-blue-300', green: 'text-emerald-300', red: 'text-rose-300', purple: 'text-violet-300', slate: 'text-slate-200' }[tone];
  return (
    <div className="bg-slate-900/60 border border-slate-700/60 rounded-xl p-4">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className={`text-xl font-semibold font-mono mt-0.5 ${c}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function Warnings({ items }) {
  if (!items || !items.length) return null;
  return (
    <div className="space-y-1.5">
      {items.map((w, i) => (
        <div key={i} className="text-xs text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">⚠ {w}</div>
      ))}
    </div>
  );
}

// ─── Pages ───────────────────────────────────────────────────────────────
const PAGES = ['Company', 'History', 'Assumptions', 'Discount rate', 'Cash flows', 'Valuation', 'Sensitivity'];

// One short "where does this come from" note per step.
const PAGE_SOURCES = [
  'Financial statements come from the company\'s 10-K and 10-Q filings on SEC EDGAR. The share price is a 15-minute-delayed quote from Cboe; shares outstanding come from the latest filing\'s cover page.',
  'Six years of reported figures from the 10-Ks. Margins, growth and ratios are calculated here from those numbers; nothing is estimated.',
  'Defaults are built from the history: growth from recent revenue trends, margins and capital spending from recent years, tax from the company\'s own rate moving to the 25% US rate. Each box says exactly how its number was set. Change any of them.',
  'The risk-free rate is today\'s 10-year Treasury yield. Beta and the market risk premium come from Professor Damodaran\'s data (NYU Stern), the source most analysts use; the debt spread comes from a credit rating estimated from interest coverage.',
  'Each year: operating profit after tax, plus depreciation, minus capital spending and the cash tied up in working capital. All of it follows from the assumptions on the previous pages.',
  'Each year\'s cash flow and the value after year 10 are discounted to today, then debt is subtracted and cash added (from the latest balance sheet) to get the value of the shares.',
  'The same valuation re-run with the discount rate and long-run growth moved up and down, to show how much the answer depends on them.',
];

function PageSource({ page }) {
  const text = PAGE_SOURCES[page];
  if (!text) return null;
  return (
    <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 text-[12px] leading-snug text-slate-400">
      <span className="font-semibold text-slate-300">Where this comes from: </span>{text}
    </div>
  );
}

function CompanyPage({ onLoad, loading, error, data, a, set, mode, setMode }) {
  const [ticker, setTicker] = useState('');
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  const timer = useRef(null);
  const search = (v) => {
    setQ(v);
    clearTimeout(timer.current);
    if (v.trim().length < 2) { setResults([]); return; }
    const mine = setTimeout(async () => {
      try {
        const r = (await api(`/api/search?q=${encodeURIComponent(v)}`)).results;
        if (timer.current === mine) setResults(r);   // ignore answers to an older query
      } catch { if (timer.current === mine) setResults([]); }
    }, 250);
    timer.current = mine;
  };
  const load = (t) => {
    const s = (t || ticker).trim().toUpperCase();
    if (!s) return;
    clearTimeout(timer.current);              // a pending name search must not reopen the list
    timer.current = null;
    setTicker(s); setResults([]); setQ(''); onLoad(s);
  };
  const lb = data?.financials?.latestBalance;
  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <div className="text-center pt-2">
        <h1 className="text-3xl font-bold text-white">DCF Valuation Model</h1>
        <p className="text-sm text-slate-400 mt-1">Intrinsic value from SEC filings, a bottom-up cost of capital, and explicit, explained assumptions.</p>
      </div>
      <Card title="Pick a company" subtitle="US companies that file 10-Ks with the SEC.">
        <div className="flex gap-2">
          <input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase().replace(/[^A-Z0-9.\-]/g, ''))}
            onKeyDown={(e) => e.key === 'Enter' && load()} placeholder="Ticker, e.g. AAPL" maxLength={10}
            className="flex-1 bg-slate-900/80 border border-slate-600/50 rounded-lg px-3 py-2.5 text-white font-mono tracking-wider outline-none focus:border-blue-500/60" />
          <button onClick={() => load()} disabled={loading || !ticker}
            className="px-5 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm font-semibold">
            {loading ? 'Loading…' : 'Load'}
          </button>
        </div>
        <div className="relative mt-3">
          <input value={q} onChange={(e) => search(e.target.value)} placeholder="…or search by company name"
            className="w-full bg-slate-900/80 border border-slate-600/50 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-blue-500/60" />
          {results.length > 0 && (
            <div className="absolute z-20 w-full mt-1 bg-slate-800 border border-slate-600 rounded-lg shadow-xl max-h-72 overflow-y-auto">
              {results.map((r) => (
                <button key={r.cik + r.ticker} onClick={() => load(r.ticker)}
                  className="w-full text-left px-3 py-2 hover:bg-slate-700/70 flex justify-between text-sm border-b border-slate-700/40 last:border-0">
                  <span className="text-slate-200">{r.name}</span><span className="font-mono text-blue-300">{r.ticker}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {error && <div className="mt-3 text-sm text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-lg px-3 py-2">{error}</div>}
      </Card>

      {data && a && (
        <>
          <Card title={`${data.companyName} (${data.ticker})`}
            subtitle={`${data.profile?.sicDescription || 'Industry code unavailable'}${data.profile?.sic ? ` · SIC ${data.profile.sic}` : ''} · fiscal years ${data.financials.years.slice(-1)[0]}–${data.financials.years[0]}`}>
            {data.isFinancial && <Warnings items={['This looks like a bank, insurer or other financial company. For these, debt is raw material rather than financing, so a free-cash-flow DCF is not a reliable method. A dividend or excess-return model fits better.']} />}
            <div className="grid sm:grid-cols-3 gap-3 mt-2">
              <NumInput label="Share price" kind="num" prefix="$" suffix="" value={a.price} onChange={(v) => set({ price: v })}
                hint={data.quote?.source ? `From ${data.quote.source}.` : 'No price source responded - enter it.'} />
              <NumInput label="Diluted shares (millions)" kind="num" suffix="M" value={a.shares / 1e6} onChange={(v) => set({ shares: v * 1e6 })}
                hint={data.shares?.basis ? `From ${data.shares.basis}.` : 'Enter manually.'} />
              <Stat label="Market cap" value={money(a.price * a.shares)} tone="slate" />
            </div>
            <p className="text-[11px] text-slate-500 mt-3">Balance sheet for the equity bridge: {lb?.date ? `latest filing dated ${lb.date}` : 'latest 10-K'}. Financial statements: SEC EDGAR XBRL.</p>
            <div className="mt-2"><Warnings items={[
              data.filingsNote,
              data.shares?.needsCheck && 'The share count on the filing cover page did not match the other share counts. Check diluted shares against the latest 10-Q before relying on the per-share value.',
              lb?.staleNote,
              !(a.price > 0) && 'No share price was found. Enter it above - it sets the market-value weights in the WACC.',
              // A market cap below 0.5% of revenue is not a real company price; it is
              // almost always a share count for one class only (Berkshire's Class A).
              a.price > 0 && a.shares > 0 && (data.financials.aligned.revenue || [])[0] > 0
                && a.price * a.shares < 0.005 * data.financials.aligned.revenue[0]
                && `The share count gives a market cap of ${money(a.price * a.shares)}, implausibly small next to revenue of ${money(data.financials.aligned.revenue[0])}. The filing may list only one share class, or count shares in a different class from the quoted price. Check diluted shares against the latest 10-Q.`,
              !(data.financials.aligned.capex || []).slice(0, 3).some((x) => x != null)
                && 'Capital spending was not found in the last 3 years of filings (some companies tag it with their own labels), so capex is set equal to D&A. Check the cash-flow statement and enter the real figure on the Assumptions page.',
            ].filter(Boolean)} /></div>
          </Card>
          <Card title="Method">
            <Toggle value={mode} onChange={setMode} options={[['unlevered', 'Free cash flow to the firm, at WACC'], ['levered', 'Free cash flow to equity, at cost of equity']]} />
            <Why>{mode === 'unlevered'
              ? 'The standard approach: value the operating business with cash flows before debt, then subtract debt. Robust when leverage changes.'
              : 'Values equity directly from cash flows after interest and borrowing. Assumes debt grows in line with revenue.'}</Why>
          </Card>
        </>
      )}
    </div>
  );
}

function HistoryPage({ data, history }) {
  const fin = data.financials, a = fin.aligned, src = fin.sources;
  const rows = [
    ['Revenue', 'revenue'], ['Operating income (EBIT)', 'operatingIncome'], ['Interest expense', 'interestExpense'],
    ['Pre-tax income', 'preTaxIncome'], ['Income tax', 'incomeTax'], ['Net income', 'netIncome'], null,
    ['Depreciation & amortization', 'dna'], ['Capital expenditures', 'capex'], ['Finance-lease asset additions', 'financeLeaseAdditions'],
    ['Stock-based compensation', 'sbc'], null,
    ['Cash & equivalents', 'cash'], ['Short-term investments', 'shortTermInvestments'], ['Long-term investments', 'longTermInvestments'],
    ['Current assets', 'currentAssets'], ['Current liabilities', 'currentLiabilities'], ['Total debt', 'totalDebt'], ['Total assets', 'totalAssets'],
  ];
  const derived = [
    ['Revenue growth', (r) => pct(r.revGrowth)], ['Operating margin', (r) => pct(r.ebitMargin)], ['Effective tax rate', (r) => pct(r.taxRate)],
    ['D&A / revenue', (r) => pct(r.daPct)], ['Capex / revenue', (r) => pct(r.capexPct)], ['Working capital / revenue', (r) => pct(r.nwcPct)],
    ['Return on invested capital', (r) => pct(r.roic)], ['EBITDA', (r) => money(r.ebitda)],
  ];
  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold text-white">Historical financials</h2>
      <Card title="As reported" subtitle="From 10-K XBRL data. Hover a line to see which XBRL tag it came from.">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-slate-400 border-b border-slate-700"><th className="text-left py-1.5 pr-3 font-medium">Fiscal year</th>
              {fin.years.map((y) => <th key={y} className="text-right py-1.5 px-2 font-medium">{y}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => r === null ? <tr key={i}><td colSpan={fin.years.length + 1} className="py-1" /></tr> : (
                <tr key={r[1]} className="border-b border-slate-800/80" title={src[r[1]] || 'not reported'}>
                  <td className="py-1.5 pr-3 text-slate-300">{r[0]}{fin.derived?.includes(r[1]) && <span className="text-amber-300"> (derived)</span>}</td>
                  {fin.years.map((y, j) => <td key={y} className="text-right px-2 font-mono text-slate-200">{money(a[r[1]]?.[j])}</td>)}
                </tr>))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Ratios" subtitle="What the default assumptions are built from.">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-slate-400 border-b border-slate-700"><th className="text-left py-1.5 pr-3 font-medium" />
              {history.map((r) => <th key={r.year} className="text-right py-1.5 px-2 font-medium">{r.year}</th>)}</tr></thead>
            <tbody>{derived.map(([l, f]) => (
              <tr key={l} className="border-b border-slate-800/80"><td className="py-1.5 pr-3 text-slate-300">{l}</td>
                {history.map((r) => <td key={r.year} className="text-right px-2 font-mono text-slate-200">{f(r)}</td>)}</tr>))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function PathEditor({ label, values, onChange, why, years }) {
  const n = values.length;
  const setEnds = (first, last) => onChange(values.map((_, i) => first + (last - first) * (n > 1 ? i / (n - 1) : 1)));
  return (
    <div>
      <div className="flex items-end gap-3">
        <div className="w-36"><NumInput label={`${label}: year 1`} value={values[0]} onChange={(v) => setEnds(v, values[n - 1])} /></div>
        <div className="w-36"><NumInput label={`year ${n}`} value={values[n - 1]} onChange={(v) => setEnds(values[0], v)} /></div>
        <span className="text-[11px] text-slate-500 pb-2">straight line between, or edit any year below</span>
      </div>
      <div className="grid grid-cols-5 lg:grid-cols-10 gap-1.5 mt-2">
        {values.map((v, i) => (
          <div key={i}><div className="text-[10px] text-slate-500 text-center">{years[i]}</div>
            <NumInput small dp={1} value={v} onChange={(x) => onChange(values.map((y, j) => (j === i ? x : y)))} /></div>
        ))}
      </div>
      <Why>{why}</Why>
    </div>
  );
}

function AssumptionsPage({ a, set, why, reset, years }) {
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-white">Projection assumptions</h2>
        <button onClick={reset} className="text-xs text-slate-300 border border-slate-600 rounded-lg px-3 py-1.5 hover:bg-slate-800">Reset to defaults</button>
      </div>
      <Card title="Operations, years 1–10">
        <div className="space-y-5">
          <PathEditor label="Revenue growth" values={a.revenueGrowth} onChange={(v) => set({ revenueGrowth: v })} why={why.revenueGrowth} years={years} />
          <PathEditor label="Operating margin" values={a.ebitMargin} onChange={(v) => set({ ebitMargin: v })} why={why.ebitMargin} years={years} />
          <PathEditor label="Tax rate" values={a.taxRate} onChange={(v) => set({ taxRate: v })} why={why.taxRate} years={years} />
          <div className="grid sm:grid-cols-3 gap-4">
            <NumInput label="Tax loss carryforward today" kind="num" prefix="$" suffix="M" dp={0}
              value={isNum(a.startingNol) ? a.startingNol / 1e6 : 0}
              onChange={(v) => set({ startingNol: Math.max(0, v) * 1e6 })}
              hint={why.startingNol} />
          </div>
        </div>
      </Card>
      <Card title="Reinvestment">
        <div className="grid sm:grid-cols-3 gap-4">
          <NumInput label="D&A / revenue" value={a.daPct} onChange={(v) => set({ daPct: v })} hint={why.daPct} />
          <NumInput label="Capex / revenue" value={a.capexPct} onChange={(v) => set({ capexPct: v })} hint={why.capexPct} />
          <NumInput label="Working capital / revenue" value={a.nwcPct} onChange={(v) => set({ nwcPct: v })} hint={why.nwcPct} />
        </div>
        <div className="mt-4 space-y-2">
          <Check label="Fade capex to steady state by year 10" value={a.capexFade} onChange={(v) => set({ capexFade: v })}
            hint={why.capexFade || 'Capex moves from the rate above to D&A plus the reinvestment the terminal growth rate needs. Off: the rate above is held for every year.'} />
          <Check label="Add back stock-based compensation" value={a.addBackSBC} onChange={(v) => set({ addBackSBC: v })}
            hint={`Off by default: stock pay is a real cost to shareholders (it dilutes them), even though no cash leaves. Latest SBC: ${pct(a.sbcPct)} of revenue.`} />
        </div>
      </Card>
      <Card title="Terminal value (after year 10)">
        <div className="grid sm:grid-cols-3 gap-4">
          <NumInput label="Terminal growth" value={a.terminalGrowth} onChange={(v) => set({ terminalGrowth: v })} hint={why.terminalGrowth} />
          <NumInput label="Return on new investment (RONIC)" value={a.ronic} onChange={(v) => set({ ronic: v })} hint={why.ronic} />
          <NumInput label="Terminal tax rate" value={a.terminalTax} onChange={(v) => set({ terminalTax: v })} hint="Marginal rate: the long-run tax a profitable US company pays." />
          <NumInput label="Terminal beta cap (optional)" kind="num" suffix="" dp={2} optional value={a.terminalBetaCap}
            onChange={(v) => set({ terminalBetaCap: isNum(v) && v > 0 ? v : null })} hint={why.terminalBetaCap} />
          <NumInput label="Terminal beta floor (optional)" kind="num" suffix="" dp={2} optional value={a.terminalBetaFloor}
            onChange={(v) => set({ terminalBetaFloor: isNum(v) && v > 0 ? v : null })} hint={why.terminalBetaCap} />
        </div>
        <div className="mt-4 grid sm:grid-cols-2 gap-4 items-start">
          <div>
            <span className="block text-[11px] font-medium text-slate-400 mb-1">Method</span>
            <Toggle value={a.terminalMethod} onChange={(v) => set({ terminalMethod: v })}
              options={[['gordon', 'Perpetuity growth'], ['exit', 'Exit multiple'], ['average', 'Average']]} />
            <Why>Perpetuity growth is the default: FCF(11) = NOPAT(11) × (1 − g ÷ RONIC), so growth has to be paid for with reinvestment. The exit multiple is a market-based cross-check.</Why>
          </div>
          <NumInput label="Exit EV / EBITDA multiple" kind="num" suffix="×" value={a.exitMultiple} onChange={(v) => set({ exitMultiple: v })} hint={why.exitMultiple} />
        </div>
        <div className="mt-4 space-y-2">
          <Check label="Mid-year discounting" value={a.midYear} onChange={(v) => set({ midYear: v })}
            hint="Cash arrives through the year, not on 31 December, so each year is discounted from its midpoint." />
          <Check label={`Value as of the latest balance sheet (stub period of ${((a.stubYears || 0) * 12).toFixed(0)} months)`} value={a.stubPeriod}
            onChange={(v) => set({ stubPeriod: v })} hint={why.stub} />
          <Check label="Count long-term marketable securities as cash" value={a.includeLongTermInvestments} onChange={(v) => set({ includeLongTermInvestments: v })}
            hint="Non-operating investments add to equity value. Turn off if they are strategic stakes that are already in operating income." />
        </div>
      </Card>
    </div>
  );
}

function RatePage({ a, set, why, v, data, mode }) {
  const c = v?.coc;
  const rating = E.currentRating(a);
  const rf = data.market.riskFree;
  const pickIndustry = (name) => {
    const ind = data.market.industries.find((x) => x.name === name);
    set({ industry: name, unleveredBeta: ind ? ind.unleveredBeta : a.unleveredBeta, betaOverride: null });
  };
  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold text-white">Discount rate</h2>
      <div className="grid sm:grid-cols-3 gap-3">
        <Stat label="Cost of equity" value={pct(c?.ke, 2)} sub={`Rf + β × ERP`} />
        <Stat label="After-tax cost of debt" value={pct(c?.kdAfter, 2)} sub={`(Rf + spread) × (1 − t)`} />
        <Stat label={mode === 'levered' ? 'Discount rate (cost of equity)' : 'WACC'} value={pct(v?.discountRate, 2)} tone="purple"
          sub={`${pct(c?.wE)} equity · ${pct(c?.wD)} debt`
            + (v && v.cocTerminal && v.cocTerminal.betaCapped ? ` · terminal ${pct(v.terminalRate, 2)} (β ${v.cocTerminal.leveredBeta.toFixed(2)})` : '')} />
      </div>
      <Card title="Cost of equity">
        <div className="grid sm:grid-cols-3 gap-4">
          <NumInput label="Risk-free rate (10-year Treasury)" value={a.riskFree} onChange={(v) => set({ riskFree: v })}
            hint={`${rf.source}, ${rf.date}.`} />
          <NumInput label="Equity risk premium" value={a.erp} onChange={(v) => set({ erp: v })}
            hint={data.market.erpNote || `Damodaran's implied ERP for the S&P 500, ${data.market.dataDate}.`} />
          <div>
            <span className="block text-[11px] font-medium text-slate-400 mb-1">Industry (for beta)</span>
            <select value={a.industry} onChange={(e) => pickIndustry(e.target.value)}
              className="w-full bg-slate-900/80 border border-slate-600/50 rounded-lg px-2 py-1.5 text-sm text-white">
              {data.market.industries.map((x) => <option key={x.name} value={x.name}>{x.name} (βu {x.unleveredBeta.toFixed(2)})</option>)}
            </select>
            <Why>{why.beta}</Why>
          </div>
        </div>
        <div className="grid sm:grid-cols-3 gap-4 mt-4">
          <NumInput label="Unlevered beta" kind="num" suffix="" dp={2} value={a.unleveredBeta} onChange={(v) => set({ unleveredBeta: v })} />
          <NumInput label="Levered beta override (optional)" kind="num" suffix="" optional value={a.betaOverride} onChange={(v) => set({ betaOverride: isNum(v) && v > 0 ? v : null })}
            hint="Leave empty to use the industry beta re-levered: βL = βu × (1 + (1 − t) × D/E)." />
          <Stat label="Levered beta used" value={c ? c.leveredBeta.toFixed(2) : '—'} sub={`D/E ${pct(c?.de)}`} tone="slate" />
        </div>
      </Card>
      <Card title="Cost of debt and weights">
        <div className="grid sm:grid-cols-3 gap-4">
          <div>
            <Stat label="Synthetic rating" value={a.autoSpread ? rating.rating : 'manual'} sub={isNum(rating.coverage) ? `interest coverage ${rating.coverage.toFixed(1)}×` : (rating.assumed ? 'no interest expense found' : 'no interest expense')} tone="slate" />
            <Why>{a.autoSpread ? why.costOfDebt : 'Spread entered by hand.'}</Why>
          </div>
          <div>
            <NumInput label="Default spread" value={rating.spread} onChange={(v) => set({ spread: v, autoSpread: false })}
              hint={a.autoSpread ? `Damodaran's spread for ${rating.rating}, using the ${(a.price || 0) * (a.shares || 0) >= 5e9 ? 'large' : 'small'}-firm table.` : 'Your spread.'} />
            {!a.autoSpread && <button onClick={() => set({ autoSpread: true })} className="text-[11px] text-blue-300 mt-1">Use the synthetic rating again</button>}
          </div>
          <NumInput label="Pre-tax cost of debt override (optional)" optional value={a.costOfDebtOverride} onChange={(v) => set({ costOfDebtOverride: isNum(v) && v > 0 ? v : null })}
            hint={`Default: risk-free + spread = ${pct(a.riskFree + rating.spread, 2)}.`} />
        </div>
        <div className="grid sm:grid-cols-3 gap-4 mt-4">
          <NumInput label="Marginal tax rate (debt shield)" value={a.marginalTax} onChange={(v) => set({ marginalTax: v })}
            hint={c && c.taxShield === 0 ? 'Operating income is negative, so interest saves no tax right now.' : 'Federal 21% plus state taxes.'} />
          <NumInput label="Target debt weight D/V (optional)" optional value={a.targetDebtWeight} onChange={(v) => set({ targetDebtWeight: isNum(v) && v >= 0 ? v : null })}
            hint={`Empty = today's market weights: debt ${money(a.debt)} vs equity ${money(a.price * a.shares)}.`} />
        </div>
      </Card>
    </div>
  );
}

const NO_REVENUE = 'No annual revenue was found in this company\'s filings, so its cash flows cannot be projected.';

function CashFlowPage({ v, mode }) {
  if (!v) return <Warnings items={[NO_REVENUE]} />;
  if (!v.proj) return null;
  const lev = mode === 'levered';
  const lines = [
    ['Revenue', 'revenue', money], ['Growth', 'growth', (x) => pct(x)], ['Operating income', 'ebit', money],
    ['Operating margin', 'ebitMargin', (x) => pct(x)],
    ...(v.proj.some((p) => p.nolUsed > 0) ? [['Tax losses used', 'nolUsed', money]] : []),
    ['− Taxes', 'taxes', money], ['= NOPAT', 'nopat', money],
    ...(lev ? [['− Interest', 'interest', money], ['Net income', 'netIncome', money]] : []),
    ['+ D&A', 'da', money], ['− Capex', 'capex', money], ['− Change in working capital', 'dNwc', money],
    ...(lev ? [['+ Net borrowing', 'netBorrowing', money]] : []),
    [lev ? '= Free cash flow to equity' : '= Free cash flow to the firm', 'fcf', money],
    ['Discount factor', 'df', (x) => x.toFixed(3)], ['Present value', 'pv', money],
  ];
  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold text-white">Cash flow build</h2>
      <Card title={lev ? 'FCFE = net income + D&A − capex − Δ working capital + net borrowing' : 'FCFF = EBIT × (1 − t) + D&A − capex − Δ working capital'}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-slate-400 border-b border-slate-700"><th className="text-left py-1.5 pr-3" />
              {v.proj.map((p) => <th key={p.year} className="text-right px-2 py-1.5 font-medium">{p.year}</th>)}</tr></thead>
            <tbody>{lines.map(([l, k, f]) => (
              <tr key={k} className={`border-b border-slate-800/80 ${l.startsWith('=') ? 'font-semibold text-white' : 'text-slate-300'}`}>
                <td className="py-1.5 pr-3 whitespace-nowrap">{l}</td>
                {v.proj.map((p) => <td key={p.year} className="text-right px-2 font-mono">{f(p[k])}</td>)}</tr>))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function ImpliedCard({ implied, a, v }) {
  if (!implied || (implied.growth == null && implied.rate == null)) return null;
  const g1 = a.revenueGrowth[0], gn = a.revenueGrowth[a.revenueGrowth.length - 1];
  return (
    <Card title="What today's price implies" subtitle="A reverse DCF: hold every other input and solve for the one that makes the value equal the share price.">
      <div className="grid sm:grid-cols-2 gap-3">
        <Stat label={`Revenue growth, every year for ${implied.years} years`} value={implied.growth == null ? 'out of range' : pct(implied.growth)}
          sub={`Your forecast: ${pct(g1)} in year 1, fading to ${pct(gn)}`} tone="purple" />
        <Stat label="Discount rate" value={implied.rate == null ? 'out of range' : pct(implied.rate, 2)}
          sub={`Your forecast: ${pct(v.discountRate, 2)} · 10-year Treasury: ${pct(a.riskFree, 2)}`} tone="purple" />
      </div>
      <Why>A large gap between value and price usually means the market expects much more growth than the forecast, or accepts a much lower return, not that the arithmetic is off. If the implied discount rate is close to or below the Treasury yield, the price is hard to justify on cash flows alone at today's rates.</Why>
    </Card>
  );
}

function ValuationPage({ v, a, mode, data, implied }) {
  if (!v) return <Warnings items={[NO_REVENUE]} />;
  if (v.error) return <Warnings items={[v.error]} />;
  const up = v.upside;
  const lb = data.financials.latestBalance;
  const extra = [
    ...(a.pensionDeficit > 0 ? [['− Unfunded pension (after tax)', -a.pensionDeficit]] : []),
    ...(a.equityInvestments > 0 ? [['+ Stakes in unconsolidated companies (book value)', a.equityInvestments]] : []),
  ];
  const nolLine = v.pvNolLeft > 0 ? [[`+ Tax losses left after year 10 (${money(v.nolLeft)})`, v.pvNolLeft]] : [];
  const bridge = mode === 'levered' ? [
    ['PV of free cash flow to equity', v.sumPV], ['+ PV of terminal value', v.pvTV], ...nolLine,
    ['+ Cash & securities', a.cash], ...(a.includeLongTermInvestments ? [['+ Long-term investments', a.longTermInvestments]] : []),
    ['− Minority interest', -a.minorityInterest], ['− Preferred stock', -a.preferredStock], ...extra,
  ] : [
    ['PV of free cash flow, years 1–10', v.sumPV], ['+ PV of terminal value', v.pvTV], ...nolLine, ['= Enterprise value', v.enterpriseValue, true],
    ['− Debt (incl. finance leases)', -a.debt], ['− Minority interest', -a.minorityInterest], ['− Preferred stock', -a.preferredStock],
    ['+ Cash & short-term investments', a.cash], ...(a.includeLongTermInvestments ? [['+ Long-term investments', a.longTermInvestments]] : []),
    ...extra,
  ];
  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold text-white">Valuation</h2>
      <div className="grid sm:grid-cols-4 gap-3">
        <Stat label="Value per share" value={price(v.perShare)} tone="green" />
        <Stat label="Share price" value={price(a.price)} tone="slate" />
        <Stat label={up >= 0 ? 'Upside' : 'Downside'} value={pct(up)} tone={up >= 0 ? 'green' : 'red'} />
        <Stat label="Terminal value share" value={pct(v.tvShare)} sub="of total present value" tone="slate" />
      </div>
      <Warnings items={[
        data.isFinancial && 'Financial company: a free-cash-flow DCF is not a reliable method for banks and insurers - treat this value with caution.',
        data.shares?.needsCheck && 'The share count needs checking (see the Company page).',
        ...v.warnings,
      ].filter(Boolean)} />
      <ImpliedCard implied={implied} a={a} v={v} />
      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="From cash flows to value per share" subtitle={`Balance-sheet items as of ${lb?.date || 'the latest 10-K'}.`}>
          <div className="space-y-1.5 text-sm">
            {bridge.map(([l, x, bold]) => (
              <div key={l} className={`flex justify-between ${bold ? 'border-t border-slate-600 pt-1.5 font-semibold text-white' : 'text-slate-300'}`}>
                <span>{l}</span><span className="font-mono">{money(x)}</span></div>))}
            <div className="flex justify-between border-t border-slate-600 pt-1.5 font-semibold text-white"><span>= Equity value</span><span className="font-mono">{money(v.equityValue)}</span></div>
            <div className="flex justify-between text-slate-300"><span>÷ Diluted shares</span><span className="font-mono">{count(a.shares)}</span></div>
            <div className="flex justify-between border-t border-slate-600 pt-1.5 font-semibold text-emerald-300"><span>= Value per share</span><span className="font-mono">{price(v.perShare)}</span></div>
          </div>
        </Card>
        <Card title="Terminal value" subtitle={`Discount rate ${pct(v.discountRate, 2)}`
          + (Math.abs(v.terminalRate - v.discountRate) > 1e-9 ? ` (terminal period ${pct(v.terminalRate, 2)})` : '')
          + ` · terminal growth ${pct(a.terminalGrowth)}`}>
          <div className="space-y-1.5 text-sm text-slate-300">
            <div className="flex justify-between"><span>NOPAT in year 11</span><span className="font-mono">{money(v.nopatNext)}</span></div>
            <div className="flex justify-between"><span>Reinvestment rate (g ÷ RONIC)</span><span className="font-mono">{pct(v.reinvestRate)}</span></div>
            <div className="flex justify-between"><span>Free cash flow in year 11</span><span className="font-mono">{money(v.fcfNext)}</span></div>
            <div className="flex justify-between border-t border-slate-700 pt-1.5"><span>Perpetuity-growth value</span><span className="font-mono">{money(v.tvGordon)}</span></div>
            <div className="flex justify-between"><span>…implies EV / EBITDA of</span><span className="font-mono">{mult(v.impliedExitMultiple)}</span></div>
            <div className="flex justify-between border-t border-slate-700 pt-1.5"><span>Exit-multiple value ({mult(a.exitMultiple)})</span><span className="font-mono">{money(v.tvExit)}</span></div>
            <div className="flex justify-between"><span>…implies perpetual growth of</span><span className="font-mono">{pct(v.impliedGrowthFromExit)}</span></div>
            <div className="flex justify-between border-t border-slate-700 pt-1.5 text-white"><span>Used ({{ gordon: 'perpetuity', exit: 'exit multiple', average: 'average' }[v.terminalMethod]}), present value</span><span className="font-mono">{money(v.pvTV)}</span></div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function SensitivityPage({ s, a }) {
  if (!s) return null;
  const tone = (x) => (!isNum(x) || !(a.price > 0) ? 'text-slate-300' : x >= a.price ? 'text-emerald-300' : 'text-rose-300');
  const Table = ({ title, rows, fmt, baseLabel }) => (
    <Card title={title} subtitle={a.price > 0 ? `Value per share. Green is above today's price of ${price(a.price)}.` : 'Value per share.'}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead><tr className="text-slate-400"><th className="text-left py-1.5 pr-2 font-sans font-medium">↓ / discount rate →</th>
            {s.rates.map((r) => <th key={r} className={`text-right px-2 ${Math.abs(r - s.base.rate) < 1e-9 ? 'text-blue-300' : ''}`}>{pct(r, 2)}</th>)}</tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.label} className="border-t border-slate-800">
              <td className={`py-1.5 pr-2 ${Math.abs(row.label - baseLabel) < 1e-9 ? 'text-blue-300' : 'text-slate-400'}`}>{fmt(row.label)}</td>
              {row.values.map((x, i) => {
                const base = Math.abs(row.label - baseLabel) < 1e-9 && Math.abs(s.rates[i] - s.base.rate) < 1e-9;
                return <td key={i} className={`text-right px-2 ${tone(x)} ${base ? 'bg-blue-500/15 rounded' : ''}`}>{price(x)}</td>;
              })}</tr>))}
          </tbody>
        </table>
      </div>
    </Card>
  );
  return (
    <div className="space-y-5">
      <h2 className="text-xl font-semibold text-white">Sensitivity</h2>
      <Table title="Discount rate × terminal growth (perpetuity method)" rows={s.growthTable} fmt={(g) => pct(g)} baseLabel={s.base.growth} />
      <Table title="Discount rate × exit EV/EBITDA multiple" rows={s.multipleTable} fmt={mult} baseLabel={s.base.multiple} />
    </div>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────
function App() {
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [mode, setMode] = useState('unlevered');
  const [a, setA] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const latestLoad = useRef(0);
  const onLoad = useCallback(async (ticker) => {
    const id = ++latestLoad.current;            // only the most recent request may land
    setLoading(true); setError(null);
    try {
      const d = await api(`/api/company?ticker=${encodeURIComponent(ticker)}`);
      if (id !== latestLoad.current) return;
      const def = E.defaultAssumptions(d);
      setData(d); setDefaults(def); setA(def.assumptions);
    } catch (e) { if (id === latestLoad.current) setError(e.message); }
    finally { if (id === latestLoad.current) setLoading(false); }
  }, []);

  const set = (patch) => setA((p) => ({ ...p, ...patch }));
  const reset = () => defaults && setA({ ...defaults.assumptions, price: a.price, shares: a.shares });
  const history = useMemo(() => (data ? E.historicalMetrics(data.financials) : []), [data]);
  const v = useMemo(() => (data && a ? E.value(data.financials, a, mode) : null), [data, a, mode]);
  const s = useMemo(() => (data && a && page === 6 ? E.sensitivity(data.financials, a, mode) : null), [data, a, mode, page]);
  const implied = useMemo(() => (data && a && page === 5 ? E.marketImplied(data.financials, a, mode) : null), [data, a, mode, page]);
  const years = v?.proj?.map((p) => p.year) || [];

  const ready = !!(data && a);
  const body = [
    <CompanyPage onLoad={onLoad} loading={loading} error={error} data={data} a={a} set={set} mode={mode} setMode={setMode} />,
    ready && <HistoryPage data={data} history={history} />,
    ready && <AssumptionsPage a={a} set={set} why={defaults.why} reset={reset} years={years} />,
    ready && <RatePage a={a} set={set} why={defaults.why} v={v} data={data} mode={mode} />,
    ready && <CashFlowPage v={v} mode={mode} />,
    ready && <ValuationPage v={v} a={a} mode={mode} data={data} implied={implied} />,
    ready && <SensitivityPage s={s} a={a} />,
  ][page];

  return (
    <div className="flex min-h-screen">
      <nav className="w-56 shrink-0 border-r border-slate-800 bg-slate-900/60 p-4 hidden md:block">
        <div className="text-sm font-semibold text-white mb-1">DCF Model</div>
        <div className="text-[11px] text-slate-500 mb-4 font-mono">{data ? data.ticker : 'no company loaded'}</div>
        {PAGES.map((p, i) => (
          <button key={p} disabled={i > 0 && !ready} onClick={() => setPage(i)}
            className={`w-full text-left px-3 py-2 rounded-lg text-sm mb-0.5 disabled:opacity-30 ${page === i ? 'bg-blue-500/15 text-blue-300' : 'text-slate-400 hover:text-slate-200'}`}>
            {i + 1}. {p}
          </button>
        ))}
        {v && !v.error && (
          <div className="mt-6 p-3 rounded-lg bg-slate-800/80 border border-slate-700/60">
            <div className="text-[11px] text-slate-400">Value per share</div>
            <div className="text-lg font-mono text-emerald-300">{price(v.perShare)}</div>
            <div className="text-[11px] text-slate-500">{a.price > 0 ? `${pct(v.upside)} vs ${price(a.price)}` : ''}</div>
          </div>
        )}
      </nav>
      <main className="flex-1 min-w-0 p-5 md:p-8 max-w-6xl">
        <div className="md:hidden mb-4"><select value={page} onChange={(e) => setPage(+e.target.value)} className="w-full bg-slate-900 border border-slate-700 rounded-lg p-2 text-sm text-white">
          {PAGES.map((p, i) => <option key={p} value={i} disabled={i > 0 && !ready}>{i + 1}. {p}</option>)}</select></div>
        <PageSource page={page} />
        {body}
        {ready && (
          <div className="flex justify-between mt-8 pt-4 border-t border-slate-800 text-sm">
            {page > 0 ? <button onClick={() => setPage(page - 1)} className="text-slate-400 hover:text-white">← {PAGES[page - 1]}</button> : <span />}
            {page < PAGES.length - 1 && <button onClick={() => setPage(page + 1)} className="px-4 py-2 rounded-lg bg-blue-600/20 text-blue-300 hover:bg-blue-600/30">{PAGES[page + 1]} →</button>}
          </div>
        )}
        <p className="text-[11px] text-slate-600 mt-8">For education and research. Not investment advice. Sources: SEC EDGAR, US Treasury, Damodaran Online (NYU Stern), Cboe / Yahoo Finance / Stooq.</p>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
