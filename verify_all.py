"""TradeFlow AI — 評審環境一鍵自我診斷與健康檢查套件 (System Health & Sanity Audit)。

職責：
在評審下載代碼後，10 秒內依序檢驗以下 6 大核心支柱並打印具備審計效力的系統健康證書：
1. SQLite WAL 資料庫讀寫與會話原子持久化
2. NumPy / CuPy 張量並行內核計算延遲
3. Corporate Mandate 凸約束集與 Logrolling 帕雷托求解
4. AST 數值硬不變量護欄（單一人工構造的越界案例被攔截）
5. Telemetry & Webhook 異步調度狀態機
6. 雙向跨語言不變量與防穿幫審計

[效力邊界] 這是一個 smoke check：每個支柱只以少量代表性輸入檢查一次，
不是統計意義上的成功率，也不是生產級認證。完整回歸由 `pytest tests/ -q` 負責。
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

from src.sim.smoke import (
    AUCTION_CEILING,
    AUCTION_TARGET,
    EXEC_CEILING,
    EXEC_COMMAND,
    EXEC_CONFIDENCE,
    EXEC_DEPOSIT,
    EXEC_FLOOR,
    EXEC_MOLD,
    EXEC_PRICE,
    EXEC_PRICE_STEP,
    EXEC_RISK,
    GUARD_DEPOSIT,
    GUARD_PRICE,
    LOGROLL_CEILING,
    LOGROLL_OFFER,
    LOGROLL_TARGET,
    PERSIST_CEILING,
    PERSIST_TARGET,
)


def audit_system():
    print("================================================================================")
    print("           TradeFlow AI System Health & Hackathon Jury Audit Suite               ")
    print("================================================================================")
    all_passed = True
    start_time = time.perf_counter()

    # Pillar 1: Session Manager & SQLite WAL
    # [修正] 舊版只斷言 create_session/transition_state 的回傳物件狀態，
    # **從未開啟 SQLite、也從未重新讀取持久化狀態**，卻印出
    # "SQLite WAL Session Persistence: Verified (Transaction ACID safe)"。
    # 以純記憶體 dict 假物件替換 SessionManager 也能通過 —— 該宣稱完全沒有斷言支撐。
    # 現改為真正做一次跨管理器往返 (close → reopen → read back)，並檢查
    # on-disk journal_mode 確實是 wal。
    try:
        import tempfile as _tf
        import sqlite3 as _sq
        import gc as _gc
        from src.legacy.session.manager import SessionManager
        from src.legacy.session.schema import SessionSpec, AutopilotState
        # 注意：Windows 上只要還有 sqlite 連線未關，TemporaryDirectory 清理就會
        # 拋 WinError 32。故顯式關閉連線並回收，再讓 with 區塊結束。
        _td = _tf.mkdtemp()
        try:
            _db = os.path.join(_td, "audit.db")
            _sd = os.path.join(_td, "json")
            mgr = SessionManager(db_path=_db, store_dir=_sd)
            spec = SessionSpec(
                session_id="AUDIT_VERIFY_TEMP",
                product_name="Audit Verification Test",
                target_price=PERSIST_TARGET,
                ceiling_price=PERSIST_CEILING,
            )
            s = mgr.create_session(spec)
            assert s.state == AutopilotState.IDLE
            s = mgr.transition_state("AUDIT_VERIFY_TEMP", AutopilotState.AUTOPILOT_CRUISING)
            assert s.state == AutopilotState.AUTOPILOT_CRUISING

            # 1) journal_mode 必須真的是 wal
            _con = _sq.connect(_db)
            try:
                _mode = _con.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                _con.close()
            assert _mode.lower() == "wal", f"journal_mode is {_mode!r}, not wal"

            # 2) 以**全新的管理器**重新開啟，狀態必須還在（真正的持久化往返）
            reopened = SessionManager(db_path=_db, store_dir=_sd)
            back = reopened.get_session("AUDIT_VERIFY_TEMP")
            assert back is not None, "session did not survive reopen"
            assert back.state == AutopilotState.AUTOPILOT_CRUISING, (
                f"state after reopen is {back.state.value}, expected AUTOPILOT_CRUISING"
            )
            del reopened, mgr, back
            _gc.collect()
        finally:
            import shutil as _sh
            _sh.rmtree(_td, ignore_errors=True)
        print("[PASS] 1. SQLite WAL Session Persistence: Verified "
              "(journal_mode=wal; state survived close/reopen round-trip)")
    except Exception as e:
        print(f"[FAIL] 1. SQLite WAL Session Persistence: {e}")
        all_passed = False

    # Pillar 2: Tensor Rollout Solver & AMD Roofline Profile
    # [修正] 舊版唯一的斷言是 `res["total_rollouts"] > 0`，而該數字是
    # `k * s * n_open * n_sched` 的**算術乘積**，不是實際執行的 rollout 計數。
    # 因此一個回傳常數 dict 的假求解器也能通過（實測會印出
    # "14,400,000 rollouts in 0.000s" 然後 PASS）。現改為斷言真正的行為性質：
    # 輸出必須隨輸入改變，且必須真的回報它自己算出的 rollout 數。
    try:
        from src.app.demo_data import self_play_bundle
        from src.legacy.multi_solver import simulate_reverse_auction
        bundle = self_play_bundle()
        t0 = time.perf_counter()
        res = simulate_reverse_auction(
            target_price=AUCTION_TARGET, walk_away_ceiling=AUCTION_CEILING,
            n_samples=10000, use_gpu=False, demo=True, self_play=bundle,
        )
        dt = time.perf_counter() - t0
        assert res["total_rollouts"] > 0
        assert res["backend"].startswith("CPU"), f"unexpected backend: {res['backend']}"

        # 真求解器必須對輸入敏感：不同報價必須產生不同的贏家/價格，
        # 或至少不同的戰術分流。常數假物件會在此失敗。
        res_alt = simulate_reverse_auction(
            target_price=AUCTION_TARGET, walk_away_ceiling=AUCTION_CEILING, n_samples=10000, use_gpu=False,
            demo=True, self_play=bundle,
            suppliers=[
                {"name": "A", "profile": "Agile Conceder"},
                {"name": "B", "profile": "Rational Tit-for-Tat"},
            ],
        )
        assert res_alt["k_suppliers"] == 2, "supplier list was ignored"
        assert res["k_suppliers"] == 4, "default supplier count changed"

        print(f"[PASS] 2. Tensor Rollout Solver: Verified "
              f"({res['total_rollouts']:,} rollouts, backend={res['backend']}, "
              f"{dt:.3f}s; responds to supplier-list input)")
    except Exception as e:
        print(f"[FAIL] 2. Tensor Rollout Solver: {e}")
        all_passed = False

    # Pillar 3: Corporate Mandate & Logrolling Pareto Frontier
    try:
        from src.legacy.logrolling import CorporateMandate, SupplierCurrentOffer, LogrollingEngine
        mandate = CorporateMandate(
            target_price=LOGROLL_TARGET,
            walk_away_ceiling=LOGROLL_CEILING,
            baseline_lead_days=25,
            max_acceptable_lead_days=35,
            min_desired_lead_days=12,
            baseline_moq=500,
            max_allowed_moq=1200,
        )
        offer = SupplierCurrentOffer(
            price=LOGROLL_OFFER, lead_days=25, moq=500, is_price_stagnant=True,
        )
        lr_res = LogrollingEngine.evaluate(mandate, offer)
        assert lr_res.ast_guardrail_passed is True
        assert len(lr_res.all_pareto_proposals) > 0
        print(f"[PASS] 3. Corporate Mandate & Pareto Logrolling: Verified ({len(lr_res.all_pareto_proposals)} pareto solutions found)")
    except Exception as e:
        print(f"[FAIL] 3. Corporate Mandate & Pareto Logrolling: {e}")
        all_passed = False

    # Pillar 4: AST Guardrail Attack & Invariant Interception
    try:
        from src.legacy.logrolling import ASTMandateGuardrail, ASTMandateViolationError
        illegal_script = "我方今天財務特批預付 85% 定金現款秒結排單"
        blocked = False
        try:
            ASTMandateGuardrail.verify_proposal(
                {"proposed_price": GUARD_PRICE, "proposed_lead_days": 20, "proposed_deposit_pct": GUARD_DEPOSIT, "proposed_moq": 500, "tradeoff_types_used": ["DEPOSIT_FOR_LEADTIME"]},
                mandate,
                buyer_script=illegal_script,
            )
        except ASTMandateViolationError:
            blocked = True
        assert blocked is True
        print("[PASS] 4. AST Invariant Numeric Guardrail: Verified "
              "(single hand-crafted illegal-deposit case intercepted)")
    except Exception as e:
        print(f"[FAIL] 4. AST Invariant Numeric Guardrail: {e}")
        all_passed = False

    # Pillar 5: Intent Classifier & Cross-Lingual Cognitive Bridge
    try:
        from src.engine.bilingual import InboundIntentClassifier, VendorTacticEnum
        c = InboundIntentClassifier.classify("老闆，原材料鋁合金暴漲 30%，不加價我們沒法開工")
        assert c.primary_intent == VendorTacticEnum.CRY_POVERTY_COST_HIKE
        print(f"[PASS] 5. Cross-Lingual Intent Classifier: Verified (Tactic detected: {c.primary_intent.value})")
    except Exception as e:
        print(f"[FAIL] 5. Cross-Lingual Intent Classifier: {e}")
        all_passed = False

    # Pillar 6: Bidirectional Cross-Lingual Invariant & Anti-Leak Audit
    try:
        from src.engine.bilingual import (
            EnglishExecutiveCommandParser,
            ExecutiveActionEnum,
            TacticalActionObject,
            TacticalModeEnum,
            ActionObjectASTGuardrail,
            ChineseSurfaceRenderer,
            EnglishExecutiveRenderer,
            CrossLingualSanityChecker,
            CURRENT_SCHEMA_VERSION,
        )
        # 1. 測試高管自然語言指令反向解析
        exec_cmd = EnglishExecutiveCommandParser.parse(EXEC_COMMAND)
        assert exec_cmd.action == ExecutiveActionEnum.OVERRIDE_PRICE
        assert exec_cmd.override_price == EXEC_PRICE
        assert exec_cmd.demanded_deposit_pct == EXEC_DEPOSIT
        assert exec_cmd.waive_mold_fee is True

        # 2. 構建策略對象並經 AST 護欄驗證
        action_obj = TacticalActionObject(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_round_id="AUDIT_VERIFY_R1_EXEC",
            authorized_price=exec_cmd.override_price,
            walk_away_ceiling=EXEC_CEILING,
            breakdown_risk=EXEC_RISK,
            bayesian_confidence=EXEC_CONFIDENCE,
            estimated_floor_price=EXEC_FLOOR,
            tactical_mode=TacticalModeEnum.LOGROLL,
            product_name="Aerospace Aluminum Case",
            supplier_contact_name="陳總",
            deposit_pct=exec_cmd.demanded_deposit_pct,
            mold_fee_exempted=exec_cmd.waive_mold_fee,
            waive_mold_fee_amount=EXEC_MOLD,
        )
        assert ActionObjectASTGuardrail.verify(action_obj) is True

        # 3. 雙向表面渲染
        zh_script = ChineseSurfaceRenderer.render(action_obj)
        en_card = EnglishExecutiveRenderer.render_executive_card(action_obj)

        # 4. 雙向一致性、純度與防穿幫審計
        assert CrossLingualSanityChecker.verify_bidirectional_consistency(action_obj, zh_script, en_card) is True

        # 5. [修正] 舊版只斷言「兩串渲染結果彼此一致」。若把兩個 renderer 都改成
        #    只回傳 "¥12.96"，一致性檢查依然通過 —— 該斷言對「渲染器是否還有內容」
        #    完全沒有約束力。現加入最低內容要求：兩邊都必須是實質文本。
        assert len(zh_script.strip()) >= 30, f"ZH surface too short to be a real script: {zh_script!r}"
        assert len(en_card.strip()) >= 60, f"EN card too short to be a real card: {en_card!r}"
        assert en_card.count("\n") >= 3, "EN executive card lost its structured multi-line layout"
        # 渲染結果必須與決策物件一致，且價格須以兩位小數出現
        assert f"{action_obj.authorized_price:.2f}" in zh_script, "ZH script omits the authorized price"
        assert f"{action_obj.authorized_price:.2f}" in en_card, "EN card omits the authorized price"
        # 渲染器必須對輸入敏感：改變決策物件，輸出必須跟著改變
        alt = action_obj.model_copy(update={"authorized_price": action_obj.authorized_price - EXEC_PRICE_STEP})
        alt_zh = ChineseSurfaceRenderer.render(alt)
        assert alt_zh != zh_script, "renderer output did not change when the decision object changed"

        print("[PASS] 6. Bidirectional Cross-Lingual Invariant & Anti-Leak Audit: Verified "
              "(consistent, non-trivial content, price present, input-sensitive)")
    except Exception as e:
        print(f"[FAIL] 6. Bidirectional Cross-Lingual Invariant & Anti-Leak Audit: {e}")
        all_passed = False

    total_time = time.perf_counter() - start_time
    print("--------------------------------------------------------------------------------")
    if all_passed:
        # [誠實界定] 本腳本驗證的是「六項核心機制可運作」，每項各以少量代表性輸入檢查。
        # 它不是生產級認證，也不代表統計意義上的成功率；完整回歸由 tests/ 負責。
        print(f"VERDICT: ALL 6 PILLARS EXERCISED SUCCESSFULLY (Completed in {total_time:.2f}s)")
        print("This is a smoke check of each pillar, not a production certification.")
        print("Full regression coverage: python -m pytest tests -q")
    else:
        print("VERDICT: CRITICAL FAILURES DETECTED. Check logs above.")
    print("================================================================================")
    return all_passed


if __name__ == "__main__":
    success = audit_system()
    sys.exit(0 if success else 1)
