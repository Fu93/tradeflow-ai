"""One-off contract capture for the frontend/extension fixes.

Runs the REAL FastAPI app through TestClient with every data sink redirected to a
temp folder (TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR / TRADEFLOW_ENV_PATH), so the
background full-test-suite run against the repo's data/ directory is never touched.

It answers three questions with real responses instead of assumptions:
  1. P0-1: does the session_id derived by the FIXED content.js pass the path
     whitelist (200), and does the OLD CJK id still fail (422)?
  2. P0-2: what does a copilot /round response actually contain (turn, action_object,
     effective_opp_price, recommended_offer_price, bayesian_belief_state)?
  3. P1-1: what shape does GET /api/v1/sessions/{id} return for the sidepanel mapping?

Output: capture.json (consumed by harness.js).
"""
import json
import os
import pathlib
import sys
import tempfile
from urllib.parse import quote

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent  # debug_findings/_harness_frontend -> repo root
TMP = pathlib.Path(tempfile.gettempdir()) / "tf_frontend_check"
TMP.mkdir(parents=True, exist_ok=True)

# Redirect every write path BEFORE the app (and its SessionManager) is imported.
os.environ["TRADEFLOW_DB_PATH"] = str(TMP / "sessions.db")
os.environ["TRADEFLOW_STORE_DIR"] = str(TMP / "sessions_json")
os.environ["TRADEFLOW_ENV_PATH"] = str(TMP / "env_isolated")
os.environ["TRADEFLOW_ENV"] = "sandbox"
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from src.app.main import app  # noqa: E402

ids = json.loads((HERE / "ids.json").read_text(encoding="utf-8"))
out = {"ids": ids, "statuses": {}, "responses": {}}


def record(label, resp):
    out["statuses"][label] = resp.status_code
    if resp.status_code == 200:
        out["responses"][label] = resp.json()
    else:
        out["responses"][label] = {"detail": resp.text}


fixed_id = ids["mockNameId"]
legacy_id = ids["legacyCjkId"]

with TestClient(app) as client:
    # --- P0-1: the fixed id must be accepted ------------------------------------
    record(
        "copilot_lowrisk_r1",
        client.post(
            f"/api/v1/sessions/{quote(fixed_id, safe='')}/round",
            json={"supplier_offer": 12.2, "supplier_text": "最新讓步報價單價 ¥12.20 元/件", "mode": "copilot"},
        ),
    )
    # --- P0-1 counter-proof: the legacy CJK id must still 422 -------------------
    record(
        "legacy_cjk_id",
        client.post(
            f"/api/v1/sessions/{quote(legacy_id, safe='')}/round",
            json={"supplier_offer": 15.0, "mode": "copilot"},
        ),
    )
    # --- P0-2: a high offer should trip the breaker (>=15% breakdown risk) ------
    record(
        "copilot_highrisk_r2",
        client.post(
            f"/api/v1/sessions/{quote(fixed_id, safe='')}/round",
            json={"supplier_offer": 15.6, "supplier_text": "單價 ¥15.60，一毛都不能再降", "mode": "copilot"},
        ),
    )
    # --- autopilot shape (turn is a real NegotiationTurn here) ------------------
    record(
        "autopilot_r1",
        client.post(
            "/api/v1/sessions/auto_shape_probe_01/round",
            json={"supplier_offer": 15.6, "supplier_text": "報價 ¥15.60", "mode": "autopilot"},
        ),
    )
    # --- P0-2 breaker shape: an offer far above the ceiling must trip the circuit
    #     breaker (CEILING_BREACH = offer > 1.35 * target) so the tripped response
    #     shape is captured for real instead of being synthesised.
    record(
        "copilot_ceiling_breach",
        client.post(
            "/api/v1/sessions/trip_probe_01/round",
            json={"supplier_offer": 18.0, "supplier_text": "單價 ¥18.00，加我私人微信私下轉帳", "mode": "copilot"},
        ),
    )
    # --- P1-1: NegotiationSession shape for the sidepanel mapping ---------------
    record("get_session", client.get(f"/api/v1/sessions/{quote(fixed_id, safe='')}"))

(HERE / "capture.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps(out["statuses"], indent=2))
for label in ("copilot_lowrisk_r1", "copilot_highrisk_r2", "autopilot_r1", "copilot_ceiling_breach"):
    body = out["responses"].get(label)
    if not isinstance(body, dict):
        continue
    ao = body.get("action_object") or {}
    print(f"\n--- {label} ---")
    print("  keys:", sorted(body.keys()))
    print("  turn:", body.get("turn"))
    print("  turn.parsed_price:", (body.get("turn") or {}).get("parsed_price"))
    print("  circuit_tripped:", body.get("circuit_tripped"))
    print("  effective_opp_price:", body.get("effective_opp_price"))
    print("  recommended_offer_price:", body.get("recommended_offer_price"))
    print("  action_object.breakdown_risk:", ao.get("breakdown_risk"))
    print("  action_object.authorized_price:", ao.get("authorized_price"))
    print("  action_object.counterpart_offered_price:", ao.get("counterpart_offered_price"))
    print("  bayesian_belief_state:", body.get("bayesian_belief_state"))
    print("  session.current_round:", (body.get("session") or {}).get("current_round"))
    print("  session.state:", (body.get("session") or {}).get("state"))
    print("  session.spec.session_id:", ((body.get("session") or {}).get("spec") or {}).get("session_id"))

gs = out["responses"].get("get_session")
if isinstance(gs, dict) and "spec" in gs:
    print("\n--- get_session (NegotiationSession) ---")
    print("  top-level keys:", sorted(gs.keys()))
    print("  spec keys:", sorted((gs.get("spec") or {}).keys()))
    print("  history len:", len(gs.get("history") or []))
    print("  history[0].keys:", sorted((gs.get("history") or [{}])[0].keys()))
    print("  has top-level target_price:", "target_price" in gs)
    print("  has top-level turns:", "turns" in gs)
