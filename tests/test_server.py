"""The local server: static files, input validation, and refusing other sites."""
import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import dcf_model  # noqa: E402


@pytest.fixture
def base(tmp_path, monkeypatch):
    # Serve a copy of web/ from a path that itself contains a "src" folder,
    # which must not stop the app from loading.
    web = tmp_path / "src" / "project" / "web"
    shutil.copytree(os.path.join(ROOT, "web"), web)
    monkeypatch.setattr(dcf_model, "WEB_DIR", web.resolve())
    monkeypatch.setattr(dcf_model, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setitem(dcf_model.DEMO, "on", True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dcf_model.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_app_files_served_even_under_a_src_directory(base):
    for path in ("/", "/app.js", "/engine.js", "/app.css", "/vendor/react.production.min.js"):
        assert get(base + path)[0] == 200, path


def test_source_and_traversal_not_served(base):
    for path in ("/src/app.jsx", "/../dcf_model.py", "/%2e%2e/dcf_model.py", "/.hidden"):
        assert get(base + path)[0] == 404, path


def test_other_websites_refused(base):
    assert get(base + "/api/health", {"Host": "evil.example"})[0] == 403
    assert get(base + "/api/health", {"Origin": "https://evil.example"})[0] == 403
    assert get(base + "/api/health", {"Origin": base})[0] == 200


def test_ticker_validated(base):
    assert get(base + "/api/company?ticker=%3Cscript%3E")[0] == 400
    assert get(base + "/api/company?ticker=")[0] == 400


def test_demo_company_round_trip(base):
    code, body, headers = get(base + "/api/company?ticker=ACME")
    assert code == 200
    d = json.loads(body)
    assert d["financials"]["years"][0] == 2025
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers
