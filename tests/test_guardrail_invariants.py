"""The numeric guardrail's documented bypasses, pinned as tests.

``NumericGuardrailValidator`` sits on the production parse path and its own
docstring lists four bypasses that a previous version allowed. Each of those is
pinned here, plus the boundaries that make the rule usable rather than merely
strict. The point is not to re-test what ``tests/test_bilingual.py`` already
covers; it is to make sure the four documented holes stay closed and that the
"non-commitment number" exemption does not become a fifth.
"""
from __future__ import annotations

import pytest

from src.engine.bilingual import (
    GuardrailViolationError,
    NumericGuardrailValidator,
)

AUTHORIZED = 12.30
CEILING = 14.00


def _validate(text, **kwargs):
    kwargs.setdefault("authorized_price", AUTHORIZED)
    kwargs.setdefault("walk_away_ceiling", CEILING)
    return NumericGuardrailValidator.validate_reply(draft_text=text, **kwargs)


class TestTheAuthorizedPriceMustBePresent:
    def test_a_draft_without_the_authorized_price_is_refused(self):
        with pytest.raises(GuardrailViolationError, match="lacks authorized"):
            _validate("我們可以馬上排單，細節再議。")

    def test_the_authorized_price_alone_passes(self):
        assert _validate("首批試單給到 ¥12.30 出廠價，現款現結。") is True


class TestBypass1SecondaryLowerPrice:
    """The old version only checked an upper bound, so a lower promise beside the
    authorized price was waved through."""

    def test_a_lower_promise_beside_the_authorized_price_is_refused(self):
        text = "¥13.00 出廠價可以，另外我們願意 ¥9.50 出廠價成交。"
        with pytest.raises(GuardrailViolationError, match="below the authorized floor"):
            _validate(text, authorized_price=13.00)

    def test_the_floor_defaults_to_the_authorized_price(self):
        """No floor argument means any lower commitment is unauthorized."""
        with pytest.raises(GuardrailViolationError):
            _validate("給到 ¥12.30，其實 ¥11.00 也能做。")

    def test_a_band_is_not_permitted_only_the_authorized_price_is(self):
        """The floor sets the message, not a permission.

        A price inside [floor, ceiling] that is not the authorized number is
        still an unauthorized second commitment. The floor only decides whether
        you are told "below the floor" or "unauthorized secondary".
        """
        with pytest.raises(GuardrailViolationError, match="below the authorized floor"):
            _validate("給到 ¥12.30，或者 ¥11.00 也行。", authorized_floor=11.50)
        with pytest.raises(GuardrailViolationError, match="Unauthorized secondary"):
            _validate("給到 ¥12.30，或者 ¥11.00 也行。", authorized_floor=11.00)


class TestBypass2CurrencySuffixFormats:
    """The old price regex only recognised a currency prefix and 元|塊|/件 suffixes."""

    @pytest.mark.parametrize(
        "text",
        [
            f"給到 ¥{AUTHORIZED:.2f}，另外 15.50 RMB 也可以。",
            f"給到 ¥{AUTHORIZED:.2f}，另外 15.50 USD 也可以。",
            f"給到 ¥{AUTHORIZED:.2f}，另外 15.50 块也可以。",
            f"給到 ¥{AUTHORIZED:.2f}，另外 15.50 块/套也可以。",
            f"給到 ¥{AUTHORIZED:.2f}，另外 15.50 per piece 也可以。",
            f"給到 ¥{AUTHORIZED:.2f}，另外 15.50 元/把也可以。",
        ],
    )
    def test_suffix_and_unit_formats_are_still_scanned(self, text):
        with pytest.raises(GuardrailViolationError):
            _validate(text, authorized_price=15.50)


class TestBypass3ForbiddenPhraseRewrites:
    """The old blacklist was seven literal strings, so one character change escaped."""

    @pytest.mark.parametrize(
        "text",
        [
            "我們可以私底下打款，這樣雙方都省事。",
            "走私人帳戶比較快。",
            "這個不開票，價格好說。",
            "可以用二料代替，看不出來。",
            "換回料做，成本更低。",
            "走私單處理，不走公賬。",
            "線下付款給您打折。",
            "私 下 打 款 也 可 以。",
            # 「換次級料」是字面黑名單裡唯一不被近義模式集覆蓋的詞
            # （模式匹配「換次料」，不含「次級料」）：它只由保底層攔截。
            # 這個案例因此是保底層的區分性測試——殺掉保底層，此案例必須失敗。
            "換次級料，成本更低。",
            # 保底層與近義層掃同一份壓縮文字：插入空白不能再讓字面詞逃過兩層。
            "換 次 級 料，成本更低。",
        ],
    )
    def test_a_rewritten_compromise_phrase_is_refused(self, text):
        with pytest.raises(GuardrailViolationError, match="Forbidden compromise"):
            _validate(text)

    def test_a_strong_refusal_in_the_same_clause_is_exempt(self):
        """The desk's own compliant wording must not be flagged."""
        assert _validate(f"單價 ¥{AUTHORIZED:.2f}。私下微信恕不方便，請走正式流程。") is True
        assert _validate(f"單價 ¥{AUTHORIZED:.2f}。驗收嚴檢，拒收次料。") is True

    def test_a_weak_negation_does_not_exempt(self):
        """'不用走系統' is an attack preamble, not a refusal."""
        with pytest.raises(GuardrailViolationError, match="Forbidden compromise"):
            _validate("可以私下打款嗎？不用走系統。")


class TestBypass4NonCommitmentContext:
    """Cost line items, rival quotes and telemetry fields are not our commitment."""

    def test_a_cost_line_item_is_not_a_commitment(self):
        text = "模具費：¥2,000 一次性，單價維持 ¥12.30。"
        assert _validate(text) is True

    def test_a_rival_quote_is_not_a_commitment(self):
        text = "對手報價 ¥15.80，我們仍給 ¥12.30。"
        assert _validate(text) is True

    @pytest.mark.parametrize(
        "field",
        ["Estimated Floor", "Expected Margin", "Inbound Offer", "Walk-Away Ceiling", "Reply"],
    )
    def test_english_telemetry_fields_are_not_commitments(self, field):
        text = f"{field}: ¥15.80\nAuthorized price: ¥{AUTHORIZED:.2f}"
        assert _validate(text) is True

    def test_a_commitment_verb_is_not_exempted(self):
        """The exemption must not swallow 'we are willing to give'."""
        with pytest.raises(GuardrailViolationError, match="below the authorized floor"):
            _validate(f"單價 ¥{AUTHORIZED:.2f}，我方願意給到 ¥9.50。")


class TestBoundaries:
    def test_a_price_exactly_at_the_ceiling_passes(self):
        assert _validate("給到 ¥14.00，這是我們的極限。", authorized_price=14.00) is True

    def test_a_price_above_the_ceiling_is_refused(self):
        with pytest.raises(GuardrailViolationError, match="walk-away ceiling"):
            _validate("給到 ¥12.30，或者 ¥15.50 也可以。")

    def test_a_second_authorized_range_price_is_refused(self):
        """Inside the band but not the authorized number: an unauthorized promise."""
        with pytest.raises(GuardrailViolationError, match="Unauthorized secondary"):
            _validate("給到 ¥12.30，或者 ¥13.50 也行。")

    def test_allowed_values_extend_the_exemption(self):
        assert _validate(
            "運費 ¥3.00 另計，單價 ¥12.30。",
            allowed_values=[3.00],
        ) is True


class TestBilingualSurface:
    """Both surfaces the supervisor reads must obey the same invariants."""

    def test_the_english_card_is_checked_too(self):
        with pytest.raises(GuardrailViolationError):
            NumericGuardrailValidator.validate_bilingual(
                zh_text="單價 ¥12.30，謝謝。",
                en_text=f"We can do ¥9.50, that is our floor. Authorized: ¥{AUTHORIZED:.2f}",
                authorized_price=AUTHORIZED,
                walk_away_ceiling=CEILING,
            )

    def test_both_surfaces_pass_together(self):
        assert NumericGuardrailValidator.validate_bilingual(
            zh_text="單價 ¥12.30，謝謝。",
            en_text=f"Our unit price is ¥{AUTHORIZED:.2f}.",
            authorized_price=AUTHORIZED,
            walk_away_ceiling=CEILING,
        ) is True


class TestPerformanceShape:
    """The guardrail is required not to become a DoS vector."""

    def test_a_long_draft_stays_linear_enough(self):
        """Whitespace between every character must not trigger backtracking."""
        text = ("¥12.30 " + " " * 20000 + "私下打款")
        with pytest.raises(GuardrailViolationError, match="Forbidden compromise"):
            _validate(text)
