"""The hosted (WSGI) entry point in app.py: same answers as the local server,
a clear message when the SEC contact is missing, the UI source never served,
and lookups never cached by a shared CDN."""
import json

import pytest

import app as hosted
import dcf_model


def call(path, query="", method="GET"):
    got = {}

    def start_response(status, headers):
        got["status"] = int(status.split()[0])
        got["headers"] = dict(headers)
    body = b"".join(hosted.app({"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query},
                               start_response))
    return got["status"], got["headers"], body


@pytest.fixture
def contact(monkeypatch):
    monkeypatch.setenv("DCF_SEC_CONTACT", "test@example.com")


def test_page_and_assets_are_served_with_security_headers():
    code, headers, body = call("/")
    assert code == 200 and b"<html" in body.lower()
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert call("/engine.js")[0] == 200


def test_source_dotfiles_and_traversal_are_not_served():
    for path in ("/src/app.jsx", "/../dcf_model.py", "/.gitignore", "/%2e%2e/app.py", "/nope.js"):
        assert call(path)[0] == 404, path


def test_missing_contact_is_a_clear_503(monkeypatch):
    monkeypatch.delenv("DCF_SEC_CONTACT", raising=False)
    monkeypatch.setattr(dcf_model, "_read_config", lambda: {})
    code, headers, body = call("/api/company", "ticker=AAPL")
    assert code == 503 and headers["Cache-Control"] == "no-store"
    assert "DCF_SEC_CONTACT" in json.loads(body)["error"]


def test_bad_ticker_400_and_demo_company_cached(contact, monkeypatch):
    assert call("/api/company", "ticker=%3Cscript%3E")[0] == 400
    assert call("/api/company", method="POST")[0] == 405
    assert call("/api/other")[0] == 404
    monkeypatch.setitem(dcf_model.DEMO, "on", True)
    code, headers, body = call("/api/company", "ticker=ACME")
    # Never cached by a shared CDN: one cached company must not answer for another.
    assert code == 200 and "private" in headers["Cache-Control"] and "s-maxage" not in headers["Cache-Control"]
    assert json.loads(body)["ticker"] == "ACME"
    code, _, body = call("/api/search", "q=ac")
    assert code == 200 and json.loads(body)["results"][0]["ticker"] == "ACME"
