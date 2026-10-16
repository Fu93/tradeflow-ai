"""Safe Linear Regex Engine (SafeLinearRegexEngine).

三層後端，依可用性自動選擇：
  1. Google RE2（Thompson NFA/DFA，嚴格 O(n)）—— 若已安裝 `re2`。
  2. Python `regex` 模組 + 10ms 硬超時守衛 —— 若已安裝 `regex`。
  3. 內建 `re` 兜底。

[重要邊界] Tier 3（內建 re）**沒有**超時守衛，且若未安裝 re2/regex 則完全
沒有 O(n) 保證。真正的第一道防線是 `search()/finditer()/findall()` 進入點的
輸入截斷（MAX_INPUT_LEN = 1000 字元），它把潛在回溯的搜尋空間限制在上限內。
對外聲稱時應說「輸入截斷 + RE2/超時守衛」，不可宣稱任何輸入長度下皆為線性。

[獨立安全審計修正 — 見 DEEP_DEFECT_AUDIT.md §附錄]
審計實測顯示本模組的 ReDoS 保護是**環境相依**的：
  * `requirements.txt` 原本既未列 `re2` 也未列 `regex`。開發機剛好兩者都有
    （site-packages），因此「看似有效」；但嚴格依 requirements 安裝的 CI 與
    Docker 映像會靜默落到 Tier 3。
  * 強制關閉前兩層後量測 `(a+)+b` 對 `a`*n+`c` 的災難性回溯：
    n=16 3.2ms、18 13.6ms、20 59ms、22 251ms、24 1000ms；n=1000（**在
    MAX_INPUT_LEN 之內**）則永久不返回，行程被殺於 120s。
  * 因此「輸入截斷即可保護 Tier 3」的假設只在**非指數**模式下成立；
    截斷把上限從「無限」降為「1000 字元的搜尋空間」，但指數模式在該空間內
    依然爆炸。

本次修正：
  1. 降級到 Tier 3 時**明確記錄警告**（原本 Tier 1/Tier 2 的失敗被靜默吞掉，
     安全等級可以無聲滑落）。
  2. 新增 `backend_is_guarded` 屬性與 `assert_guard_or_warn()`，讓呼叫端能
     判斷當前後端是否**真的**具備 O(n) 或超時保證，而不必相信 docstring。
  3. `requirements.txt` 已加入 `regex`（Tier 2 超時守衛），使乾淨安裝即有保護。
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
            except Exception as exc:
                # 包含不支援的語法 (如 lookaround)。不再靜默：降級是有安全性
                # 後果的事件（見模組 docstring），必須留痕。
                logger.warning(
                    "[safe_regex] RE2 could not compile pattern %r (%s); "
                    "falling through to a weaker backend.",
                    pattern[:40], exc,
                )

        # 嘗試 Tier 2: Python regex + 10ms 硬超時守衛
        if HAS_REGEX:
            try:
                regex_flags = regex.V1
                if ignore_case:
                    regex_flags |= regex.IGNORECASE
                self._compiled = regex.compile(pattern, regex_flags)
                self.backend = "regex"
                return
            except Exception as exc:
                logger.warning(
                    "[safe_regex] `regex` could not compile pattern %r (%s); "
                    "falling through to stdlib re WITHOUT a timeout guard.",
                    pattern[:40], exc,
                )

        # 嘗試 Tier 3: 內建 re 兜底
        re_flags = re.IGNORECASE if ignore_case else 0
        self._compiled = re.compile(pattern, re_flags)
        self.backend = "standard_re"
        if self._looks_backtracking_prone(pattern):
            # 明確警告：此模式下沒有超時守衛，且此模式看似含可嵌套量詞。
            logger.warning(
                "[safe_regex] Pattern %r compiled on stdlib `re` (Tier 3): no "
                "timeout guard and input is only bounded by MAX_INPUT_LEN=%d. "
                "This pattern appears to contain nested quantifiers and may "
                "backtrack catastrophically even within that bound. "
                "Install `regex` (Tier 2) or `re2` (Tier 1) for real protection.",
                pattern[:40], MAX_INPUT_LEN,
            )

    # ------------------------------------------------------------------
    # 後端保證查詢 (讓呼叫端不必相信 docstring)
    # ------------------------------------------------------------------
    @property
    def backend_is_guarded(self) -> bool:
        """當前後端是否具備真正的 O(n) 或超時保證。

        True 僅限 Tier 1 (`re2`) 與 Tier 2 (`regex` + timeout)。
        Tier 3 (`standard_re`) 回 False —— 它只有 MAX_INPUT_LEN 截斷。
        """
        return self.backend in ("re2", "regex")

    @staticmethod
    def _looks_backtracking_prone(pattern: str) -> bool:
        """粗略啟發式：偵測可能導致災難性回溯的嵌套量詞 (如 `(a+)+`)。

        這**不是**完備的判定，也刻意保守：命中即警告，不拒絕編譯。
        目的是讓 Tier 3 的風險在日誌中可見，而非宣稱能靜態證明安全性。
        """
        try:
            # 群組內含量詞，且該群組本身被量詞修飾 -> 可能是嵌套量詞。
            return re.search(r"\([^)]*[+*][^)]*\)\s*[+*{]", pattern) is not None
        except re.error:
            return False

    def assert_guard_or_warn(self, context: str = "") -> bool:
        """在缺少超時/O(n) 保證時留下高可見度警告，回傳是否受保護。"""
        if not self.backend_is_guarded:
            logger.warning(
                "[safe_regex] UNGUARDED backend '%s' in use%s for pattern %r. "
                "Only MAX_INPUT_LEN=%d bounds the search space.",
                self.backend,
                f" ({context})" if context else "",
                self.raw_pattern[:40],
                MAX_INPUT_LEN,
            )
        return self.backend_is_guarded

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
