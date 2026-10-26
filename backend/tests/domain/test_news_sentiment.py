"""Rule-based news sentiment: category rules, wording rules, and how news tone and price reaction combine."""

import pytest

from app.domain import news_sentiment as ns


def filing(cat, summary=""):
    return {"kind": "exchange_filing", "category": cat, "summary": summary}


def headline(text, summary=""):
    return {"kind": "news", "headline": text, "summary": summary}


@pytest.mark.parametrize(("item", "tone"), [
    (filing("Bagging/Receiving of orders/contracts"), 1),
    (filing("Buyback"), 1),
    (filing("Resignation of Director/KMP/SMP"), -1),
    (filing("Action(s) taken or orders passed"), -1),
    (filing("Corporate Insolvency Resolution Process"), -1),
    (filing("Press Release", "Company X has informed the Exchange regarding a press release"), 0),  # boilerplate: neutral
    (filing("Credit Rating- Revision", "CRISIL has upgraded the long-term rating"), 1),  # text decides
    (filing("Credit Rating- Revision", "ICRA downgraded the rating"), -1),
    (headline("Infosys share price drops 4% after Q2"), -1),
    (headline("BSE shares jump 4% as MOFSL upgrades stock to buy"), 1),
    (headline("Sensex falls but Tata Steel rallies"), 0),  # mixed wording
    (headline("Markets open flat"), 0),
])
def test_item_tone(item, tone):
    assert ns.item_tone(item)["tone"] == tone


def test_bullish_when_news_and_price_agree():
    s = ns.stock_sentiment([{"tone": 1, "reason": "order win"}], 3.2)
    assert s["label"] == "Bullish" and s["score"] == 2 and s["confidence"] == "moderate"
    assert s["basis"] == ["news reads positive (order win)", "price up 3.20% since the news"]


def test_bearish_on_price_alone_and_neutral_when_parts_cancel():
    assert ns.stock_sentiment([{"tone": 0, "reason": None}], -2.66)["label"] == "Bearish"
    s = ns.stock_sentiment([{"tone": 1, "reason": "dividend"}], -1.8)
    assert s["label"] == "Neutral" and s["confidence"] == "low"


def test_small_moves_and_missing_moves_leave_reaction_neutral():
    s = ns.stock_sentiment([{"tone": -1, "reason": "resignation of a director or key officer"}], 0.7)
    assert s["label"] == "Bearish" and s["price_reaction"] == 0 and "within ±1.5%" in s["basis"][1]
    s = ns.stock_sentiment([], None)
    assert s["label"] == "Neutral" and s["basis"][1] == "no price move since the news yet"


def test_news_tone_is_the_sign_of_the_items_sum():
    items = [{"tone": 1, "reason": "order win"}, {"tone": 1, "reason": "dividend"}, {"tone": -1, "reason": "litigation"}]
    assert ns.stock_sentiment(items, None)["news_tone"] == 1
