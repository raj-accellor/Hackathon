"""Macro adapters against responses recorded from the live services on 2026-10-26."""

import json
from pathlib import Path

import httpx
import pytest

from app.connectors.base import ProviderUnavailable
from app.connectors.macro import ChartProvider, FredProvider, MospiCpiProvider, parse_chart, parse_cpi

FX = Path(__file__).parents[1] / "fixtures" / "live" / "market_pulse"


def test_parse_chart_uses_provider_price_time_and_previous_session_close():
    data = json.loads((FX / "chart_brent_2026-10-26.json").read_text())
    d = parse_chart(data, "BZ=F")
    meta = data["chart"]["result"][0]["meta"]
    assert d["value"] == meta["regularMarketPrice"] and d["currency"] == "USD"
    assert d["as_of"].startswith("2026-10-26")
    # the previous close is the provider's own reference: price minus its stated change
    assert d["prev_close"] == pytest.approx(meta["regularMarketPrice"] - meta["fulldayChange"])
    no_stated = json.loads(json.dumps(data))
    no_stated["chart"]["result"][0]["meta"].pop("fulldayChange")
    d2 = parse_chart(no_stated, "BZ=F")  # without it: the last daily close before the market time
    assert d2["prev_close"] == [c for day, c in d2["history"] if day < d2["as_of"][:10]][-1]
    assert len(d["history"]) >= 15 and all(c is not None for _, c in d["history"])


def test_parse_chart_error_payload_is_unavailable_not_a_value():
    with pytest.raises(ProviderUnavailable) as e:
        parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}}, "NOPE")
    assert e.value.state == "unavailable"


def test_parse_cpi_keeps_all_india_combined_general_newest_first():
    rows = parse_cpi(json.loads((FX / "mospi_cpi_2026-10-26.json").read_text()))
    assert rows and rows[0]["period"] == "2025-12-01" and rows[0]["inflation_yoy"] == pytest.approx(1.33)
    assert [r["period"] for r in rows] == sorted((r["period"] for r in rows), reverse=True)
    assert parse_cpi({"data": [{"year": 2025, "month": "December", "sector": "Rural", "group": "General", "inflation": "2"}]}) == []


async def test_unconfigured_adapters_report_not_configured():
    for p, call in ((ChartProvider(None, 5, "t"), lambda p: p.series("BZ=F")), (MospiCpiProvider(None, 5, "t"), lambda p: p.india_cpi()),
                    (FredProvider(None, "https://fred.example", 5, "t"), lambda p: p.observations("CPIAUCSL"))):
        assert p.status()["state"] == "not_configured"
        with pytest.raises(ProviderUnavailable) as e:
            await call(p)
        assert e.value.state == "not_configured"
        await p.close()


async def test_fred_skips_missing_values_and_never_exposes_the_key():
    seen = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, json={"observations": [{"date": "2026-09-01", "value": "4.25"}, {"date": "2026-08-01", "value": "."}]})

    p = FredProvider("secret-key", "https://fred.example", 5, "t", transport=httpx.MockTransport(h))
    assert await p.observations("FEDFUNDS") == [("2026-09-01", 4.25)]
    assert "secret-key" not in repr(p) and "secret-key" not in json.dumps(p.status())
    assert seen[0].params["sort_order"] == "desc"
    await p.close()


async def test_http_errors_are_unavailable_and_trip_no_cached_value():
    calls = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    p = ChartProvider("https://chart.example/v8/finance/chart", 5, "t", transport=httpx.MockTransport(h))
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await p.series("BZ=F")
    assert len(calls) == 2  # a failure is never cached as data
    await p.close()
