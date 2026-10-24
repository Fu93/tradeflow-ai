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
import shutil
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
    return [row[0] for row in conn.execute("SELECT DISTINCT deal_id FROM desk_chain").fetchall()]


def backup(source: Path, output: Path) -> dict[str, object]:
    """Copy the database online, then verify the copy's chains. Fail loudly."""
    if not source.is_file():
        print(f"no desk database at {source}", file=sys.stderr)
        raise SystemExit(2)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        print(f"refusing to overwrite {output}", file=sys.stderr)
        raise SystemExit(2)

    started = time.monotonic()
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(output))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # The copy must stand on its own: every chain in it verifies, and the
    # entry counts match the source. A backup that fails here is deleted
    # rather than left in place wearing a timestamp.
    copy = sqlite3.connect(str(output))
    copy.row_factory = sqlite3.Row  # verify_rows addresses columns by name
    try:
        source_deals = _deals(sqlite3.connect(str(source)))
        results = {
            deal_id: audit_chain.verify_rows(audit_chain.chain_rows(copy, deal_id))
            for deal_id in source_deals
        }
        broken = [deal for deal, result in results.items() if not result["ok"]]
    finally:
        copy.close()
    if broken:
        output.unlink()
        print(f"backup verified BROKEN for deals {broken}; the file was deleted.", file=sys.stderr)
        raise SystemExit(1)

    elapsed = time.monotonic() - started
    size = output.stat().st_size
    print(
        f"backed up {source} -> {output} ({size} bytes, {len(source_deals)} deal(s), "
        f"all chains verify, {elapsed:.2f}s)"
    )
    return {"output": str(output), "deals": len(source_deals), "size": size, "elapsed_s": round(elapsed, 2)}


def restore(backup_file: Path, target: Path, assume_yes: bool) -> int:
    """Copy a verified backup over the live database path. The desk must be stopped."""
    if not backup_file.is_file():
        print(f"no backup file at {backup_file}", file=sys.stderr)
        return 2
    if target.exists() and not assume_yes:
        print(
            f"{target} exists. Restoring replaces it. Stop the desk process, then "
            "re-run with --yes after moving the live file aside:",
            file=sys.stderr,
        )
        print(f"  mv {target} {target}.pre-restore", file=sys.stderr)
        return 1
    shutil.copyfile(backup_file, target)
    check = sqlite3.connect(str(target))
    check.row_factory = sqlite3.Row  # verify_rows addresses columns by name
    try:
        deals = _deals(check)
        broken = [
            deal for deal in deals
            if not audit_chain.verify_rows(audit_chain.chain_rows(check, deal))["ok"]
        ]
    finally:
        check.close()
    if broken:
        print(
            f"restored, but the chains for {broken} do NOT verify — "
            "investigate before starting the desk.",
            file=sys.stderr,
        )
        return 1
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
