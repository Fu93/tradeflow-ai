"""未受指派檔案的修正回歸測試 (tests/test_lead_medium_fixes.py).

涵蓋 Lead 直接處理、且不屬於任何隊友 write scope 的項目：

1. `src/engine/safe_regex.py` 的後端保證可查詢化（見 DEEP_DEFECT_AUDIT.md 附錄）：
   降級到 Tier 3（內建 re）時不再靜默；`backend_is_guarded` 必須誠實回報當前
   後端是否真的具備 O(n) 或超時保證。
   修正前：Tier 1/Tier 2 的 `except Exception: pass` 沒有任何痕跡，
   且無法從外部得知目前是否受保護。

2. `src/transport/base.py::extract_offer` 的輸入上限：
   該模組直接使用內建 `re`，未截斷的來信文本使模式呈二次成長
   （獨立審計實測 64,016 字元 -> 42,977ms）。修正後必須對超長輸入有界。

3. `src/cli.py::run_benchmark` 的 stale key 崩潰：
   舊版讀 `report['roofline_specs']['theoretical_max_speedup']`，而
   `src/engine/benchmark.py` 已不再輸出該鍵 -> `python -m src.cli --benchmark`
   必定 KeyError、exit 1（已實測）。
"""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.safe_regex import (
    MAX_INPUT_LEN,
    SafeLinearPattern,
    SafeLinearRegexEngine,
)
from src.transport.base import MAX_TEXT_LEN, BaseTransport


class TestSafeRegexBackendHonesty(unittest.TestCase):
    """後端保證必須可查詢，且降級不得靜默。"""

    def test_backend_is_guarded_property_exists_and_is_bool(self):
        p = SafeLinearRegexEngine.compile("ABC")
        self.assertIsInstance(p.backend_is_guarded, bool)

    def test_tier3_reports_unguarded(self):
        """強制 Tier 3 後，backend_is_guarded 必須為 False（誠實）。"""
        p = SafeLinearPattern("ABC")
        # 直接模擬降級到內建 re，等同於未安裝 re2/regex 的乾淨安裝情境。
        import re as _re
        p._compiled = _re.compile("ABC")
        p.backend = "standard_re"
        self.assertFalse(
            p.backend_is_guarded,
            "Tier 3 (stdlib re) 沒有超時守衛，必須回報為未受保護",
        )

    def test_tier1_or_tier2_reports_guarded(self):
        """只要宿主具備 re2 或 regex，就必須回報為受保護。"""
        p = SafeLinearRegexEngine.compile("ABC")
        if p.backend in ("re2", "regex"):
            self.assertTrue(p.backend_is_guarded)
        else:
            self.assertFalse(p.backend_is_guarded)

    def test_assert_guard_or_warn_returns_flag(self):
        p = SafeLinearRegexEngine.compile("ABC")
        result = p.assert_guard_or_warn("unit-test")
        self.assertEqual(result, p.backend_is_guarded)

    def test_degrades_loudly_when_backends_unavailable(self):
        """Tier 1/Tier 2 不可用時，降級必須留下 warning（不得靜默）。"""
        import src.engine.safe_regex as sr

        saved_re2, saved_regex = sr.HAS_RE2, sr.HAS_REGEX
        try:
            sr.HAS_RE2 = False
            sr.HAS_REGEX = False
            with self.assertLogs("tradeflow.safe_regex", level="WARNING") as cm:
                p = SafeLinearPattern("(a+)+b")
            self.assertEqual(p.backend, "standard_re")
            self.assertTrue(
                any("UNGUARDED" in msg or "no timeout guard" in msg or "Tier 3" in msg
                    for msg in cm.output),
                f"降級至 Tier 3 時必須留下可見警告，實際日誌: {cm.output}",
            )
        finally:
            sr.HAS_RE2, sr.HAS_REGEX = saved_re2, saved_regex

    def test_backtracking_heuristic_flags_nested_quantifier(self):
        self.assertTrue(SafeLinearPattern._looks_backtracking_prone(r"(a+)+b"))
        self.assertFalse(SafeLinearPattern._looks_backtracking_prone(r"abc123"))
        self.assertFalse(SafeLinearPattern._looks_backtracking_prone(r"[0-9]+\.[0-9]{1,2}"))

    def test_truncation_still_applied(self):
        """截斷行為不得因本次修正而改變。"""
        p = SafeLinearRegexEngine.compile("ZZZMARKER")
        # 標記落在截斷點之外，因此不應被找到。
        beyond = "x" * (MAX_INPUT_LEN + 500) + "ZZZMARKER"
        self.assertIsNone(p.search(beyond))
        # 標記在截斷點之內，必須被找到。
        within = "ZZZMARKER" + "x" * (MAX_INPUT_LEN + 500)
        self.assertIsNotNone(p.search(within))


class TestTransportExtractOfferBounded(unittest.TestCase):
    """extract_offer 必須對超長輸入有界，且正常抽取不受影響。"""

    def test_normal_extraction_unaffected(self):
        self.assertAlmostEqual(BaseTransport.extract_offer("單價 12.50 元"), 12.50)
        self.assertAlmostEqual(BaseTransport.extract_offer("¥13.80"), 13.80)
        self.assertAlmostEqual(BaseTransport.extract_offer("最低只能給到 13.2"), 13.2)
        self.assertEqual(BaseTransport.extract_offer(""), None)
        self.assertEqual(BaseTransport.extract_offer(None), None)

    def test_long_input_is_bounded(self):
        """超長輸入不得造成二次成長；必須在合理的時間內完成。"""
        # 獨立審計：未截斷時 64,016 字元需 ~43 秒。
        payload = ("原料漲價，成本高 " * 4000) + "單價 12.50 元"  # ~32k+ 字元
        self.assertGreater(len(payload), MAX_TEXT_LEN)
        t0 = time.perf_counter()
        res = BaseTransport.extract_offer(payload)
        dt = time.perf_counter() - t0
        self.assertLess(
            dt, 1.0,
            f"extract_offer 對 {len(payload)} 字元耗時 {dt:.3f}s，未受 MAX_TEXT_LEN 約束",
        )
        # 價格在截斷點之後，因此不會被抽到 —— 這是截斷的既定代價，
        # 但資源有界是必要條件。
        self.assertIsNone(res)

    def test_price_within_prefix_still_extracted(self):
        payload = "單價 12.50 元 " + ("後續雜訊 " * 5000)
        self.assertGreater(len(payload), MAX_TEXT_LEN)
        self.assertAlmostEqual(BaseTransport.extract_offer(payload), 12.50)

    def test_max_text_len_constant_is_sane(self):
        self.assertGreater(MAX_TEXT_LEN, 200)
        self.assertLess(MAX_TEXT_LEN, 10000)


class TestCliBenchmarkNoStaleKey(unittest.TestCase):
    """run_benchmark 不得再讀取 benchmark.run() 未輸出的鍵。

    注意：此類別刻意**不**呼叫 `benchmark.run()` 之外的產出步驟
    （`save_json` / `plot_benchmark` 會改寫 tracked 的 results/bench.json 與
    results/benchmark.png 這兩份已量測證據檔）。因此這裡只對
    `report["roofline_specs"]` 的**鍵集合**做靜態與結構驗證。
    """

    def test_cli_source_does_not_index_removed_key(self):
        """不得再以 subscript 讀取已移除的鍵。

        注意：修復說明註解中會**提及**該鍵名（解釋為何移除），因此這裡檢查的是
        «實際取值存取»（`['theoretical_max_speedup']`），而不是字串存在與否。
        """
        import inspect
        import src.cli as cli
        src = inspect.getsource(cli)
        self.assertNotIn(
            "['theoretical_max_speedup']", src,
            "src/cli.py 仍以 subscript 讀取已移除的 roofline key，--benchmark 會 KeyError",
        )
        self.assertNotIn(
            '["theoretical_max_speedup"]', src,
            "src/cli.py 仍以 subscript 讀取已移除的 roofline key，--benchmark 會 KeyError",
        )

    def test_benchmark_source_key_contract(self):
        """benchmark.run() 的 roofline_specs 必須提供 CLI 依賴的鍵。

        以原始碼層面確認契約，避免為驗證而重跑 profiling 並改寫證據檔。
        """
        import inspect
        import src.engine.benchmark as benchmark
        src = inspect.getsource(benchmark)
        self.assertIn("theoretical roofline lower-bound estimate, not measured", src)
        self.assertNotIn("bandwidth_ratio_cpu_to_mi300x", src)
        self.assertNotIn("cpu_bandwidth_gb_s", src)
        self.assertNotIn("theoretical_max_speedup", src)

    def test_cli_uses_the_present_key(self):
        import inspect
        import src.cli as cli
        src = inspect.getsource(cli)
        self.assertIn("theoretical roofline lower-bound estimate, not measured", src)
        self.assertNotIn("bandwidth_ratio_cpu_to_mi300x", src)
        self.assertNotIn("['theoretical_max_speedup']", src)


if __name__ == "__main__":
    unittest.main()
