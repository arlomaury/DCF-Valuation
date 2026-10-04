"""Hosted version of the app (Vercel's Python runtime loads `app` from here).

The local app is dcf_model.py: a server bound to 127.0.0.1 that you run on
your own computer. This file serves the same page and the same two read-only
endpoints as a standard WSGI application, so it can be put online:

    GET /                    the page (web/index.html and its assets)
    GET /api/search?q=...    company search
    GET /api/company?ticker= one company's filings, price and market inputs

They only read public data (SEC EDGAR, Treasury, Cboe/Yahoo/Stooq) and keep no user
state, so there is nothing to log in to and nothing to leak. SEC downloads
are cached on the server, so repeat lookups don't reach SEC again.

Set DCF_SEC_CONTACT in the host's environment variables: SEC asks every
automated client to identify itself with a contact email.
"""
import json
import os
import urllib.parse
from pathlib import Path

# The only writable place on a serverless host. Set before dcf_model reads it.
os.environ.setdefault("DCF_CACHE_DIR", "/tmp/dcf_model_cache")

import dcf_model  # noqa: E402

WEB_DIR = dcf_model.WEB_DIR
# No shared (edge) caching for lookups: Vercel's CDN served one company's
# answer for every ?ticker= on the first deployment. The server keeps its own
# disk cache of SEC downloads, so repeat lookups are still fast.
CACHE_SEARCH = "private, max-age=300"
CACHE_COMPANY = "private, max-age=300"
SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", dcf_model.CSP),
    ("Strict-Transport-Security", "max-age=63072000"),
]
STATUS = {200: "200 OK", 400: "400 Bad Request", 404: "404 Not Found",
          405: "405 Method Not Allowed", 500: "500 Internal Server Error",
          502: "502 Bad Gateway", 503: "503 Service Unavailable"}


def _respond(start_response, code, body, ctype, cache="no-store"):
    start_response(STATUS[code], [("Content-Type", ctype), ("Content-Length", str(len(body))),
                                  ("Cache-Control", cache)] + SECURITY_HEADERS)
    return [body]


def _json(start_response, obj, code=200, cache="no-store"):
    return _respond(start_response, code, json.dumps(obj, allow_nan=False).encode(),
                    "application/json", cache)


def _static(start_response, path):
    name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
    f = (WEB_DIR / name).resolve()
    if WEB_DIR not in f.parents or not f.is_file():
        return _json(start_response, {"error": "not found"}, 404)
    rel = f.relative_to(WEB_DIR).parts
    if rel[0] == "src" or any(part.startswith(".") for part in rel):
        return _json(start_response, {"error": "not found"}, 404)
    ctype = dcf_model.CONTENT_TYPES.get(f.suffix)
    if not ctype:
        return _json(start_response, {"error": "not found"}, 404)
    return _respond(start_response, 200, f.read_bytes(), ctype, "public, max-age=0, must-revalidate")


def app(environ, start_response):
    if environ.get("REQUEST_METHOD", "GET") not in ("GET", "HEAD"):
        return _json(start_response, {"error": "method not allowed"}, 405)
    path = environ.get("PATH_INFO") or "/"
    params = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""))
    if not path.startswith("/api/"):
        try:
            return _static(start_response, path)
        except (ValueError, OSError):            # e.g. a NUL byte in the path
            return _json(start_response, {"error": "not found"}, 404)
    if path.startswith("/api/company/"):              # /api/company/AAPL works too
        params["ticker"] = [urllib.parse.unquote(path[len("/api/company/"):])]
        path = "/api/company"
    if path not in ("/api/search", "/api/company"):
        return _json(start_response, {"error": "not found"}, 404)
    if not dcf_model.sec_contact():
        return _json(start_response, {"error": "This site is not configured yet (no SEC contact "
                                               "email). The owner needs to set DCF_SEC_CONTACT."}, 503)
    try:
        if path == "/api/search":
            q = params.get("q", [""])[0][:80]
            return _json(start_response, {"results": dcf_model.search_companies(q)}, cache=CACHE_SEARCH)
        ticker = params.get("ticker", [""])[0].strip().upper()
        if not dcf_model.TICKER_RE.match(ticker):
            return _json(start_response, {"error": "Enter a valid ticker symbol, e.g. AAPL."}, 400)
        return _json(start_response, dcf_model.load_company(ticker), cache=CACHE_COMPANY)
    except dcf_model.TickerNotFound as e:
        return _json(start_response, {"error": str(e)}, 404, cache=CACHE_COMPANY)
    except (ValueError, dcf_model.FetchError, RuntimeError) as e:
        return _json(start_response, {"error": str(e)}, 502)
    except Exception as e:                       # pragma: no cover - safety net
        print(f"unexpected error: {e!r}")
        return _json(start_response, {"error": "Unexpected error - please try again."}, 500)


if __name__ == "__main__":                       # quick local check of the hosted build
    from wsgiref.simple_server import make_server
    print("Hosted build at http://127.0.0.1:8790")
    make_server("127.0.0.1", 8790, app).serve_forever()
