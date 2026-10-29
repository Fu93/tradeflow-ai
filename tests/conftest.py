"""Suite-wide guard: no test may write into the repository's real session store.

[為什麼需要這個檔案 — 2026-10-17 稽核]

`data/sessions.db` 與 `data/sessions_json/` 是**使用者本機的示範資料**（gitignore），
但有好幾個測試只是 `TestClient(app)` 就開始建會話、跑輪次，於是每跑一次
`pytest tests`，儀表板就長出幾列 `TEST_PRESET_*`、`async-regression-01`、
`mold-guard-*` 這類殘骸。實際量測：跑完一輪全套測試，本機多出 4 個會話與 4 個
輪次，全部是測試留下的。

個別測試（test_api.py、test_api_v4.py、test_api_security.py）早就各自做了隔離，
但那是「誰記得誰做」—— 新測試檔案不會記得。這裡改成**預設隔離**：每個測試模組
拿到自己的臨時資料庫，模組結束後把全域 manager 還原回真實路徑。

範圍刻意用 module 而非 function：同一模組內的測試原本共用一個資料庫，改成
function 會改變它們彼此的假設；module 範圍只切斷跨模組污染，正是這裡要修的。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

# A developer's real provider keys must never reach the suite: a test that
# expects "no key configured" would otherwise call the real (paid) service.
# Tests that need a key set a fake one with monkeypatch.
os.environ["TRADEFLOW_SKIP_DOTENV"] = "1"
for _provider_key in (
    "TRADEFLOW_LLM_API_KEY",
    "TRADEFLOW_LLM_BASE_URL",
    "TRADEFLOW_LLM_MODEL",
    "TRADEFLOW_DASHBOARD_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "NVIDIA_API_KEY",
    "TAVILY_API_KEY",
    "VLLM_API_KEY",
    "VLLM_BASE_URL",
    "VLLM_MODEL",
    "EVOLUS_API_KEY",
    "EVOLUS_APP_ID",
    "COMTRADE_API_KEY",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_REFRESH_TOKEN",
    "IMAP_PASSWORD",
    "SMTP_HOST",
    "EMAIL_PASSWORD",
):
    os.environ.pop(_provider_key, None)

# Session create must not call Comtrade during the suite. A test that needs
# the live client deletes this variable and passes a fake transport.
os.environ.setdefault("TRADEFLOW_MARKET_OFFLINE", "1")
# The suite still exercises v1. A production process leaves this unset.
os.environ.setdefault("TRADEFLOW_LEGACY", "1")
# The desk chain is HMAC-SHA256. Tests use a fixed key; production reads TRADEFLOW_CHAIN_KEY.
os.environ.setdefault("TRADEFLOW_CHAIN_KEY", "test-chain-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import main as api_main  # noqa: E402
from src.engine.model import bind_archetypes  # noqa: E402
from src.sim.archetypes import OPPONENT_TYPE_NAMES, OPPONENT_TYPE_ROWS  # noqa: E402

bind_archetypes(OPPONENT_TYPE_ROWS, OPPONENT_TYPE_NAMES)


@pytest.fixture(scope="module", autouse=True)
def _isolated_session_store():
    tmp = tempfile.mkdtemp(prefix="tf_test_")
    prev = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
    api_main.session_mgr.__init__(
        db_path=os.path.join(tmp, "sessions.db"),
        store_dir=os.path.join(tmp, "sessions_json"),
    )
    try:
        yield tmp
    finally:
        # 還原成測試開始前的路徑，其他模組（與使用者自己的伺服器）不受影響。
        api_main.session_mgr.__init__(db_path=prev[0], store_dir=prev[1])
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_model_budget():
    """Each test starts with today's paid-call counter at zero (see LLM_DAILY_CALL_CAP)."""
    from src.engine.llm_config import reset_model_budget

    reset_model_budget()
    yield
    reset_model_budget()
