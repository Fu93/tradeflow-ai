"""TradeFlow Web Dashboard — FastAPI routers.

Mounts at / and provides:
  GET  /                         -> dark FinTech dashboard HTML
  GET  /api/v1/stream/session    -> SSE live session feed
  GET  /api/v1/config/telemetry  -> read current telemetry config
  POST /api/v1/config/telemetry  -> write + hot-reload telemetry config
  POST /api/v1/telemetry/test-push -> fire a one-off test notification

[SECURITY] The /api/v1 surface here is served by `api_v1`, an APIRouter created with
`dependencies=[Depends(require_api_key)]` (imported from src.app.main). Every route
on it is authenticated. `GET /api/v1/stream/session` carries the same dependency on
its own decorator: a browser `EventSource` cannot attach custom headers, and
`require_api_key` is fail-open in the sandbox demo — so the dashboard feed keeps
working there — while production, where target prices and walk-away ceilings must
not stream to whoever reaches the port, refuses unauthenticated subscribers.
main.py's route audit no longer exempts it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

# [P0 REGRESSION] 這裡原本是 `from ..app.main import require_api_key`，與
# main.py 檔尾的 `from ..web.routes_web import ...` 形成互相 import 的循環。
# 以 `python -m src.app.main`（README 記載的啟動指令）執行時，main 的身分是
# `__main__`，`src.app.main` 不在 sys.modules 中，routes_web 的這行匯入會觸發
# 半初始化模組的重新載入並拋 ImportError，使服務完全無法啟動。
# 現改為從不依賴 app/web 的 security 模組匯入，循環徹底切斷。
from ..app.security import require_api_key

logger = logging.getLogger("tradeflow.web")

_HERE = Path(__file__).parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"

# [REGRESSION — 見 DEEP_DEFECT_AUDIT.md §6.4 的同類問題]
# 舊版把 `.env` 路徑寫死為 `Path(".env")`，因此**任何測試或工具呼叫
# telemetry 設定端點時都會改寫真實的 `.env`** —— 這在本次修復期間實際發生過
# （`TELEGRAM_CHAT_ID=100200300` 被寫進 repo 的真實 .env）。
# 現改為可經 `TRADEFLOW_ENV_PATH` 覆寫，讓測試能把寫入導向暫存檔；
# 預設值不變，維運行為不受影響。
def _env_path() -> Path:
    """回傳 .env 的實際路徑；可由 TRADEFLOW_ENV_PATH 覆寫（供測試隔離）。"""
    override = os.getenv("TRADEFLOW_ENV_PATH")
    return Path(override) if override else Path(".env")


# 開放路由：僅儀表板 HTML 與明確豁免的 SSE 串流。
router = APIRouter()

# 認證路由：本檔的 /api/v1 端點全部掛在帶門禁的 router 上。
# 這裡建立獨立 router（而非重用 main 的 api_v1 物件），否則對同一個 router
# 呼叫兩次 include_router 會把整組路由註冊兩次。
api_v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@router.get("/desk", response_class=HTMLResponse, include_in_schema=False)
async def live_desk():
    html = (TEMPLATES_DIR / "desk.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


_FAVICON_PATH = Path(__file__).resolve().parents[2] / "extension" / "icons" / "icon48.png"


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers request this path even when the page sets a data-URI icon."""
    if not _FAVICON_PATH.is_file():
        return Response(status_code=204)
    return Response(content=_FAVICON_PATH.read_bytes(), media_type="image/png")


# ---------------------------------------------------------------------------
# SSE live session feed
# ---------------------------------------------------------------------------

async def _session_event_generator(request: Request, max_seconds: float | None = None):
    """Push session state snapshots every 1 second via Server-Sent Events.

    [REGRESSION] This loop used to wrap the whole collection step in
    `except Exception: pass`. Any read failure was therefore invisible: the
    dashboard rendered "sessions: []", indistinguishable from "there are no
    sessions", and the operator could not tell the feed was broken. Errors are
    now logged and surfaced as an `error` field in the SSE payload so the
    dashboard can show a degraded state, and the stream stays alive.

    The stream is also bounded by `max_seconds` (idle/lifetime cap) and always
    runs its cleanup in a `finally` block — previously it had no timeout and no
    teardown, so each hung EventSource kept a SQLite-backed SessionManager and
    a 1s polling loop alive forever.
    """
    # [REGRESSION] 舊版每個 SSE 連線都自建一個 SessionManager（各自開一條
    # SQLite 連線），而且它讀的是預設路徑 —— 在有 TRADEFLOW_DB_PATH/測試隔離
    # 的情況下會讀到「另一個」資料庫，前端永遠看到空清單。改為共用 app 的
    # 單例 manager，與 REST 端點看到完全相同的資料。
    import src.app.main as _main  # type: ignore
    mgr = _main.session_mgr

    # [P1 AUDIT FIX — 2026-10-17] 生命週期上限原本硬寫 300 秒。Starlette 1.7 的
    # TestClient 會把整個 streaming response 緩衝到串流「結束」才回傳，因此任何
    # 對 SSE 端點的 TestClient 測試都會靜默等待 300 秒（看起來像當機，且整套
    # 測試因此多花 ~10 分鐘）。改為可用環境變數設定，預設值不變；測試可調成
    # 1 秒以快速驗證首幀語義。
    if max_seconds is None:
        try:
            max_seconds = float(os.getenv("TRADEFLOW_SSE_MAX_SECONDS", "300"))
        except ValueError:
            max_seconds = 300
    started = time.monotonic()
    try:
        while True:
            if await request.is_disconnected():
                break
            if time.monotonic() - started > max_seconds:
                # 生命週期上限：請前端重連（EventSource 會自動重試），
                # 避免單一連線無限期佔用輪詢資源。
                payload = json.dumps({
                    "ts": time.time(), "sessions": [],
                    "error": f"stream lifetime limit ({max_seconds:.0f}s) reached; reconnect",
                })
                yield f"data: {payload}\n\n"
                break

            sessions = []
            stream_error = None
            try:
                # [PERF 2026-10-24] list_sessions() is synchronous SQLite.
                # Calling it on the event loop every second meant N open
                # dashboards blocked EVERY request for the duration of each
                # read; to_thread keeps the loop responsive. The fixture
                # SessionManager used in tests is equally safe to call from
                # a worker thread (its own connection per call).
                for s in await asyncio.to_thread(mgr.list_sessions):
                    if s:
                        sessions.append({
                            "session_id": s.spec.session_id,
                            "state": s.state.value,
                            "product": s.spec.product_name,
                            "target": s.spec.target_price,
                            "ceiling": s.spec.ceiling_price,
                            "rounds": len(s.history),
                            "last_p_break": s.history[-1].p_break if s.history else None,
                            "last_posterior": s.history[-1].posterior_mean if s.history else None,
                        })
            except Exception as exc:
                # 絕不吞錯：記錄 + 明確標示此幀失敗，讓操作者能區分
                # 「真的沒有會話」與「讀取失敗」。
                logger.exception("SSE session snapshot collection failed")
                # [SECURITY 2026-10-24] The type name is enough for an operator
                # with the logs; the message text can carry internal paths and
                # this frame reaches unauthenticated subscribers.
                stream_error = f"{type(exc).__name__} (see server logs)"

            payload_obj: Dict[str, Any] = {"ts": time.time(), "sessions": sessions}
            if stream_error:
                payload_obj["error"] = stream_error
            yield f"data: {json.dumps(payload_obj)}\n\n"
            await asyncio.sleep(1.0)
    finally:
        # 明確釋放（SessionManager 內部為每次查詢自建連線，這裡只是終止輪詢）。
        logger.debug("SSE session stream closed after %.1fs", time.monotonic() - started)


# [SECURITY 2026-10-24] The stream is gated by require_api_key, which is the
# same split every /api/v1 route uses: fail-open with a loud warning in the
# sandbox demo (where the open dashboard needs it and EventSource cannot
# attach headers), fail-closed in production — where target prices, walk-away
# ceilings and posteriors must not stream to anyone who can reach the port.
@router.get("/api/v1/stream/session", dependencies=[Depends(require_api_key)])
async def stream_session(request: Request):
    return StreamingResponse(
        _session_event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Telemetry config read / write / hot-reload
# ---------------------------------------------------------------------------

def _read_env_config() -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    env_path = _env_path()
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def _sanitize_env_value(value: str) -> str:
    """One .env line per key, no exceptions.

    [SECURITY 2026-10-24] A value containing a newline used to be written
    verbatim, so ``{"TELEGRAM_CHAT_ID": "x\\nTRADEFLOW_API_KEY=owned"}`` grew
    the .env file a brand-new key — and python-dotenv's last-occurrence-wins
    turned that into a full credential takeover on the next start. Values are
    restricted to printable, line-free characters; anything else is dropped
    character by character, so an injected line becomes part of the same line
    (or nothing) and can never start a new one.
    """
    return "".join(ch for ch in str(value) if ch.isprintable() and ch not in "\r\n\\\\").strip()


def _write_env_config(updates: Dict[str, str]) -> None:
    """Atomically merge updates into .env (creates file if absent).

    路徑由 `_env_path()` 決定（可經 TRADEFLOW_ENV_PATH 覆寫）；`os.replace` 要求
    同磁區，故暫存檔寫在目標檔的同目錄下。
    """
    env_path = _env_path()
    cfg = _read_env_config()
    safe_updates = {
        k: _sanitize_env_value(v)
        for k, v in updates.items()
        if _sanitize_env_value(v) != ""
    }
    cfg.update(safe_updates)
    lines = [f"{k}={v}" for k, v in sorted(cfg.items())]
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, env_path)


def _is_production() -> bool:
    """生產模式判定（大小寫不敏感；未設定 = sandbox）。"""
    return os.getenv("TRADEFLOW_ENV", "sandbox").strip().lower() == "production"


_TELEMETRY_FIELDS = [
    # [修正] 舊版列出 CALLMEBOT_*/PUSHPLUS_*/SMTP_* 等 key，但
    # TelemetryNotifier.__init__ 實際只接受這三個通道的參數。沿用真實通道 key，
    # 避免「存了卻從未生效」的假配置。
    "WHATSAPP_PHONE", "WHATSAPP_APIKEY",
    "SERVERCHAN_SENDKEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
]


@api_v1.get("/config/telemetry")
async def get_telemetry_config():
    """讀取遙測設定狀態。已由 api_v1 router 的 require_api_key 認證。

    [REGRESSION] 舊版回傳 `val[:4] + "..."`，等於洩漏每個憑證的前 4 個字元，
    對短憑證甚至是完整的。現在只回報布林值 `set`，不再透露任何憑證內容。
    """
    cfg = _read_env_config()
    redacted: Dict[str, Any] = {}
    for field in _TELEMETRY_FIELDS:
        val = cfg.get(field, "")
        redacted[field] = {"set": bool(val)}
    return JSONResponse({"config": redacted})


@api_v1.post("/config/telemetry")
async def update_telemetry_config(request: Request):
    """寫入 + 熱重載遙測設定。已由 api_v1 router 的 require_api_key 認證。

    [REGRESSION] 舊版本端點無認證，任何人 POST 就能以 os.replace 原子改寫
    伺服器的 .env（審計實測可寫入任意白名單欄位，例如清空
    TELEGRAM_BOT_TOKEN 讓熔斷告警靜默失效）。
    [REGRESSION] 舊版在 production 模式同樣照寫不誤。現在 production 直接拒絕
    （不觸碰 .env），沙盒模式保留原本的熱重載行為方便黑客松演示。
    """
    if _is_production():
        return JSONResponse(
            status_code=403,
            content={
                "status": "refused",
                "hot_reload": False,
                "reason": (
                    "Refusing to rewrite .env in production mode. Configure telemetry "
                    "credentials through the deployment environment/secret manager instead."
                ),
                "fields_updated": [],
            },
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Invalid JSON body."})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Expected a JSON object."})

    # 欄位白名單維持不變：只允許遙測通道欄位，杜絕任意 key 寫入 .env。
    updates: Dict[str, str] = {k: str(v) for k, v in body.items() if k in _TELEMETRY_FIELDS}
    _write_env_config(updates)

    # Hot-reload TelemetryNotifier in-place
    try:
        from src.transport.telemetry import TelemetryNotifier  # type: ignore
        import src.app.main as _main  # type: ignore
        cfg = _read_env_config()
        # [修正] 舊版傳了 notifier 不認識的參數（callmebot_phone/pushplus_token/
        # smtp_*），被 except Exception 吞掉 -> hot_reload 永遠 false，且配置從未生效。
        # 現在只傳 TelemetryNotifier.__init__ 真正接受的通道參數。
        _main.notifier.__init__(  # type: ignore
            whatsapp_phone=cfg.get("WHATSAPP_PHONE") or None,
            whatsapp_apikey=cfg.get("WHATSAPP_APIKEY") or None,
            wechat_sendkey=cfg.get("SERVERCHAN_SENDKEY") or None,
            telegram_token=cfg.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=cfg.get("TELEGRAM_CHAT_ID") or None,
        )
        hot_reload = True
    except Exception as exc:
        logger.warning("Telemetry notifier hot-reload failed: %s", exc)
        hot_reload = False

    return JSONResponse({"status": "saved", "hot_reload": hot_reload, "fields_updated": list(updates.keys())})


# ---------------------------------------------------------------------------
# One-click test push
# ---------------------------------------------------------------------------

@api_v1.get("/market")
async def market_reference(
    hs: str | None = None,
    keyword: str | None = None,
    q: str | None = None,
    year: int | None = None,
    reporter: int = 826,
    partner: int = 156,
    flow: str = "M",
    view: str | None = None,
    refresh: bool = False,
):
    """UK-from-China (or another reporter/partner) customs reference.

    A keyword does not pick an HS code. Pass hs (2-6 digits) to fetch.
    view=card adds the world total and earlier years for the dashboard card.
    The payload is reference data. It is not a price and not a solver input.
    """
    from ..market.trade_data import lookup_market, lookup_market_card

    if view == "card":
        result = await asyncio.to_thread(
            lookup_market_card,
            hs=hs,
            keyword=keyword or q,
            reporter=reporter,
            partner=partner,
            flow=flow,
            refresh=refresh,
        )
    else:
        result = await asyncio.to_thread(
            lookup_market,
            hs=hs,
            keyword=keyword or q,
            year=year,
            reporter=reporter,
            partner=partner,
            flow=flow,
        )
    status = 422 if result.get("reason") == "bad_request" else 200
    return JSONResponse(status_code=status, content=result)


@api_v1.post("/telemetry/test-push")
async def test_push(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Invalid JSON body."})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Expected a JSON object."})
    channel = body.get("channel", "all")
    # The message rides a paid notification channel; cap it like every other
    # caller-supplied text so a test push cannot relay a book through it.
    message = str(body.get("message") or "TradeFlow connectivity test — system nominal.")[:500]
    results: Dict[str, Any] = {}
    try:
        import src.app.main as _main  # type: ignore
        notifier = _main.notifier  # type: ignore
        if channel in ("whatsapp", "all"):
            results["whatsapp"] = await asyncio.get_event_loop().run_in_executor(
                None, notifier.notify_whatsapp, message
            )
        if channel in ("telegram", "all"):
            results["telegram"] = await asyncio.get_event_loop().run_in_executor(
                None, notifier.notify_telegram, message
            )
        if channel in ("pushplus", "all"):
            results["pushplus"] = await asyncio.get_event_loop().run_in_executor(
                None, notifier.notify_pushplus, message
            )
    except Exception as exc:
        results["error"] = str(exc)
    return JSONResponse({"channel": channel, "results": results})
