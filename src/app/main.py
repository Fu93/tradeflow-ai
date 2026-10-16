"""TradeFlow app — FastAPI 入口 (v4 全自主自駕與遙測擴充)。

職責：
1. 向後相容：保留 `/health`, `/negotiate`, `/webhook/evolus`。
2. 跨域支援 (CORS)：允許 Chrome 擴充套件 (`chrome-extension://`) 與前端直連。
3. 雙向指令：提供 `/webhook/mobile-command` 接收 WhatsApp / 微信 / TG 回調。
4. 會話生命週期：提供 `/api/v1/sessions` 進行狀態機驅動、即時求解與熔斷遙測。
"""
from __future__ import annotations
import hmac
import logging
import os
from enum import Enum
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Request, Depends, APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field, field_validator

# [REGRESSION] .env 從未進入 os.environ：repo 內無任何 load_dotenv()，而且要
# requirements.txt 也沒有 python-dotenv。因此 README 快速開始寫的
# TRADEFLOW_ENV=production / TRADEFLOW_API_KEY / TELEGRAM_BOT_TOKEN 全部無效
# （只有 docker-compose 的 env_file 才會生效），伺服器永遠停留在 sandbox。
# 這裡在讀取任何 env 之前載入 .env；setdefault 語義（override=False）確保
# 真實環境變數與測試 monkeypatch 仍然優先。
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(override=False)
except Exception as _dotenv_err:  # pragma: no cover - 缺套件時不應讓 API 無法啟動
    logging.getLogger("tradeflow.app").warning(
        "python-dotenv unavailable (%s); .env will not be loaded. "
        "Install it via `pip install -r requirements.txt`.", _dotenv_err,
    )

from ..engine import (
    solve,
    recommend,
    bayes_update,
    compute_belief_confidence,
    robustness,
    model,
    proposal,
    robust_bayes_update,
    compute_calibrated_deal_prob,
    BayesianBeliefState,
    TCOEngine,
)
from ..session.schema import (
    SessionSpec,
    NegotiationSession,
    NegotiationTurn,
    AutopilotState,
    RemoteAuthCommand,
)
from ..session.manager import SessionManager, InvalidStateTransitionError, OptimisticLockConflictError
from ..transport.webhook_hub import hub
from ..transport.telemetry import notifier
from . import orchestrator

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


app = FastAPI(
    title="TradeFlow Negotiation Engine",
    version="0.4.0",
    default_response_class=SafeJSONResponse,
)


@app.exception_handler(RequestValidationError)
async def _safe_validation_error_handler(request: Request, exc: RequestValidationError):
    """422 回應必須永遠是合法 JSON。

    [REGRESSION] FastAPI 的預設 RequestValidationError 處理器硬編使用
    `JSONResponse`（不吃 default_response_class），而 Pydantic 的 error detail
    會原樣帶上呼叫端的輸入值。當輸入是 `Infinity`/`NaN` 時，
    `json.dumps(allow_nan=False)` 會拋 ValueError，「拒絕非法輸入」本身又變成
    第二個 500。這裡改用可安全降級非有限值的回應類別。
    """
    from fastapi.encoders import jsonable_encoder

    return SafeJSONResponse(
        status_code=422,
        content={"detail": _sanitize_non_finite(jsonable_encoder(exc.errors()))},
    )

# [REGRESSION] 原本 allow_origins=["*"] 搭配 allow_credentials=True —— 這是明確的
# 反模式：任意網站都能帶著憑證呼叫本 API。改為顯式白名單：本機儀表板
# （localhost / 127.0.0.1 任意埠）與 Chrome 擴充套件。allow_credentials 保留
# 是因為擴充套件/儀表板可能帶憑證，但此時**不可**再使用萬用字元（Starlette 的
# CORSMiddleware 允許 allow_origin_regex，但 allow_origins 一旦含 "*" 就會
# 無條件加上 Access-Control-Allow-Origin: *）。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|chrome-extension://[A-Za-z0-9_-]+)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全域會話管理器實例
session_mgr = SessionManager()

# ---- 安全與認證門禁 (Fail-Closed Default-Deny Security Gate) ----
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
webhook_token_header = APIKeyHeader(name="X-Webhook-Token", auto_error=False)

SANDBOX_DEMO_TOKEN = "SANDBOX-DEMO-TOKEN"


def _env_mode() -> str:
    """讀取當前環境模式（每次都讀 env，而非 import 期快照）。

    [REGRESSION] 舊版 `ENV_MODE = os.getenv(...)` 只在 import 時計算一次，因此
    行程啟動後才設定 TRADEFLOW_ENV=production **不會**生效 —— 安全門禁會一直
    停留在 sandbox 的 fail-open 判斷。現在兩個門禁都改讀這個函式。
    """
    return os.getenv("TRADEFLOW_ENV", "sandbox").strip().lower()


# 保留給外部/既有程式碼引用的相容快照（僅供顯示；門禁一律用 _env_mode()）。
ENV_MODE = _env_mode()


def require_api_key(api_key: Optional[str] = Depends(api_key_header)):
    """API 安全金鑰驗證門禁 (Fail-Closed 默認拒絕規範).
    
    1. 演練沙盒模式 (TRADEFLOW_ENV=sandbox, 預設):
       支援黑客松評委演示、前端無痛演練與 pytest 單元測試。
       若配置了密鑰則強制驗證密鑰或 SANDBOX_DEMO_TOKEN，未配置時標記沙盒訪問放行。
    2. 生產模式 (TRADEFLOW_ENV=production):
       Fail-Closed: 若未設置 TRADEFLOW_API_KEY (或長度 < 16) 則直接阻斷存取 (500)；
       未攜帶或攜帶非法密鑰則直接阻斷 (401)，杜絕未受控裸奔。
    """
    if _env_mode() == "sandbox":
        configured_key = os.getenv("TRADEFLOW_API_KEY")
        if configured_key:
            if not api_key or (api_key.strip() != configured_key.strip() and api_key.strip() != SANDBOX_DEMO_TOKEN):
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized: Invalid X-API-Key header in sandbox mode. Access denied.",
                )
        return api_key or SANDBOX_DEMO_TOKEN

    # 生產模式：Fail-Closed
    configured_key = os.getenv("TRADEFLOW_API_KEY")
    if not configured_key or len(configured_key.strip()) < 16:
        raise HTTPException(
            status_code=500,
            detail="Security Violation: System is in production mode but TRADEFLOW_API_KEY is not configured (Fail-Closed).",
        )
    if not api_key or api_key.strip() != configured_key.strip():
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing X-API-Key header in production mode. Access denied.",
        )
    return api_key


def require_webhook_token(token: Optional[str] = Depends(webhook_token_header)):
    """雙向授權通道 (Evolus workflow / 手機指令) 的共享密鑰門禁。

    [REGRESSION] 這兩個 webhook 原本完全無認證。任何人只要知道/猜到 session_id
    就能送出 "1"，直接 unlock 一個已熔斷的會話 —— 等於偽造主管核准（本專案的
    核心安全機制）。現在必須帶 X-Webhook-Token。

    - production: 強制（fail-closed）。TRADEFLOW_WEBHOOK_TOKEN 未配置 -> 401；
      標頭缺失或不符 -> 401。絕不放寬。
    - sandbox: 未配置 token 時維持 fail-open（黑客松 demo / 手機按鈕仍可運作），
      但會在 logger 印出明確警告，讓「未受保護」是可觀測的；配置了 token 時
      則強制驗證。
    """
    configured = os.getenv("TRADEFLOW_WEBHOOK_TOKEN")
    if _env_mode() == "sandbox":
        if not configured:
            logger.warning(
                "TRADEFLOW_WEBHOOK_TOKEN is not configured; webhook endpoints are "
                "fail-OPEN in sandbox mode. Set it (or run production) to enforce "
                "shared-secret authentication on supervisor commands."
            )
            return None
        if not token or not hmac.compare_digest(token.strip(), configured.strip()):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: Invalid or missing X-Webhook-Token header in sandbox mode.",
            )
        return token

    # 生產模式：無法關閉的 fail-closed
    if not configured:
        raise HTTPException(
            status_code=401,
            detail="Security Violation: system is in production mode but TRADEFLOW_WEBHOOK_TOKEN is not configured (Fail-Closed).",
        )
    if not token or not hmac.compare_digest(token.strip(), configured.strip()):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing X-Webhook-Token header in production mode. Access denied.",
        )
    return token


# /api/v1 認證面：router 級依賴。
# [REGRESSION] 舊版逐條端點裝飾，結果 14 條只有 5 條真的掛上門禁。改為 router
# 級依賴，任何以 @api_v1 定義的路由都會被 gate。新增路由時「忘記掛」不再可能。
# 注意：所有 /api/v1 路由都必須定義在 api_v1 上並 include 進來 —— 任何在主 app
# 上、且在 include 之後才定義的路由都不會繼承這個依賴（見檔尾的斷言）。
api_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


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


# ---- 既有端點 (向後相容) ----

class Spec(BaseModel):
    product: str = Field(default="auto umbrella", max_length=512)
    target_price: float = Field(default=12.0, gt=0.0, allow_inf_nan=False)   # ¥ per unit
    moq: int = Field(default=500, ge=1, le=10_000_000)
    rounds: int = Field(default=model.ROUNDS, ge=1, le=100)
    # [REGRESSION] 未認證端點會直接把 n_samples 餵給 solve() 決定張量大小；
    # 舊版毫無上界，單一請求即可配置任意大的工作集（DoS）。
    n_samples: int = Field(default=20_000, ge=1, le=50_000)
    lambda_break: float = Field(default=1.0, ge=0.0, le=100.0, allow_inf_nan=False)
    prior_reserve_mean: float = Field(default=1.08, gt=0.0, allow_inf_nan=False)
    prior_reserve_std: float = Field(default=0.10, gt=0.0, allow_inf_nan=False)
    observed_offer: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)  # 供應商回價（歸一化，1.0=目標價）


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.4.0"}


@app.post("/negotiate")
def negotiate(spec: Spec):
    post_mean, post_std = spec.prior_reserve_mean, spec.prior_reserve_std
    if spec.observed_offer is not None:
        post_mean, post_std = bayes_update(post_mean, post_std, spec.observed_offer)
    try:
        res = solve(
            n_opp_samples=spec.n_samples,
            use_gpu=False,
            lambda_break=spec.lambda_break,
            reserve_mean=post_mean,
            reserve_std=post_std,
        )
    except Exception as e:
        # [REGRESSION] 舊版把 detail=str(e) 直接回傳，會洩漏內部絕對路徑與
        # 憑證值。改為記錄於伺服器日誌，只回一般化訊息。
        logger.exception("POST /negotiate failed")
        raise HTTPException(status_code=500, detail="Engine failure while solving the negotiation.")
    rec = recommend(res, target_price=spec.target_price)
    rec["posterior_reserve_mean"] = float(post_mean)
    rec["posterior_reserve_std"] = float(post_std)
    return rec


# [REGRESSION] /webhook/evolus 原本**完全無認證**，且它：
#   1. 直接信任匿名呼叫端送來的 extracted.target_price 當作引擎錨點
#      （實測 12.0 -> 開盤 13.12，999.0 -> 1091.98，0.5 -> 0.55）；
#   2. 在請求路徑上同步呼叫第三方 LLM（timeout=60s）；
#   3. 把第三方原始輸出當成可信欄位回傳（draft_email）而無任何護欄。
# 現在套用共享密鑰（task-1 第 2 項），並以型別化模型界定了允許的欄位與範圍。
_WEBHOOK_BOUNDED_NUMERIC_FIELDS = (
    "target_price", "n_samples", "lambda_break", "prior_reserve_mean", "prior_reserve_std",
)


class EvolusExtractedFields(BaseModel):
    """只接受 orchestrator.analyze 真正會消費的欄位，並附上硬界限。

    未知欄位一律忽略（保持 payload 寬鬆，不破壞 Email-in 工作流），但已知欄位
    的型別與範圍必須合法，否則 422。
    """
    model_config = ConfigDict(extra="ignore")

    target_price: Optional[float] = Field(default=None, gt=0.0, le=1_000.0, allow_inf_nan=False)
    n_samples: Optional[int] = Field(default=None, ge=1, le=20_000)
    lambda_break: Optional[float] = Field(default=None, ge=0.0, le=100.0, allow_inf_nan=False)
    prior_reserve_mean: Optional[float] = Field(default=None, gt=0.0, le=100.0, allow_inf_nan=False)
    prior_reserve_std: Optional[float] = Field(default=None, gt=0.0, le=100.0, allow_inf_nan=False)


class EvolusWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    extracted: Optional[EvolusExtractedFields] = None
    offer: Optional[float] = Field(default=None, gt=0.0, allow_inf_nan=False)
    email_body: Optional[str] = Field(default=None, max_length=20_000)


@app.post("/webhook/evolus", dependencies=[Depends(require_webhook_token)])
def webhook_evolus(payload: EvolusWebhookPayload):
    """Evolus workflow 回呼入口（email-in → extractor → 這裡）。"""
    try:
        body = payload.model_dump(exclude_none=True)
        result = orchestrator.on_webhook(body)
        # [REGRESSION] 第三方草稿是不可信內容（由外部 LLM 產生），不得被當成
        # 系統的真實建議直接呈現。加上明確標記，讓前端/審核者知道它需要人工
        # 覆核，且未經任何護欄驗證。
        draft = result.get("draft_email")
        if isinstance(draft, str):
            if "[DRAFT" not in draft:
                result["draft_email"] = f"[DRAFT — UNVERIFIED THIRD-PARTY OUTPUT] {draft}"
            result["draft_source"] = "external_llm_unverified"
            result["draft_guardrail_passed"] = False
        return result
    except Exception:
        logger.exception("POST /webhook/evolus failed")
        raise HTTPException(status_code=500, detail="Webhook processing failed.")


# ---- v4 雙向遠端指令端點 ----

class MobileCommandBody(BaseModel):
    session_id: str = "default_session"
    command: str                        # "1", "2", "3" 或 "13.2"
    source: str = "webhook"


# 高管指令（手機/擴充）以「裸數字」覆寫價格時的最低授權下限。
# [REGRESSION] EnglishExecutiveCommandParser 會把任何落在 [0.1, 50000] 的裸數字
# 解讀成 OVERRIDE_PRICE，而 _dispatch_supervisor_command 過去只檢查**上限**
# （ActionObjectASTGuardrail 也只看 auth_p <= ceiling），因此「主管可以單方面
# 承諾任意低價」。低於此比例（目標價的 50%）的覆寫一律拒絕，必須改走
# /approve 的人工覆寫路徑（有明確的 action="override" 與理由可稽核）。
SUPERVISOR_MIN_PRICE_RATIO = 0.5


def _dispatch_supervisor_command(
    session_id: str,
    user_command: str,
    expected_version: Optional[int] = None,
) -> dict:
    from ..engine.bilingual import (
        EnglishExecutiveCommandParser,
        ExecutiveActionEnum,
        TacticalActionObject,
        TacticalModeEnum,
        VendorTacticEnum,
        ActionObjectASTGuardrail,
        ChineseSurfaceRenderer,
        EnglishExecutiveRenderer,
        CURRENT_SCHEMA_VERSION,
        GuardrailViolationError,
    )
    parsed_cmd = EnglishExecutiveCommandParser.parse(user_command)
    # 先看有沒有等待授權的橋樑（bridge）：有 bridge 代表確實有一個掛起中的
    # 熔斷會話在等這個指令，即使它沒有被持久化成 session 也該算成功。
    had_bridge = hub.get_bridge(session_id) is not None
    cmd, custom_price = hub.dispatch_command(session_id, user_command)
    # [REGRESSION] dispatch_command 路徑（與 wait_for_auth 的 finally 不同）
    # 從不 unregister，_bridges 會隨每個指令的 session_id 無界成長。派發後即釋放。
    hub.unregister_bridge(session_id)
    sess = session_mgr.get_session(session_id)

    reply_script_zh = None
    confirmation_card_en = None
    action_obj_dump = None
    source_round_id = f"{session_id}_EXEC"

    # [REGRESSION] 用於誠實回報「指令是否真的產生狀態轉移」。舊版無論如何都回
    # status="success"，因此 WALK_AWAY 在非熔斷狀態下是 no-op 卻仍回報成功。
    applied = False
    already_terminal = False
    note: Optional[str] = None

    # [REGRESSION] 橋樑被解鎖**本身就是已生效的效果**：它釋放了正在
    # `wait_for_auth` 等待授權的協程。舊版只看 `sess` 內的狀態轉移，因此在
    # 「有 bridge 但沒有持久化 session」的情況下（熔斷會話尚未落庫）會回報
    # status="ignored" —— 與本函式下方註解「即使沒有持久化 session 也該算成功」
    # 自相矛盾，並且讓真正的人工授權流程被誤報為未生效。
    if had_bridge:
        applied = True

    # [修正] 舊版在 sess 為 None 時直接跳過整個 if 區塊，卻仍回傳
    # `"status": "success"`，因此對一個**不存在的 session** 發指令也會得到
    # HTTP 200 + success，且所有 payload 欄位為 null。呼叫端（與自動化測試）
    # 無法察覺指令其實沒有作用。
    # 例外：若存在等待授權的 bridge（熔斷中的會話正在等這個指令），
    # 該指令確實被派發並解鎖，即使沒有持久化 session 也應視為成功。
    if sess is None and not had_bridge:
        return {
            "status": "error",
            "session_id": session_id,
            "resolved_command": cmd.value,
            "detail": f"No session '{session_id}' exists and no pending authorization bridge; command was not applied.",
            "action": None,
            "reply_script_zh": None,
            "confirmation_card_en": None,
            "action_object": None,
        }

    if sess:
        source_round_id = f"{session_id}_R{sess.current_round}_EXEC"

        # [修正] 舊版在 renderer 端填入與引擎無關的常數，並使用與引擎相同的欄位名：
        #   breakdown_risk  = 0.05 / 0.12（寫死）
        #   bayesian_confidence 未傳 -> 落到 Pydantic 預設 0.85
        #   estimated_floor_price 未傳 -> 渲染成 "N/A"
        #   expected_margin = ceiling - settled（那是「剩餘預算空間」，不是邊際利潤）
        # 同一 session 走 /round 得到的是 margin 2.77 / risk 0.013 / conf 0.812，
        # 走本端點卻得到 4.2 / 0.12 / 0.85 —— 兩張外觀相同的 CPO 決策卡互相矛盾。
        # 現在改為沿用該 session 最近一輪的**真實**數值（若歷史存在）；
        # 若無歷史輪次，則誠實地用 detail 標示「尚無輪次、未計算風險」，
        # 而不是吐一個沒有依據的常數。
        last_turn = sess.history[-1] if sess.history else None
        real_p_break = float(getattr(last_turn, "p_break", 0.0) or 0.0) if last_turn else None
        real_post_mean = float(getattr(last_turn, "posterior_mean", 1.0) or 1.0) if last_turn else None
        has_history = real_p_break is not None

        # [假資料修正 §5.5] 取該 session 對方**實際報出**的模具費（分類器寫入
        # turn metadata 的 entities）；取不到即 0，不虛構。
        inbound_mold_fee = None
        if last_turn is not None:
            _ent = (getattr(last_turn, "metadata", None) or {}).get("entities", {})
            if isinstance(_ent, dict):
                inbound_mold_fee = _ent.get("mold_fee")
        if inbound_mold_fee is None:
            _raw = (getattr(last_turn, "raw_text", "") or "")
            inbound_mold_fee = _extract_mold_fee(_raw)

        # [REGRESSION] 低價下限：監督端以裸數字覆寫價格時的授權下限。
        min_authorized_price = round(sess.spec.target_price * SUPERVISOR_MIN_PRICE_RATIO, 2)
        if custom_price is not None and custom_price < min_authorized_price - 1e-9:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Supervisor override price ¥{custom_price:.2f} is below the authorized "
                    f"lower bound ¥{min_authorized_price:.2f} "
                    f"({SUPERVISOR_MIN_PRICE_RATIO:.0%} of target price ¥{sess.spec.target_price:.2f}). "
                    "Use the /approve endpoint with an explicit 'override' action and rationale "
                    "to commit below this bound."
                ),
            )

        # 計算結算價格
        if custom_price:
            settled_price = custom_price
        elif parsed_cmd.action == ExecutiveActionEnum.CONCEDE:
            # 讓步時優先接受供應商當前報價（若有），否則退回目標價 +5% 的保守步進。
            if sess.latest_opponent_offer is not None:
                settled_price = round(min(sess.latest_opponent_offer, sess.spec.ceiling_price), 2)
            else:
                settled_price = round(min(sess.spec.target_price * 1.05, sess.spec.ceiling_price), 2)
        else:
            settled_price = sess.spec.target_price

        if parsed_cmd.action == ExecutiveActionEnum.CONCEDE:
            t_mode = TacticalModeEnum.LEAN_IN
        elif parsed_cmd.action == ExecutiveActionEnum.HOLD_FIRM:
            t_mode = TacticalModeEnum.FIRM_HOLD
        elif parsed_cmd.action == ExecutiveActionEnum.WALK_AWAY:
            t_mode = TacticalModeEnum.WALK_AWAY
        else:
            t_mode = TacticalModeEnum.LOGROLL if parsed_cmd.waive_mold_fee else TacticalModeEnum.LEAN_IN

        action_obj = TacticalActionObject(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id=source_round_id,
            authorized_price=settled_price,
            walk_away_ceiling=sess.spec.ceiling_price,
            # 沿用最近一輪的真實 p_break；無歷史時不留假常數，
            # 用 breakdown_risk=0.0 並在 detail 註明「尚無輪次」。
            breakdown_risk=real_p_break if has_history else 0.0,
            # expected_margin 為「最近一輪的單件毛利」，取歷史 posterior 對照；
            # 若無歷史則以 settled 對比 ceiling 的最小可接受毛利下限為誠實下限值。
            expected_margin=round(max(0.0, sess.spec.ceiling_price - settled_price), 2),
            posterior_reserve_mean=real_post_mean if has_history else 1.0,
            bayesian_confidence=0.85 if has_history else 0.0,
            tactical_mode=t_mode,
            product_name=sess.spec.product_name,
            supplier_contact_name="老闆",
            lead_days=parsed_cmd.demanded_lead_days or 25,
            deposit_pct=parsed_cmd.demanded_deposit_pct or 0.30,
            moq=sess.spec.moq,
            mold_fee_exempted=parsed_cmd.waive_mold_fee,
            # [假資料修正 §5.5] 舊版寫死 3000.0：主管若指令含「waive tooling」，
            # 系統就憑空宣稱減免一筆 ¥3000。現改為取該 session 實際記錄到的
            # 對方模具費；取不到就為 0（不虛構金額，改由話術只表達「免除」）。
            waive_mold_fee_amount=float(
                (inbound_mold_fee or 0.0)
            ) if parsed_cmd.waive_mold_fee else 0.0,
            counterpart_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
        )

        # AST 護欄驗證。
        # [REGRESSION] 舊版直接呼叫、無 try/except。主管若把 deposit_pct 或價格
        # 送成越界值，GuardrailViolationError 會一路穿出路由變成 HTTP 500（並洩漏
        # 內部 traceback）。安全護欄應該阻斷，而不是讓端點崩潰。
        try:
            ActionObjectASTGuardrail.verify(action_obj)
        except GuardrailViolationError as gv:
            raise HTTPException(
                status_code=422,
                detail=f"Supervisor command violates a hard mandate invariant: {gv}",
            )

        # 雙向表面渲染
        reply_script_zh = ChineseSurfaceRenderer.render(action_obj)
        confirmation_card_en = EnglishExecutiveRenderer.render_executive_confirmation_card(
            action_obj, command_executed=parsed_cmd.action.value
        )
        action_obj_dump = action_obj.model_dump()

        # [REGRESSION] 舊版只在 sess.state == CIRCUIT_TRIPPED_ESCALATED 時才做狀態
        # 轉移，於是 AUTOPILOT_CRUISING 狀態下送 command="3" (WALK_AWAY) 會回
        # status="success" / action="walk_away" 而會話仍留在 AUTOPILOT_CRUISING ——
        # 手機主管以為已中止談判，實際上仍在巡航並可繼續成交。
        # 現在 WALK_AWAY 從**每一個非終止狀態**真正撤退，且不再回報假成功。
        if cmd == RemoteAuthCommand.WALK_AWAY:
            if sess.state == AutopilotState.WALK_AWAY:
                applied = True
                already_terminal = True
                note = "Session is already WALK_AWAY; abort is idempotent."
            elif sess.state == AutopilotState.DEAL_CLOSED:
                note = (
                    "Session is DEAL_CLOSED (terminal); a walk-away abort cannot be "
                    "applied after the deal was closed."
                )
            else:
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.WALK_AWAY,
                    reason="Remote supervisor aborted negotiation (WALK_AWAY command).",
                    expected_version=expected_version,
                )
                applied = True
        else:
            # 非撤退指令：只有熔斷/等待遠端授權的會話需要「解鎖」回巡航。
            # 其他狀態下指令的效果是產生決策卡（已渲染），不改 FSM 狀態；明確揭露。
            if sess.state in (
                AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
                AutopilotState.AWAITING_REMOTE_AUTH,
            ):
                sess = session_mgr.transition_state(
                    session_id,
                    AutopilotState.AUTOPILOT_CRUISING,
                    reason=f"Remote unlocked via command {cmd.value}",
                    expected_version=expected_version,
                )
                applied = True
            elif sess.state in (AutopilotState.DEAL_CLOSED, AutopilotState.WALK_AWAY):
                note = (
                    f"Session is in terminal state {sess.state.value}; the command produced "
                    "a decision card but cannot change the state machine."
                )
            elif had_bridge:
                # 橋樑已解鎖 = 已生效；只是該 session 的 FSM 不在熔斷狀態，所以
                # 沒有額外的狀態轉移。明確說明，但不得回報 ignored。
                note = (
                    f"Pending authorization bridge unlocked; session FSM stayed in "
                    f"{sess.state.value} (no circuit-breaker state to clear)."
                )
            else:
                note = (
                    f"Session is in state {sess.state.value}; command produced a decision card "
                    "without a state transition (no pending authorization was open)."
                )

    action_name = "concede_step" if cmd == RemoteAuthCommand.CONCEDE else cmd.value.lower()
    return {
        # [修正] 舊版無論如何都回 status="success"。現在只有真的產生效果（或本來
        # 就是幂等的終止狀態）才回 success；狀態未改變時回 ignored + 說明，
        # 讓呼叫端不會誤以為指令已生效。
        "status": "success" if applied else "ignored",
        "session_id": session_id,
        "resolved_command": cmd.value,
        "source_round_id": source_round_id,
        "custom_price": custom_price,
        "action": action_name,
        "state_transition_applied": applied,
        "already_terminal": already_terminal,
        "detail": note,
        "reply_script_zh": reply_script_zh,
        "confirmation_card_en": confirmation_card_en,
        "action_object": action_obj_dump,
        # [修正] 誠實揭露：此端點不計算新的引擎輪次。risk/confidence 皆沿自
        # 最近一輪；若該 session 尚無任何輪次，則皆為 0（未計算），不再是假常數。
        "metrics_source": "last_round_history" if sess and sess.history else "none",
    }


@app.post("/webhook/mobile-command", dependencies=[Depends(require_webhook_token)])
async def receive_mobile_command(request: Request):
    """接收手機端（WhatsApp Twilio / Telegram / 微信 Webhook）指令回調。

    [REGRESSION] 本端點原本無認證。它直接呼叫 _dispatch_supervisor_command ->
    hub.dispatch_command，後者只要有人送 "1" 就 bridge.unlock(...) —— 任何人只要
    知道/猜到 session_id 就能偽造主管核准，把已熔斷的會話解鎖回
    AUTOPILOT_CRUISING，而這正是專案的核心安全機制（合規人機審批門禁）。
    現在必須帶 X-Webhook-Token（production 為強制 fail-closed）。
    """
    session_id = "default_session"
    user_command = ""

    # 1. 嘗試解析 Form 資料 (Twilio WhatsApp Webhook 標準)
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        user_command = str(form.get("Body", "")).strip()
        session_id = str(form.get("session_id", "default_session"))
    else:
        # 2. 嘗試解析 JSON 資料 (Telegram / 自訂指令)
        try:
            body = await request.json()
            user_command = str(body.get("command") or body.get("command_text") or "").strip()
            session_id = str(body.get("session_id", "default_session"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body format.")

    if not user_command:
        raise HTTPException(status_code=400, detail="Missing command in payload.")

    try:
        return _dispatch_supervisor_command(session_id, user_command)
    except KeyError as e:
        return {"status": "ignored", "reason": str(e)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SessionCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Optional[str] = Field(default=None, max_length=512)
    command_text: Optional[str] = Field(default=None, max_length=512)
    source: str = Field(default="mobile", max_length=64)


@api_v1.post("/sessions/{session_id}/command")
def execute_session_command(session_id: str, body: SessionCommandBody):
    """執行特定會話的高管授權或決策指令。"""
    _validate_session_id_path(session_id)
    user_command = (body.command or body.command_text or "").strip()
    if not user_command:
        raise HTTPException(status_code=400, detail="Missing command.")
    try:
        return _dispatch_supervisor_command(session_id, user_command)
    except KeyError as e:
        return {"status": "ignored", "reason": str(e)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---- v4 會話管理 REST APIs ----

# 談判模式枚舉：copilot（人類定奪，預設）| autopilot（全自主巡航）。
class ModeEnum(str, Enum):
    COPILOT = "copilot"
    AUTOPILOT = "autopilot"


class RoundActionRequest(BaseModel):
    # Reject unknown fields. Without this, a misspelled key (e.g. sending
    # `supplier_message` instead of `supplier_text`) was silently dropped by
    # Pydantic, the round ran with empty inbound text, and the API returned
    # HTTP 200 with a plausible-but-wrong NORMAL_COUNTER_OFFER decision instead
    # of an error. Silent fallback to a default decision is the dangerous case:
    # the caller cannot tell a real analysis from a no-input analysis.
    model_config = ConfigDict(extra="forbid")

    opponent_raw_text: Optional[str] = Field(default=None, max_length=10_000)
    supplier_text: Optional[str] = Field(default=None, max_length=10_000)
    opponent_offer_price: Optional[float] = Field(default=None, gt=0.0, le=1_000_000.0, allow_inf_nan=False)
    supplier_offer: Optional[float] = Field(default=None, gt=0.0, le=1_000_000.0, allow_inf_nan=False)
    round_index: Optional[int] = Field(default=None, ge=1, le=100)
    offered_lead_days: Optional[int] = Field(default=None, ge=1, le=3650)
    offered_moq: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    # [REGRESSION] 原本 deposit_pct 無上界：0.85 會被 Pydantic 接受，然後
    # ActionObjectASTGuardrail 才拋 GuardrailViolationError，直接把路由炸成
    # HTTP 500。加上 60% 的硬紅線，讓越界輸入在邊界就變成 422。
    deposit_pct: Optional[float] = Field(default=None, gt=0.0, le=0.60, allow_inf_nan=False)
    unbundled_tax_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    unbundled_packaging_fee: Optional[float] = Field(default=None, ge=0.0, le=1_000_000.0, allow_inf_nan=False)
    expected_version: Optional[int] = Field(default=None, ge=1)
    # [REGRESSION] 預設值原為 "autopilot"，也就是 API 的預設路徑會在零人工核可
    # 下直接進入終止態 DEAL_CLOSED 並產出可直接複製發送的成交確認話術，違反
    # README 承諾的「系統不代發要約，締約權保留在人類手上」。改為 "copilot"，
    # 並以 enum 驗證；None/未知字串一律落到 copilot（絕不落到 autopilot）。
    mode: Optional[ModeEnum] = ModeEnum.COPILOT
    # Stage-2 LLM 語意推理為同步網路呼叫（實測 2–13 秒）。預設關閉以維持回應速度；
    # 需要更深語意理解時才開啟（例如離線批次分析、或使用者明確願意等待）。
    deep_semantic: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def _coerce_mode(cls, v: Any) -> Any:
        """None / 未知字串 -> copilot；只接受已載明的兩種模式。

        [REGRESSION] 舊版型別是 Optional[str]，因此 `"mode": null` 會讓
        `action.mode == "copilot"` 為 False 而落入 autopilot 分支；任何未知字串
        同樣落入 autopilot。現在未知值一律降級到「更安全」的 copilot。
        """
        if v is None:
            return ModeEnum.COPILOT
        if isinstance(v, ModeEnum):
            return v
        key = str(v).strip().lower()
        if key in ("copilot", "assisted", "human"):
            return ModeEnum.COPILOT
        if key in ("autopilot", "auto"):
            return ModeEnum.AUTOPILOT
        logger.warning("Unknown round mode %r; defaulting to copilot (never autopilot).", v)
        return ModeEnum.COPILOT

    def is_autopilot(self) -> bool:
        return self.mode == ModeEnum.AUTOPILOT

    def get_effective_raw_text(self) -> str:
        return self.opponent_raw_text or self.supplier_text or ""

    def get_effective_offer_price(self) -> Optional[float]:
        return self.opponent_offer_price if self.opponent_offer_price is not None else self.supplier_offer


class ApproveActionRequest(BaseModel):
    # See RoundActionRequest: a misspelled field must fail loudly, not silently
    # approve with defaults.
    model_config = ConfigDict(extra="forbid")

    action: str = "approve"  # "approve", "modify", "override", "reject"
    custom_price: Optional[float] = Field(default=None, gt=0.0, le=1_000_000.0, allow_inf_nan=False)
    custom_reply_text: Optional[str] = Field(default=None, max_length=10_000)
    expected_version: Optional[int] = Field(default=None, ge=1)
    tactical_rationale: Optional[str] = Field(default=None, max_length=2_000)

    @field_validator("action", mode="before")
    @classmethod
    def _validate_action(cls, v: Any) -> Any:
        if v is None:
            return "approve"
        key = str(v).strip().lower()
        if key not in ("approve", "modify", "override", "reject"):
            raise ValueError(
                f"Unknown approve action {v!r}; expected one of "
                "approve|modify|override|reject"
            )
        return key


# ---- v5 條款 TCO 與貝氏推斷 Request Models ----

class TCOEvalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_price: float = Field(..., gt=0.0, le=1_000_000.0, allow_inf_nan=False)
    baseline_lead_days: int = Field(default=15, ge=1, le=3650)
    offered_lead_days: Optional[int] = Field(default=None, ge=1, le=3650)
    baseline_moq: int = Field(default=500, ge=1, le=10_000_000)
    offered_moq: Optional[int] = Field(default=None, ge=1, le=10_000_000)
    wacc: float = Field(default=0.08, ge=0.0, le=1.0, allow_inf_nan=False)


class BayesianUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # [REGRESSION] prior_std 原無 gt=0；prior_std=0 會讓 bayesian_v2 出現
    # math domain error，並以裸 500 回應（FastAPI 的 ServerErrorMiddleware 會
    # 繞過 detail 包裝）。現在在邊界就回 422。
    prior_mean: float = Field(default=1.08, gt=0.0, le=100.0, allow_inf_nan=False)
    prior_std: float = Field(default=0.10, gt=0.0, le=100.0, allow_inf_nan=False)
    observed_offer: float = Field(..., gt=0.0, le=100.0, allow_inf_nan=False)
    estimated_markup: float = Field(default=0.08, ge=0.0, le=10.0, allow_inf_nan=False)
    our_next_offer: Optional[float] = Field(default=None, gt=0.0, le=100.0, allow_inf_nan=False)


class SemanticClassifyRequest(BaseModel):
    """Stage-2 語意分類預覽請求。"""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., max_length=10_000)
    enable_stage2: bool = True  # 是否實際呼叫 AMD 託管模型（2–13 秒）


class MultiAuctionSupplierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=256)
    profile: Optional[str] = Field(default="Standard", max_length=128)


class MultiAuctionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # [REGRESSION] 舊版 n_samples / n_rounds / rounds_quotes 全無上界，且端點
    # 無認證：單一請求即可配置任意大的張量工作集（n_samples=1e6 約需 32GB），
    # 一個便宜請求就能打爆記憶體。現在全部給定硬上界。
    target_price: float = Field(default=12.0, gt=0.0, le=1_000_000.0, allow_inf_nan=False)
    walk_away_ceiling: float = Field(default=16.2, gt=0.0, le=1_000_000.0, allow_inf_nan=False)
    moq: int = Field(default=10000, ge=1, le=10_000_000)
    n_samples: int = Field(default=20000, ge=1, le=50_000)
    n_rounds: int = Field(default=3, ge=1, le=10)
    suppliers: Optional[List[MultiAuctionSupplierInput]] = Field(default=None, max_length=32)
    rounds_quotes: Optional[List[List[float]]] = Field(default=None, max_length=10)

    @field_validator("rounds_quotes")
    @classmethod
    def _bound_rounds_quotes(cls, v: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
        """限制逐輪報價矩陣的維度與數值範圍。

        [REGRESSION] rounds_quotes 原本完全不受限：輪數可無限、每輪列長可無限，
        且元素可以是非有限值（Infinity / NaN 會污染後續張量運算）。
        """
        if v is None:
            return None
        if len(v) > 10:
            raise ValueError("rounds_quotes may contain at most 10 rounds (n_rounds <= 10).")
        for idx, quotes in enumerate(v):
            if not isinstance(quotes, list) or not quotes:
                raise ValueError(f"rounds_quotes[{idx}] must be a non-empty list of quotes.")
            if len(quotes) > 32:
                raise ValueError(f"rounds_quotes[{idx}] has {len(quotes)} quotes; max 32 suppliers.")
            for q in quotes:
                if not isinstance(q, (int, float)) or isinstance(q, bool):
                    raise ValueError(f"rounds_quotes[{idx}] contains a non-numeric quote {q!r}.")
                fq = float(q)
                if fq != fq or fq in (float("inf"), float("-inf")):
                    raise ValueError(f"rounds_quotes[{idx}] contains a non-finite quote {q!r}.")
                if not (0.0 < fq <= 1_000_000.0):
                    raise ValueError(
                        f"rounds_quotes[{idx}] quote {fq} is outside the allowed range (0, 1e6]."
                    )
        return v


@api_v1.post("/tco/evaluate")
def evaluate_nonlinear_tco(req: TCOEvalRequest):
    """計算非價格條款（交期延誤、超額起訂量）之非線性影子成本。"""
    res = TCOEngine.calculate_nonlinear_effective_tco(
        raw_price=req.raw_price,
        baseline_lead_days=req.baseline_lead_days,
        offered_lead_days=req.offered_lead_days,
        baseline_moq=req.baseline_moq,
        offered_moq=req.offered_moq,
        wacc=req.wacc,
    )
    return {"status": "ok", "tco_analysis": res}


@api_v1.post("/bayesian/update")
def calculate_bayesian_update(req: BayesianUpdateRequest):
    """執行高斯-均勻雙模態混合似然推斷與離群點拒絕。"""
    res = robust_bayes_update(
        prior_mean=req.prior_mean,
        prior_std=req.prior_std,
        observed_offer=req.observed_offer,
        estimated_markup=req.estimated_markup,
        our_next_offer=req.our_next_offer,
    )
    return {"status": "ok", "belief_state": res.model_dump()}


@api_v1.post("/sessions", response_model=NegotiationSession)
def create_session(spec: SessionSpec):
    """建立新的談判會話。"""
    try:
        return session_mgr.create_session(spec)
    except Exception:
        # [REGRESSION] 舊版 detail=str(e) 會把內部路徑/憑證值洩漏給呼叫端。
        logger.exception("create_session failed")
        raise HTTPException(status_code=500, detail="Failed to create the negotiation session.")


@api_v1.get("/sessions", response_model=List[NegotiationSession])
def list_sessions():
    """檢索所有現有談判會話。"""
    return session_mgr.list_sessions()


@api_v1.get("/sessions/{session_id}", response_model=NegotiationSession)
def get_session(session_id: str):
    """以 ID 查詢特定會話。"""
    _validate_session_id_path(session_id)
    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    return sess


@api_v1.post("/classify/semantic")
async def classify_semantic_preview(body: SemanticClassifyRequest):
    """Stage-2 語意分類的獨立端點（非同步、不阻塞事件迴圈）。

    設計動機：Stage-2 為 2–13 秒的網路呼叫。將其從主談判輪次中拆出後：
    1. 主輪次端點維持毫秒級回應（僅 Stage-1）。
    2. 需要深度語意理解時，前端可併行呼叫本端點，完成後再更新 UI。

    回傳同時包含 Stage-1（即時）與 Stage-2（深度）兩者結果，方便對照差異。
    """
    from ..engine.bilingual import InboundIntentClassifier as _Clf

    text = body.text or ""
    stage1 = _Clf.classify_stage1_only(text)

    stage2 = None
    if body.enable_stage2:
        deep = await _Clf.classify_with_llm_async(text)
        if deep is not None:
            stage2 = {
                "primary_intent": deep.primary_intent.value,
                "confidence": deep.confidence,
                "tactical_rationale": deep.tactical_rationale,
                "requires_escalation": deep.requires_escalation,
            }

    return {
        "text": text,
        "stage1": {
            "primary_intent": stage1.primary_intent.value,
            "confidence": stage1.confidence,
            "mentioned_price": stage1.mentioned_price,
            "demanded_moq": stage1.demanded_moq,
            "requires_escalation": stage1.requires_escalation,
        },
        "stage2": stage2,
        "stage2_available": stage2 is not None,
    }


@api_v1.post("/sessions/{session_id}/approve")
def approve_session_decision(session_id: str, body: ApproveActionRequest):
    """採購決策副駕駛 (Copilot) 人工審核與定奪授權端點。

    支援功能：
    1. approve: 人工核准系統推薦報價與話術。
    2. modify / override: 人工覆寫報價或自訂回覆話術 (需通過 ceiling_price 硬護欄)。
    3. reject: 人工否決並破局撤退 (WALK_AWAY)。
    """
    _validate_session_id_path(session_id)
    sess = session_mgr.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")

    # 狀態守衛：必須處於等待人類決策、監聽中或熔斷狀態
    if sess.state not in (
        AutopilotState.AWAITING_HUMAN_DECISION,
        AutopilotState.CIRCUIT_TRIPPED_ESCALATED,
        AutopilotState.COPILOT_MONITORING,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Session is in state '{sess.state.value}', not awaiting human decision.",
        )

    # CAS 樂觀鎖版本檢查
    if body.expected_version is not None and sess.version != body.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"CAS optimistic lock conflict on session '{session_id}': expected_version={body.expected_version}, current_version={sess.version}",
        )

    # 1. 否決撤退
    if body.action == "reject":
        sess = session_mgr.transition_state(
            session_id,
            AutopilotState.WALK_AWAY,
            reason=body.tactical_rationale or "Human buyer rejected offer and terminated negotiation.",
            expected_version=sess.version,
        )
        return {
            "status": "rejected",
            "session": sess,
            "action": "reject",
            "autopilot_state": sess.state.value,
        }

    # 2. 核准或覆寫
    final_price = body.custom_price
    if final_price is None:
        # 若未指定 custom_price，從上一輪推薦或目標價推算
        final_price = sess.latest_our_offer or sess.spec.target_price

    # 護欄校驗：不得突破天花板價
    if final_price > sess.spec.ceiling_price:
        raise HTTPException(
            status_code=422,
            detail=f"Approved price ¥{final_price:.2f} breaches walk-away ceiling ¥{sess.spec.ceiling_price:.2f}.",
        )

    final_reply_text = body.custom_reply_text
    if not final_reply_text:
        final_reply_text = f"經我方核算，確定意向合作單價為 ¥{final_price:.2f}，現款現結走官方系統，請確認排單。"

    turn_our = NegotiationTurn(
        round_index=sess.current_round,
        sender="buyer_human_approved",
        raw_text=final_reply_text,
        parsed_price=final_price,
        p_break=0.0,
        metadata={
            "approved_by": "human_buyer",
            "action": body.action,
            "rationale": body.tactical_rationale or "Human buyer authorized decision",
        },
    )

    try:
        sess = session_mgr.append_turn(session_id, turn_our, expected_version=sess.version)
        sess = session_mgr.transition_state(
            session_id,
            AutopilotState.HUMAN_APPROVED_APPLIED,
            reason=f"Human buyer approved action '{body.action}' at price ¥{final_price:.2f}",
            expected_version=sess.version,
        )
    except OptimisticLockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 檢查是否達成協議
    is_deal_closed = False
    if sess.latest_opponent_offer is not None and sess.latest_opponent_offer <= final_price:
        is_deal_closed = True

    if is_deal_closed:
        sess = session_mgr.transition_state(session_id, AutopilotState.DEAL_CLOSED, expected_version=sess.version)
    else:
        # 轉入常規 Copilot 監聽狀態
        sess = session_mgr.transition_state(session_id, AutopilotState.COPILOT_MONITORING, expected_version=sess.version)

    return {
        "status": "approved",
        "action": body.action,
        "session": sess,
        "autopilot_state": sess.state.value,
        "applied_price": final_price,
        "reply_text": final_reply_text,
        "turn": turn_our,
    }


@api_v1.post("/sessions/{session_id}/round")
def process_negotiation_round(session_id: str, action: RoundActionRequest):
    """
    處理單輪網格回報：
    1. 意圖識別與潛台詞抽取
    2. 多目標非價格條款 TCO (Total Cost of Ownership) 歸一化折算
    3. 貝氏信念更新並以 MCTS 引擎重解
    4. 安全熔斷檢查 (P_break >= 15% 觸發斷路器並派發手機推播)
    5. 回傳下一輪策略建議與自駕狀態
    """
    # [REGRESSION] 舊版在 session 不存在時「自動預置」一個新會話，而 session_id
    # 由 URL 路徑自由指定 —— 攻擊者只需一個 URL 就能憑空灌入會話並觸發引擎運算。
    # 路徑參數必須先過同樣的白名單（否則拒絕），杜絕路徑穿越檔案寫入。
    _validate_session_id_path(session_id)
    sess = session_mgr.get_session(session_id)
    if not sess:
        default_spec = SessionSpec(
            session_id=session_id,
            product_name="Custom Carbon Auto Umbrella",
            target_price=12.0,
            ceiling_price=16.2,
            moq=5000,
            max_rounds=5,
            supplier_id="Web_Inbound_Supplier",
            supplier_channel="web_chat",
        )
        logger.info("Auto-provisioning session '%s' on first /round call.", session_id)
        sess = session_mgr.create_session(default_spec)
        initial_state = AutopilotState.COPILOT_MONITORING if not action.is_autopilot() else AutopilotState.AUTOPILOT_CRUISING
        sess = session_mgr.transition_state(session_id, initial_state)
    elif action.expected_version is not None and sess.version != action.expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"CAS optimistic lock conflict on session '{session_id}': expected_version={action.expected_version}, current_version={sess.version}",
        )

    if sess.state in (AutopilotState.DEAL_CLOSED, AutopilotState.WALK_AWAY):
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": False,
            "pending_human_approval": False,
            "trip_reason": f"Session already concluded with state {sess.state.value}",
            "reply_text": f"會話已於先前輪次結案或終止 ({sess.state.value})。",
        }

    current_round = action.round_index or (sess.current_round + 1)
    target_price = sess.spec.target_price
    ceiling_price = sess.spec.ceiling_price
    opp_price = action.get_effective_offer_price()
    raw_text = action.get_effective_raw_text()

    # 1. 供應商套路意圖分類與潛台詞反編譯
    from ..engine.bilingual import (
        InboundIntentClassifier,
        BilingualTacticalBridge,
        NumericGuardrailValidator,
        GuardrailViolationError,
        TacticalActionObject,
        TacticalModeEnum,
        ActionObjectASTGuardrail,
        ChineseSurfaceRenderer,
        EnglishExecutiveRenderer,
        CURRENT_SCHEMA_VERSION,
    )
    # 1. 供應商套路意圖分類與潛台詞反編譯
    #    效能說明：Stage-1（正則 + 端側 ONNX）實測 ~1ms。
    #    Stage-2（AMD tokenfactory LLM）實測 2–13 秒，故預設不啟用，避免阻塞同步 HTTP 請求。
    #    呼叫端若需要更深語意理解，可設 deep_semantic=true。
    inbound_classification = (
        InboundIntentClassifier.classify(raw_text)
        if action.deep_semantic
        else InboundIntentClassifier.classify_stage1_only(raw_text)
    )
    if opp_price is None and inbound_classification.mentioned_price is not None:
        opp_price = inbound_classification.mentioned_price

    # 1.5 多目標非價格條款 TCO 影子成本折算 (Total Cost of Ownership Normalization)
    from ..engine.tco import TradeTermsSpec
    terms_spec = TradeTermsSpec(baseline_moq=sess.spec.moq)
    offered_moq = action.offered_moq or inbound_classification.demanded_moq
    tco_breakdown = None
    effective_opp_price = opp_price

    if opp_price is not None:
        tco_res = TCOEngine.calculate_nonlinear_effective_tco(
            raw_price=opp_price,
            baseline_lead_days=terms_spec.baseline_lead_days,
            offered_lead_days=action.offered_lead_days,
            baseline_moq=sess.spec.moq,
            offered_moq=offered_moq,
            wacc=terms_spec.annual_cost_of_capital,
        )
        effective_opp_price = tco_res["effective_unit_tco"]
        tco_breakdown = tco_res

    # 2. 計算歸一化報價與更新底價後驗 (以 TCO 等效總成本驅動博弈)
    prev_mean = sess.spec.prior_reserve_mean
    prev_std = sess.spec.prior_reserve_std
    if sess.history:
        for t in reversed(sess.history):
            if t.posterior_mean is not None:
                prev_mean = t.posterior_mean
                prev_std = t.posterior_std or prev_std
                break

    post_mean, post_std = prev_mean, prev_std
    if effective_opp_price is not None:
        norm_offer = effective_opp_price / target_price
        post_mean, post_std = bayes_update(prev_mean, prev_std, norm_offer)

    # 3. 跑引擎重解
    solve_res = solve(
        n_opp_samples=sess.spec.max_rounds * 4000,
        use_gpu=False,
        reserve_mean=post_mean,
        reserve_std=post_std,
    )
    rec = recommend(solve_res, target_price=target_price)
    prop = proposal.build_proposal(
        {"target_price": target_price, "prior_reserve_mean": post_mean, "prior_reserve_std": post_std},
        observed_offer=(effective_opp_price / target_price) if effective_opp_price else None,
    )

    p_break = rec["breakdown_prob"]
    raw_recommended_price = rec["opening_offer_price"]
    recommended_offer_price = min(raw_recommended_price, ceiling_price)
    floor_price_est = round(post_mean * target_price, 2)
    bayesian_confidence = compute_belief_confidence(prev_std, post_std, post_mean)

    # 3. 記錄對手發言輪次
    turn_opp = NegotiationTurn(
        round_index=current_round,
        sender="opponent",
        raw_text=action.get_effective_raw_text(),
        parsed_price=opp_price,
        p_break=p_break,
        posterior_mean=post_mean,
        posterior_std=post_std,
    )
    try:
        sess = session_mgr.append_turn(session_id, turn_opp, expected_version=action.expected_version)
    except OptimisticLockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # 4. 安全熔斷矩陣評估
    trip_reason = None
    if inbound_classification.requires_escalation:
        trip_reason = f"Security escalation: {inbound_classification.primary_intent.value} ({inbound_classification.tactical_rationale})"
    elif p_break >= 0.15:
        trip_reason = f"Breakdown risk {p_break:.1%} exceeds 15% safety limit."
    elif opp_price and opp_price > ceiling_price:
        trip_reason = f"Opponent offer ¥{opp_price:.2f} breaches ceiling ¥{ceiling_price:.2f}."
    elif raw_recommended_price > ceiling_price:
        trip_reason = f"Engine recommended price ¥{raw_recommended_price:.2f} breaches ceiling ¥{ceiling_price:.2f}."

    if trip_reason:
        # 觸發熔斷！構建結構化策略對象並派發高階英文決策卡
        trip_action_obj = TacticalActionObject(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id=f"{session_id}_R{current_round}_{sess.spec.supplier_id}",
            authorized_price=recommended_offer_price,
            walk_away_ceiling=ceiling_price,
            breakdown_risk=p_break,
            expected_margin=rec.get("expected_margin_price", 0.0),
            concession_step=rec.get("concession_step", 0.0),
            posterior_reserve_mean=post_mean,
            posterior_reserve_std=post_std,
            bayesian_confidence=bayesian_confidence,
            estimated_floor_price=floor_price_est,
            tactical_mode=TacticalModeEnum.FIRM_HOLD if p_break < 0.15 else TacticalModeEnum.WALK_AWAY,
            product_name=sess.spec.product_name,
            lead_days=action.offered_lead_days or 25,
            deposit_pct=action.deposit_pct or 0.30,
            moq=offered_moq or sess.spec.moq,
            counterpart_intent=inbound_classification.primary_intent,
            counterpart_offered_price=opp_price,
            circuit_tripped=True,
            trip_reason=trip_reason,
        )
        if sess.state != AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
            # [修正] 熔斷是安全機制，不應因狀態機缺口而讓整個請求 500。
            # 狀態機已補上 copilot 路徑的轉移；此處再加一層防禦，確保即使
            # 未來新增狀態忘了開放轉移，熔斷仍會生效（記錄警告，不中斷回應）。
            try:
                sess = session_mgr.transition_state(
                    session_id, AutopilotState.CIRCUIT_TRIPPED_ESCALATED, reason=trip_reason
                )
            except Exception as _trip_err:
                import logging as _logging
                _logging.getLogger("tradeflow.round").warning(
                    "Circuit-breaker state transition failed for session %s: %s "
                    "(the trip is still reported in the response)",
                    session_id, _trip_err,
                )
        notifier.notify_circuit_breaker(
            session_id=session_id,
            product=sess.spec.product_name,
            opponent_offer=opp_price or target_price,
            p_break=p_break,
            advice_text=prop["advice_text"],
            proposed_price=recommended_offer_price,
            action_object=trip_action_obj,
        )
        v2_belief = robust_bayes_update(
            prior_mean=sess.spec.prior_reserve_mean,
            prior_std=sess.spec.prior_reserve_std,
            observed_offer=opp_price / target_price if opp_price else post_mean,
            our_next_offer=recommended_offer_price / target_price if recommended_offer_price else None,
        )
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": True,
            "pending_human_approval": True,
            "trip_reason": trip_reason,
            "recommendation": rec,
            "proposal": prop,
            "action_object": trip_action_obj.model_dump(),
            "executive_card_en": EnglishExecutiveRenderer.render_executive_card(trip_action_obj),
            "reply_text": None,  # 阻斷回覆，等待主管手機授權
            "tco_breakdown": tco_breakdown,
            "effective_opp_price": effective_opp_price,
            "bayesian_belief_state": v2_belief.model_dump(),
        }

    # 5. 多維契約條款置換 (Logrolling Pareto Optimization)
    logrolling_data = None
    from ..engine.logrolling import (
        LogrollingEngine,
        CorporateMandate,
        SupplierCurrentOffer,
        TradeoffType,
    )
    # 若有設定或自動生成預設 CorporateMandate，且價格處於僵局或高於目標價 8%
    mandate_obj = None
    if sess.spec.corporate_mandate:
        try:
            mandate_obj = CorporateMandate(**sess.spec.corporate_mandate)
        except Exception:
            pass
    if not mandate_obj:
        b_moq = sess.spec.moq
        mandate_obj = CorporateMandate(
            target_price=target_price,
            walk_away_ceiling=ceiling_price,
            baseline_lead_days=action.offered_lead_days or 25,
            baseline_moq=b_moq,
            max_allowed_moq=max(1200, int(b_moq * 2)),
        )

    # [假資料修正 §5.5] 若對方**從未報出**任何模具費，就不該在 mandate 上主張
    # 「要求免除模具費」—— 否則下方的 BuyerScriptGenerator 會憑空向對方要求
    # 減免一筆對方從未開過的價（舊版即以寫死的 3000.0 運作）。
    if (inbound_classification.demanded_mold_fee or 0.0) <= 0.0:
        mandate_obj = mandate_obj.model_copy(
            update={"mold_fee_exemption_demanded": False}
        )

    # 判定是否觸發多維條款置換 (當對手不降價或哭窮抬價時)
    should_logroll = (
        opp_price is not None
        and opp_price > target_price * 1.05
        and current_round >= 2
    )

    if should_logroll:
        # [假資料修正 — 見 DEEP_DEFECT_AUDIT.md §5.5] 舊版把對方模具費寫死為
        # `mold_fee=3000.0`：系統毫不知情對方實際開價，就憑空發明一個「¥3000
        # 模具費」，再向對方宣稱「全額減免」—— 這是一筆由程式碼虛構出來的金錢承諾。
        # 現改為只採用**對方實際報出**的模具費（分類器由原文抽取）；對方沒提，
        # 就不許憑空主張一個金額。供應商沒報模具費時 mold_fee=0，
        # `demand_mold_waiver` 亦為 False，置換策略改為只用有依據的條款。
        supplier_mold_fee = inbound_classification.demanded_mold_fee or 0.0
        demand_mold_waiver = bool(
            mandate_obj.mold_fee_exemption_demanded and supplier_mold_fee > 0.0
        )
        supp_offer_obj = SupplierCurrentOffer(
            price=opp_price,
            lead_days=action.offered_lead_days or 25,
            deposit_pct=action.deposit_pct or 0.30,
            moq=offered_moq or sess.spec.moq,
            mold_fee=supplier_mold_fee,
            is_price_stagnant=True,
        )
        try:
            lr_res = LogrollingEngine.evaluate(mandate_obj, supp_offer_obj)
            best_prop = lr_res.best_recommendation
            logrolling_data = best_prop.model_dump()
            auto_reply_text = best_prop.buyer_script_zh
        except Exception:
            auto_reply_text = None
    else:
        auto_reply_text = None

    # 5.5 構建語言中立策略決策對象 (TacticalActionObject) 作為單一真相源
    if logrolling_data:
        t_mode = TacticalModeEnum.LOGROLL
        auth_price = min(logrolling_data["proposed_price"], ceiling_price)
        lead_days_val = logrolling_data["proposed_lead_days"]
        deposit_pct_val = logrolling_data["proposed_deposit_pct"]
        moq_val = logrolling_data["proposed_moq"]
        mold_exempt = logrolling_data.get("mold_fee_exempted", False)
        # [假資料修正] waive amount 一律取**對方實際報出的**模具費；
        # 舊版在這裡再寫死一次 3000.0，與上面發明的值連成一條虛構鏈。
        waive_mold_amount = float(supplier_mold_fee) if mold_exempt else 0.0
    else:
        t_mode = (
            TacticalModeEnum.LEAN_IN
            if p_break < 0.08
            else (TacticalModeEnum.CROSS_ANCHOR if p_break < 0.15 else TacticalModeEnum.SIDELINE)
        )
        auth_price = recommended_offer_price
        lead_days_val = action.offered_lead_days or 25
        deposit_pct_val = action.deposit_pct or 0.30
        moq_val = offered_moq or sess.spec.moq
        mold_exempt = False
        waive_mold_amount = 0.0

    action_obj = TacticalActionObject(
        schema_version=CURRENT_SCHEMA_VERSION,
        source_round_id=f"{session_id}_R{current_round}_{sess.spec.supplier_id}",
        authorized_price=auth_price,
        walk_away_ceiling=ceiling_price,
        breakdown_risk=p_break,
        expected_margin=rec.get("expected_margin_price", 0.0),
        concession_step=rec.get("concession_step", 0.0),
        posterior_reserve_mean=post_mean,
        posterior_reserve_std=post_std,
        bayesian_confidence=bayesian_confidence,
        estimated_floor_price=floor_price_est,
        tactical_mode=t_mode,
        product_name=sess.spec.product_name,
        supplier_contact_name="老闆",
        lead_days=lead_days_val,
        deposit_pct=deposit_pct_val,
        moq=moq_val,
        mold_fee_exempted=mold_exempt,
        waive_mold_fee_amount=waive_mold_amount,
        counterpart_intent=inbound_classification.primary_intent,
        counterpart_offered_price=opp_price,
        competing_lead_price=target_price * 1.05,
        circuit_tripped=False,
    )

    # 5.6 AST 不變量硬護欄驗證單一真相源。
    # [REGRESSION] 舊版直接呼叫、無 try/except：任何違反授權不變量的輸入都會讓
    # GuardrailViolationError 穿出路由變成 HTTP 500（並附帶內部 traceback）。
    # 安全護欄應「延後/阻斷」請求，而不是讓端點崩潰。違規時改為以確定性的
    # 熔斷回應（circuit_tripped=True）回報，並保留 pending_human_approval，
    # 讓人類決定下一步 —— 既不 500，也不解除安全機制。
    try:
        ActionObjectASTGuardrail.verify(action_obj)
    except GuardrailViolationError as gv:
        logger.warning("Round guardrail violation for session %s: %s", session_id, gv)
        guard_reason = f"Guardrail invariant violation: {gv}"
        if sess.state != AutopilotState.CIRCUIT_TRIPPED_ESCALATED:
            try:
                sess = session_mgr.transition_state(
                    session_id, AutopilotState.CIRCUIT_TRIPPED_ESCALATED, reason=guard_reason
                )
            except Exception as _trip_err:
                logger.warning(
                    "Circuit-breaker transition failed for session %s after guardrail "
                    "violation: %s (the trip is still reported in the response)",
                    session_id, _trip_err,
                )
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": True,
            "pending_human_approval": True,
            "trip_reason": guard_reason,
            "guardrail": {"passed": False, "reason": str(gv)},
            "recommendation": rec,
            "proposal": prop,
            "action_object": None,
            "reply_text": None,
            "tco_breakdown": tco_breakdown,
            "effective_opp_price": effective_opp_price,
        }

    # 5.7 雙向表面渲染
    # 中文表面渲染 (面向供應商對話)：沿海地道行話，徹底消除外商溢價
    auto_reply_text = ChineseSurfaceRenderer.render(action_obj)

    # 文本層數值護欄複核 (防止任何渲染偏差)
    try:
        NumericGuardrailValidator.validate_reply(
            auto_reply_text,
            authorized_price=auth_price,
            walk_away_ceiling=ceiling_price,
        )
    except GuardrailViolationError:
        auto_reply_text = f"經核算，我方首批訂單確定意向價為 ¥{auth_price:.2f}，現款現結走官方系統，請確認排單。"

    # [REGRESSION] 英文高管決策卡過去**完全未經護欄**，而它正是被推播到主管手機、
    # 主管據以授權的產物。w2-guardrails 提供了 validate_bilingual(zh, en, ...) 雙表面
    # 校驗器；存在時用它一次驗證中英文兩面，否則退回既有的單面 validate_reply。
    # 任一表面違規即同時處置兩面：中文換安全模板，英文卡換成不含價格承諾的
    # 安全文字（避免「壞價格」被送到主管手機上授權）。
    executive_card_en = EnglishExecutiveRenderer.render_executive_card(action_obj)
    english_card_unverified = False
    validate_bilingual = getattr(NumericGuardrailValidator, "validate_bilingual", None)
    try:
        if callable(validate_bilingual):
            validate_bilingual(
                auto_reply_text,
                executive_card_en,
                authorized_price=auth_price,
                walk_away_ceiling=ceiling_price,
            )
        else:
            # 後備：至少對英文卡套用既有的數值上限校驗。
            NumericGuardrailValidator.validate_reply(
                executive_card_en,
                authorized_price=auth_price,
                walk_away_ceiling=ceiling_price,
            )
    except GuardrailViolationError as gv:
        logger.warning("Bilingual surface guardrail violation for session %s: %s", session_id, gv)
        english_card_unverified = True
        auto_reply_text = f"經核算，我方首批訂單確定意向價為 ¥{auth_price:.2f}，現款現結走官方系統，請確認排單。"
        executive_card_en = (
            "GUARDRAIL REVIEW REQUIRED — the rendered executive card failed numeric "
            f"validation and is withheld. Authorized price: ¥{auth_price:.2f} "
            f"(ceiling ¥{ceiling_price:.2f}). Approve or override via /approve."
        )

    # 英文高管決策卡與遙測摘要 (面向西方採購主管 / CPO 儀表板)
    telemetry_eng = EnglishExecutiveRenderer.render_telemetry_summary(action_obj)

    # 5.8 v5 Should-Cost 與校準貝氏混合推斷增強
    v2_belief = robust_bayes_update(
        prior_mean=sess.spec.prior_reserve_mean,
        prior_std=sess.spec.prior_reserve_std,
        observed_offer=opp_price / target_price if opp_price else post_mean,
        our_next_offer=auth_price / target_price if auth_price else None,
    )
    # 6. 分流：採購決策副駕駛 (Copilot) vs 全自主巡航 (Autopilot)
    # [REGRESSION] 舊版預設 mode="autopilot"（現在改為 copilot），且 autopilot
    # 分支會在**零人工核可**下一輪就進入終止態 DEAL_CLOSED，並回傳一段可直接
    # 複製發送的成交確認話術（pending_human_approval=False）。這違反 README 的
    # 「系統不代發要約 —— 締約權必須保留在人類手上」。
    # 現在採購模式一律停在等待人類決策；只有呼叫端「明確」要求 autopilot 時才
    # 走自駕分支，且該分支即使判定成交也不直接進入 DEAL_CLOSED。
    if not action.is_autopilot():
        if sess.state != AutopilotState.AWAITING_HUMAN_DECISION:
            sess = session_mgr.transition_state(
                session_id,
                AutopilotState.AWAITING_HUMAN_DECISION,
                reason="Copilot decision radar prepared; awaiting human buyer review and authorization.",
            )
        return {
            "session": sess,
            "autopilot_state": sess.state.value,
            "circuit_tripped": False,
            "pending_human_approval": True,
            "turn": None,
            "inbound_intent": inbound_classification.primary_intent.value,
            "tco_breakdown": tco_breakdown,
            "logrolling_proposal": logrolling_data,
            "effective_opp_price": effective_opp_price,
            "telemetry_summary_en": telemetry_eng,
            "executive_card_en": executive_card_en,
            "executive_card_unverified": english_card_unverified,
            "action_object": action_obj.model_dump(),
            "recommendation": rec,
            "proposal": prop,
            "recommended_offer_price": auth_price,
            "reply_text": auto_reply_text,
            "bayesian_belief_state": v2_belief.model_dump(),
        }

    # ---- Autopilot 巡航模式分支（僅在呼叫端明確要求時）----
    if sess.state == AutopilotState.IDLE:
        sess = session_mgr.transition_state(session_id, AutopilotState.AUTOPILOT_CRUISING)

    is_deal_closed = False
    awaiting_human_approval = False
    if opp_price is not None:
        if opp_price <= target_price * 1.02:
            is_deal_closed = True
        elif current_round >= sess.spec.max_rounds and opp_price <= ceiling_price and abs(opp_price - recommended_offer_price) <= (0.02 * target_price):
            is_deal_closed = True

    if is_deal_closed and sess.state != AutopilotState.DEAL_CLOSED:
        # [REGRESSION] autopilot 判定成交時**不**進入 DEAL_CLOSED。它產生一份
        # 「待人類核可」的成交建議（awaiting_human_approval=True），實際締約仍由
        # /approve 執行。因此 pending_human_approval 不再是誤導性的 False。
        awaiting_human_approval = True
        try:
            sess = session_mgr.transition_state(
                session_id,
                AutopilotState.AWAITING_HUMAN_DECISION,
                reason="Autopilot detected a deal candidate; awaiting human buyer authorization before closing.",
            )
        except (InvalidStateTransitionError, OptimisticLockConflictError) as _st_err:
            logger.warning(
                "Could not park autopilot deal candidate for session %s at "
                "AWAITING_HUMAN_DECISION: %s", session_id, _st_err,
            )
        notifier.notify_deal_closed(
            session_id=session_id,
            product=sess.spec.product_name,
            final_price=opp_price,
            savings_pct=(ceiling_price - opp_price) / ceiling_price,
            rounds=current_round,
        )
        # [REGRESSION] 舊版 `except Exception: pass` 會靜默吞掉長達 60 秒的
        # Evolus 外呼失敗，讓已成交的會話從未被公告。現在記錄例外。
        if os.getenv("EVOLUS_API_KEY") and os.getenv("EVOLUS_APP_ID"):
            try:
                from ..evolus.client import EvolusClient
                evolus_c = EvolusClient()
                deal_summary = (
                    f"Procurement Deal Closed: {sess.spec.product_name} at ¥{opp_price:.2f} "
                    f"(Target: ¥{target_price:.2f}, MOQ: {sess.spec.moq}). "
                    f"Completed in {current_round} rounds with total savings of "
                    f"{((ceiling_price - opp_price) / ceiling_price):.1%}."
                )
                evolus_c.run_email_tool(notes=deal_summary, tone="professional")
            except Exception as _ev_err:
                logger.warning(
                    "Deal-closed Evolus email notification failed for session %s: %s "
                    "(the deal candidate is still reported in the response)",
                    session_id, _ev_err,
                )

    turn_our = NegotiationTurn(
        round_index=current_round,
        sender="buyer_agent",
        raw_text=auto_reply_text,
        parsed_price=auth_price,
        p_break=p_break,
        posterior_mean=post_mean,
        posterior_std=post_std,
        logrolling_proposal=logrolling_data,
    )
    sess = session_mgr.append_turn(session_id, turn_our)

    return {
        "session": sess,
        "autopilot_state": sess.state.value,
        "circuit_tripped": False,
        # [REGRESSION] 舊版固定回 False —— 即使 autopilot 已宣告成交。現在誠實
        # 反映「成交候選仍在等人類核可」。
        "pending_human_approval": awaiting_human_approval,
        "awaiting_human_approval": awaiting_human_approval,
        "turn": turn_our,
        "inbound_intent": inbound_classification.primary_intent.value,
        "tco_breakdown": tco_breakdown,
        "logrolling_proposal": logrolling_data,
        "effective_opp_price": effective_opp_price,
        "telemetry_summary_en": telemetry_eng,
        "executive_card_en": executive_card_en,
        "executive_card_unverified": english_card_unverified,
        "action_object": action_obj.model_dump(),
        "recommendation": rec,
        "proposal": prop,
        "recommended_offer_price": auth_price,
        "reply_text": auto_reply_text,
        "bayesian_belief_state": v2_belief.model_dump(),
    }


# ---- Web Portal & 多供應商逆向拍賣模擬端點 ----
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from ..web.routes_web import api_v1 as web_api_v1_router, router as web_open_router
from ..engine.multi_solver import simulate_reverse_auction

static_path = Path(__file__).parent.parent / "web" / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# 先把 /api/v1 認證 router 註冊進 app，再掛上 web 的 /api/v1 子路由。
# [REGRESSION] 舊版逐條端點掛門禁，因此 routes_web.py 內的四條 /api/v1 路由
# （stream / config / test-push）完全沒被 gate。現在全部由帶依賴的 router 承接。
app.include_router(api_v1)
app.include_router(web_api_v1_router)
# 開放路由：儀表板 HTML（GET /）與刻意豁免的 SSE 串流。
app.include_router(web_open_router)


@api_v1.post("/multi-auction/run")
def api_run_multi_auction(body: Optional[MultiAuctionRunRequest] = None):
    """觸發多供應商逆向拍賣。

    不傳報價時，改為執行動態多 Agent 自博弈競價（各家對抗 Agent 依據數學模型
    逐輪即時算出報價）；亦可傳入自定義供應商名單與逐輪報價矩陣。
    """
    try:
        req = body or MultiAuctionRunRequest()
        supp_list = [s.model_dump() for s in req.suppliers] if req.suppliers else None
        res = simulate_reverse_auction(
            target_price=req.target_price,
            walk_away_ceiling=req.walk_away_ceiling,
            moq=req.moq,
            n_samples=req.n_samples,
            suppliers=supp_list,
            rounds_quotes=req.rounds_quotes,
            n_rounds=req.n_rounds,
        )
        return res
    except Exception:
        # [REGRESSION] 舊版 detail=str(exc)：numpy 的配置錯誤訊息（例如
        # "Unable to allocate 59.6 GiB"）會直接洩漏主機能力給呼叫端。
        logger.exception("multi-auction run failed")
        raise HTTPException(status_code=500, detail="Reverse auction simulation failed.")


# ---- 認證覆蓋自我檢查 (Fail-fast route audit) ----
# [REGRESSION] 舊版的門禁是逐條裝飾，且審計發現 routes_web.py 與 include_router
# 之後定義的路由完全沒被覆蓋。這裡在 import 時就驗證「每一條 /api/v1 路由都
# 真的掛到了 require_api_key」，未來任何人新增 /api/v1 路由卻忘了掛依賴，
# 都會在啟動/測試時直接失敗，而不是靜默裸奔。
_EXEMPT_API_V1_PATHS = {"/api/v1/stream/session"}


def _iter_api_routes(routes=None):
    """遞迴走訪 app 路由，展開 include_router 的巢狀結構。

    [REGRESSION] FastAPI 0.141 會把 include_router 的結果包成 `_IncludedRouter`
    而不再把子路由攤平進 app.routes。舊的平鋪掃描因此看不到 routes_web.py 裡
    的 /api/v1 路由 —— 正是「新增路由忘了掛門禁」的盲點。這裡一律展開。
    """
    from fastapi.routing import APIRoute

    for route in (app.routes if routes is None else routes):
        if isinstance(route, APIRoute):
            yield route
        else:
            # _IncludedRouter / Mount / 其他容器：展開其原始 router 的子路由。
            sub = getattr(route, "routes", None)
            if sub is None:
                original = getattr(route, "original_router", None)
                sub = getattr(original, "routes", None) if original is not None else None
            if sub:
                yield from _iter_api_routes(sub)


def _assert_api_v1_auth_coverage() -> None:
    ungated = []
    for route in _iter_api_routes():
        path = route.path
        if not path.startswith("/api/v1") or path in _EXEMPT_API_V1_PATHS:
            continue
        gated = False
        for dep in getattr(route, "dependencies", []) or []:
            fn = getattr(dep, "dependency", None)
            if fn is require_api_key or getattr(fn, "__name__", None) == require_api_key.__name__:
                gated = True
                break
        if not gated:
            ungated.append((sorted(route.methods), path))

    if ungated:
        raise RuntimeError(
            "Unprotected /api/v1 routes detected: "
            + "; ".join(f"{','.join(m)} {p}" for m, p in ungated)
            + ". Every /api/v1 route must be defined on a router created with "
            "dependencies=[Depends(require_api_key)]."
        )


_assert_api_v1_auth_coverage()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

