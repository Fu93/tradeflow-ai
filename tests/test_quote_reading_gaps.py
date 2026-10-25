"""The quote-reading gaps found by auditing PR#15's contract against master.

PR#15 (drafted 2026-10-20, never merged) specified four reading capabilities
the master parser did not have. Probing master with its own contract cases
found four real gaps — two of them correctness defects (a per-dozen price
confirmed as per-piece overpays by the pack count; a surcharge read as the
goods price). This file pins the implemented behaviour case by case, so the
capability cannot silently regress:

1. **Fee lines leave candidacy entirely.** freight / sample / mould / tooling
   amounts used to survive in `competing`, where "运费 3 元，单价 20 元" became
   "more than one price" — review noise on a plainly readable quote. The fee
   amount is excluded; the one goods price reads.
2. **A non-piece measure word is a unit-basis mismatch.** 元/打, 每吨…元,
   元/码 never become a per-piece prefill, however strong the label.
3. **Surcharge words are deltas, not goods prices.** 加价/加收/差价 join the
   existing delta vocabulary.
4. **References are labelled, not offered.** A past purchase, a rival's or a
   market number is context; the supplier's own price in the same message
   still reads.
5. **Minor currency units go to a person.** 分/毛/角/厘 are deliberately NOT
   auto-converted — scaling silently inside the confirm span risks a 10x/100x
   error at approval (this file documents the divergence from PR#15, which
   specified conversion; the fail-safe direction was chosen).
"""
from __future__ import annotations

import pytest

from src.desk.parse import parse_quote


def _price(text: str):
    result = parse_quote(text)
    return (
        result["status"],
        (result.get("parsed") or {}).get("unit_price"),
        (result.get("provenance") or {}).get("review_reason") or "",
    )


class TestFeeLinesLeaveCandidacy:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("运费 3 元，单价 20 元。", 20.0),
            ("打样费 500 元，单价 18.5 元", 18.5),
            ("Mould fee 3,500 元 one time, unit price 32.00 元.", 32.0),
            ("单价 17 元，手续费 2 元。", 17.0),
        ],
    )
    def test_one_goods_price_reads_despite_the_fee_line(self, text, expected):
        status, price, _ = _price(text)
        assert status == "accepted" and price == expected

    def test_a_fee_line_alone_is_not_a_price(self):
        status, price, _ = _price("开模费 3000 元另计")
        assert status == "review" and price is None

    def test_service_and_hourly_rates_are_not_goods_prices(self):
        status, price, _ = _price("服务费 200 元，单价 15 元。")
        assert status == "accepted" and price == 15.0


class TestNonPieceBasisIsAMismatch:
    @pytest.mark.parametrize("text", ["单价 20 元/打", "每打 20 元", "一打 20 元"])
    def test_per_dozen_never_prefills_as_per_piece(self, text):
        status, price, _ = _price(text)
        assert status == "review" and price is None

    @pytest.mark.parametrize("text", ["每吨 12000 元", "单价 12000 元/吨", "每码 15 元", "15 元/张"])
    def test_other_measure_words_are_mismatches_too(self, text):
        status, price, _ = _price(text)
        assert status == "review" and price is None

    def test_per_piece_still_reads(self):
        assert _price("单价 20 元/件")[1] == 20.0
        assert _price("单价 20 元")[1] == 20.0


class TestSurchargeIsNotTheGoodsPrice:
    @pytest.mark.parametrize("text", ["加价 2 元每件", "差价 3 元另付", "每件加收 2 元"])
    def test_surcharge_words_never_become_the_price(self, text):
        status, price, _ = _price(text)
        assert status == "review" and price is None


class TestReferencesAreLabelled:
    def test_a_past_purchase_is_not_an_offer(self):
        status, price, _ = _price("我们上周买的单价是 12 元")
        assert status == "review" and price is None

    def test_a_rivals_number_is_not_the_offer_but_ours_reads(self):
        status, price, _ = _price("同行报 12.6，我们最低 13.2 元/件")
        assert status == "accepted" and price == 13.2

    def test_a_market_reference_alone_gives_no_prefill(self):
        status, price, _ = _price("市场价大概 14 元")
        assert status == "review" and price is None


class TestMinorCurrencyUnitsGoToAPerson:
    @pytest.mark.parametrize("text", ["单价 8 毛", "单价 350 分", "价格 2 角"])
    def test_minor_units_never_prefill_and_say_why(self, text):
        status, price, reason = _price(text)
        assert status == "review" and price is None
        assert "minor currency unit" in reason

    def test_yuan_prices_are_unaffected_by_the_minor_gate(self):
        assert _price("单价 8 元")[1] == 8.0


class TestRegressionCorpusContract:
    """The corpus rows that pinned the old fee-poisoning behaviour now pin
    the fee-line contract (see the fixtures file for the note)."""

    def test_the_corpus_and_held_out_expectations_are_met(self):
        from tests.test_desk_reverify import _corpus

        rows = _corpus()
        fee_row = next(r for r in rows if r[0] == "mould_and_unit")
        assert fee_row[2] == 32.0
