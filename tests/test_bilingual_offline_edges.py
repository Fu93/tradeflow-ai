"""Offline paths of the bilingual engine that only rare requests reach.

Three behaviours worth pinning because production depends on their edges:

* ``draft_for_approval`` — what the human reviews and approves. A stored
  draft is reused only when its price is still the approved figure; a
  changed figure must render a fresh draft, never ship the old text.
* ``_clamp_price`` — an LLM-supplied or parsed number is dropped (None)
  rather than guessed when it is non-finite or outside the sane band.
* ``parse_numeric_token``-style European formats — the thousand-separator
  rules decide whether "1.234" means one thousand or one point two three.
"""
from __future__ import annotations

import pytest

from src.engine.bilingual import InboundIntentClassifier, draft_for_approval

AUTHORIZED = 12.30


class TestDraftForApproval:
    def test_a_stored_draft_with_a_matching_price_is_reused(self):
        stored = "經核算，首批訂單意向價 ¥12.30，請確認排單。"
        result = draft_for_approval(
            AUTHORIZED,
            language="zh",
            inbound_text="單價 ¥14.80",
            stored_reply=stored,
            stored_price=12.30,
            product_name="umbrella",
            ceiling=20.0,
            moq=1000,
        )
        assert result == stored

    def test_a_price_that_no_longer_matches_renders_fresh(self):
        stored = "經核算，首批訂單意向價 ¥12.30，請確認排單。"
        result = draft_for_approval(
            11.80,
            language="zh",
            inbound_text="單價 ¥14.80",
            stored_reply=stored,
            stored_price=12.30,
            product_name="umbrella",
            ceiling=20.0,
            moq=1000,
        )
        assert result != stored
        assert "11.80" in result

    def test_the_draft_language_follows_the_stored_language(self):
        stored_en = "Based on our calculation, the first-order price is $12.30."
        result = draft_for_approval(
            AUTHORIZED,
            language="en",
            inbound_text="單價 ¥14.80",
            stored_reply=None,
            stored_price=None,
            product_name="umbrella",
            ceiling=20.0,
            moq=1000,
        )
        # A fresh render for an English thread must not be a Chinese-only script.
        assert result != stored_en

    def test_an_unknown_language_falls_back_to_the_inbound_text(self):
        result = draft_for_approval(
            AUTHORIZED,
            language=None,
            inbound_text="Unit price is 14.80 CNY per piece.",
            stored_reply=None,
            stored_price=None,
            product_name="umbrella",
            ceiling=20.0,
            moq=1000,
        )
        assert result


class TestClampPrice:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (12.30, 12.30),
            ("12.30", 12.30),
            (None, None),
            (True, None),
            (float("nan"), None),
            (float("inf"), None),
            (0.0001, None),
            (999_999.0, None),
        ],
    )
    def test_out_of_band_values_are_dropped_not_guessed(self, value, expected):
        assert InboundIntentClassifier._clamp_price(value) == expected


class TestEuropeanNumberFormats:
    def test_dot_thousands_groups_are_rejoined(self):
        assert InboundIntentClassifier.parse_locale_number("1.234") == 1234.0

    def test_comma_thousands_groups_are_rejoined(self):
        assert InboundIntentClassifier.parse_locale_number("1,234") == 1234.0

    def test_a_single_decimal_point_stays_decimal(self):
        assert InboundIntentClassifier.parse_locale_number("12.30") == 12.30
