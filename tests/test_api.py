"""FastAPI 端點測試（離線，用 TestClient；不呼叫真 Evolus）。

需要 fastapi + httpx（TestClient 依赖）。若未裝則跳過。
"""
import sys, os, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fastapi.testclient import TestClient
    from src.app import main as api_main
except Exception as e:
    print(f"SKIP test_api: {e}")
    raise SystemExit(0)

import unittest


class _IsolatedClientCase(unittest.TestCase):
    """Point the global SessionManager at a private temp store.

    [REGRESSION] These API tests used to write the repo's REAL data/sessions.db
    (session ids were fixed strings, and a failed assertion left rows behind).
    The SessionManager now honours TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR, so
    every test class gets its own store and parallel runs cannot collide.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tf_test_api_")
        self._orig_env = {
            k: os.environ.get(k)
            for k in ("TRADEFLOW_DB_PATH", "TRADEFLOW_STORE_DIR", "TRADEFLOW_WEBHOOK_TOKEN")
        }
        os.environ["TRADEFLOW_DB_PATH"] = os.path.join(self._tmp, "sessions.db")
        os.environ["TRADEFLOW_STORE_DIR"] = os.path.join(self._tmp, "sessions_json")
        # Keep the sandbox fail-open behaviour these tests were written against.
        os.environ.pop("TRADEFLOW_WEBHOOK_TOKEN", None)
        # Remember where the global manager pointed so tearDown can restore a LIVE
        # location before the temp dir is deleted (otherwise a later test class
        # would inherit a manager whose store_dir no longer exists).
        self._prev_paths = (api_main.session_mgr.db_path, api_main.session_mgr.store_dir)
        api_main.session_mgr.__init__(
            db_path=os.environ["TRADEFLOW_DB_PATH"],
            store_dir=os.environ["TRADEFLOW_STORE_DIR"],
        )
        self.c = TestClient(api_main.app)

    def tearDown(self):
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        api_main.session_mgr.__init__(
            db_path=self._prev_paths[0], store_dir=self._prev_paths[1]
        )
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestAPI(_IsolatedClientCase):
    def test_health(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_favicon_and_extension_icons(self):
        page = self.c.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('rel="icon"', page.text)
        self.assertIn("data:image/png;base64,", page.text)
        icon = self.c.get("/favicon.ico")
        self.assertNotEqual(icon.status_code, 404)
        self.assertEqual(icon.status_code, 200)
        self.assertTrue(icon.content.startswith(b"\x89PNG"))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest_path = os.path.join(root, "extension", "manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = __import__("json").load(handle)
        for size in ("16", "48", "128"):
            rel = manifest["icons"][size]
            self.assertEqual(manifest["action"]["default_icon"][size], rel)
            self.assertTrue(os.path.isfile(os.path.join(root, "extension", rel)))

    def test_negotiate(self):
        r = self.c.post("/negotiate", json={
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "n_samples": 2000,
            "observed_offer": 1.25,
        })
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r.text)
        self.assertNotIn("opening_offer_price", r.text)

    def test_webhook_responds_with_draft(self):
        """Webhook 必須回傳草稿與建議，且**不得因外部服務狀態而失敗**。

        注意：本測試刻意**不**斷言「必須是 offline stub」。舊版本斷言
        `"offline stub" in draft_email`，會在金鑰配置正確時失敗，等於把
        「外部整合壞掉」鎖進測試 —— 反而讓真正的 Evolus 回歸無法被察覺。

        正確的契約是：無論 Evolus 可用與否，端點都必須 200 並回傳可用的草稿。

        [SECURITY] 該端點現在需要 X-Webhook-Token（production 強制）。這裡在
        sandbox 且 token 未配置的前提下測試（fail-open），與 demo 行為一致。
        """
        r = self.c.post("/webhook/evolus", json={
            "extracted": {"target_price": 1.0, "ceiling_price": 16.2, "expected_markup": 0.0},
            "offer": 1.0, "email_body": "We can do ¥15, MOQ 500."})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("unconfirmed numbers used in decisions: 3", r.text)
        self.assertNotIn("opening_offer_price", r.text)


class TestUnknownFieldRejection(_IsolatedClientCase):
    """未知欄位必須回 422，不可被靜默忽略。

    [REGRESSION] 原本 `RoundActionRequest` 未設 `extra="forbid"`，因此把
    `supplier_text` 誤打成 `supplier_message` 時，Pydantic 會默默丟棄該欄位，
    該輪以「空文本」執行，並回傳 HTTP 200 + 看似合理的 `NORMAL_COUNTER_OFFER`
    決策。呼叫端無法分辨這是真實分析還是無輸入的預設值 —— 這比報錯更危險。
    """

    def test_round_rejects_unknown_field(self):
        r = self.c.post(
            "/api/v1/sessions/unknown-field-01/round",
            json={"supplier_message": "原料漲價", "supplier_quote": 14.8},
        )
        self.assertEqual(
            r.status_code, 422,
            "a misspelled field must be rejected, not silently dropped",
        )

    def test_round_accepts_canonical_fields(self):
        """正確欄位名仍須正常運作（確認 422 來自 extra=forbid，而非欄位改名）。"""
        created = self.c.post("/api/v1/sessions", json={
            "session_id": "unknown-field-02",
            "product_name": "Sample",
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "expected_markup": 0.0,
        })
        self.assertEqual(created.status_code, 200, created.text)
        r = self.c.post(
            "/api/v1/sessions/unknown-field-02/round",
            json={"opponent_raw_text": "老闆，碰擊布原料暴漲，¥14.80 做不了",
                  "opponent_offer_price": 14.80},
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("unconfirmed numbers used in decisions: 1", r.json()["detail"])
        self.assertNotIn("supplier_message", r.json()["detail"])
        stored = self.c.get("/api/v1/sessions/unknown-field-02")
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.json()["history"], [])

    def test_multi_auction_rejects_unknown_field(self):
        r = self.c.post(
            "/api/v1/multi-auction/run",
            json={"targt_price": 12.0},  # deliberate typo
        )
        self.assertEqual(r.status_code, 422)


class TestApiAuthIsEnforced(_IsolatedClientCase):
    """[audit §7 item 1] The old suite codified "no key needed" as PASSING.

    With a key configured, every /api/v1 route must refuse an anonymous caller
    with 401. This is the regression that the previous suite actively hid.
    """

    def setUp(self):
        super().setUp()
        self._orig_key = os.environ.get("TRADEFLOW_API_KEY")
        os.environ["TRADEFLOW_API_KEY"] = "a-real-configured-key-1234"

    def tearDown(self):
        if self._orig_key is None:
            os.environ.pop("TRADEFLOW_API_KEY", None)
        else:
            os.environ["TRADEFLOW_API_KEY"] = self._orig_key
        super().tearDown()

    def test_list_sessions_requires_key(self):
        self.assertEqual(self.c.get("/api/v1/sessions").status_code, 401)

    def test_round_requires_key(self):
        r = self.c.post("/api/v1/sessions/auth-probe-01/round",
                        json={"opponent_offer_price": 13.0})
        self.assertEqual(r.status_code, 401)

    def test_telemetry_config_requires_key(self):
        self.assertEqual(self.c.get("/api/v1/config/telemetry").status_code, 401)

    def test_valid_key_is_accepted(self):
        r = self.c.get("/api/v1/sessions",
                       headers={"X-API-Key": "a-real-configured-key-1234"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
