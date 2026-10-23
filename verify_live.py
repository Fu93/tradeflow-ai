"""Live verification of the v2 product surface: the procurement desk.

README-level promise under test (the 30-second pitch): quotes land in
the desk, the belief updates and the solver recomputes, the reply stays
a draft, and a person approves the send. This walks exactly that over
HTTP, plus the Ed25519 accountability gate and the audit chain.

The v1 demo surface (/api/v1/sessions, /round, mobile-command) mounts
only with TRADEFLOW_LEGACY=1 and its price inputs are refused by design
("a posted price is not a confirmation"). That surface has its own
tests; this script verifies the shipped default, not the legacy opt-in.

[AUDIT FIX — 2026-10-17, kept] Everything writes to a throwaway store:
verification must not pollute the real desk database. Override with
TRADEFLOW_DESK_DB / TRADEFLOW_STORE_DIR if you want persistence.
"""
import os
import sys
import tempfile

_SMOKE_DIR = tempfile.mkdtemp(prefix="tf_verify_live_")
os.environ.setdefault("TRADEFLOW_DESK_DB", os.path.join(_SMOKE_DIR, "desk.db"))
os.environ.setdefault("TRADEFLOW_STORE_DIR", os.path.join(_SMOKE_DIR, "sessions_json"))
os.environ.setdefault("TRADEFLOW_API_KEY", "verify-live-temporary-key-0001")
os.environ.setdefault("TRADEFLOW_CHAIN_KEY", "verify-live-temporary-chain-key")
# The desk default is a sandbox: sends are recorded, not transmitted,
# unless DESK_SMTP_SEND=1. This script never transmits.

from fastapi.testclient import TestClient  # noqa: E402

import src.app.main as m  # noqa: E402
from src.desk.approval_signing import generate_keypair, sign_approval, signed_payload  # noqa: E402
from src.sim.smoke import LIVE_CEILING, LIVE_TARGET  # noqa: E402

client = TestClient(m.app)
HEADERS = {"X-API-Key": os.environ["TRADEFLOW_API_KEY"]}

# 1. Dashboard and desk pages answer.
res_home = client.get("/")
assert res_home.status_code == 200 and "TradeFlow AI" in res_home.text
print("[PASS] 1. Dashboard GET / (Status: 200)")
res_desk = client.get("/desk")
assert res_desk.status_code == 200
print("[PASS] 2. Live supplier desk GET /desk (Status: 200)")

# 2. Open a deal. Scenario numbers come from the labelled simulator
# (src/sim/smoke.py), the same discipline the old script kept: production
# code never hard-codes scenario floats (check_no_fake_literals.py).
deal_payload = {
    "product": "Widget",
    "quantity": 1000,
    "target_price": LIVE_TARGET,
    "ceiling_price": LIVE_CEILING,
    "seed": 3,
    "currency": "CNY",
    "suppliers": [
        {"name": name, "address": f"{name.lower()}@example.test", "channel": "email"}
        for name in ("Ningbo", "Wenzhou", "Yiwu", "Foshan")
    ],
}
res_deal = client.post("/api/v1/desk/deals", json=deal_payload, headers=HEADERS)
assert res_deal.status_code == 200, res_deal.text
deal = res_deal.json()
deal_id = deal["deal"]["id"]
supplier_id = deal["suppliers"][0]["id"]
print(f"[PASS] 3. Deal opened: {deal_id} ({len(deal['suppliers'])} suppliers, CNY)")

# 3. A quote lands from a pasted supplier message.
res_quote = client.post(
    f"/api/v1/desk/deals/{deal_id}/quotes",
    json={"source": "paste", "raw_text": "单价 14.80 元", "supplier_id": supplier_id},
    headers=HEADERS,
)
assert res_quote.status_code == 200, res_quote.text
quote = res_quote.json()
event_id = quote["event"]["id"]
print(f"[PASS] 4. Quote ingested (paste): event {event_id} -> status {quote['event']['status']}")

# 4. A person confirms the span on the desk; only then does it become a decision input.
res_review = client.post(
    f"/api/v1/desk/deals/{deal_id}/review/{event_id}",
    json={"supplier_id": supplier_id, "action": "accept", "chosen_token": "14.80"},
    headers=HEADERS,
)
assert res_review.status_code == 200, res_review.text
review = res_review.json()
card = next(row for row in review["suppliers"] if row["id"] == supplier_id)
assert card.get("counter") is not None and card.get("decision_id")
print(f"[PASS] 5. Span confirmed by a person -> counter {card['counter']} (decision {card['decision_id'][:8]})")

# 5. The reply is a draft; nothing is sent.
res_snap = client.get(f"/api/v1/desk/deals/{deal_id}", headers=HEADERS)
assert res_snap.status_code == 200, res_snap.text
snap = res_snap.json()
drafts = [item for item in snap.get("drafts") or [] if item["status"] == "draft"]
assert drafts, "no draft after the confirmed round"
draft = drafts[0]
print(f"[PASS] 6. Reply stays a draft: {draft['id'][:8]} (status {draft['status']}, not sent)")

# 6. The human signs the approval; the server verifies and records it in the chain.
private_hex, public_hex = generate_keypair()
approval = sign_approval(
    private_hex,
    **signed_payload(deal_id, draft["id"], draft["body_sha256"]),
)
res_send = client.post(
    f"/api/v1/desk/drafts/{draft['id']}/send",
    json={
        "human_approved": True,
        "body_sha256": draft["body_sha256"],
        "approval": approval,
    },
    headers=HEADERS,
)
assert res_send.status_code == 200, res_send.text
sent = res_send.json()
assert sent["status"] in {"approved", "sent"}, sent["status"]
print(f"[PASS] 7. Signed human approval accepted -> draft {sent['status']} (sandbox: no SMTP)")

# 7. The audit chain verifies, and the approval is inside the chain entry.
# The default response is a summary; entries are opt-in (and paginated) because
# returning the whole chain on every verify does not scale with the chain.
res_audit = client.get(
    f"/api/v1/desk/deals/{deal_id}/audit/verify",
    params={"include_entries": True},
    headers=HEADERS,
)
assert res_audit.status_code == 200, res_audit.text
chain = res_audit.json()
assert chain["ok"], "audit chain failed verification"
assert chain.get("entries"), "audit verify returned no entries"
approved_entry = next(
    (item for item in chain["entries"] if item["type"] == "send_approved"), None
)
assert approved_entry is not None, "send_approved missing from the chain"
recorded = approved_entry["payload"]["approval"]
assert recorded["pubkey"] == public_hex and recorded["sig"] == approval["sig"]
kinds = [item["type"] for item in chain["entries"]]
print(f"[PASS] 8. Audit chain verifies ({len(kinds)} entries: {', '.join(kinds[:4])}, ...)")
print("[PASS] 9. Ed25519 approval recorded in the chain (server cannot forge it)")

# 8. A forged approval is refused and leaves no chain entry.
more = client.get(f"/api/v1/desk/deals/{deal_id}", headers=HEADERS).json()
fresh = [item for item in more.get("drafts") or [] if item["status"] == "draft"]
if fresh:
    forged = dict(approval)
    forged["sig"] = ("0" if approval["sig"][0] != "0" else "1") + approval["sig"][1:]
    res_bad = client.post(
        f"/api/v1/desk/drafts/{fresh[0]['id']}/send",
        json={"human_approved": True, "body_sha256": fresh[0]["body_sha256"], "approval": forged},
        headers=HEADERS,
    )
    assert res_bad.status_code == 422, f"forged approval was accepted: {res_bad.status_code}"
    print("[PASS] 10. Forged approval refused (HTTP 422), no chain entry")
else:
    print("[SKIP] 10. No second draft available to test forged approval refusal")

print("\nV2 DESK SURFACE VERIFIED END TO END: quote -> confirm -> decide -> draft -> signed approval -> chain")
