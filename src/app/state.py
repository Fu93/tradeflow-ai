"""Shared process state for the FastAPI app layer.

[ARCHITECTURE 2026-10-24] Extracted from the 3,004-line ``main.py`` so the
route modules can import the session manager, the legacy-engine gate and the
shared helpers without importing the assembly module back — the same
cycle-cutting move ``security.py`` made earlier. Nothing here imports
``src.app.main``; ``main.py`` imports this module and re-exports these names,
so every existing import path keeps working.
"""
from __future__ import annotations
import logging
import os
from collections import OrderedDict
from typing import Any, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..engine.backend import resolve_use_gpu
from ..session.manager import SessionManager

# V1 engines stay unloaded unless this process is asked to mount them.
_LEGACY_ON = os.environ.get("TRADEFLOW_LEGACY", "0") == "1"
if _LEGACY_ON:
    from src.engine.one_step import one_step_counter
    from src.engine.bayesian_v2 import robust_location
    from src.engine.hazard import HazardBelief
    from ..engine import (
        compute_belief_confidence,
        model,
        proposal,
        robust_bayes_update,
        BayesianBeliefState,
        TCOEngine,
    )
else:
    # [ARCHITECTURE] The v1 engines are deliberately absent on a default boot:
    # every name above exists so imports keep resolving, and the routes that
    # would call them are legacy-gated off. The type-ignores document that
    # runtime decision; they are not a request to weaken the check elsewhere.
    import types

    one_step_counter = None  # type: ignore[assignment]
    robust_location = None  # type: ignore[assignment]
    HazardBelief = None  # type: ignore[assignment,misc]
    compute_belief_confidence = None  # type: ignore[assignment]
    proposal = None  # type: ignore[assignment]
    robust_bayes_update = None  # type: ignore[assignment]
    BayesianBeliefState = None  # type: ignore[assignment,misc]
    TCOEngine = None  # type: ignore[assignment,misc]
    model = types.SimpleNamespace(ROUNDS=int("5"))  # type: ignore[assignment]

logger = logging.getLogger("tradeflow.app")


def _sanitize_non_finite(obj: Any) -> Any:
    """遞迴把非有限浮點換成字串，讓回應永遠是合法 JSON。

    [REGRESSION] 當呼叫端送出非標準的 `Infinity`，Pydantic 會把它放進 422 的
    error detail（`input` 欄位）。FastAPI 的預設 JSONResponse 使用
    `json.dumps(allow_nan=False)`，於是「拒絕請求」這件事本身會再拋
    ValueError -> 又是 500。把非有限值降級為字串即可，也讓錯誤訊息可讀。
    """
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return "Infinity" if obj > 0 else ("-Infinity" if obj < 0 else "NaN")
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_non_finite(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    """JSON 回應：允許非有限值被安全降級，永不因 NaN/Infinity 而 500。"""

    def render(self, content: Any) -> bytes:
        import json as _json
        return _json.dumps(
            _sanitize_non_finite(content),
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


# ---------------------------------------------------------------------------
# [PERF] /round 求解結果快取（有界 LRU）
# 同一會話每輪 /round 都以相同輸入重解 20,000 樣本（≈1.1-1.7s）。solve() 為
# 確定性函數（固定 seed 20261013，見 src/engine/solver.py），故以「完整輸入」
# 為鍵的快取不會回傳過期建議：任何輸入（樣本數、後驗均值/標準差、GPU 旗標）
# 不同必不命中。僅快取 solve() 的昂貴輸出；recommend() 只讀不改（已核實）。
_SOLVE_CACHE_MAX = 128
_SOLVE_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()


def _solve_round_cached(n_opp_samples: int, use_gpu: bool | None, reserve_mean: float, reserve_std: float):
    resolved = resolve_use_gpu(use_gpu)
    key = (int(n_opp_samples), bool(resolved), float(reserve_mean), float(reserve_std))
    cached = _SOLVE_CACHE.get(key)
    if cached is not None:
        _SOLVE_CACHE.move_to_end(key)
        return cached
    # [RETIRED 2026-10-23] The lines that followed this raise in the original
    # main.py referenced an undefined ``result`` and could never execute;
    # ruff F821 now catches what was dead on arrival. The raise is the whole
    # behaviour, and the raise is what the tests assert.
    raise RuntimeError(
        "The legacy round cache is retired. /round maximises expected surplus of the one-step accept-or-reject model."
    )



# 全域會話管理器實例
session_mgr = SessionManager()



def _legacy_route(register):
    """Register a v1 route only when TRADEFLOW_LEGACY=1."""
    if _LEGACY_ON:
        return register

    def _skip(*_args, **_kwargs):
        def _keep(fn):
            return fn

        return _keep

    return _skip


def _validate_session_id_path(session_id: str) -> str:
    """URL 路徑中的 session_id 也必須符合白名單。

    [REGRESSION] session_id 由呼叫端自由提供，且 manager._atomic_write_json 會把
    它直接拼進 `store_dir/{session_id}.json`。`../../models/vocab` 會逃出
    store_dir；`C:/.../pwn` 甚至會讓 os.path.join 丟棄 store_dir，再用 os.replace
    原子覆寫該路徑。路徑參數不經過 SessionSpec 驗證，所以在這裡補上。
    """
    from ..session.schema import SAFE_SESSION_ID_RE

    if not isinstance(session_id, str) or not SAFE_SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid session_id in path: must match ^[A-Za-z0-9._-]{1,128}$ "
                "(path separators, drive prefixes and '..' are not allowed)."
            ),
        )
    return session_id


# ---- 假資料防護：模具費必須來自對方原文，不得由程式碼虛構 ----
def _extract_mold_fee(text: str) -> Optional[float]:
    """由對方原文抽取其實際報出的模具費；抽不到回 None。

    [假資料修正 §5.5] 舊版硬編 `mold_fee=3000.0`，系統在毫不知情對方開價的
    情況下就憑空發明一筆「¥3000 模具費」並向對方宣稱全額減免。
    現改為只用對方實際上提到的數字（含「模具費另計」但無數字 → 視為未報價，
    回 None），寧可沒有金額也不虛構。
    """
    if not text:
        return None
    from ..engine.bilingual import InboundIntentClassifier as _Clf
    try:
        cls_res = _Clf.classify_stage1_only(text)
    except Exception:
        return None
    mf = getattr(cls_res, "demanded_mold_fee", None)
    if mf is None:
        return None
    try:
        return float(mf) if float(mf) > 0 else None
    except (TypeError, ValueError):
        return None


def _latest_recommended_price(sess) -> Optional[float]:
    """取得最近一輪引擎建議價（copilot 模式下唯一可靠的「核准對象」）。

    `/round` 在 copilot 分支會把 `recommended_offer_price` 寫入該輪對手
    turn 的 metadata（見 process_negotiation_round 的 annotate 呼叫）。
    這裡只讀取已持久化的真實值；任何缺值/非數值/非有限值一律回 None，
    由呼叫端決定 fallback，絕不自行推算。
    """
    if sess is None:
        return None
    for t in reversed(getattr(sess, "history", []) or []):
        meta = getattr(t, "metadata", None) or {}
        if not isinstance(meta, dict):
            continue
        raw = meta.get("recommended_offer_price")
        if raw is None or isinstance(raw, bool):
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val != val or val in (float("inf"), float("-inf")) or val <= 0:
            continue
        return val
    return None


def _reserve_not_identified(sess) -> bool:
    """True when the last round said one ask is not a reserve."""
    for turn in reversed(getattr(sess, "history", []) or []):
        meta = getattr(turn, "metadata", None) or {}
        if isinstance(meta, dict) and meta.get("reserve_source") == "ask_only":
            return True
    return False



