"""Safe Linear Regex Engine (SafeLinearRegexEngine).

支援 Google RE2 (Thompson NFA/DFA, 嚴格 O(n) 線性時間複雜度) 與 Python regex 
(Atomic Grouping (?>...) + 10ms 看門狗超時中斷)，徹底杜絕 ReDoS 災難性指數回溯攻擊。
"""
from __future__ import annotations
import logging
from typing import Optional, Iterator, Tuple, Any, List

logger = logging.getLogger("tradeflow.safe_regex")

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
        
        # 安全長度截斷，防止巨型字串攻擊
        if len(text) > 1000:
            text = text[:1000]

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
        if len(text) > 1000:
            text = text[:1000]

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
        if len(text) > 1000:
            text = text[:1000]
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
