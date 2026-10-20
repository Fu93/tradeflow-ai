"""T3 shadow gate: the would-be accept is recorded and never applied."""
from __future__ import annotations


def test_shadow_never_applies_and_wilson_fails_closed():
    from src.desk.shadow import evaluate_shadow, progress, wilson_interval
    from src.desk.span_read import read_amounts, read_one

    assert wilson_interval(1, 0) is None
    low, high = wilson_interval(1, 1)
    assert 0.0 <= low <= high <= 1.0
    amounts = read_amounts("1,000.50 and 2.5")
    assert 1000.50 in amounts
    assert read_one("") is None
    reading = {
        "status": "accepted",
        "parsed": {"unit_price": 15.60, "currency": "CNY"},
    }
    mention = {
        "speaker": "supplier",
        "polarity": "affirmative",
        "currency": "CNY",
        "unit": "per_piece",
        "class": "own_unit_offer",
        "evidence_span": "15.60元/件",
        "is_question": False,
    }
    shadow = evaluate_shadow(
        "报价 15.60元/件",
        reading,
        low=1,
        high=100,
        mentions=[mention],
        extractor_configured=True,
    )
    assert shadow["applied"] is False
    assert shadow["auto_accept"] is False
    assert shadow["would_accept"] is True
    message = "报价 15.60元/件"

    def refused(**overrides: object) -> bool:
        bad = dict(mention)
        bad.update(overrides)
        decision = evaluate_shadow(
            message,
            reading,
            low=1,
            high=100,
            mentions=[bad],
            extractor_configured=True,
        )
        return bool(decision["would_accept"])

    assert refused(speaker="buyer") is False
    assert refused(polarity="negative") is False
    assert refused(is_question=True) is False
    lot = dict(mention)
    lot["unit"] = "lot"
    lot["evidence_span"] = "15.60元"
    lot_decision = evaluate_shadow(
        "价格 15.60元",
        reading,
        low=1,
        high=100,
        mentions=[lot],
        extractor_configured=True,
    )
    assert lot_decision["would_accept"] is False
    unmarked = dict(mention)
    unmarked["unit"] = "lot"
    unmarked["evidence_span"] = "18.40 CNY per unit"
    unmarked_decision = evaluate_shadow(
        "18.40 CNY per unit",
        reading,
        low=1,
        high=100,
        mentions=[unmarked],
        extractor_configured=True,
    )
    assert unmarked_decision["would_accept"] is False
    from src.desk.shadow import _neighborhood

    assert _neighborhood("hello", "missing") == ""
    instruction = dict(mention)
    instruction["class"] = "instruction"
    instruction["evidence_span"] = "15.60元/件"
    vetoed = evaluate_shadow(
        "报价 15.60元/件",
        reading,
        low=1,
        high=100,
        mentions=[instruction, mention],
        extractor_configured=True,
    )
    assert vetoed["applied"] is False
    assert vetoed["would_accept"] is False
    gate = progress([{"would_accept": True, "resolved": True, "matched": True}])
    assert gate["auto_accept"] is False
    assert gate["confirmed_unchanged"] == 1
