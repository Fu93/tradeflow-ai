"""Quoted amounts keep their currency. USD is not silently treated as CNY."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.engine.bilingual import InboundIntentClassifier
from src.engine.quote_fields import extract_stage1
from src.engine.quote_read import read_supplier_quote


class TestQuotedCurrency(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("TRADEFLOW_USD_CNY_RATE", None)

    def _quote(self, text: str):
        return InboundIntentClassifier.classify_stage1_only(text)

    def test_required_strings_do_not_become_cny(self):
        samples = (
            "3.20 USD/pc",
            "Price: 3.20 USD/pc",
            "unit price 3.2 usd",
            "USD 3.20",
        )
        for text in samples:
            with self.subTest(text=text):
                found = self._quote(text)
                self.assertEqual(found.mentioned_currency, "USD")
                self.assertIsNone(found.mentioned_price)
                self.assertAlmostEqual(found.quoted_amount, 3.2)
                self.assertIn("TRADEFLOW_USD_CNY_RATE", found.price_currency_note or "")
                self.assertNotEqual(found.mentioned_price, found.quoted_amount)

    def test_rmb_per_piece_stays_cny(self):
        found = self._quote("15.60 RMB per piece")
        self.assertEqual(found.mentioned_currency, "CNY")
        self.assertAlmostEqual(found.mentioned_price, 15.60)
        self.assertAlmostEqual(found.quoted_amount, 15.60)
        self.assertIsNone(found.price_currency_note)

    def test_chinese_yuan_quote_still_extracts(self):
        found = self._quote("最低批發價 14.80 元/把，500個起訂")
        self.assertEqual(found.mentioned_currency, "CNY")
        self.assertAlmostEqual(found.mentioned_price, 14.80)

    def test_english_price_without_currency_is_not_cny(self):
        found = self._quote("Price: 3.20")
        self.assertIsNone(found.mentioned_currency)
        self.assertIsNone(found.mentioned_price)
        self.assertAlmostEqual(found.quoted_amount, 3.20)
        self.assertIn("No currency was stated", found.price_currency_note or "")

    def test_other_currencies_are_never_converted(self):
        with patch.dict(os.environ, {"TRADEFLOW_USD_CNY_RATE": "7.1"}):
            found = self._quote("15.60 EUR per piece")
        self.assertEqual(found.mentioned_currency, "EUR")
        self.assertIsNone(found.mentioned_price)
        self.assertAlmostEqual(found.quoted_amount, 15.60)
        self.assertIn("not converted", found.price_currency_note or "")

    def test_usd_converts_only_with_the_env_rate(self):
        with patch.dict(os.environ, {"TRADEFLOW_USD_CNY_RATE": "7.1"}):
            found = self._quote("3.20 USD/pc")
        self.assertEqual(found.mentioned_currency, "USD")
        self.assertAlmostEqual(found.quoted_amount, 3.20)
        self.assertAlmostEqual(found.mentioned_price, 3.20 * 7.1)
        note = found.price_currency_note or ""
        self.assertIn("TRADEFLOW_USD_CNY_RATE", note)
        self.assertIn("environment", note)

    def test_offline_reader_flags_usd(self):
        result = read_supplier_quote("3.20 USD/pc", amd_live=False)
        fields = result["fields"]
        self.assertIsNone(fields["price"])
        self.assertEqual(fields["currency"], "USD")
        self.assertAlmostEqual(fields["quoted_amount"], 3.20)
        self.assertIsNone(result["buyer_price"])

    def test_stage1_keeps_sample_cny_prices(self):
        fields = extract_stage1("单价：14.80元。5000個。21天。30%定金。")
        self.assertAlmostEqual(fields["price"], 14.80)
        self.assertEqual(fields["currency"], "CNY")


if __name__ == "__main__":
    unittest.main()
