"""Open-interest analytics from NSE's daily F&O bhavcopy (deterministic, no estimates).

Buildup classification compares the day's futures price change with the day's change in open interest:

| price | open interest | classification  | usual reading                          |
|-------|---------------|-----------------|----------------------------------------|
| up    | up            | long_buildup    | new long positions                     |
| down  | up            | short_buildup   | new short positions                    |
| up    | down          | short_covering  | shorts closing positions               |
| down  | down          | long_unwinding  | longs closing positions                |

A zero change in either leg is left unclassified. The readings are conventions, not predictions.

Per underlying, open interest and its change are summed across all futures expiries (so a roll from
the near to the next month does not show up as unwinding), and the price change is the near-month
contract's settlement change against its previous close.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

BUILDUPS = ("long_buildup", "short_buildup", "short_covering", "long_unwinding")
BUILDUP_LABEL = {"long_buildup": "Long buildup", "short_buildup": "Short buildup", "short_covering": "Short covering",
                 "long_unwinding": "Long unwinding"}
FUTURES_TYPES = {"STF": "stock", "IDF": "index"}


def classify_buildup(price_change_pct: float | None, oi_change_pct: float | None) -> str | None:
    if price_change_pct is None or oi_change_pct is None or price_change_pct == 0 or oi_change_pct == 0:
        return None
    if oi_change_pct > 0:
        return "long_buildup" if price_change_pct > 0 else "short_buildup"
    return "short_covering" if price_change_pct > 0 else "long_unwinding"


def _f(v: Any) -> float | None:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "-") else None
    except (TypeError, ValueError):
        return None


@dataclass
class FuturesRow:
    symbol: str
    kind: str  # stock | index
    trade_date: str
    near_expiry: str
    close: float | None
    prev_close: float | None
    price_change_pct: float | None
    underlying: float | None
    open_interest: float
    oi_change: float
    oi_change_pct: float | None
    volume_contracts: float
    turnover_cr: float | None
    lot_size: float | None
    buildup: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_futures(rows: list[dict[str, str]]) -> list[FuturesRow]:
    """One row per futures underlying from the F&O bhavcopy (UDiFF column names)."""
    by: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        kind = FUTURES_TYPES.get(r.get("FinInstrmTp", ""))
        if kind and r.get("TckrSymb"):
            by[(r["TckrSymb"], kind)].append(r)
    out: list[FuturesRow] = []
    for (sym, kind), contracts in by.items():
        contracts.sort(key=lambda r: r.get("XpryDt") or "")
        near = contracts[0]
        oi = sum(_f(c.get("OpnIntrst")) or 0.0 for c in contracts)
        d_oi = sum(_f(c.get("ChngInOpnIntrst")) or 0.0 for c in contracts)
        prev_oi = oi - d_oi
        close = _f(near.get("SttlmPric")) or _f(near.get("ClsPric"))
        prev = _f(near.get("PrvsClsgPric"))
        pchg = round((close / prev - 1) * 100, 4) if close is not None and prev not in (None, 0) else None
        oichg = round(d_oi / prev_oi * 100, 4) if prev_oi > 0 else None
        tv = sum(_f(c.get("TtlTrfVal")) or 0.0 for c in contracts)
        out.append(FuturesRow(symbol=sym, kind=kind, trade_date=near.get("TradDt", ""), near_expiry=near.get("XpryDt", ""), close=close,
                              prev_close=prev, price_change_pct=pchg, underlying=_f(near.get("UndrlygPric")), open_interest=oi,
                              oi_change=d_oi, oi_change_pct=oichg,
                              volume_contracts=sum(_f(c.get("TtlTradgVol")) or 0.0 for c in contracts),
                              turnover_cr=round(tv / 1e7, 2) if tv else None, lot_size=_f(near.get("NewBrdLotQty")),
                              buildup=classify_buildup(pchg, oichg)))
    return out


def rank(rows: list[FuturesRow], key: str, n: int, reverse: bool = True) -> list[dict[str, Any]]:
    have = [r for r in rows if getattr(r, key) is not None]
    have.sort(key=lambda r: getattr(r, key), reverse=reverse)
    return [r.to_dict() for r in have[:n]]


def buildup_lists(rows: list[FuturesRow], n: int = 15) -> dict[str, list[dict[str, Any]]]:
    """Each buildup bucket, strongest open-interest change first."""
    out: dict[str, list[dict[str, Any]]] = {}
    for b in BUILDUPS:
        sel = [r for r in rows if r.buildup == b]
        sel.sort(key=lambda r: abs(r.oi_change_pct or 0), reverse=True)
        out[b] = [r.to_dict() for r in sel[:n]]
    return out


def participant_positions(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Participant-wise open interest rows with net index-futures, stock-futures and index-option positions."""
    out = []
    for r in rows:
        who = (r.get("Client Type") or "").strip()
        if not who:
            continue
        g = {k.strip(): _f(v) for k, v in r.items() if k}

        def net(a: str, b: str, g: dict[str, float | None] = g) -> float | None:
            x, y = g.get(a), g.get(b)
            return None if x is None or y is None else x - y

        fil, fis = g.get("Future Index Long"), g.get("Future Index Short")
        out.append({"participant": who, "future_index_long": g.get("Future Index Long"), "future_index_short": g.get("Future Index Short"),
                    "future_index_net": net("Future Index Long", "Future Index Short"),
                    "future_stock_net": net("Future Stock Long", "Future Stock Short"),
                    "option_index_call_net": net("Option Index Call Long", "Option Index Call Short"),
                    "option_index_put_net": net("Option Index Put Long", "Option Index Put Short"),
                    "total_long": g.get("Total Long Contracts"), "total_short": g.get("Total Short Contracts"),
                    "index_futures_long_short_ratio": round(fil / fis, 3) if fil is not None and fis else None})
    return out


def live_futures(prev_rows: list[dict[str, str]], quotes: dict[str, dict[str, Any]], today: str) -> list[FuturesRow]:
    """Intraday futures rows: live broker quotes (keyed `NFO:<contract name>`, each with last_price, prev_close, oi,
    volume) against the previous trading day's F&O bhavcopy. Per underlying, OI and its previous value are summed over
    the same contracts (those the broker quoted and that have not expired), so the change compares like with like.
    The price change is the nearest live contract's last price against the broker's previous close (the bhavcopy
    settlement when the broker gives none)."""
    by: dict[tuple[str, str], list[tuple[dict[str, str], dict[str, Any]]]] = defaultdict(list)
    for r in prev_rows:
        kind = FUTURES_TYPES.get(r.get("FinInstrmTp", ""))
        q = quotes.get(f"NFO:{r.get('FinInstrmNm', '')}")
        if kind and q is not None and (r.get("XpryDt") or "") >= today:
            by[(r["TckrSymb"], kind)].append((r, q))
    out: list[FuturesRow] = []
    for (sym, kind), pairs in by.items():
        pairs.sort(key=lambda p: p[0].get("XpryDt") or "")
        near, nq = pairs[0]
        oi = [q.get("oi") for _, q in pairs]
        if any(v is None for v in oi):
            continue
        cur_oi = float(sum(oi))  # type: ignore[arg-type]
        prev_oi = sum(_f(r.get("OpnIntrst")) or 0.0 for r, _ in pairs)
        last = nq.get("last_price")
        prev = nq.get("prev_close") or _f(near.get("SttlmPric")) or _f(near.get("ClsPric"))
        pchg = round((last / prev - 1) * 100, 4) if last is not None and prev else None
        d_oi = cur_oi - prev_oi
        oichg = round(d_oi / prev_oi * 100, 4) if prev_oi > 0 else None
        lot = _f(near.get("NewBrdLotQty"))
        vol = sum(q.get("volume") or 0.0 for _, q in pairs)
        out.append(FuturesRow(symbol=sym, kind=kind, trade_date=today, near_expiry=near.get("XpryDt", ""), close=last, prev_close=prev,
                              price_change_pct=pchg, underlying=None, open_interest=cur_oi, oi_change=d_oi, oi_change_pct=oichg,
                              volume_contracts=round(vol / lot) if lot else vol, turnover_cr=None, lot_size=lot,
                              buildup=classify_buildup(pchg, oichg)))
    return out
