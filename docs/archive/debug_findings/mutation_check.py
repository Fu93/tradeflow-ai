"""Mutation check: prove the test suite can actually fail.

Each entry injects one deliberate bug into production code, runs the tests that
are supposed to catch it, and restores the file. A mutation that SURVIVES is a
hole in the suite (or a missing test) -- exactly what this pass hunts for.

    python debug_findings/mutation_check.py               # run everything
    python debug_findings/mutation_check.py fsm_illegal   # run one
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (name, file, anchor, replacement, pytest subset)
PY_MUTATIONS = [
    (
        "price_clamp_removed",
        "src/engine/bilingual.py",
        "        val = float(value)\n"
        "        if cls.MIN_MENTIONED_PRICE <= val <= cls.MAX_MENTIONED_PRICE:\n"
        "            return val\n"
        "        return None",
        "        val = float(value)\n"
        "        return val  # MUTANT",
        ["tests/test_bilingual.py", "tests/test_logrolling_guardrail.py"],
    ),
    (
        "guardrail_patterns_emptied",
        "src/engine/bilingual.py",
        "        for pattern in cls.FORBIDDEN_COMPROMISE_PATTERNS:",
        "        for pattern in ():  # MUTANT",
        ["tests/test_logrolling_guardrail.py"],
    ),
    (
        "approve_uses_target_price",
        "src/app/main.py",
        '                    "recommended_offer_price": float(auth_price),',
        '                    "recommended_offer_price": float(target_price),  # MUTANT',
        ["tests/test_audit_2026_10_17_fixes.py", "tests/test_copilot_human_approval.py"],
    ),
    (
        "annotate_last_turn_noop",
        "src/session/manager.py",
        "        if not isinstance(extra, dict) or not extra:\n            return False",
        "        if not isinstance(extra, dict) or not extra:\n"
        "            return False\n"
        "        return False  # MUTANT",
        ["tests/test_audit_2026_10_17_fixes.py", "tests/test_copilot_human_approval.py"],
    ),
    (
        "auction_savings_constant",
        "src/engine/multi_solver.py",
        "        total_savings_pct = savings_vs_first_quote_pct",
        "        total_savings_pct = 0.222  # MUTANT",
        ["tests/test_multi_solver.py", "tests/test_multi_demo.py"],
    ),
    (
        "solver_best_index_fixed",
        "src/engine/solver.py",
        '    i_open, i_sched = solve_result["best"]',
        "    i_open, i_sched = 0, 0  # MUTANT",
        ["tests/test_solver_behavior.py", "tests/test_multi_solver.py"],
    ),
    (
        "fsm_illegal_transition_allowed",
        "src/session/manager.py",
        "        allowed_next = VALID_TRANSITIONS.get(current_state, [])\n"
        "        if new_state not in allowed_next:",
        "        allowed_next = VALID_TRANSITIONS.get(current_state, [])\n"
        "        if False:  # MUTANT",
        ["tests/test_session_fsm.py"],
    ),
    (
        "cas_conflict_ignored",
        "src/session/manager.py",
        "            if cursor.rowcount == 0:\n"
        "                # 樂觀鎖衝突：版本號或當前狀態已被其他並發協程搶先修改",
        "            if False:  # MUTANT\n"
        "                # 樂觀鎖衝突：版本號或當前狀態已被其他並發協程搶先修改",
        ["tests/test_concurrency_cas.py"],
    ),
    (
        "stage2_error_swallowed",
        "src/app/main.py",
        '        "stage2_error": stage2_error,',
        '        "stage2_error": None,  # MUTANT',
        ["tests/test_audit_2026_10_17_fixes.py", "tests/test_async_stage2.py"],
    ),
    (
        "safe_regex_truncation_removed",
        "src/engine/safe_regex.py",
        "        # 安全長度截斷：Tier 3（內建 re）沒有超時守衛，這是它唯一的保護。\n"
        "        if len(text) > MAX_INPUT_LEN:\n"
        "            text = text[:MAX_INPUT_LEN]",
        "        # MUTANT: no truncation",
        ["tests/test_safe_regex.py"],
    ),
]

# Front-end mutations are exercised in a real browser instead of pytest.
JS_MUTATIONS = [
    (
        "dashboard_risk_zero_forged",
        "src/web/static/js/app.js",
        '  document.getElementById("cbPct").textContent = pBreak === null ? "—" : `${pBreak}%`;',
        '  document.getElementById("cbPct").textContent = "0.0%";  // MUTANT',
    ),
    (
        "dashboard_prior_hardcoded",
        "src/web/static/js/app.js",
        "  const priorMean = session.spec ? tfNum(session.spec.prior_reserve_mean, null) : null;",
        "  const priorMean = 1.08;  // MUTANT",
    ),
]

JS_PROBE = r'''
import json, sys, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
DEAL = "UI_MUTANT_01"

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())

PRIOR = 1.25
TARGET = 12.0
try:
    post("/api/v1/sessions", {"session_id": DEAL, "product_name": "Mutant Umbrella",
                              "target_price": TARGET, "ceiling_price": 16.2, "moq": 5000,
                              "max_rounds": 5, "prior_reserve_mean": PRIOR})
except Exception as exc:
    print(json.dumps({"setup_error": repr(exc)}))
    sys.exit(2)
try:
    post(f"/api/v1/sessions/{DEAL}/round", {"opponent_raw_text": "lowest 14.80",
                                            "opponent_offer_price": 14.80, "mode": "copilot"})
except Exception as exc:
    print(json.dumps({"round_error": repr(exc)}))

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 950})
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.wait_for_timeout(1500)
    pg.evaluate("""(deal) => {
      const rows = [...document.querySelectorAll('.session-item')];
      const r = rows.find(x => x.textContent.includes(deal));
      if (r) r.click();
    }""", DEAL)
    pg.wait_for_timeout(3000)
    out = pg.evaluate("""() => {
      const inst = (typeof Chart !== 'undefined' && Chart.getChart) ? Chart.getChart('beliefChart') : null;
      return {
        risk: (document.getElementById('cbPct') || {}).textContent || '',
        chart: inst ? inst.data.datasets[0].data : [],
        empty: !!(document.getElementById('beliefEmpty') || {}).hidden,
      };
    }""")
    b.close()

risk = out["risk"].strip()
chart = out["chart"]
ok = True
if risk == "0.0%":
    ok = False
if risk != "\u2014" and not (risk.endswith("%") and 0.0 < float(risk.rstrip("%")) < 100.0):
    ok = False
# The prior point must be this session's own prior_reserve_mean * target,
# not the umbrella default 1.08 (which renders as 12.96 for a 12.0 target).
expected_prior = PRIOR * TARGET
if not chart or chart[0] is None or abs(chart[0] - expected_prior) > 0.01:
    ok = False
print(json.dumps({"risk": risk, "chart": chart, "expected_prior": expected_prior,
                  "full_chart": out["empty"], "ok": ok}))
sys.exit(0 if ok else 1)
'''


def run_pytest(subset, timeout=900):
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:randomly"] + subset
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return 124, time.time() - t0, ["TIMEOUT"]
    tail = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()][-1:]
    return p.returncode, time.time() - t0, tail or [""]


def run_js_probe(timeout=300):
    t0 = time.time()
    script = os.path.join(ROOT, "debug_findings", "_js_mutant_probe.py")
    with io.open(script, "w", encoding="utf-8") as fh:
        fh.write(JS_PROBE)
    try:
        p = subprocess.run([sys.executable, script], capture_output=True, text=True,
                           timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return 124, time.time() - t0, ["TIMEOUT"]
    finally:
        try:
            os.remove(script)
        except OSError:
            pass
    return p.returncode, time.time() - t0, [(p.stdout or "").strip() or (p.stderr or "").strip()[-200:]]


def patch(path, anchor, replacement):
    """Return (original_text, newline) or None when the anchor is absent."""
    full = os.path.join(ROOT, path)
    raw = io.open(full, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    if anchor not in text:
        return None
    return text.replace(anchor, replacement, 1), nl


def mutate(name, path, anchor, replacement, runner):
    patched = patch(path, anchor, replacement)
    if patched is None:
        return name, "ANCHOR-MISSING", "0s", ""
    new_text, nl = patched
    full = os.path.join(ROOT, path)
    backup = full + ".mutbak"
    shutil.copy2(full, backup)
    try:
        with io.open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text.replace("\n", nl))
        code, dur, tail = runner()
    finally:
        shutil.move(backup, full)
    verdict = "KILLED" if code != 0 else "SURVIVED"
    return name, verdict, f"{dur:.0f}s", " ".join(tail)[:160]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = []

    for name, path, anchor, repl, subset in PY_MUTATIONS:
        if only and only != name:
            continue
        print(f"... {name}", flush=True)
        results.append(mutate(name, path, anchor, repl, lambda s=subset: run_pytest(s)))

    for name, path, anchor, repl in JS_MUTATIONS:
        if only and only != name:
            continue
        print(f"... {name} (browser)", flush=True)
        results.append(mutate(name, path, anchor, repl, run_js_probe))

    print("\n=== mutation results ===")
    survived = []
    for name, verdict, dur, tail in results:
        print(f"  {verdict:15s} {name:34s} {dur:>5s}  {tail}")
        if verdict != "KILLED":
            survived.append(name)
    print(f"\ntotal={len(results)} killed={len(results)-len(survived)} holes={len(survived)}")
    if survived:
        print("holes: " + ", ".join(survived))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
