"""Tickertape fallback: fills only `missing` metrics, never overrides NSE values, respects bank rules, keeps lineage.

The screen payload below is a sanitised copy of a real `in_screen_stocks_read` response (2026-10-26, RELIANCE/HDFCBANK)."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.result import MetricValue, Status, ok
from app.services import provider_ratios as pr
from app.services import screener

RELI = {"mrktCapf": 1639061.09, "ttmPe": 21.93, "pbr": 1.509, "roe": 7.709, "roce": 9.18, "dbtEqt": 0.371, "divDps": 0.495,
        "evebitd": 9.525, "pftMrg": 7.437, "rvng": 10.53, "aint": 5.55, "qcur": 1.098, "ccnc": 12.3}
WHEN = datetime(2026, 10, 26, 17, 0, tzinfo=UTC)


def row(values, sid="RELI", raw=None):
    return SimpleNamespace(values=values, raw=raw if raw is not None else RELI, provider_ref=sid, retrieved_at=WHEN, source_url="https://www.tickertape.in/stocks/x")


def missing(name, reason="no financial statements ingested for this instrument"):
    return MetricValue(name, None, Status.MISSING, reason, "x", "")


def test_map_metrics_uses_advertised_fields_and_drops_invalid_multiples():
    m = pr.map_metrics({**RELI, "evebit": -4.0, "ps": None, "aqui": "n/a"})
    assert m["pe"] == 21.93 and m["market_cap"] == 1639061.09 and m["debt_equity"] == 0.371 and m["roe"] == 7.709
    assert "ev_ebit" not in m  # a negative multiple is not meaningful and is not stored as a value
    assert "ps" not in m and "quick_ratio" not in m
    assert "cash_conversion_cycle" not in m  # not mapped: Tickertape's figure was implausible for some stocks


def test_overlay_fills_only_missing_and_records_provenance():
    bundle = {"pe": missing("pe"), "roe": ok("roe", 9.25, "pct", "NSE formula", {}),
              "pb": MetricValue("pb", None, Status.NOT_MEANINGFUL, "book value is negative", "x", "")}
    filled = pr.overlay(bundle, row(pr.map_metrics(RELI)), "industrial")
    assert filled == ["pe"]
    assert bundle["pe"].value == 21.93 and bundle["pe"].status == Status.OK
    assert bundle["pe"].inputs["provider"] == "tickertape_mcp" and bundle["pe"].inputs["tickertape_field"] == "ttmPe"
    assert bundle["pe"].inputs["retrieved_at"] == WHEN.isoformat() and bundle["pe"].formula.startswith("Tickertape:")
    assert bundle["roe"].value == 9.25  # NSE value wins
    assert bundle["pb"].status == Status.NOT_MEANINGFUL and bundle["pb"].value is None  # never replaced


def test_overlay_applies_bank_rules_to_fallback_values():
    bundle = {n: missing(n) for n in ("debt_equity", "ev_ebitda", "roce", "roe", "pe")}
    filled = pr.overlay(bundle, row(pr.map_metrics(RELI), "HDBK"), "financial")
    assert set(filled) == {"roe", "pe"}
    for n in ("debt_equity", "ev_ebitda", "roce"):
        assert bundle[n].status == Status.NOT_APPLICABLE and bundle[n].value is None


def test_overlay_without_a_row_leaves_missing_as_missing():
    bundle = {"pe": missing("pe")}
    assert pr.overlay(bundle, None, "industrial") == [] and bundle["pe"].status == Status.MISSING


def test_parse_resolved_reads_the_tools_line_format():
    text = ("id:RELI|isin:INE002A01018|exchange:NSE|nseTicker:RELIANCE|bseTicker:RELIANCE|assetType:stock|slug:/stocks/reliance-industries-RELI\n"
            "id:HDBK|isin:INE040A01034|exchange:NSE|nseTicker:HDFCBANK|bseTicker:HDFCBANK|assetType:stock|slug:/stocks/hdfc-bank-HDBK")
    assert pr.parse_resolved(text) == {"RELIANCE": "RELI", "HDFCBANK": "HDBK"}
    assert pr.parse_resolved("") == {}


def test_screener_marks_cells_filled_from_tickertape():
    m = SimpleNamespace(instrument_id=1, flat={"pe": 21.93, "roe": 9.2}, score={}, is_demo=False, as_of=WHEN, computed_at=WHEN, versions=None,
                        detail={"pe": {"status": "ok", "inputs": {"provider": "tickertape_mcp"}}, "roe": {"status": "ok", "inputs": {}}})
    i = SimpleNamespace(id=1, symbol="RELIANCE", name="Reliance", exchange="NSE", sector="Energy", currency="INR")
    out = screener.run([(m, i)], filters=[], sectors=None, search=None, sort="symbol", order="asc", page=1, page_size=10, columns=["pe", "roe"])
    assert out["items"][0]["fallback"] == {"pe": "tickertape_mcp"}


def test_non_positive_tickertape_multiple_is_not_meaningful_rather_than_missing():
    raw = {**RELI, "ttmPe": -12.4}
    bundle = {"pe": missing("pe")}
    assert pr.overlay(bundle, row(pr.map_metrics(raw), raw=raw), "industrial") == []
    assert bundle["pe"].status == Status.NOT_MEANINGFUL and bundle["pe"].value is None and "non-positive" in bundle["pe"].reason
