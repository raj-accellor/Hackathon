"""Open-interest buildup rules and futures aggregation, checked by hand against NSE's F&O bhavcopy columns."""

import csv
import io
import zipfile
from pathlib import Path

import pytest

from app.domain import derivatives as dv

FX = Path(__file__).parents[1] / "fixtures" / "live" / "market_pulse"


@pytest.mark.parametrize(("price", "oi", "expected"), [
    (1.2, 3.0, "long_buildup"), (-1.2, 3.0, "short_buildup"), (1.2, -3.0, "short_covering"), (-1.2, -3.0, "long_unwinding"),
    (0.0, 3.0, None), (1.0, 0.0, None), (None, 3.0, None), (1.0, None, None),
])
def test_classify_buildup_quadrants_and_unclassified_cases(price, oi, expected):
    assert dv.classify_buildup(price, oi) == expected


def _row(sym, expiry, close, prev, oi, d_oi, kind="STF", vol="10", tv="1000000"):
    return {"FinInstrmTp": kind, "TckrSymb": sym, "XpryDt": expiry, "TradDt": "2026-10-23", "SttlmPric": str(close), "ClsPric": str(close),
            "PrvsClsgPric": str(prev), "OpnIntrst": str(oi), "ChngInOpnIntrst": str(d_oi), "TtlTradgVol": vol, "TtlTrfVal": tv,
            "UndrlygPric": str(close), "NewBrdLotQty": "100"}


def test_aggregate_sums_oi_across_expiries_and_uses_near_month_price():
    rows = [_row("ABC", "2026-11-24", 105, 100, 300, 100), _row("ABC", "2026-10-27", 102, 100, 700, -50),
            {"FinInstrmTp": "STO", "TckrSymb": "ABC", "XpryDt": "2026-10-27", "OpnIntrst": "999"}]  # options are ignored
    (r,) = dv.aggregate_futures(rows)
    assert r.symbol == "ABC" and r.kind == "stock" and r.near_expiry == "2026-10-27"
    assert r.open_interest == 1000 and r.oi_change == 50
    assert r.oi_change_pct == pytest.approx(50 / 950 * 100, abs=1e-4)  # previous OI = 1000 - 50
    assert r.price_change_pct == pytest.approx(2.0)  # near month 102 vs 100
    assert r.buildup == "long_buildup" and r.volume_contracts == 20 and r.turnover_cr == pytest.approx(0.2)


def test_roll_from_near_to_next_month_is_not_unwinding():
    # 400 contracts move from October to November: total OI is unchanged, so no buildup is claimed.
    rows = [_row("ROLL", "2026-10-27", 99, 100, 600, -400), _row("ROLL", "2026-11-24", 99.5, 100, 400, 400)]
    (r,) = dv.aggregate_futures(rows)
    assert r.oi_change == 0 and r.buildup is None


def test_missing_previous_close_leaves_price_change_and_buildup_empty():
    (r,) = dv.aggregate_futures([{**_row("X", "2026-10-27", 10, 0, 100, 10), "PrvsClsgPric": ""}])
    assert r.price_change_pct is None and r.buildup is None


def test_buildup_lists_and_rank_on_recorded_nse_file():
    with zipfile.ZipFile(FX / "fo_bhavcopy_20261023_subset.csv.zip") as z:
        rows = list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode())))
    fut = dv.aggregate_futures(rows)
    by = {r.symbol: r for r in fut}
    assert by["NIFTY"].kind == "index" and by["ABCAPITAL"].kind == "stock"
    # ABCAPITAL on 23 Oct 2026: OI 13,810,500 (Oct) + 3,087,600 (Dec) + Nov contract; near-month settle 346.50 vs 357.85
    a = by["ABCAPITAL"]
    assert a.near_expiry == "2026-10-27" and a.price_change_pct == pytest.approx((346.50 / 357.85 - 1) * 100, abs=1e-3)
    assert a.open_interest == sum(float(r["OpnIntrst"]) for r in rows if r["TckrSymb"] == "ABCAPITAL" and r["FinInstrmTp"] == "STF")
    lists = dv.buildup_lists([r for r in fut if r.kind == "stock"])
    assert set(lists) == set(dv.BUILDUPS)
    for b, items in lists.items():
        assert all(i["buildup"] == b for i in items)
        assert [abs(i["oi_change_pct"]) for i in items] == sorted((abs(i["oi_change_pct"]) for i in items), reverse=True)
    top = dv.rank(fut, "oi_change_pct", 3)
    assert [t["oi_change_pct"] for t in top] == sorted((t["oi_change_pct"] for t in top), reverse=True)


def test_participant_positions_from_recorded_file():
    lines = (FX / "fao_participant_oi_23102026.csv").read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("Client Type"))
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    pos = {p["participant"]: p for p in dv.participant_positions(rows)}
    assert set(pos) == {"Client", "DII", "FII", "Pro", "TOTAL"}
    fii = pos["FII"]
    assert fii["future_index_net"] == 36986 - 317169  # long minus short, from the file
    assert fii["index_futures_long_short_ratio"] == pytest.approx(round(36986 / 317169, 3))
    assert pos["TOTAL"]["future_index_net"] == 0  # every long has a short
