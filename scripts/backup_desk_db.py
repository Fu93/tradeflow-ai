"""Back up the desk SQLite database, and verify what was backed up.

Why this exists
---------------
The desk's whole integrity story lives in one SQLite file: the audit chain,
the decisions, the drafts. The compose volume keeps that file alive across
container rebuilds, but a volume is not a backup — ``docker compose down -v``
destroys it, and so does a bad deploy. This script takes an online backup
with SQLite's own backup API (consistent even while the desk is writing,
thanks to WAL), then opens the copy and re-verifies every deal's chain, so a
backup that cannot prove its own integrity says so instead of sitting there
looking reassuring.

Usage:

    python scripts/backup_desk_db.py                    # default output path
    python scripts/backup_desk_db.py --output /tmp/desk.db
    python scripts/backup_desk_db.py restore --from backup.db --yes

Restore copies the backup over the live database path. The desk must be
stopped first: SQLite's backup API is for taking copies, not for merging a
snapshot into a database that has moved on.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import os
import tempfile
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.desk import audit_chain  # noqa: E402
from src.desk.store import db_path  # noqa: E402


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("data") / "backups" / f"desk-{stamp}.db"


def _deals(conn: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in conn.execute("SELECT id FROM deals").fetchall()]


def _verify(conn: sqlite3.Connection) -> list[str]:
    from src.desk.migrations import CURRENT_SCHEMA_VERSION

    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Database has broken references")
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > CURRENT_SCHEMA_VERSION:
        raise ValueError("Backup schema is newer than this build")
    required = {
        "deals", "suppliers", "quote_events", "drafts", "desk_chain", "decisions", "snapshots", "hazard_outcomes",
    }
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not required <= tables:
        raise ValueError("Not a complete desk database")
    deals = _deals(conn)
    broken = [deal for deal in deals if not audit_chain.verify_rows(audit_chain.chain_rows(conn, deal))["ok"]]
    if broken:
        raise ValueError(f"Audit chains do not verify for deals {broken}")
    return deals


def _stage_copy(source: Path, folder: Path) -> tuple[Path, list[str]]:
    """Take a consistent SQLite snapshot, including committed WAL content.

    Verify the copy itself: the live source may acquire new deals immediately
    after the backup boundary. No source state from a later moment is compared.
    """
    handle, name = tempfile.mkstemp(prefix=".desk-verified-", suffix=".db", dir=folder)
    os.close(handle)
    staged = Path(name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(staged)) as dst:
                src.backup(dst)
                deals = _verify(dst)
                # Leave one self-contained file, with no restoration dependency
                # on a stale journal file under the destination's name.
                dst.execute("PRAGMA journal_mode=DELETE")
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        return staged, deals
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _sync_folder(folder: Path) -> None:
    # Persist the directory entry as well as the SQLite bytes after publication.
    if os.name == "posix":
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def backup(source: Path, output: Path) -> dict[str, object]:
    """Publish only a verified, self-contained snapshot. Never overwrite."""
    if not source.is_file():
        print(f"no desk database at {source}", file=sys.stderr)
        raise SystemExit(2)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        print(f"refusing to overwrite {output}", file=sys.stderr)
        raise SystemExit(2)
    started = time.monotonic()
    staged = None
    try:
        staged, deals = _stage_copy(source, output.parent)
        # A concurrent backup to the same name cannot replace the winner.
        os.link(staged, output)
        _sync_folder(output.parent)
    except (sqlite3.Error, ValueError, OSError) as exc:
        print(f"backup refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    elapsed = time.monotonic() - started
    size = output.stat().st_size
    print(f"backed up {source} -> {output} ({size} bytes, {len(deals)} deal(s), all chains verify, {elapsed:.2f}s)")
    return {"output": str(output), "deals": len(deals), "size": size, "elapsed_s": round(elapsed, 2)}


def restore(backup_file: Path, target: Path, assume_yes: bool) -> int:
    """Verify in a staging file BEFORE atomically replacing an offline database."""
    if not backup_file.is_file():
        print(f"no backup file at {backup_file}", file=sys.stderr)
        return 2
    if backup_file.resolve() == target.resolve() or (target.exists() and os.path.samefile(backup_file, target)):
        print("backup and target must be different files", file=sys.stderr)
        return 2
    if target.exists() and not assume_yes:
        print(f"{target} exists. Stop the desk, then re-run with --yes to replace it.", file=sys.stderr)
        return 1
    # A live or uncheckpointed SQLite WAL must never be paired with replacement
    # bytes. Refuse instead of deleting recovery data or guessing it is unused.
    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        print("restore refused: stop the desk and checkpoint/close the target database first; journal files remain",
              file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    published = False
    try:
        staged, deals = _stage_copy(backup_file, target.parent)
        if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("target journal appeared; the desk must remain stopped during restore")
        if assume_yes:
            os.replace(staged, target)
        else:
            os.link(staged, target)
        published = True
        _sync_folder(target.parent)
    except (sqlite3.Error, ValueError, OSError) as exc:
        detail = ("replacement installed but durability sync failed" if published
                  else "the existing database was not replaced")
        print(f"restore refused: {exc}; {detail}", file=sys.stderr)
        return 1
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    print(f"restored {backup_file} -> {target} ({len(deals)} deal(s), all chains verify).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup-desk-db", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    backup_parser = sub.add_parser("backup", help="Online backup + chain verification.")
    backup_parser.add_argument(
        "--output",
        default=None,
        help="Destination file (default data/backups/desk-<stamp>.db).",
    )
    backup_parser.add_argument(
        "--source",
        default=None,
        help="Source database (default TRADEFLOW_DESK_DB, else data/desk.db).",
    )

    restore_parser = sub.add_parser("restore", help="Copy a backup over the live path. Stop the desk first.")
    restore_parser.add_argument("--from", dest="from_file", required=True)
    restore_parser.add_argument(
        "--target",
        default=None,
        help="Live path (default TRADEFLOW_DESK_DB, else data/desk.db).",
    )
    restore_parser.add_argument("--yes", action="store_true", help="Replace an existing live database.")

    args = parser.parse_args(argv)
    if args.cmd == "backup":
        source = Path(args.source) if args.source else Path(db_path())
        output = Path(args.output) if args.output else _default_output()
        backup(source, output)
        return 0
    if args.cmd == "restore":
        target = Path(args.target) if args.target else Path(db_path())
        return restore(Path(args.from_file), target, args.yes)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
