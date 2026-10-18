# Temp cleanup script (scratch, not repo source): clear test-junk negotiation
# sessions for the demo. FULL BACKUP first (SQLite db + JSON store), then clear.
# Everything is recoverable from debug_findings/_sessions_backup/.
import json
import os
import shutil
import sqlite3
import time

ROOT = os.getcwd()
DB = os.path.join(ROOT, "data", "sessions.db")
STORE = os.path.join(ROOT, "data", "sessions_json")
BACKUP = os.path.join(ROOT, "debug_findings", "_sessions_backup")
os.makedirs(BACKUP, exist_ok=True)

stamp = time.strftime("%Y%m%d-%H%M%S")

# 1. Backup DB (copy the file + WAL/SHM sidecars if present) and JSON store.
if os.path.exists(DB):
    shutil.copy2(DB, os.path.join(BACKUP, f"sessions.db.{stamp}.bak"))
    for suffix in ("-wal", "-shm"):
        side = DB + suffix
        if os.path.exists(side):
            shutil.copy2(side, os.path.join(BACKUP, f"sessions.db.{stamp}.bak{suffix}"))
    print("DB backed up")
if os.path.isdir(STORE):
    shutil.make_archive(
        os.path.join(BACKUP, f"sessions_json.{stamp}"), "zip", STORE
    )
    print("JSON store archived")

# 2. Inventory before cleanup.
def list_db_ids(path):
    ids = []
    try:
        conn = sqlite3.connect(path)
        try:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            print("DB tables:", tables)
            for t in tables:
                cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})").fetchall()]
                print(f"  table {t}: cols={cols}")
                id_col = next(
                    (c for c in cols if "session" in c.lower() and "id" in c.lower()), None
                )
                if id_col:
                    ids = [r[0] for r in conn.execute(f"SELECT {id_col} FROM {t}").fetchall()]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print("DB read error:", e)
    return ids

db_ids = list_db_ids(DB) if os.path.exists(DB) else []
json_ids = os.listdir(STORE) if os.path.isdir(STORE) else []
print(f"before: db_rows={len(db_ids)} json_files={len(json_ids)}")
sample = (db_ids or json_ids)[:15]
print("sample ids:", sample)

# 3. Clear: delete all rows / JSON files (they are all test/demo artifacts;
#    the demo creates its sessions fresh). Backup above restores everything.
if os.path.exists(DB):
    conn = sqlite3.connect(DB)
    try:
        tables = [
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        for t in tables:
            n = conn.execute(f"DELETE FROM {t}").rowcount
            print(f"cleared table {t}: ~{n} rows")
        conn.commit()
        # Reclaim WAL state cleanly.
        conn.execute("VACUUM")
    finally:
        conn.close()

if os.path.isdir(STORE):
    removed = 0
    for name in os.listdir(STORE):
        p = os.path.join(STORE, name)
        try:
            if os.path.isfile(p):
                os.remove(p)
                removed += 1
            else:
                shutil.rmtree(p)
                removed += 1
        except OSError as e:
            print("skip", name, e)
    print(f"removed {removed} JSON entries")

# 4. Verify empty.
db_after = list_db_ids(DB) if os.path.exists(DB) else []
json_after = os.listdir(STORE) if os.path.isdir(STORE) else []
print(f"after: db_rows={len(db_after)} json_files={len(json_after)}")
print("CLEANUP_DONE")
