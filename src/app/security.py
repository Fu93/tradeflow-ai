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
       支援黑客松評委演示、前端無痛演練與 pytest 單元測試。
       若配置了密鑰則強制驗證密鑰或 SANDBOX_DEMO_TOKEN，未配置時標記沙盒訪問放行。
    2. 生產模式 (TRADEFLOW_ENV=production):
       Fail-Closed: 若未設置 TRADEFLOW_API_KEY (或長度 < 16) 則直接阻斷存取 (500)；
       未攜帶或攜帶非法密鑰則直接阻斷 (401)，杜絕未受控裸奔。
    """
    if env_mode() == "sandbox":
        configured_key = os.getenv("TRADEFLOW_API_KEY")
        if configured_key:
            if not supplied or (supplied.strip() != configured_key.strip() and supplied.strip() != SANDBOX_DEMO_AUTH):
                raise HTTPException(
                    status_code=401,
                    detail="Unauthorized: Invalid X-API-Key header in sandbox mode. Access denied.",
                )
        return supplied or SANDBOX_DEMO_AUTH

    # 生產模式：Fail-Closed
    configured_key = os.getenv("TRADEFLOW_API_KEY")
    if not configured_key or len(configured_key.strip()) < 16:
        raise HTTPException(
            status_code=500,
            detail="Security Violation: System is in production mode but TRADEFLOW_API_KEY is not configured (Fail-Closed).",
        )
    if not supplied or supplied.strip() != configured_key.strip():
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
            detail="Security Violation: system is in production mode but TRADEFLOW_WEBHOOK_TOKEN is not configured (Fail-Closed).",
        )
    if not supplied or not hmac.compare_digest(supplied.strip(), configured.strip()):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Invalid or missing X-Webhook-Token header in production mode. Access denied.",
        )
    return supplied
