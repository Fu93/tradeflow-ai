# Temp verification script (not repo source): functional checks for the debug fixes.
import os
import sys
import time
import logging

sys.path.insert(0, os.getcwd())

# Redirect session storage to a temp dir BEFORE importing the app.
import tempfile

_tmp = tempfile.mkdtemp(prefix="tf_verify_")
os.environ["TRADEFLOW_DB_PATH"] = os.path.join(_tmp, "sessions.db")
os.environ["TRADEFLOW_STORE_DIR"] = os.path.join(_tmp, "sessions_json")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} {detail}")
    print(("PASS " if cond else "FAIL ") + name + (" | " + detail if detail else ""), flush=True)


# ---------- 1. bilingual price-scan parser ----------
from src.engine.bilingual import EnglishExecutiveCommandParser, ExecutiveActionEnum

cases = [
    ("can you do 5000 units at 13.8", 13.8),
    ("at 13.8", 13.8),
    ("offer 13.8", 13.8),
    ("$13.8", 13.8),
    ("we can do 13.5", 13.5),
    ("accept 14.20", 14.2),  # 既有孤立小數 fallback（本就命中）
]
for text, expected in cases:
    cmd = EnglishExecutiveCommandParser.parse(text)
    got = cmd.override_price
    check(f"parse[{text!r}]", got == expected, f"expected={expected} got={got}")

cmd = EnglishExecutiveCommandParser.parse("1000 to 2000 units")
check("parse['1000 to 2000 units'] -> no price", cmd.override_price is None, f"got={cmd.override_price}")

cmd = EnglishExecutiveCommandParser.parse("price 5000 units")
check("parse['price 5000 units'] -> no price (quantity lookahead)", cmd.override_price is None, f"got={cmd.override_price}")

# ---------- 2. FastAPI app with fixes ----------
from fastapi.testclient import TestClient
from src.app.main import app

records = []


class _Cap(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


logging.getLogger().addHandler(_Cap())
logging.getLogger("tradeflow.app").addHandler(_Cap())
logging.getLogger("tradeflow.app").setLevel(logging.INFO)

with TestClient(app) as client:
    r = client.get("/health")
    check("health 200", r.status_code == 200, r.text[:60])

    spec = {
        "session_id": "verify-cache-a",
        "product_name": "test umbrella",
        "target_price": 12.0,
        "ceiling_price": 15.0,
        "moq": 1000,
        "max_rounds": 5,
        "supplier_id": "supplier-verify-01",
    }
    r = client.post("/api/v1/sessions", json=spec)
    check("create session A", r.status_code == 200)

    round_body = {
        "opponent_raw_text": "老闆，原料漲價了，最低報 14.20 元/把，沒利潤！",
        "opponent_offer_price": 14.20,
    }
    t0 = time.time()
    r1 = client.post("/api/v1/sessions/verify-cache-a/round", json=round_body)
    t_cold = time.time() - t0
    check("round A1 200", r1.status_code == 200, f"{t_cold:.2f}s")

    # Session B: identical spec & identical first-round input -> solve inputs
    # identical (fixed seed) -> memo cache must hit (fast) with identical values.
    spec_b = dict(spec, session_id="verify-cache-b")
    r = client.post("/api/v1/sessions", json=spec_b)
    check("create session B", r.status_code == 200)
    t0 = time.time()
    r2 = client.post("/api/v1/sessions/verify-cache-b/round", json=round_body)
    t_warm = time.time() - t0
    check("round B1 200", r2.status_code == 200, f"{t_warm:.2f}s")
    check(
        "solver cache hit (warm << cold)",
        t_warm < max(0.3, t_cold * 0.4),
        f"cold={t_cold:.2f}s warm={t_warm:.2f}s",
    )
    try:
        a, b = r1.json(), r2.json()
        ra, rb = a.get("recommendation") or {}, b.get("recommendation") or {}
        same = ra.get("opening_offer_price") == rb.get("opening_offer_price") and ra.get(
            "breakdown_prob"
        ) == rb.get("breakdown_prob")
        check("cache returns identical recommendation", same)
    except Exception as e:  # noqa: BLE001
        check("cache returns identical recommendation", False, f"compare error {e}")

    # v2_belief consistency: round 2 on session A -> deal probability must move
    # (accumulated posterior), not reset to the same prior-based value.
    r3 = client.post("/api/v1/sessions/verify-cache-a/round", json=round_body)
    check("round A2 200", r3.status_code == 200)
    try:
        j1, j3 = r1.json(), r3.json()
        b1 = j1.get("bayesian_belief_state") or {}
        b3 = j3.get("bayesian_belief_state") or {}
        dp1, dp3 = b1.get("deal_probability"), b3.get("deal_probability")
        check(
            "v2_belief deal_probability progresses (not reset)",
            dp1 is not None and dp3 is not None and dp1 != dp3,
            f"round1={dp1} round2={dp3}",
        )
    except Exception as e:  # noqa: BLE001
        check("v2_belief deal_probability progresses", False, f"error {e}")

    # suppliers=[] -> explicit 422
    r = client.post("/api/v1/multi-auction/run", json={"suppliers": []})
    check("multi-auction suppliers=[] -> 422", r.status_code == 422, f"got {r.status_code}")

    # rounds_quotes row length mismatch -> 422
    r = client.post(
        "/api/v1/multi-auction/run",
        json={"suppliers": [{"name": "A"}, {"name": "B"}], "rounds_quotes": [[13.0]]},
    )
    check("multi-auction quotes length mismatch -> 422", r.status_code == 422, f"got {r.status_code}")

    # recreated warning on duplicate session_id
    r = client.post("/api/v1/sessions", json=spec)
    check("recreate session A 200", r.status_code == 200)
    check(
        "recreated warning logged",
        any("RECREATED" in m for m in records),
    )

# ---------- 3. solver memo cache direct timing (isolate from endpoint overhead) ----------
from src.app.main import _solve_round_cached

t0 = time.time()
_r1 = _solve_round_cached(20000, False, 1.08, 0.1)
t_direct_cold = time.time() - t0
t0 = time.time()
_r2 = _solve_round_cached(20000, False, 1.08, 0.1)
t_direct_warm = time.time() - t0
check(
    "memo cache direct hit",
    t_direct_warm < 0.05 and t_direct_cold > 0.2,
    f"cold={t_direct_cold:.2f}s warm={t_direct_warm:.3f}s",
)

# ---------- 4. lifespan warmup evidence ----------
check(
    "lifespan warmup ran",
    any("warmup" in m.lower() for m in records),
)

print(f"SUMMARY PASS={len(PASS)} FAIL={len(FAIL)}")
for f in FAIL:
    print("FAILED:", f)
