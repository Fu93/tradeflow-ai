"""`scripts/reset_demo_sessions.py` 的行為鎖。

這個腳本會刪本機的會話資料，所以「刪錯東西」是最貴的失敗模式：如果樣式比對
反了、或 dry run 真的寫了 DB，使用者第一次執行就會失去示範資料。這裡把三件
事釘住：分類規則、dry run 不動 DB、--apply 只刪被分類為殘骸的那些。
"""
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "reset_demo_sessions.py"
    spec = importlib.util.spec_from_file_location("reset_demo_sessions", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


reset = _load_script()


class TestPartition(unittest.TestCase):
    def test_dashboard_presets_are_kept_by_default(self):
        keep, drop = reset.partition_session_ids(
            ["DEMO_UMBRELLA_01", "TEST_PRESET_CNC_CASE", "ui-demo-02", "PCT2469451"]
        )
        self.assertEqual(keep, ["DEMO_UMBRELLA_01"])
        # TEST_PRESET_* 是 pytest 的殘骸（test_category_preset 過去直寫真實 DB）。
        self.assertEqual(drop, ["PCT2469451", "TEST_PRESET_CNC_CASE", "ui-demo-02"])

    def test_extra_keep_pattern_is_honoured(self):
        keep, drop = reset.partition_session_ids(
            ["MY_CASE_7", "probe-e"], keep_patterns=("DEMO_*", "MY_CASE_*")
        )
        self.assertEqual(keep, ["MY_CASE_7"])
        self.assertEqual(drop, ["probe-e"])

    def test_delete_all_drops_even_presets(self):
        keep, drop = reset.partition_session_ids(["DEMO_UMBRELLA_01"], delete_all=True)
        self.assertEqual(keep, [])
        self.assertEqual(drop, ["DEMO_UMBRELLA_01"])


class TestApplyAgainstTempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tf_reset_")
        self.db = Path(self.tmp) / "sessions.db"
        self.store = Path(self.tmp) / "json"
        self.store.mkdir()
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, version INTEGER)")
            conn.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, session_id TEXT)")
            for sid in ("DEMO_UMBRELLA_01", "PCT2469451", "ui-demo-02"):
                conn.execute("INSERT INTO sessions VALUES (?, 0)", (sid,))
                conn.execute("INSERT INTO turns (session_id) VALUES (?)", (sid,))
                (self.store / f"{sid}.json").write_text("{}", encoding="utf-8")
        conn.close()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _remaining(self):
        conn = sqlite3.connect(self.db)
        try:
            return sorted(r[0] for r in conn.execute("SELECT session_id FROM sessions"))
        finally:
            conn.close()

    def test_dry_run_touches_nothing(self):
        ids = reset._list_session_ids(self.db)
        keep, drop = reset.partition_session_ids(ids)
        self.assertEqual(len(drop), 2)
        # dry run 就是「不呼叫 _delete_sessions」；這裡直接確認資料完好。
        self.assertEqual(len(ids), 3)
        self.assertTrue(all((self.store / f"{sid}.json").exists() for sid in ids))

    def test_apply_removes_only_debris(self):
        ids = reset._list_session_ids(self.db)
        _, drop = reset.partition_session_ids(ids)
        removed = reset._delete_sessions(self.db, self.store, drop)
        self.assertEqual(removed, 2)
        self.assertEqual(self._remaining(), ["DEMO_UMBRELLA_01"])
        self.assertTrue((self.store / "DEMO_UMBRELLA_01.json").exists())
        self.assertFalse((self.store / "ui-demo-02.json").exists())
        conn = sqlite3.connect(self.db)
        try:
            orphans = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(orphans, 1, "turns of a deleted session must go too")


if __name__ == "__main__":
    unittest.main()
