"""Screener: instruments left out for a missing filtered metric are counted per metric, with the reason."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.domain.theme_dsl import Filter
from app.services import screener


def snap(iid, flat, detail=None):
    m = SimpleNamespace(instrument_id=iid, flat=flat, detail=detail or {}, score={}, is_demo=False, as_of=datetime(2026, 10, 26, tzinfo=UTC),
                        computed_at=datetime(2026, 10, 26, tzinfo=UTC),
                        versions=None)
    i = SimpleNamespace(id=iid, symbol=f"S{iid}", name=f"Co {iid}", exchange="NSE", sector="IT", currency="INR")
    return m, i


def test_exclusions_are_explained_by_statement_coverage_and_recorded_reasons():
    rows = [snap(1, {"revenue_growth_yoy": 12.0}), snap(2, {}), snap(3, {}), snap(4, {}), snap(5, {}),
            snap(6, {}, {"revenue_growth_yoy": {"status": "not_meaningful", "reason": "previous revenue is zero or negative"}})]
    annual_years = {1: 2, 3: 1, 4: 0, 5: 3, 6: 2}  # 2 has no statements at all
    out = screener.run(rows, filters=[Filter(metric="revenue_growth_yoy", op="gte", value=1)], sectors=None, search=None,
                       sort="symbol", order="asc", page=1, page_size=50, columns=["close"], annual_years=annual_years)
    assert out["total"] == 1 and out["excluded_for_missing_filter_metric"] == 5
    (x,) = out["excluded_by_metric"]
    assert x["label"] == "Revenue growth YoY" and x["count"] == 5
    assert {r["reason"]: r["count"] for r in x["reasons"]} == {
        "no statements filed with NSE and no figure on Tickertape": 1,
        "listed recently: only one financial year filed, on NSE and Tickertape": 1,
        "no annual figures filed": 1,
        "not computable from the filed figures": 1,
        "prior-year figure is zero or negative, so growth is undefined": 1,
    }
    assert {r["reason"]: r["examples"] for r in x["reasons"]}["no annual figures filed"] == ["S4"]


def test_rights_entitlements_and_stopped_stocks_are_named_as_such():
    def row(iid, symbol, age):
        m, i = snap(iid, {})
        m.score = {"data_age_days": age}
        i.symbol = symbol
        return m, i

    rows = [row(1, "NATCO-RE", 2), row(2, "LTIM", 240), row(3, "OK", 1)]
    rows[2][0].flat = {"revenue_growth_yoy": 5.0}
    out = screener.run(rows, filters=[Filter(metric="revenue_growth_yoy", op="gte", value=1)], sectors=None, search=None,
                       sort="symbol", order="asc", page=1, page_size=50, columns=["close"], annual_years={3: 3})
    reasons = {r["reason"]: r["examples"] for r in out["excluded_by_metric"][0]["reasons"]}
    assert reasons == {"rights entitlements: tradeable rights, not companies, so there are no statements": ["NATCO-RE"],
                       "stopped trading: no price for over 30 days (delisted or renamed)": ["LTIM"]}
    assert out["total"] == 1
