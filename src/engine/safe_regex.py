"""Safe Linear Regex Engine (SafeLinearRegexEngine).

三層後端，依可用性自動選擇：
  1. Google RE2（Thompson NFA/DFA，嚴格 O(n)）—— 若已安裝 `re2`。
  2. Python `regex` 模組 + 10ms 硬超時守衛 —— 若已安裝 `regex`。
  3. 內建 `re` 兜底。

[重要邊界] Tier 3（內建 re）**沒有**超時守衛，且若未安裝 re2/regex 則完全
沒有 O(n) 保證。真正的第一道防線是 `search()/finditer()/findall()` 進入點的
輸入截斷（MAX_INPUT_LEN = 1000 字元），它把潛在回溯的搜尋空間限制在上限內。
對外聲稱時應說「輸入截斷 + RE2/超時守衛」，不可宣稱任何輸入長度下皆為線性。
"""
from __future__ import annotations
import logging
from typing import Optional, Iterator, Tuple, Any, List

logger = logging.getLogger("tradeflow.safe_regex")

# 單一進入點統一使用的輸入上限。截斷是 Tier 3 唯一的保護機制，
# 因此這個常數是安全性的關鍵參數，不應在別處重複定義不同的值。
MAX_INPUT_LEN = 1000

# 1. 動態偵測後端能力
try:
    import re2
    HAS_RE2 = True
except ImportError:
    re2 = None
    HAS_RE2 = False

try:
    import regex
    HAS_REGEX = True
except ImportError:
    regex = None
    HAS_REGEX = False

import re


class SafeMatch:
    """統一抽象匹配物件，對齊 re / re2 / regex 的 API 差異。"""
    __slots__ = ("_match",)

    def __init__(self, match_obj: Any):
        self._match = match_obj

    def group(self, *args) -> Any:
        return self._match.group(*args)

    def groups(self, default: Any = None) -> Tuple[Any, ...]:
        return self._match.groups(default) if hasattr(self._match, "groups") else ()

    def start(self, group: int = 0) -> int:
        return self._match.start(group)

    def end(self, group: int = 0) -> int:
        return self._match.end(group)

    def span(self, group: int = 0) -> Tuple[int, int]:
        return self._match.span(group)

    def __bool__(self) -> bool:
        return self._match is not None


class SafeLinearPattern:
    """具備 O(n) 線性保證或 10ms 硬超時守衛的安全正則編譯物件。"""

    def __init__(
        self,
        pattern: str,
        flags: int = 0,
        timeout_sec: float = 0.010,
    ):
        self.raw_pattern = pattern
        self.flags = flags
        self.timeout_sec = timeout_sec
        self.backend: str = "re"
        self._compiled: Any = None

        ignore_case = bool(flags & (re.IGNORECASE | getattr(regex, "IGNORECASE", 2)))

        # 嘗試 Tier 1: Google RE2 (Thompson NFA/DFA 嚴格 O(n) 線性時間)
        if HAS_RE2:
            try:
                opts = re2.Options()
                opts.log_errors = False
                opts.case_sensitive = not ignore_case
                opts.max_mem = 8 << 20  # 8 MB DFA 狀態緩存
                self._compiled = re2.compile(pattern, opts)
                self.backend = "re2"
                return
            except Exception:
                # 包含不支援的語法 (如 lookaround)，靜默降級至 Tier 2
                pass

        # 嘗試 Tier 2: Python regex + 10ms 硬超時守衛
        if HAS_REGEX:
            try:
                regex_flags = regex.V1
                if ignore_case:
                    regex_flags |= regex.IGNORECASE
                self._compiled = regex.compile(pattern, regex_flags)
                self.backend = "regex"
                return
            except Exception:
                pass

        # 嘗試 Tier 3: 內建 re 兜底
        re_flags = re.IGNORECASE if ignore_case else 0
        self._compiled = re.compile(pattern, re_flags)
        self.backend = "standard_re"

    def search(self, text: str) -> Optional[SafeMatch]:
        if not text:
            return None

        # 安全長度截斷：Tier 3（內建 re）沒有超時守衛，這是它唯一的保護。
        if len(text) > MAX_INPUT_LEN:
            text = text[:MAX_INPUT_LEN]

        if self.backend == "re2":
            m = self._compiled.search(text)
            return SafeMatch(m) if m else None
        elif self.backend == "regex":
            try:
                m = self._compiled.search(text, timeout=self.timeout_sec)
                return SafeMatch(m) if m else None
            except TimeoutError:
                logger.warning(f"[ReDoS Blocked] Pattern '{self.raw_pattern[:30]}' timed out after {self.timeout_sec}s")
                return None
        else:
            m = self._compiled.search(text)
            return SafeMatch(m) if m else None

    def finditer(self, text: str) -> Iterator[SafeMatch]:
        if not text:
            return
        if len(text) > MAX_INPUT_LEN:
            text = text[:MAX_INPUT_LEN]

        if self.backend == "re2":
            for m in self._compiled.finditer(text):
                yield SafeMatch(m)
        elif self.backend == "regex":
            try:
                for m in self._compiled.finditer(text, timeout=self.timeout_sec):
                    yield SafeMatch(m)
            except TimeoutError:
                logger.warning("[ReDoS Blocked] Pattern finditer timed out")
                return
        else:
            for m in self._compiled.finditer(text):
                yield SafeMatch(m)

    def findall(self, text: str) -> List[Any]:
        if not text:
            return []
        if len(text) > MAX_INPUT_LEN:
            text = text[:MAX_INPUT_LEN]
        if self.backend == "re2":
            return self._compiled.findall(text)
        elif self.backend == "regex":
            try:
                return self._compiled.findall(text, timeout=self.timeout_sec)
            except TimeoutError:
                return []
        else:
            return self._compiled.findall(text)


class SafeLinearRegexEngine:
    """線性正則工廠類別。"""
    @classmethod
    def compile(cls, pattern: str, flags: int = 0, timeout_ms: float = 10.0) -> SafeLinearPattern:
        return SafeLinearPattern(pattern, flags=flags, timeout_sec=timeout_ms / 1000.0)
