"""TradeFlow 認證門禁原語 (Authentication gate primitives).

[P0 REGRESSION] 為什麼要把這些函式從 `main.py` 抽出來：
`main.py` 為了掛載 Web Portal 而 `from ..web.routes_web import ...`（檔尾），
而 `routes_web.py` 又 `from ..app.main import require_api_key`（取得門禁依賴）。
在 `import src.app.main`（pytest / 其他模組匯入）路徑下，這個循環剛好能解開；
但以**文檔記載的啟動指令** `python -m src.app.main` 執行時，main 是以
`__main__` 身分載入、`sys.modules` 裡沒有 `src.app.main` 這個鍵，於是
`routes_web` 的 `from ..app.main import require_api_key` 會去「重新載入」一份
半初始化的 main，直接拋出：

    ImportError: cannot import name 'api_v1' from partially initialized module
    'src.web.routes_web' (most likely due to a circular import)

這使得 README / SUBMIT.md 的「一鍵啟動」在乾淨環境下 100% 崩潰。
測試全綠是因為測試走的是 `from src.app import main`（模組匯入）路徑，
恰好繞過了這個破口 —— 也就是說這個 bug 對 CI 完全隱形。

修法：把門禁原語（API key 與 webhook token 驗證）移到這個不依賴任何
app/web 模組的底層檔案；`routes_web.py` 改為從這裡匯入，循環即被切斷。
`main.py` 仍以同名 re-export，維持既有公開介面與測試相容。
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

logger = logging.getLogger("tradeflow.security")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
webhook_auth_header = APIKeyHeader(name="X-Webhook-Token", auto_error=False)

SANDBOX_DEMO_AUTH = os.getenv("TRADEFLOW_SANDBOX_DEMO_TOKEN", "SANDBOX-DEMO-TOKEN")

# Minimum acceptable API-key length. Anything shorter is a guessable secret and
# is treated as "not configured" in production.
MIN_API_KEY_LEN = 16


def _same_secret(provided: str, expected: str) -> bool:
    """Constant-time secret comparison.

    ``!=`` on str leaks the length and the position of the first differing byte
    through timing. ``hmac.compare_digest`` does not, but it raises on
    non-ASCII str, so both sides are encoded first. Length is compared first
    only to avoid the exception; that comparison is not secret-dependent
    because the expected length is a public property of the key format.
    """
    left = provided.encode("utf-8", "replace")
    right = expected.encode("utf-8", "replace")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def env_mode() -> str:
    """讀取當前環境模式（每次都讀 env，而非 import 期快照）。

    [REGRESSION] 舊版 `ENV_MODE = os.getenv(...)` 只在 import 時計算一次，因此
    行程啟動後才設定 TRADEFLOW_ENV=production **不會**生效 —— 安全門禁會一直
    停留在 sandbox 的 fail-open 判斷。現在兩個門禁都改讀這個函式。
    """
    return os.getenv("TRADEFLOW_ENV", "sandbox").strip().lower()


# 保留給外部/既有程式碼引用的相容快照（僅供顯示；門禁一律用 env_mode()）。
ENV_MODE = env_mode()


def require_api_key(supplied: Optional[str] = Depends(api_key_header)):
    """API 安全金鑰驗證門禁 (Fail-Closed 默認拒絕規範).

    1. 演練沙盒模式 (TRADEFLOW_ENV=sandbox, 預設):
       - 未配置 TRADEFLOW_API_KEY: 放行，但**每次都記錄警告**，讓「未受保護」
         是可觀測的，而不是靜默的。
       - 已配置 TRADEFLOW_API_KEY: 該金鑰是**唯一**可用的金鑰。
    2. 生產模式 (TRADEFLOW_ENV=production):
       Fail-Closed: 若未設置 TRADEFLOW_API_KEY (或長度 < 16) 則直接阻斷存取 (500)；
       未攜帶或攜帶非法密鑰則直接阻斷 (401)。

    [SECURITY 2026-10-23] 移除 `SANDBOX_DEMO_AUTH` 萬能繞過：
    舊版在 sandbox 且**已配置真金鑰**時，仍接受硬寫在公開 repo 裡的
    `SANDBOX-DEMO-TOKEN`，等於金鑰配置形同虛設。現在沙盒令牌只在
    「伺服器完全沒有金鑰」的情境下作為演示佔位符返回，永不通過驗證。
    比較改用常數時間 (`_same_secret`)，消除位元組級計時側通道。
    """
    configured = (os.getenv("TRADEFLOW_API_KEY") or "").strip()
    if env_mode() == "sandbox":
        if not configured:
            logger.warning(
                "TRADEFLOW_API_KEY is not configured; the sandbox API is fail-OPEN. "
                "Set TRADEFLOW_API_KEY (or run production) to enforce authentication."
            )
            return supplied or SANDBOX_DEMO_AUTH
        provided = (supplied or "").strip()
        # [SECURITY 2026-10-24] The shared env key and named operator keys are
        # peers here, exactly as on the desk routes; _resolve_credential also
        # sets the chain actor for named keys.
        if not provided or _resolve_credential(provided) is None:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: Invalid X-API-Key header in sandbox mode. Access denied.",
            )
        return supplied

    # 生產模式：Fail-Closed
    if not configured or len(configured) < MIN_API_KEY_LEN:
        raise HTTPException(
            status_code=500,
            detail=(
                "Security Violation: System is in production mode but TRADEFLOW_API_KEY "
                f"is not configured or is shorter than {MIN_API_KEY_LEN} characters (Fail-Closed)."
            ),
        )
    provided = (supplied or "").strip()
    if not provided or _resolve_credential(provided) is None:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing X-API-Key header in production mode. Access denied.",
        )
    return supplied


def require_webhook_token(supplied: Optional[str] = Depends(webhook_auth_header)):
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
    if env_mode() == "sandbox":
        if not configured:
            logger.warning(
                "TRADEFLOW_WEBHOOK_TOKEN is not configured; webhook endpoints are "
                "fail-OPEN in sandbox mode. Set it (or run production) to enforce "
                "shared-secret authentication on supervisor commands."
            )
            return None
        if not supplied or not hmac.compare_digest(supplied.strip(), configured.strip()):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: Invalid or missing X-Webhook-Token header in sandbox mode.",
            )
        return supplied

    # 生產模式：無法關閉的 fail-closed
    if not configured:
        raise HTTPException(
            status_code=401,
            detail=(
                "Security Violation: system is in production mode but TRADEFLOW_WEBHOOK_TOKEN "
                "is not configured (Fail-Closed)."
            ),
        )
    if not supplied or not hmac.compare_digest(supplied.strip(), configured.strip()):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing X-Webhook-Token header in production mode. Access denied.",
        )
    return supplied


def _named_key_actor(provided: str) -> Optional[str]:
    """Resolve a named operator key to its chain-facing actor, or ``None``.

    Lookup runs only when an explicit desk database is configured — a v1-only
    deployment must not have a failed login create one as a side effect. Any
    storage problem resolves to ``None``: an authentication failure is a 401,
    never a 500.
    """
    if not provided or not os.environ.get("TRADEFLOW_DESK_DB", "").strip():
        return None
    try:
        from src.desk import users
        from src.desk.store import get_store

        resolved = users.resolve(get_store().conn, provided)
    except Exception:  # noqa: BLE001 - a broken store must not turn 401 into 500
        logger.warning("Desk user lookup failed; treating the key as unknown.", exc_info=True)
        return None
    if resolved is None:
        return None
    return users.actor_label(resolved)


def _resolve_credential(provided: str) -> Optional[str]:
    """The actor behind a presented key: the shared env key or a named user.

    ``"env-key"`` is the shared ``TRADEFLOW_API_KEY`` — it authenticates but
    names nobody. A named operator key resolves to ``user:<id>:<label>`` and
    is what makes the audit chain's actor attribution possible. ``None``
    means the credential is not accepted.
    """
    configured = (os.getenv("TRADEFLOW_API_KEY") or "").strip()
    if configured and provided and len(provided) == len(configured) and hmac.compare_digest(provided, configured):
        return "env-key"
    return _named_key_actor(provided)


async def require_desk_mutation(supplied: Optional[str] = Depends(api_key_header)):
    """Every desk route needs a valid credential in every mode, including reads.

    The rest of the sandbox API stays open for the demo. The published
    sandbox demo token is not accepted here, so it cannot read supplier mail.

    Credentials, in resolution order (Phase 2, 2026-10-24):

    1. The configured ``TRADEFLOW_API_KEY`` — the server key, unchanged. Its
       actor is recorded as ``env-key``: it is shared, so it names nobody.
    2. A named operator key issued via ``scripts/manage_users.py`` — stored
       only as a SHA-256 hash in the desk database. Its actor is recorded as
       ``user:<id>:<label>`` and lands inside the audit chain entry, so the
       chain now answers *who* acted, not only *that* someone did.

    A revoked key resolves to nothing at all, exactly like an unknown one —
    revocation cannot be probed by comparing error messages. With no valid
    credential the answer is 401, unchanged; named keys add a way in, they
    do not open one.

    The dependency is async so the actor is set on the request's own context
    (a sync dependency would set it in a worker thread whose context is
    discarded); the endpoint then inherits it whether it runs on the loop or
    in the threadpool, which is what carries the actor into ``append_chain``.
    """
    provided = (supplied or "").strip()
    if provided:
        actor = _resolve_credential(provided)
        if actor is not None:
            from src.desk.users import set_current_actor

            set_current_actor(actor)
            return supplied
        raise HTTPException(
            status_code=401,
            detail="The desk needs a valid X-API-Key.",
        )
    raise HTTPException(
        status_code=401,
        detail="The desk needs TRADEFLOW_API_KEY on the server.",
    )
