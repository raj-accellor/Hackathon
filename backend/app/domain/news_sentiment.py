"""Rule-based sentiment for stocks in the news (bullish / bearish / neutral). Deterministic and explainable.

Two parts, each scored -1, 0 or +1:

1. News tone, from what the news says. An NSE announcement is first read by its category (an order win or a
   buyback reads positive, a director's resignation or regulatory action negative); a headline, or an
   announcement whose category is not decisive, is read for unambiguous positive or negative words. A stock's
   news tone is the sign of the sum over its items in the window.
2. Market reaction, from the price move since the first news: at least +REACTION_PCT reads positive, at most
   -REACTION_PCT negative. With no move available this part is 0.

Sentiment is Bullish when the two parts sum above zero, Bearish below zero, otherwise Neutral. Confidence is
"moderate" when both parts agree and "low" otherwise. This is a secondary, low-confidence reading of news and
price reaction; it is not a forecast or a recommendation.
"""

from __future__ import annotations

import re
from typing import Any

VERSION = "news-sentiment-v1"
REACTION_PCT = 1.5

# NSE announcement categories (the `desc` field), lower-cased substring -> tone and reason
CATEGORY_TONE: tuple[tuple[str, int, str], ...] = (
    ("bagging/receiving of orders", 1, "order win"),
    ("awarding of order", 1, "order win"),
    ("buyback", 1, "buyback"),
    ("bonus", 1, "bonus issue"),
    ("dividend", 1, "dividend"),
    ("capacity addition", 1, "capacity addition"),
    ("commencement of commercial production", 1, "production start"),
    ("product launch", 1, "product launch"),
    ("acquisition", 1, "acquisition"),
    ("resignation", -1, "resignation of a director or key officer"),
    ("action(s) taken or orders passed", -1, "regulatory or legal action"),
    ("action(s) initiated or orders passed", -1, "regulatory or legal action"),
    ("pendency of litigation", -1, "litigation"),
    ("corporate insolvency", -1, "insolvency proceedings"),
    ("default", -1, "default"),
    ("fraud", -1, "fraud"),
)
# Categories whose direction depends on the text (read by the word rules below)
TEXT_DECIDES = ("credit rating", "granting/withdrawal", "change in auditors")

POSITIVE = (r"\bbags?\b", r"\bwins?\b", r"\bwon\b", r"order (?:win|worth|inflow)", r"\bcontracts? worth\b", r"\bupgrade[sd]?\b",
            r"\bbeats?\b", r"\bsurg(?:e|es|ed|ing)\b", r"\bjumps?\b", r"\bsoars?\b", r"\brall(?:y|ies|ied)\b", r"\bzooms?\b",
            r"\brecord (?:high|profit|revenue|sales)\b", r"\bprofit (?:rises|jumps|surges|grows|up)\b", r"\bbuy rating\b",
            r"\btarget (?:raised|hiked)\b", r"\bapproval (?:granted|received)\b", r"\breceives? approval\b", r"\bgets? approval\b")
NEGATIVE = (r"\bplung(?:e|es|ed|ing)\b", r"\bslump(?:s|ed)?\b", r"\btumbl(?:e|es|ed)\b", r"\bcrash(?:es|ed)?\b", r"\bsinks?\b",
            r"\bslides?\b", r"\bfalls?\b", r"\bdrops?\b", r"\bdowngrade[sd]?\b", r"\bmiss(?:es|ed)\b", r"\bpenalty\b", r"\bfined?\b",
            r"\bprobe\b", r"\braids?\b", r"\bdefaults?\b", r"\bfraud\b", r"\bresign(?:s|ed|ation)\b", r"\bshow[- ]cause\b",
            r"\bloss (?:widens|widened)\b", r"\bprofit (?:falls|drops|declines|slumps|down)\b", r"\bsell rating\b",
            r"\btarget (?:cut|lowered)\b", r"\bsuspend(?:s|ed)?\b", r"\bcancell?(?:ed|ation)\b", r"\bwithdraw(?:n|al)\b")
_POS = [re.compile(p, re.I) for p in POSITIVE]
_NEG = [re.compile(p, re.I) for p in NEGATIVE]


def word_tone(text: str) -> tuple[int, str | None]:
    pos = [p.pattern for p in _POS if p.search(text)]
    neg = [p.pattern for p in _NEG if p.search(text)]
    if pos and not neg:
        return 1, "positive wording"
    if neg and not pos:
        return -1, "negative wording"
    return 0, ("mixed wording" if pos and neg else None)


def item_tone(item: dict[str, Any]) -> dict[str, Any]:
    """Tone of one news item: +1, -1 or 0, with the rule that decided it."""
    cat = str(item.get("category") or "").lower()
    if item.get("kind") == "exchange_filing" and not any(t in cat for t in TEXT_DECIDES):
        for key, tone, why in CATEGORY_TONE:
            if key in cat:
                return {"tone": tone, "reason": why}
    text = f"{item.get('headline') or ''}. {item.get('summary') or ''}" if item.get("kind") == "news" else f"{item.get('summary') or ''}"
    tone, wording = word_tone(text)
    return {"tone": tone, "reason": wording}


def stock_sentiment(item_tones: list[dict[str, Any]], move_pct: float | None) -> dict[str, Any]:
    total = sum(t["tone"] for t in item_tones)
    news = (total > 0) - (total < 0)
    reasons = sorted({t["reason"] for t in item_tones if t["tone"] != 0 and t["reason"]})
    reaction = 0 if move_pct is None else (1 if move_pct >= REACTION_PCT else -1 if move_pct <= -REACTION_PCT else 0)
    score = news + reaction
    label = "Bullish" if score > 0 else "Bearish" if score < 0 else "Neutral"
    basis = []
    if news:
        basis.append(f"news reads {'positive' if news > 0 else 'negative'} ({', '.join(reasons)})")
    else:
        basis.append("news tone neutral" + (f" ({', '.join(reasons)} offset)" if reasons else ""))
    if move_pct is None:
        basis.append("no price move since the news yet")
    else:
        basis.append(f"price {'up' if move_pct >= 0 else 'down'} {abs(move_pct):.2f}% since the news"
                     + ("" if reaction else f" (within ±{REACTION_PCT:g}%)"))
    agree = news != 0 and reaction != 0 and news == reaction
    return {"label": label, "score": score, "news_tone": news, "price_reaction": reaction,
            "confidence": "moderate" if agree else "low", "basis": basis, "version": VERSION}
