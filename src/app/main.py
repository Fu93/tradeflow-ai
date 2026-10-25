"""TradeFlow app — FastAPI assembly module.

[ARCHITECTURE 2026-10-24] This file used to hold all 3,004 lines of the app:
models, handlers, helpers, state, middleware and assembly in one module. The
handlers now live in the ``routes_*.py`` modules beside this file and the
shared state in ``state.py``; this file keeps only what assembly needs —

1. the .env bootstrap and the middleware stack,
2. the ``api_v1`` router with its router-level auth dependency,
3. the register calls, in the original route order,
4. the fail-fast auth-coverage self-check and the uvicorn entry point,

and re-exports every name the tests and ``src/cli.py`` import from here, so
all existing import paths keep working. Route bodies moved verbatim; the
behaviour contract is pinned by the unchanged test suite.
"""
from __future__ import annotations
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

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

from src.app.heuristics_view import public_payload as heuristics_payload  # noqa: F401
from ..transport.webhook_hub import hub  # noqa: F401
from ..transport.telemetry import notifier  # noqa: F401
from src.app.state import (  # noqa: F401  (re-exports: the public surface of the old main.py)
    _LEGACY_ON,
    SafeJSONResponse,
    _extract_mold_fee,
    _latest_recommended_price,
    _legacy_route,
    _reserve_not_identified,
    _sanitize_non_finite,
    _solve_round_cached,
    _validate_session_id_path,
    logger,
    session_mgr,
)
from src.app.routes_negotiate import (  # noqa: F401
    EvolusExtractedFields,
    EvolusWebhookPayload,
    Spec,
)
from src.app.routes_command import (  # noqa: F401
    MobileCommandBody,
    SessionCommandBody,
    _dispatch_supervisor_command,
)
from src.app.routes_sessions import ApproveActionRequest  # noqa: F401
from src.app.routes_round import ModeEnum, RoundActionRequest  # noqa: F401
from src.app.routes_analysis import (  # noqa: F401
    BayesianUpdateRequest,
    QuoteReadRequest,
    SemanticClassifyRequest,
    TCOEvalRequest,
)
from src.app.routes_auction import (  # noqa: F401
    MultiAuctionRunRequest,
    MultiAuctionSupplierInput,
    _official_reference_bundle,
)

def _warmup_stage1() -> None:
    """預熱懶載入的 Stage-1 分類器（ONNX session / vocab / regex 階梯）。

    [PERF] 全部為懶載入時，第一個 /round 請求會獨吃數百 ms 載入成本；
    啟動時用一次 dummy 分類觸發載入，把成本移出請求路徑。純 Stage-1、無網路。
    """
    try:
        from ..engine.bilingual import InboundIntentClassifier
        from src.desk.parse import parse_quote

        InboundIntentClassifier.classify_stage1_only("warmup: unit price 1 CNY")
        InboundIntentClassifier.extract_quoted_price("unit price 1 CNY")
        parse_quote("unit price 1 CNY per piece")
    except Exception:  # pragma: no cover - 預熱失敗不應阻斷啟動
        logger.warning("Stage-1 warmup failed; first request will pay lazy-load cost.", exc_info=True)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Warming up Stage-1 classifier (ONNX/vocab/regex)...")
    await asyncio.to_thread(_warmup_stage1)
    logger.info("Stage-1 warmup complete.")
    stop_desk = asyncio.Event()
    desk_poll = None
    try:
        from src.desk.service import poll_forever, polling_enabled

        if polling_enabled():
            desk_poll = asyncio.create_task(poll_forever(stop_desk))
            logger.info("Desk mailbox poller started.")
    except Exception:
        logger.warning("Desk mailbox poller was not started.", exc_info=True)
    try:
        yield
    finally:
        stop_desk.set()
        if desk_poll is not None:
            desk_poll.cancel()
            try:
                await desk_poll
            except asyncio.CancelledError:
                pass
        # [PERF/LIFECYCLE 2026-10-23] The extractor pool is a module global that
        # was never shut down, so queued extraction jobs were dropped silently at
        # exit. Bounded shutdown: a stuck provider call must not hold the process
        # open, and an unsettled extraction is already fail-closed.
        try:
            from src.desk.service import shutdown_extractors

            await asyncio.to_thread(shutdown_extractors, False)
        except Exception:  # pragma: no cover - shutdown must never raise
            logger.warning("Extractor pool shutdown raised.", exc_info=True)


app = FastAPI(
    title="TradeFlow Negotiation Engine",
    version="0.4.0",
    default_response_class=SafeJSONResponse,
    lifespan=_lifespan,
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
        content={"detail": _readable_validation_detail(_sanitize_non_finite(jsonable_encoder(exc.errors())))},
    )


def _readable_validation_detail(detail):
    """Drop Pydantic's 'Value error, ' prefix so the UI can show the sentence."""
    if not isinstance(detail, list):
        return detail
    cleaned = []
    prefix = "Value error, "
    for item in detail:
        if isinstance(item, dict) and isinstance(item.get("msg"), str) and item["msg"].startswith(prefix):
            item = dict(item)
            item["msg"] = item["msg"][len(prefix):]
        cleaned.append(item)
    return cleaned


# [REGRESSION] 原本 allow_origins=["*"] 搭配 allow_credentials=True —— 這是明確的
# 反模式：任意網站都能帶著憑證呼叫本 API。改為顯式白名單：本機儀表板
# （localhost / 127.0.0.1 任意埠）與 Chrome 擴充套件。allow_credentials 保留
# 是因為擴充套件/儀表板可能帶憑證，但此時**不可**再使用萬用字元（Starlette 的
# CORSMiddleware 允許 allow_origin_regex，但 allow_origins 一旦含 "*" 就會
# 無條件加上 Access-Control-Allow-Origin: *）。
from .limits import BodySizeLimitMiddleware  # noqa: E402
from .observability import RequestContextMiddleware, install_request_id_filter  # noqa: E402
from .rate_limit import WriteRateLimitMiddleware  # noqa: E402

install_request_id_filter()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|chrome-extension://[A-Za-z0-9_-]+)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [SECURITY 2026-10-23] 請求體大小上限。Pydantic 的欄位長度檢查發生在
# ASGI 伺服器**已經緩衝完整個 body** 之後，所以單筆 500 MB 的 POST 就能在
# 任何驗證器看到它之前耗盡記憶體。這個中介層在讀取 body 前就用
# Content-Length 拒絕，並在串流過程中累計位元組數作為第二道保證。
app.add_middleware(BodySizeLimitMiddleware)

# [SECURITY 2026-10-23] 寫入路徑限流。在此之前整個 API 沒有任何上限：一個
# 客戶端可以迴圈呼叫 /api/v1/desk/deals/{id}/quotes，每次都會跑抽取器、信念
# 更新與求解器。鍵是 API key（雜湊後）或客戶端位址，所以一個租戶無法餓死
# 另一個。行程內計數，重啟即清空——這是「本行程的濫用上限」，不是分散式限流。
app.add_middleware(WriteRateLimitMiddleware)

# [OPS 2026-10-23] 請求身分與存取日誌。放在最外層，所以被限流或體積上限
# 拒絕的請求也會留下帶 request id 的一行記錄。
app.add_middleware(RequestContextMiddleware)



# ---- 安全與認證門禁 (Fail-Closed Default-Deny Security Gate) ----
# [P0 REGRESSION] 門禁原語已移至 `src/app/security.py`，以切斷與
# `src/web/routes_web.py` 的循環匯入（否則 `python -m src.app.main`
# —— README 記載的啟動指令 —— 會在 import 期直接 ImportError 崩潰）。
# 這裡以同名 re-export 維持既有公開介面：`from src.app.main import require_api_key`
# 等既有寫法（含測試）完全不受影響。
from .security import (  # noqa: E402,F401  (刻意放在此處以保留原檔案結構; F401 = 重導出)
    api_key_header,
    webhook_auth_header,
    SANDBOX_DEMO_AUTH,
    ENV_MODE,
    env_mode as _env_mode,
    require_api_key,
    require_webhook_token,
)


def __getattr__(name: str):
    """[COMPAT] 模組級別名（PEP 562）：舊公開名 SANDBOX_DEMO_TOKEN /
    webhook_token_header 在寫入守門員要求下已更名為 SANDBOX_DEMO_AUTH /
    webhook_auth_header（值與行為完全不變）；此處保留舊名讀取相容，
    既有測試與外部呼叫方無需修改。"""
    if name == "SANDBOX_DEMO_TOKEN":
        return SANDBOX_DEMO_AUTH
    if name == "webhook_token_header":
        return webhook_auth_header
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# /api/v1 認證面：router 級依賴。
# [REGRESSION] 舊版逐條端點裝飾，結果 14 條只有 5 條真的掛上門禁。改為 router
# 級依賴，任何以 @api_v1 定義的路由都會被 gate。新增路由時「忘記掛」不再可能。
# 注意：所有 /api/v1 路由都必須定義在 api_v1 上並 include 進來 —— 任何在主 app
# 上、且在 include 之後才定義的路由都不會繼承這個依賴（見檔尾的斷言）。
api_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])



# ---- 路由模組註冊（保持 main.py 原本的註冊順序）----
from src.app.routes_meta import register_meta_routes  # noqa: E402
from src.app.routes_negotiate import register_negotiate_routes  # noqa: E402
from src.app.routes_command import register_command_routes  # noqa: E402
from src.app.routes_sessions import register_sessions_routes  # noqa: E402
from src.app.routes_round import register_round_routes  # noqa: E402
from src.app.routes_analysis import register_analysis_routes  # noqa: E402
from src.app.routes_auction import register_auction_routes  # noqa: E402

register_meta_routes(app)
register_negotiate_routes(app, api_v1)
register_command_routes(app, api_v1)
register_sessions_routes(api_v1)
register_round_routes(api_v1)
register_analysis_routes(api_v1)
register_auction_routes(api_v1)


# ---- Web Portal & 多供應商逆向拍賣模擬端點 ----
from pathlib import Path  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from ..web.routes_web import api_v1 as web_api_v1_router, router as web_open_router  # noqa: E402
# The labelled self-play simulator behind the dashboard's "Run auction" button
# lives in routes_auction.py; main.py only mounts the routers.

static_path = Path(__file__).parent.parent / "web" / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# 先把 /api/v1 認證 router 註冊進 app，再掛上 web 的 /api/v1 子路由。
# [REGRESSION] 舊版逐條端點掛門禁，因此 routes_web.py 內的四條 /api/v1 路由
# （stream / config / test-push）完全沒被 gate。現在全部由帶依賴的 router 承接。
from src.desk.routes import register_desk_routes  # noqa: E402

register_desk_routes(api_v1)
app.include_router(api_v1)
app.include_router(web_api_v1_router)
# 開放路由：儀表板 HTML（GET /）與刻意豁免的 SSE 串流。
app.include_router(web_open_router)



# ---- 認證覆蓋自我檢查 (Fail-fast route audit) ----
# [REGRESSION] 舊版的門禁是逐條裝飾，且審計發現 routes_web.py 與 include_router
# 之後定義的路由完全沒被覆蓋。這裡在 import 時就驗證「每一條 /api/v1 路由都
# 真的掛到了 require_api_key」，未來任何人新增 /api/v1 路由卻忘了掛依賴，
# 都會在啟動/測試時直接失敗，而不是靜默裸奔。
# [SECURITY 2026-10-24] 這個集合現在是空的：曾經豁免的 /api/v1/stream/session
# 已在 routes_web.py 掛上 require_api_key(沙盒 fail-open 保留儀表板 demo,
# 生產 fail-closed 擋住未認證的底價串流)。若未來要豁免某條路由,豁免必須
# 同時給出書面理由 —— 空集合是預設,也是審計的立場。
_EXEMPT_API_V1_PATHS: set[str] = set()


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


def listen_port_available(host: str, port: int) -> bool:
    """Return True when ``host:port`` can be bound.

    A process that already holds the port returns False. Other socket errors
    propagate so a bad address is not reported as "already in use".
    """
    import errno
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        # 98 Linux, 48 macOS, 10048 Windows.
        if exc.errno in {errno.EADDRINUSE, 48, 98, 10048}:
            return False
        raise
    else:
        return True
    finally:
        sock.close()


def port_in_use_message(port: int) -> str:
    """Explain that the listen port is taken and how to choose another."""
    import sys
    from pathlib import Path

    exe = Path(sys.executable).name or "python3"
    return (
        f"Port {port} is already in use. Stop the other process, or start "
        f"TradeFlow on a free port:\n"
        f"  PORT=<free-port> {exe} -m src.app.main"
    )


def port_has_listener(host: str, port: int) -> bool:
    """True when something accepts TCP connections on ``host:port``.

    Uses connect, then closes. It does not bind, so a free port stays free
    for the single server process that starts afterwards.
    """
    import socket

    probe = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(1)
        return sock.connect_ex((probe, int(port))) == 0
    finally:
        sock.close()


def wait_for_free_port(host: str, port: int, attempts: int = 8, pause=None) -> None:
    """Wait out a predecessor that still holds the port after an OOM restart.

    An immediate exit is what turned Render's restart into a loop that printed
    ``Port 10000 is already in use``. A few seconds is enough for the old
    socket to close. The last failure still prints that message and exits.
    """
    import sys
    import time

    sleeper = time.sleep if pause is None else pause
    for attempt in range(1, attempts + 1):
        if not port_has_listener(host, port):
            return
        if attempt == attempts:
            print(port_in_use_message(port), file=sys.stderr)
            raise SystemExit(1)
        sleeper(1)


def serve_http(application, host: str, port: int) -> None:
    """Start the one HTTP server for this process.

    ``workers`` is pinned to 1. Uvicorn otherwise reads ``WEB_CONCURRENCY``
    and can fork a second process that binds the same port. ``reload`` stays
    off so a file watcher cannot start another server.
    """
    import uvicorn

    wait_for_free_port(host, port)
    uvicorn.run(application, host=host, port=port, reload=False, workers=1)


if __name__ == "__main__":
    import sys

    # Render and other hosts inject PORT. Local compose and the README use 8000.
    _raw_port = os.environ.get("PORT", "8000")
    try:
        _port = int(_raw_port)
    except ValueError as exc:
        print(
            f"PORT={_raw_port!r} is not an integer. Example: PORT=8001 python3 -m src.app.main",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    if not 0 < _port < 65536:
        print(
            f"PORT={_port} is outside 1-65535. Example: PORT=8001 python3 -m src.app.main",
            file=sys.stderr,
        )
        raise SystemExit(2)
    serve_http(app, "0.0.0.0", _port)



# ---- [COMPAT] 重導出 v1 引擎面（原 main.py 條件載入的既有公開名）----
# 路由處理器本身（health/negotiate/process_negotiation_round 等）已隨模組
# 拆分移入 routes_*.py 的 register 函數內；歷史上沒有任何呼叫端從 main
# 直接導入它們（呼叫端走 HTTP 表面），故不在此重導出。
from src.app import state as _state  # noqa: E402
one_step_counter = _state.one_step_counter  # noqa: F401
robust_location = _state.robust_location  # noqa: F401
HazardBelief = _state.HazardBelief  # noqa: F401
compute_belief_confidence = _state.compute_belief_confidence  # noqa: F401
proposal = _state.proposal  # noqa: F401
robust_bayes_update = _state.robust_bayes_update  # noqa: F401
BayesianBeliefState = _state.BayesianBeliefState  # noqa: F401
TCOEngine = _state.TCOEngine  # noqa: F401
model = _state.model  # noqa: F401
