#!/usr/bin/env python3
"""Drive a running desk through intake, confirm, the audit chain, and send.

Usage: python scripts/desk_verify.py <base-url>

The server must already be up. TRADEFLOW_API_KEY in this process must be the
key the server is using. The script exits 0 only when every check passes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request


def _request(base: str, method: str, path: str, body: dict | None, key: str) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return exc.code, payload
        return 0, {"detail": str(exc.reason)}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/desk_verify.py <base-url>")
        return 2
    base = sys.argv[1]
    key = os.environ.get("TRADEFLOW_API_KEY", "").strip()
    if not key:
        print("FAIL server-key: TRADEFLOW_API_KEY is not set in this process")
        return 1
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name + ": " + detail)

    suppliers = [
        {"name": "Ningbo Desk", "address": "ningbo-desk@example.test"},
        {"name": "Wenzhou Desk", "address": "wenzhou-desk@example.test"},
        {"name": "Yiwu Desk", "address": "yiwu-desk@example.test"},
        {"name": "Foshan Desk", "address": "foshan-desk@example.test"},
    ]
    status, created = _request(
        base,
        "POST",
        "/api/v1/desk/deals",
        {
            "product": "desk-verify valve",
            "quantity": 10,
            "target_price": 12,
            "ceiling_price": 20,
            "currency": "CNY",
            "suppliers": suppliers,
        },
        key,
    )
    deal = created.get("deal") or {}
    deal_id = str(deal.get("id") or "")
    supplier_id = str((created.get("suppliers") or [{}])[0].get("id") or "")
    check("create-deal", status == 200 and bool(deal_id) and bool(supplier_id), f"HTTP {status}")
    if not deal_id:
        print(f"{sum(1 for _name, ok, _detail in checks if not ok)} failed")
        return 1

    status, ingested = _request(
        base,
        "POST",
        f"/api/v1/desk/deals/{deal_id}/quotes",
        {
            "source": "email",
            "raw_text": "Fixture quote: unit price 15.60 CNY per piece.",
            "sender": "ningbo-desk@example.test",
            "supplier_id": supplier_id,
        },
        key,
    )
    event = ingested.get("event") or {}
    event_id = str(event.get("id") or "")
    check(
        "ingest-fixture",
        status == 200 and event.get("status") == "review" and bool(event.get("raw_sha256")),
        f"HTTP {status} status={event.get('status')}",
    )

    status, reviewed = _request(
        base,
        "POST",
        f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
        {"action": "accept", "supplier_id": supplier_id, "chosen_token": "15.60"},
        key,
    )
    drafts = [row for row in (reviewed.get("drafts") or []) if row.get("status") == "draft"]
    draft = drafts[0] if drafts else {}
    check("confirm", status == 200 and bool(draft.get("decision_id")), f"HTTP {status} drafts={len(drafts)}")

    status, chain = _request(base, "GET", f"/api/v1/desk/deals/{deal_id}/audit/verify", None, key)
    check("chain-verifies", status == 200 and chain.get("ok") is True, f"HTTP {status} ok={chain.get('ok')}")
    status, evidence = _request(base, "GET", f"/api/v1/desk/deals/{deal_id}/evidence.json", None, key)
    types = {row.get("type") for row in evidence.get("entries") or []}
    check(
        "evidence",
        status == 200 and {"quote_ingested", "review", "decision"} <= types,
        f"HTTP {status} types={sorted(item for item in types if item)}",
    )

    body = str(draft.get("body") or "")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    draft_id = str(draft.get("id") or "")
    status, refused = _request(
        base,
        "POST",
        f"/api/v1/desk/drafts/{draft_id}/send",
        {"human_approved": False, "body_sha256": digest},
        key,
    )
    check("send-without-approval", 400 <= status < 500, f"HTTP {status} {refused.get('detail')}")

    status, sent = _request(
        base,
        "POST",
        f"/api/v1/desk/drafts/{draft_id}/send",
        {"human_approved": True, "body_sha256": digest},
        key,
    )
    check(
        "approve",
        status == 200 and sent.get("status") in {"approved", "sent"},
        f"HTTP {status} status={sent.get('status')}",
    )
    status, again = _request(base, "GET", f"/api/v1/desk/deals/{deal_id}/audit/verify", None, key)
    check("audit-after-send", status == 200 and again.get("ok") is True, f"HTTP {status} ok={again.get('ok')}")

    failed = [name for name, ok, _detail in checks if not ok]
    print(f"{len(checks) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
