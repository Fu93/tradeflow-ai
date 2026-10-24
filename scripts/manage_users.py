"""Manage named desk operator keys: issue, revoke, list.

The desk's shared ``TRADEFLOW_API_KEY`` names nobody; these named keys do.
A raw secret is printed exactly once at issue time and stored only as a
SHA-256 hash, so the operator must keep it when they see it — a database
holder cannot recover a usable key from a row.

Usage (the database follows ``TRADEFLOW_DESK_DB``):

    python scripts/manage_users.py issue --label "Wei (procurement)"
    python scripts/manage_users.py list
    python scripts/manage_users.py revoke --id 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Run as a file (python scripts/manage_users.py): put the repo on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.desk import users  # noqa: E402
from src.desk.store import DeskStore, db_path  # noqa: E402


def _open_store() -> DeskStore:
    """The desk database: TRADEFLOW_DESK_DB, else the documented default path."""
    return DeskStore(db_path())


def _cmd_issue(args: argparse.Namespace) -> int:
    if args.store is not None:
        conn = args.store.conn
    else:
        conn = _open_store().conn
    issued = users.issue(conn, args.label)
    print(json.dumps(issued, indent=2, sort_keys=True))
    print(
        "This is the only time the key is shown. Give it to the operator now; "
        "storage keeps only its hash.",
        file=sys.stderr,
    )
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    conn = args.store.conn if args.store is not None else _open_store().conn
    changed = users.revoke(conn, args.id)
    if changed:
        print(f"user {args.id} revoked; the next request with that key is refused.")
        return 0
    print(f"nothing to revoke: user {args.id} does not exist or was already revoked.", file=sys.stderr)
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    conn = args.store.conn if args.store is not None else _open_store().conn
    rows = users.list_users(conn)
    if not rows:
        print("no named desk keys issued yet.")
        return 0
    for row in rows:
        state = "revoked " + str(row["revoked_at"]) if row["revoked_at"] else "active"
        print(f"{row['id']:>4}  {row['key_prefix']}…  {row['label']}  [{state}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="manage-users",
        description="Issue, list, and revoke named desk operator keys.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    issue = sub.add_parser("issue", help="Mint one named key. The raw secret is shown once.")
    issue.add_argument("--label", required=True, help="Who holds this key, e.g. 'Wei (procurement)'.")

    revoke = sub.add_parser("revoke", help="Revoke by id. Takes effect on the next request.")
    revoke.add_argument("--id", type=int, required=True)

    sub.add_parser("list", help="List named keys. No secret material is shown.")

    args = parser.parse_args(argv)
    args.store = _open_store()
    try:
        if args.cmd == "issue":
            return _cmd_issue(args)
        if args.cmd == "revoke":
            return _cmd_revoke(args)
        if args.cmd == "list":
            return _cmd_list(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        args.store.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
