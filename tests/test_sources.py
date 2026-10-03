"""Download parsers, fed responses in the exact formats the real services
return (no network)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dcf_model  # noqa: E402

TREASURY_CSV = (
    'Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
    "09/30/2026,4.30,4.29,4.28,4.25,4.20,4.10,4.00,4.20,4.40,4.70,4.90,5.09,5.40,5.30\n"
    "09/29/2026,4.31,4.30,4.29,4.26,4.21,4.11,4.01,4.21,4.41,4.71,4.91,5.06,5.41,5.31\n"
)
FRED_CSV = "observation_date,DGS10\n2026-09-28,5.06\n2026-09-29,.\n2026-09-30,5.09\n"
STOOQ_CSV = "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-10-02,22:00:07,250.1,252.3,249.0,251.5,41234567\n"
STOOQ_ND = "Symbol,Date,Time,Open,High,Low,Close,Volume\nZZZZ.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
YAHOO_JSON = json.dumps({"chart": {"result": [{"meta": {"currency": "USD", "symbol": "AAPL", "regularMarketPrice": 251.5,
                                                        "chartPreviousClose": 249.0}}], "error": None}})


@pytest.fixture
def fake_http(monkeypatch, tmp_path):
    responses = {}
    monkeypatch.setattr(dcf_model, "CACHE_DIR", tmp_path)

    def http_get(url, headers=None, timeout=30, retries=3, opener=None):
        for key, body in responses.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return body.encode()
        raise dcf_model.FetchError("unreachable in test")
    monkeypatch.setattr(dcf_model, "http_get", http_get)
    return responses


def test_treasury_latest_10_year(fake_http):
    fake_http["home.treasury.gov"] = TREASURY_CSV
    r = dcf_model._treasury_10y()
    assert r["rate"] == pytest.approx(0.0509) and r["date"] == "2026-09-30"


def test_fred_skips_missing_days(fake_http):
    fake_http["fred.stlouisfed.org"] = FRED_CSV
    r = dcf_model._fred_10y()
    assert r["rate"] == pytest.approx(0.0509) and r["date"] == "2026-09-30"


def test_risk_free_falls_back_through_sources(fake_http):
    fake_http["fred.stlouisfed.org"] = FRED_CSV                 # Treasury unreachable
    assert dcf_model.risk_free_rate()["source"].startswith("FRED")


def test_risk_free_fallback_constant_is_not_cached(fake_http):
    r = dcf_model.risk_free_rate()                              # nothing reachable
    assert r is dcf_model.md.FALLBACK_RISK_FREE
    assert not (dcf_model.CACHE_DIR / "risk_free.json").exists()


def test_stooq_price_and_no_data(fake_http):
    fake_http["stooq.com"] = STOOQ_CSV
    assert dcf_model._price_stooq("AAPL")["price"] == 251.5
    fake_http["stooq.com"] = STOOQ_ND
    assert dcf_model._price_stooq("ZZZZ") is None


def test_yahoo_price(fake_http, monkeypatch):
    fake_http["query1.finance.yahoo.com"] = YAHOO_JSON
    fake_http["finance.yahoo.com"] = "<html></html>"
    assert dcf_model._price_yahoo("AAPL")["price"] == 251.5


def test_share_price_returns_zero_when_every_source_fails(fake_http, monkeypatch):
    monkeypatch.setattr(dcf_model, "_price_yfinance", lambda t: None)
    q = dcf_model.share_price("AAPL")
    assert q["price"] == 0 and q["source"] is None


def test_find_company_accepts_dot_or_dash_class_tickers(monkeypatch):
    monkeypatch.setattr(dcf_model, "company_tickers", lambda: {
        "0": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"}})
    assert dcf_model.find_company("brk.b")["cik_str"] == 1067983
    assert dcf_model.find_company("BRK-B")["cik_str"] == 1067983
    assert dcf_model.find_company("XYZ") is None


def test_http_get_retries_then_explains_certificate_errors(monkeypatch):
    import ssl
    import urllib.error
    calls = []

    def flaky(req, timeout=None, context=None):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        raise urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
    monkeypatch.setattr(dcf_model.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(dcf_model.time, "sleep", lambda s: None)
    with pytest.raises(dcf_model.FetchError, match="certificate"):
        dcf_model.http_get("https://example.com/x")
    assert len(calls) == 2


def test_http_get_does_not_retry_client_errors(monkeypatch):
    import urllib.error
    calls = []

    def notfound(req, timeout=None, context=None):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)
    monkeypatch.setattr(dcf_model.urllib.request, "urlopen", notfound)
    with pytest.raises(dcf_model.FetchError, match="404"):
        dcf_model.http_get("https://example.com/x")
    assert len(calls) == 1


def test_cboe_price(fake_http):
    fake_http["cdn-api.cboe.com"] = json.dumps({"data": {"symbol": "AAPL", "current_price": 333.6}})
    q = dcf_model._price_cboe("AAPL")
    assert q["price"] == 333.6 and "Cboe" in q["source"]
    fake_http["cdn-api.cboe.com"] = json.dumps({"data": {}})
    assert dcf_model._price_cboe("ZZZZ") is None
