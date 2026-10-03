"""Shared handler for the hosted (Vercel) version of the app.

The local app (dcf_model.py) is a single server bound to 127.0.0.1.  On Vercel
the static page is served from web/ and these two read-only endpoints run as
serverless functions:  /api/search  and  /api/company.

They only read public data (SEC EDGAR, Treasury, Yahoo/Stooq) and keep no user
state, so there is nothing to log in to and nothing to leak.  Responses are
cached at Vercel's edge so repeated lookups do not reach SEC again.

Set DCF_SEC_CONTACT in the Vercel project's environment variables: SEC asks
every automated client to identify itself with a contact email.
"""
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler

os.environ.setdefault("DCF_CACHE_DIR", "/tmp/dcf_model_cache")   # the only writable place on Vercel
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dcf_model  # noqa: E402

CACHE_SEARCH = "public, max-age=300, s-maxage=86400"
CACHE_COMPANY = "public, max-age=300, s-maxage=3600"


def make_handler(route):
    class handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, obj, code=200, cache="no-store"):
            body = json.dumps(obj, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if not dcf_model.sec_contact():
                return self._json({"error": "This site is not configured yet (no SEC contact email). "
                                            "The owner needs to set DCF_SEC_CONTACT."}, 503)
            try:
                if route == "search":
                    q = params.get("q", [""])[0][:80]
                    return self._json({"results": dcf_model.search_companies(q)}, cache=CACHE_SEARCH)
                ticker = params.get("ticker", [""])[0].strip().upper()
                if not dcf_model.TICKER_RE.match(ticker):
                    return self._json({"error": "Enter a valid ticker symbol, e.g. AAPL."}, 400)
                return self._json(dcf_model.load_company(ticker), cache=CACHE_COMPANY)
            except LookupError as e:
                return self._json({"error": str(e)}, 404, cache=CACHE_COMPANY)
            except (ValueError, dcf_model.FetchError, RuntimeError) as e:
                return self._json({"error": str(e)}, 502)
            except Exception as e:                       # pragma: no cover - safety net
                print(f"unexpected error: {e!r}")
                return self._json({"error": "Unexpected error - please try again."}, 500)

        def do_POST(self):
            return self._json({"error": "method not allowed"}, 405)

    return handler
