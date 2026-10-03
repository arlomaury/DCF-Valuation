#!/usr/bin/env python3
"""
DCF Valuation Model - local web app.

Run:   python3 dcf_model.py
Then the app opens at http://127.0.0.1:8787 in your browser.

Financial statements come from SEC EDGAR (XBRL company facts), the risk-free
rate from the US Treasury, industry betas / margins / credit spreads from
Damodaran (NYU Stern), and the share price from Yahoo Finance or Stooq.
No API keys are needed.  Python standard library only; `certifi` and
`yfinance` are used if installed.

SEC asks every automated client to identify itself with a contact email.
Set it once with:   python3 dcf_model.py --email you@example.com
(or the DCF_SEC_CONTACT environment variable).  It is stored in
~/.dcf_model_cache/config.json on your computer and sent only to sec.gov.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import http.cookiejar
import io
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import market_data as md
import sec_data

APP_VERSION = "2.0"
PORT_START = 8787
HERE = Path(__file__).resolve().parent
WEB_DIR = (HERE / "web").resolve()
CACHE_DIR = Path(os.environ.get("DCF_CACHE_DIR", Path.home() / ".dcf_model_cache"))
CONFIG_FILE = CACHE_DIR / "config.json"
LOCAL_NAMES = {"127.0.0.1", "localhost", "::1"}
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

UA_BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ─── Configuration ──────────────────────────────────────────────────────────
def _read_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write_config(cfg):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def sec_contact(interactive=False):
    """The contact email SEC requires in the User-Agent header."""
    email = os.environ.get("DCF_SEC_CONTACT") or _read_config().get("sec_contact")
    if email and EMAIL_RE.match(email):
        return email
    if interactive and sys.stdin.isatty():
        print("SEC EDGAR asks every program that downloads its data to include a contact")
        print("email in its requests (https://www.sec.gov/os/accessing-edgar-data).")
        print("It is saved only on this computer and sent only to sec.gov.\n")
        while True:
            email = input("Your email: ").strip()
            if EMAIL_RE.match(email):
                cfg = _read_config()
                cfg["sec_contact"] = email
                _write_config(cfg)
                return email
            print("That does not look like an email address - try again.")
    return None


def sec_user_agent():
    email = sec_contact()
    if not email:
        raise RuntimeError("No SEC contact email set. Stop the app and run: "
                           "python3 dcf_model.py --email you@example.com")
    return f"DCFValuationModel/{APP_VERSION} {email}"


# ─── HTTPS ──────────────────────────────────────────────────────────────────
def _ssl_context():
    """Verified TLS.  Uses certifi's CA bundle when installed (python.org builds
    on macOS ship without one), or DCF_CA_BUNDLE for a corporate proxy."""
    bundle = os.environ.get("DCF_CA_BUNDLE")
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()

CERT_HELP = ("HTTPS certificate check failed. On a Mac with Python from python.org, run "
             "'Install Certificates.command' in your Python folder, or run "
             "'pip3 install certifi'. Behind a corporate proxy, set DCF_CA_BUNDLE to "
             "your company's certificate file.")


class FetchError(Exception):
    pass


_sec_lock = threading.Lock()
_sec_last = [0.0]


def _sec_throttle():
    """SEC allows 10 requests/second; stay well under it."""
    with _sec_lock:
        wait = 0.15 - (time.time() - _sec_last[0])
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.time()


def http_get(url, headers=None, timeout=30, retries=3, opener=None):
    """GET bytes with retry + exponential backoff on 429 / 5xx / network errors."""
    req = urllib.request.Request(url, headers=headers or {})
    last = None
    for attempt in range(retries):
        if "sec.gov" in url:
            _sec_throttle()
        try:
            if opener:
                resp = opener.open(req, timeout=timeout)
            else:
                resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
            with resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                raise FetchError(f"{urllib.parse.urlsplit(url).netloc} returned HTTP {e.code}") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLCertVerificationError):
                raise FetchError(CERT_HELP) from e
            last = e
        except (OSError, ValueError) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(1.5 * (2 ** attempt))
    raise FetchError(f"Could not reach {urllib.parse.urlsplit(url).netloc}: {last}")


def _cached_json(name, max_age, fetch):
    """Disk cache for slow, rarely-changing downloads."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    data = fetch()
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")   # unique per thread
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return data


# ─── SEC EDGAR ──────────────────────────────────────────────────────────────
def company_tickers():
    def fetch():
        raw = http_get("https://www.sec.gov/files/company_tickers.json",
                       {"User-Agent": sec_user_agent(), "Accept": "application/json"})
        return json.loads(raw)
    return _cached_json("company_tickers.json", 86400, fetch)


def find_company(ticker):
    t = ticker.upper()
    alt = t.replace(".", "-")           # SEC writes BRK-B, people type BRK.B
    for e in company_tickers().values():
        if e.get("ticker", "").upper() in (t, alt):
            return e
    return None


def company_facts(cik):
    padded = f"{int(cik):010d}"

    def fetch():
        raw = http_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json",
                       {"User-Agent": sec_user_agent(), "Accept": "application/json"}, timeout=60)
        return json.loads(raw)
    return _cached_json(f"facts_{padded}.json", 6 * 3600, fetch)


def company_profile(cik):
    """Industry code and fiscal year end from the SEC submissions feed."""
    padded = f"{int(cik):010d}"

    def fetch():
        raw = json.loads(http_get(f"https://data.sec.gov/submissions/CIK{padded}.json",
                                  {"User-Agent": sec_user_agent(), "Accept": "application/json"}))
        return {k: raw.get(k) for k in ("name", "sic", "sicDescription", "fiscalYearEnd",
                                        "exchanges", "stateOfIncorporation")}
    try:
        return _cached_json(f"profile_{padded}.json", 7 * 86400, fetch)
    except FetchError:
        return {}


# ─── Risk-free rate ─────────────────────────────────────────────────────────
def _treasury_10y():
    year = date.today().year
    rows = []
    for y in (year, year - 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
               f"&field_tdr_date_value={y}&page&_format=csv")
        text = http_get(url, {"User-Agent": UA_BROWSER}, timeout=20, retries=2).decode("utf-8", "replace")
        for r in csv.DictReader(io.StringIO(text)):
            try:
                m, d, yy = r["Date"].split("/")
                rows.append((f"{yy}-{m}-{d}", float(r["10 Yr"])))
            except (KeyError, ValueError):
                continue
        if rows:
            break
    if not rows:
        raise FetchError("no Treasury rows")
    day, val = max(rows)
    return {"rate": val / 100, "date": day,
            "source": "US Treasury daily par yield curve, 10-year"}


def _fred_10y():
    text = http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
                    {"User-Agent": UA_BROWSER}, timeout=20, retries=2).decode("utf-8", "replace")
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) == 2 and r[1] not in ("", ".") and r[0][:1].isdigit():
            rows.append((r[0], float(r[1])))
    if not rows:
        raise FetchError("no FRED rows")
    day, val = max(rows)
    return {"rate": val / 100, "date": day, "source": "FRED DGS10 (10-year Treasury)"}


def risk_free_rate():
    def fetch():
        for fn in (_treasury_10y, _fred_10y):
            try:
                r = fn()
                if 0 < r["rate"] < 0.2:
                    return r
            except (FetchError, OSError, ValueError) as e:
                print(f"  ⚠ risk-free source failed: {e}")
        return md.FALLBACK_RISK_FREE
    r = _cached_json("risk_free.json", 6 * 3600, fetch)
    if r is md.FALLBACK_RISK_FREE:
        (CACHE_DIR / "risk_free.json").unlink(missing_ok=True)   # retry next time
    return r


# ─── Share price ────────────────────────────────────────────────────────────
def _price_yfinance(ticker):
    try:
        import yfinance as yf
    except ImportError:
        return None
    info = yf.Ticker(ticker.replace(".", "-")).fast_info
    price = float(info.last_price or 0)
    return {"price": price, "currency": getattr(info, "currency", "USD") or "USD",
            "source": "yfinance"} if price > 0 else None


_yahoo = {"opener": None}


def _price_yahoo(ticker):
    if not _yahoo["opener"]:
        jar = http.cookiejar.CookieJar()
        _yahoo["opener"] = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=SSL_CTX))
        try:
            http_get("https://finance.yahoo.com", {"User-Agent": UA_BROWSER}, timeout=10,
                     retries=1, opener=_yahoo["opener"])
        except FetchError:
            pass
    sym = urllib.parse.quote(ticker.replace(".", "-"))
    raw = http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d",
                   {"User-Agent": UA_BROWSER, "Accept": "application/json"},
                   timeout=15, retries=2, opener=_yahoo["opener"])
    meta = json.loads(raw)["chart"]["result"][0]["meta"]
    price = float(meta.get("regularMarketPrice") or 0)
    return {"price": price, "currency": meta.get("currency", "USD"),
            "source": "Yahoo Finance"} if price > 0 else None


def _price_stooq(ticker):
    sym = urllib.parse.quote(ticker.replace("-", ".").lower() + ".us")
    text = http_get(f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv",
                    {"User-Agent": UA_BROWSER}, timeout=12, retries=1).decode("utf-8", "replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or rows[0].get("Close") in (None, "", "N/D"):
        return None
    price = float(rows[0]["Close"])
    return {"price": price, "currency": "USD", "source": "Stooq"} if price > 0 else None


def share_price(ticker):
    for fn in (_price_yfinance, _price_yahoo, _price_stooq):
        try:
            q = fn(ticker)
            if q:
                print(f"  ✓ price ${q['price']:.2f} ({q['source']})")
                return q
        except Exception as e:           # any source can fail; try the next
            print(f"  ⚠ {fn.__name__[7:]} price failed: {e}")
            if fn is _price_yahoo:
                _yahoo["opener"] = None
    return {"price": 0, "currency": "USD", "source": None}


# ─── Assemble everything the browser needs for one company ──────────────────
DEMO = {"on": False}


def load_demo(ticker):
    import demo_data
    parsed = sec_data.parse_company_facts(demo_data.demo_company())
    return {"ticker": "ACME", "companyName": "Acme Corp (demo data)", "cik": 0,
            "profile": {"sic": "3560", "sicDescription": "General Industrial Machinery (demo)"},
            "industry": "Machinery", "isFinancial": False, "financials": parsed,
            "shares": sec_data.choose_share_count(parsed),
            "quote": {"price": 30.0, "currency": "USD", "source": "demo data"},
            "market": market_inputs(demo=True)}


def load_company(ticker):
    if DEMO["on"]:
        return load_demo(ticker)
    match = find_company(ticker)
    if not match:
        raise LookupError(
            f'Ticker "{ticker}" was not found in SEC EDGAR. It may be a foreign company '
            "that files a 20-F, an OTC stock, or a fund. US-listed companies that file "
            "10-Ks are supported.")
    cik = match["cik_str"]
    print(f"\n📡 {ticker}: {match.get('title')} (CIK {cik})")
    parsed = sec_data.parse_company_facts(company_facts(cik))
    profile = company_profile(cik)
    industry = md.industry_for_sic(profile.get("sic"))
    quote = share_price(match["ticker"])
    return {
        "ticker": match["ticker"],
        "companyName": profile.get("name") or match.get("title", ticker),
        "cik": cik,
        "profile": profile,
        "industry": industry,
        "isFinancial": industry in md.FINANCIAL_INDUSTRIES,
        "financials": parsed,
        "shares": sec_data.choose_share_count(parsed),
        "quote": quote,
        "market": market_inputs(),
    }


def market_inputs(demo=False):
    return {
        "riskFree": md.FALLBACK_RISK_FREE if demo else risk_free_rate(),
        "erp": md.IMPLIED_ERP,
        "marginalTaxRate": md.MARGINAL_TAX_RATE,
        "dataDate": md.DATA_DATE,
        "industries": md.industry_table(),
        "ratingsLarge": [[b if b != float("-inf") else -1e9, r, s] for b, r, s in md.RATINGS_LARGE],
        "ratingsSmall": [[b if b != float("-inf") else -1e9, r, s] for b, r, s in md.RATINGS_SMALL],
        "largeFirmCutoff": md.LARGE_FIRM_CUTOFF,
    }


def search_companies(q, limit=15):
    if DEMO["on"]:
        return [{"ticker": "ACME", "name": "Acme Corp (demo data)", "cik": 0}]
    q = q.strip().lower()
    if not q:
        return []
    exact, starts, contains = [], [], []
    for e in company_tickers().values():
        t, n = e.get("ticker", "").lower(), e.get("title", "").lower()
        item = {"ticker": e["ticker"], "name": e.get("title", ""), "cik": e["cik_str"]}
        if t == q:
            exact.append(item)
        elif t.startswith(q) or n.startswith(q):
            starts.append(item)
        elif q in n:
            contains.append(item)
    return (exact + starts + contains)[:limit]


# ─── Local web server ───────────────────────────────────────────────────────
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "DCFModel"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def _send(self, code, body, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, allow_nan=False).encode(), "application/json")

    def _allowed(self):
        """Only answer this app's own page.  Binding to 127.0.0.1 keeps the
        network out, but not other websites open in the same browser; the Host
        check also stops DNS-rebinding attacks."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        origin = self.headers.get("Origin")
        if host not in LOCAL_NAMES:
            return False
        if origin is not None:
            o = urllib.parse.urlsplit(origin)
            if o.scheme != "http" or (o.hostname or "").lower() not in LOCAL_NAMES:
                return False
        return True

    def do_GET(self):
        if not self._allowed():
            return self._json({"error": "forbidden"}, 403)
        parsed = urllib.parse.urlsplit(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/search":
                q = params.get("q", [""])[0][:80]
                return self._json({"results": search_companies(q)})
            if parsed.path == "/api/company":
                ticker = params.get("ticker", [""])[0].strip().upper()
                if not TICKER_RE.match(ticker):
                    return self._json({"error": "Enter a valid ticker symbol, e.g. AAPL."}, 400)
                return self._json(load_company(ticker))
            if parsed.path == "/api/health":
                return self._json({"ok": True, "secContact": bool(sec_contact())})
            return self._static(parsed.path)
        except LookupError as e:
            return self._json({"error": str(e)}, 404)
        except (ValueError, FetchError, RuntimeError) as e:
            return self._json({"error": str(e)}, 502)
        except Exception as e:                       # pragma: no cover - safety net
            print(f"  ✗ unexpected error: {e!r}")
            return self._json({"error": "Unexpected error - see the terminal for details."}, 500)

    def _static(self, path):
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        f = (WEB_DIR / name).resolve()
        if WEB_DIR not in f.parents or not f.is_file():
            return self._json({"error": "not found"}, 404)
        rel = f.relative_to(WEB_DIR).parts
        if rel[0] == "src" or any(part.startswith(".") for part in rel):
            return self._json({"error": "not found"}, 404)     # UI source and dotfiles are not served
        ctype = CONTENT_TYPES.get(f.suffix)
        if not ctype:
            return self._json({"error": "not found"}, 404)
        return self._send(200, f.read_bytes(), ctype, cache="no-cache")


def find_free_port(start=PORT_START, tries=20):
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError(f"No free port between {start} and {start + tries - 1}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="DCF valuation model (local web app)")
    ap.add_argument("--email", help="contact email SEC requires (saved for next time)")
    ap.add_argument("--port", type=int, default=PORT_START)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--demo", action="store_true", help="offline demo with a made-up company")
    args = ap.parse_args(argv)
    DEMO["on"] = args.demo

    if args.email:
        if not EMAIL_RE.match(args.email):
            sys.exit("That does not look like an email address.")
        cfg = _read_config()
        cfg["sec_contact"] = args.email
        _write_config(cfg)
        print(f"✓ SEC contact email saved to {CONFIG_FILE}")
    if not args.demo and not sec_contact(interactive=True):
        sys.exit("An SEC contact email is required: python3 dcf_model.py --email you@example.com")

    if not (WEB_DIR / "index.html").is_file():
        sys.exit(f"Missing {WEB_DIR / 'index.html'} - run from a complete copy of the project.")

    port = find_free_port(args.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"\nDCF Valuation Model running at {url}   (Ctrl+C to stop)\n")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ Stopped.")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
