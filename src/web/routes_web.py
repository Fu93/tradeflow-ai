"""TradeFlow Web Dashboard — FastAPI router.

Mounts at / and provides:
  GET  /                         -> dark FinTech dashboard HTML
  GET  /api/v1/stream/session    -> SSE live session feed
  GET  /api/v1/config/telemetry  -> read current telemetry config
  POST /api/v1/config/telemetry  -> write + hot-reload telemetry config
  POST /api/v1/telemetry/test-push -> fire a one-off test notification
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

_HERE = Path(__file__).parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"
ENV_PATH = Path(".env")

router = APIRouter()


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# SSE live session feed
# ---------------------------------------------------------------------------

async def _session_event_generator(request: Request):
    """Push session state snapshots every 1 second via Server-Sent Events."""
    # import here to avoid circular imports at module load time
    from src.session.manager import SessionManager  # type: ignore
    mgr = SessionManager()

    while True:
        if await request.is_disconnected():
            break

        sessions = []
        try:
            for s in mgr.list_sessions():
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
        except Exception:
            pass

        payload = json.dumps({"ts": time.time(), "sessions": sessions})
        yield f"data: {payload}\n\n"
        await asyncio.sleep(1.0)


@router.get("/api/v1/stream/session")
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
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg


def _write_env_config(updates: Dict[str, str]) -> None:
    """Atomically merge updates into .env (creates file if absent)."""
    cfg = _read_env_config()
    cfg.update({k: v for k, v in updates.items() if v != ""})
    lines = [f"{k}={v}" for k, v in sorted(cfg.items())]
    tmp = ENV_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)


_TELEMETRY_FIELDS = [
    "CALLMEBOT_PHONE", "CALLMEBOT_API_KEY",
    "PUSHPLUS_TOKEN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_TO",
    "WECHAT_PUSHPLUS_TOKEN",
]


@router.get("/api/v1/config/telemetry")
async def get_telemetry_config():
    cfg = _read_env_config()
    # Redact secrets — only reveal whether they are set
    redacted: Dict[str, Any] = {}
    for field in _TELEMETRY_FIELDS:
        val = cfg.get(field, "")
        redacted[field] = {"set": bool(val), "preview": val[:4] + "..." if len(val) > 4 else val}
    return JSONResponse({"config": redacted})


@router.post("/api/v1/config/telemetry")
async def update_telemetry_config(request: Request):
    body = await request.json()
    updates: Dict[str, str] = {k: str(v) for k, v in body.items() if k in _TELEMETRY_FIELDS}
    _write_env_config(updates)

    # Hot-reload TelemetryNotifier in-place
    try:
        from src.transport.telemetry import TelemetryNotifier  # type: ignore
        import src.app.main as _main  # type: ignore
        cfg = _read_env_config()
        _main.notifier.__init__(  # type: ignore
            callmebot_phone=cfg.get("CALLMEBOT_PHONE", ""),
            callmebot_api_key=cfg.get("CALLMEBOT_API_KEY", ""),
            pushplus_token=cfg.get("PUSHPLUS_TOKEN", ""),
            telegram_bot_token=cfg.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=cfg.get("TELEGRAM_CHAT_ID", ""),
            smtp_host=cfg.get("SMTP_HOST", ""),
            smtp_port=int(cfg.get("SMTP_PORT", "587")),
            smtp_user=cfg.get("SMTP_USER", ""),
            smtp_password=cfg.get("SMTP_PASSWORD", ""),
            smtp_to=cfg.get("SMTP_TO", ""),
        )
        hot_reload = True
    except Exception as exc:
        hot_reload = False

    return JSONResponse({"status": "saved", "hot_reload": hot_reload, "fields_updated": list(updates.keys())})


# ---------------------------------------------------------------------------
# One-click test push
# ---------------------------------------------------------------------------

@router.post("/api/v1/telemetry/test-push")
async def test_push(request: Request):
    body = await request.json()
    channel = body.get("channel", "all")
    message = body.get("message", "TradeFlow connectivity test — system nominal.")
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
