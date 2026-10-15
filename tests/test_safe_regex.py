"""SafeRegex 截斷語意回歸測試。

[為什麼需要這個測試]
`TARGET_ARCHITECTURE_AND_ROADMAP.md` 曾聲稱「30,000 字元 ReDoS 壓力測試單句
匹配時延 0.16ms」。該數字在結構上不可能成立：`SafeLinearPattern` 的所有進入點
都會先把輸入截斷至 `MAX_INPUT_LEN`（1,000 字元），因此 30,000 字元從未被完整
掃描。實測 4 字元與 30,000 字元的耗時比為 ~0.8x，而非線性掃描應有的 ~7500x。

本測試把截斷行為與「長輸入不得比短輸入顯著更慢」這兩個性質固定下來，
避免日後有人移除截斷、卻繼續沿用同一個對外宣稱。
"""
from __future__ import annotations
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.engine.safe_regex import SafeLinearRegexEngine, SafeLinearPattern, MAX_INPUT_LEN


def test_max_input_len_is_documented_constant():
    assert MAX_INPUT_LEN == 1000, (
        "MAX_INPUT_LEN changed; re-verify the ReDoS wording in "
        "TARGET_ARCHITECTURE_AND_ROADMAP.md before updating this test."
    )


def test_search_truncates_beyond_max_input_len():
    """超過上限的輸入必須被截斷：落在 1000 字元之後的匹配不可被找到。"""
    p = SafeLinearRegexEngine.compile("ZZZUNIQUE")
    padded = "x" * (MAX_INPUT_LEN + 500) + "ZZZUNIQUE"
    assert p.search(padded) is None, (
        "pattern beyond MAX_INPUT_LEN was matched, so input is NOT truncated"
    )


def test_match_within_limit_is_found():
    """上限之內的匹配必須正常命中（確認上一個測試不是因為 pattern 壞掉）。"""
    p = SafeLinearRegexEngine.compile("ZZZUNIQUE")
    assert p.search("ZZZUNIQUE" + "x" * 10) is not None


def test_long_input_is_not_slower_than_short_input():
    """截斷的直接推論：30,000 字元不得比 4 字元顯著更慢。

    若此測試失敗，代表截斷被移除 —— 那麼「30,000 字元壓力測試」的說法才可能
    成立，而 docs 與本測試都必須同步更新。
    """
    p = SafeLinearRegexEngine.compile("原料漲價")
    short, long_ = "原料漲價", "原料漲價" * 7500  # 4 vs 30,000 chars
    assert len(long_) == 30000

    def timeit(text: str, n: int = 300) -> float:
        t0 = time.perf_counter()
        for _ in range(n):
            p.search(text)
        return (time.perf_counter() - t0) / n

    t_short = timeit(short)
    t_long = timeit(long_)
    # 若真的完整掃描 30,000 字元，比例會是數千倍。允許 50x 的寬鬆上限以吸收噪音。
    assert t_long < t_short * 50 + 1e-4, (
        f"30k-char search ({t_long*1e6:.2f}us) is far slower than 4-char "
        f"({t_short*1e6:.2f}us) -> truncation may have been removed"
    )
