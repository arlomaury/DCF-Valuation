"""The hosted (Vercel) endpoints in api/: same answers as the local server,
a clear message when the SEC contact is missing, and edge caching only on
successful lookups."""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    monkeypatch.setenv("DCF_CACHE_DIR", str(tmp_path))
    import company
    import search
    import dcf_model
    servers = []

    def serve(h):
        s = ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
        return f"http://127.0.0.1:{s.server_port}"
    yield serve(search.handler), serve(company.handler), dcf_model, monkeypatch
    for s in servers:
        s.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.headers.get("Cache-Control"), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Cache-Control"), json.loads(e.read())


def test_missing_contact_is_a_clear_503(hosted):
    _, c, dcf_model, mp = hosted
    mp.delenv("DCF_SEC_CONTACT", raising=False)
    mp.setattr(dcf_model, "_read_config", lambda: {})
    code, cache, body = get(c + "/api/company?ticker=AAPL")
    assert code == 503 and cache == "no-store" and "DCF_SEC_CONTACT" in body["error"]


def test_bad_ticker_400_and_demo_company_cached(hosted):
    s, c, dcf_model, mp = hosted
    mp.setenv("DCF_SEC_CONTACT", "test@example.com")
    assert get(c + "/api/company?ticker=%3Cscript%3E")[0] == 400
    mp.setitem(dcf_model.DEMO, "on", True)
    code, cache, body = get(c + "/api/company?ticker=ACME")
    assert code == 200 and "s-maxage" in cache and body["ticker"] == "ACME"
    code, cache, body = get(s + "/api/search?q=ac")
    assert code == 200 and body["results"][0]["ticker"] == "ACME"


def test_vercel_config_serves_only_the_built_app():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["outputDirectory"] == "web"
    ignored = (ROOT / ".vercelignore").read_text().split()
    assert "web/src/" in ignored and "tests/" in ignored
