"""清掉本機 demo 用的殘留會話，保留可以直接示範的預設情境。

為什麼需要這支腳本：`data/sessions.db` 是 gitignore 的本機狀態，跑過一次
`pytest tests`、`verify_live.py` 或任何一次對拍的稽核腳本之後，裡面就躺著
`PCT2469451`、`XSSPROD1792225324`、`probe-e`、`ui-demo-01` 這類測試殘骸。
儀表板一打開就是幾十列沒意義的會話，評審看到的第一眼就是雜訊。

預設行為是**只列出、不刪除**（dry run）；要真的刪除必須加 `--apply`。

用法：
    python scripts/reset_demo_sessions.py               # 只印出將保留／將刪除
    python scripts/reset_demo_sessions.py --apply       # 真的刪除殘骸
    python scripts/reset_demo_sessions.py --apply --all # 全清（連 DEMO_* 一起）
    python scripts/reset_demo_sessions.py --keep 'MY_*' --apply

保留規則是 fnmatch 樣式，預設只保留 `DEMO_*`（儀表板 Category Preset 下拉選單
建立的那組），其餘一律視為殘骸 —— 包括 `TEST_PRESET_*`，那是 `pytest` 留下的
（該測試已改為寫入臨時目錄，但舊資料庫裡的殘骸仍需清一次）。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_KEEP = ("DEMO_*",)


def partition_session_ids(
    session_ids: list[str],
    keep_patterns: tuple[str, ...] = DEFAULT_KEEP,
    delete_all: bool = False,
) -> tuple[list[str], list[str]]:
    """把會話切成 (保留, 待刪除)。純函式，方便測試與 dry-run 共用。"""
    if delete_all:
        return [], sorted(session_ids)
    keep, drop = [], []
    for sid in session_ids:
        if any(fnmatch.fnmatchcase(sid, pat) for pat in keep_patterns):
            keep.append(sid)
        else:
            drop.append(sid)
    return sorted(keep), sorted(drop)


def _resolve_paths() -> tuple[Path, Path]:
    from src.legacy.session.manager import DEFAULT_DB_PATH, DEFAULT_STORE_DIR

    return Path(DEFAULT_DB_PATH), Path(DEFAULT_STORE_DIR)


def _list_session_ids(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT session_id FROM sessions ORDER BY session_id").fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def _delete_sessions(db_path: Path, store_dir: Path, session_ids: list[str]) -> int:
    """刪 DB 內的 sessions/turns 兩張表，並移除對應的 JSON 快照。"""
    removed = 0
    conn = sqlite3.connect(db_path)
    try:
        with conn:  # 單一交易：中途失敗不會留下半刪的會話
            for sid in session_ids:
                conn.execute("DELETE FROM turns WHERE session_id = ?", (sid,))
                cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
                removed += cur.rowcount if cur.rowcount > 0 else 0
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()
    for sid in session_ids:
        snap = store_dir / f"{sid}.json"
        try:
            snap.unlink()
        except FileNotFoundError:
            pass
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清掉本機殘留的 demo/測試會話（預設 dry run）。")
    ap.add_argument("--apply", action="store_true", help="真的執行刪除；不加則只列出")
    ap.add_argument("--all", action="store_true", help="連 DEMO_* / TEST_PRESET_* 一起刪")
    ap.add_argument(
        "--keep",
        action="append",
        metavar="PATTERN",
        help="額外保留的 session_id 樣式（可重複，例如 --keep 'MY_CASE_*'）",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出結果（供腳本取用）")
    args = ap.parse_args(argv)

    db_path, store_dir = _resolve_paths()
    ids = _list_session_ids(db_path)
    keep_patterns = DEFAULT_KEEP + tuple(args.keep or ())
    keep, drop = partition_session_ids(ids, keep_patterns, delete_all=args.all)

    removed = _delete_sessions(db_path, store_dir, drop) if (args.apply and drop) else 0

    if args.json:
        print(
            json.dumps(
                {
                    "db_path": str(db_path),
                    "applied": bool(args.apply),
                    "kept": keep,
                    "deleted": drop,
                    "rows_deleted": removed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"DB: {db_path}")
    print(f"保留 {len(keep)} 個：" + (", ".join(keep) if keep else "（無）"))
    if drop:
        print(f"{'已刪除' if args.apply else '將刪除'} {len(drop)} 個：")
        for sid in drop:
            print(f"  - {sid}")
    else:
        print("沒有需要清理的會話。")
    if drop and not args.apply:
        print("\n[dry run] 這是預覽。要真的刪除請加 --apply。")
    elif args.apply:
        print(f"\n[applied] DB 刪除 {removed} 列會話，並移除對應的 JSON 快照。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
