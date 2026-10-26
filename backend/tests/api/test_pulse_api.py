"""Market pulse routes in demo mode: every route answers, validates its parameters, and shows no live values."""

import pytest


@pytest.mark.parametrize("path", ["/api/v1/markets/indices", "/api/v1/markets/news", "/api/v1/macro", "/api/v1/flows/fii-dii",
                                  "/api/v1/flows/participant-oi", "/api/v1/derivatives/movers", "/api/v1/derivatives/oi"])
async def test_pulse_routes_answer_without_live_values_in_demo(demo, path):
    r = await demo.client.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_demo"] is True and "retrieved_at" in body
    text = r.text
    if path.endswith("/indices"):
        assert all(i["value"] is None and i["state"] == "not_configured" for i in body["indices"])
    elif path.endswith("/macro"):
        assert body["coverage"]["available"] == 0
    elif path.endswith("/fii-dii"):
        assert body["latest_state"]["state"] == "not_configured" and body["latest"] is None
    else:
        assert body["state"] == "not_configured", text


@pytest.mark.parametrize(("path", "params"), [("/api/v1/markets/news", {"days": 0}), ("/api/v1/markets/news", {"days": 8}),
                                              ("/api/v1/flows/fii-dii", {"days": 0}), ("/api/v1/derivatives/movers", {"segment": "x"}),
                                              ("/api/v1/derivatives/oi", {"on": "26-10-2026"}), ("/api/v1/derivatives/oi", {"limit": 500})])
async def test_pulse_routes_validate_parameters(demo, path, params):
    r = await demo.client.get(path, params=params)
    assert r.status_code == 422 and "request_id" in r.text


async def test_sources_status_lists_the_new_sources(demo):
    keys = {c["key"] for c in (await demo.client.get("/api/v1/sources/status")).json()["connectors"]}
    assert {"nse_filings", "macro_chart", "mospi_cpi", "fred"} <= keys


async def test_history_routes_answer_and_validate(demo):
    r = await demo.client.get("/api/v1/flows/history", params={"start": "2026-09-01", "end": "2026-10-26"})
    assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["start"] == "2026-09-01"
    bad = await demo.client.get("/api/v1/flows/history", params={"start": "2026-10-26", "end": "2026-09-01"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_range"
    long = await demo.client.get("/api/v1/flows/history", params={"start": "2020-01-01", "end": "2026-10-26"})
    assert long.status_code == 422 and long.json()["error"]["code"] == "range_too_long"
    ref = await demo.client.post("/api/v1/flows/history/refresh", json={"start": "2026-10-01", "end": "2026-10-26"})
    assert ref.status_code == 200 and ref.json()["refresh"]["state"] == "not_configured"  # demo mode never fetches real data


async def test_macro_includes_interpretation(demo):
    body = (await demo.client.get("/api/v1/macro")).json()
    assert body["interpretation"]["stance"] == "Insufficient data" and body["interpretation"]["signals_read"] == 0
