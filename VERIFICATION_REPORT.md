# VERIFICATION_REPORT — Independent Re-check of DEEP_DEFECT_AUDIT.md Remediation

- **Scope**: every item in `DEEP_DEFECT_AUDIT.md` (6 Critical, 11 High, ~17 Medium), verified by
  **executed probes against the code on disk**, not by reading implementer summaries.
- **Date**: this session | **Repo**: C:\Users\nn201\Desktop\tradeflow-ai | Branch: `master` (uncommitted working tree)
- **Executor**: the Team Lead.
  *Process disclosure*: task-6 designated a dedicated independent verifier. The verifier teammate
  failed to start twice (first: active-child limit; second: the background agent terminated twice
  before producing any output, despite two scoped re-wakes). To close the objective, the Lead ran the
  verification probes personally. This is **less independent than intended** and is flagged as such in
  §5. The raw outputs below are from commands run in this session, not claims copied from implementers.
- **Baseline regression**: `python -m pytest tests -q` → **327 passed, 68 subtests passed in 777.89s**
  (baseline before remediation: 110 passed). Run twice by the Lead with identical results.

---

## 1. Verdict table — audit Critical / High items

| # | Audit item (§) | Verdict | Key evidence (raw) |
|---|---|---|---|
| C1 | §1.1 Endpoints unauthenticated (14 routes) | **VERIFIED FIXED** | 11 probes with key configured: all `401 GATED`; control `GET /api/v1/sessions` with key → `200`. Covers sessions list/get/create/round/approve/multi-auction/tco/bayesian/config telemetry GET+POST/classify. |
| C2 | §1.1 `POST /config/telemetry` unauthenticated `.env` write (the probe that *actually overwrote the real `.env`* during the original audit) | **VERIFIED FIXED** | Production mode → `403 {"status":"refused","hot_reload":false,"reason":"Refusing to rewrite .env in production mode..."}`; temp-.env `sha256 identical: True`. Additionally the test harness now redirects writes via `TRADEFLOW_ENV_PATH` — full `tests/test_api_security.py` (64 tests) ran with the repo `.env` hash **byte-identical before/after** (`7A1B4733…3F2E` both). |
| C3 | §1.2 CORS `*` + credentials | **VERIFIED FIXED** | `Origin: https://evil.example` → `ACAO=None` (simple + preflight); `http://localhost:5173` → echoed; `chrome-extension://abcdefghijklmnop` → echoed. Wildcard with credentials no longer possible. |
| C4 | §1.3 Unbounded DoS (`n_samples=4e9` → 59.6 GiB alloc) | **VERIFIED FIXED** | `n_samples=4e9 → 422`; `n_samples=-5 → 422`; `prior_std=0 → 422` (was bare 500 math-domain); 400-char session_id → `422`. |
| C5 | §1.4 Forgeable mobile-approval channel | **VERIFIED FIXED** | prod + no `X-Webhook-Token` → `401`; prod + wrong token → `401`. Sandbox unset token = fail-open **with a warning log** (documented choice; see §3 residual risk R2). |
| C6 | §5.1 Default mode reaches DEAL_CLOSED unattended | **VERIFIED FIXED** | no-mode round → `autopilot_state=AWAITING_HUMAN_DECISION, pending_human_approval=True`; `mode="bogus"` → same (+ log "defaulting to copilot (never autopilot)"); explicit `mode="autopilot"` still works. |
| H1 | §2.1 Guardrail upper-bound-only (¥9.50 bypass) | **VERIFIED FIXED** | 4/4 bypasses now blocked: duplicate-lower-price → `GuardrailViolationError "Price ¥9.50 is below the authorized floor ¥13.00"`; `15.50 RMB`, `15.50/pcs`, `USD 15.50` all blocked. Control draft passes. |
| H2 | §2.2 Forbidden-phrase list = 7 literals | **VERIFIED FIXED** (by implementer tests, code reviewed) | `FORBIDDEN_COMPROMISE_PATTERNS` regex set present in `bilingual.py`; `tests/test_logrolling_guardrail.py` (39 tests) passes; implementer supplied before/after proof (paraphrases passed pre-fix). Lead re-verified the price-floor half directly; phrase set itself re-run via the suite. |
| H3 | §2.3 English card never guardrailed | **VERIFIED FIXED** | `validate_bilingual` **called** on the live `/round` path (`grep main.py` → `True`), not merely defined. Response includes `executive_card_unverified` marker for external drafts. |
| H4 | §5.2 Logrolling script discarded at `main.py:958` | **VERIFIED FIXED** | LOGROLL branch now renders deposit/MOQ terms; `tests/test_logrolling_guardrail.py` asserts the selected proposal's terms reach the rendered reply (w2). |
| H5 | §5.3 `/command "3"` reports success but no state change | **VERIFIED FIXED** | `command "3"` → `status=success, state_transition_applied=True`, then **`final state=WALK_AWAY`** (pre-fix: stayed AUTOPILOT_CRUISING). |
| H6 | §1.6 Stored XSS via `session_id` → `innerHTML` | **VERIFIED FIXED** | `tests/test_web_xss.py` → **38 passed**; helper-based escaping present; implementer proved non-vacuity: the guard flags **30 sinks on `git show HEAD:` pre-fix sources**, 0 post-fix. |
| H7 | §3.1 Ghost buyer history (`b_offer + 0.05`) | **VERIFIED FIXED** | TitForTat now returns different quotes for different buyer histories (`1.2500` vs `1.1500`); buyer history driven by real counter-offers. |
| H8 | §3.2 First round ignores per-supplier quotes | **VERIFIED FIXED** | quotes `[13.26,15.60,15.92,16.80]` → counters `[12.00,13.67,14.08,14.71]`, **4/4 distinct** (pre-fix: all identical). |
| H9 | §4.1 OOD-500 = train-set accuracy (92.00%) | **VERIFIED FIXED** | Independent recompute: train/test **exact-text intersection = 0**; held-out accuracy **63.08% (123/195)**, train 92.79%, held-out w/o ONNX 40.51% — matches implementer's numbers exactly. `models/intent_classifier_v5.onnx` retrained from `data/ood_train.json` only (`models/onnx_export_metrics.json`). Docs: "92.00" now appears **only** as a correction record in README:178. |
| H10 | §4.3 Benchmark baseline non-differentiation (4/5 identical) | **VERIFIED FIXED** | Regenerated `results/self_play_benchmark.md` (100 matches, seed 42): engine vs baseline differ on **5/5** (8.8/9.2, 20.0/18.4, 19.7/16.0, 15.0/15.1, 15.6/16.1); engine wins 2/5, trails on Hard-Bargainer — **reported honestly in LIMITATIONS §2 rather than tuned away**. Baseline now permitted to violate (proxy shows 20%/40%). |
| H11 | §4.4 "28.8M rollouts (measured)" | **VERIFIED FIXED** | `total_rollouts` unchanged (product by design) but all wording corrected to "tensor grid cells / parameterised product, not an execution count" in README, PITCH, SUBMISSION_DRAFT, LIMITATIONS (incl. the 144M inner-loop discrepancy now documented), `verify_live.py`, `cli.py`, `multi_supplier_auction.md`, `DEMO_VIDEO_FILMING_GUIDE.md`, `index.html`. |

---

## 2. Medium items — verdicts

| Audit item | Verdict | Evidence |
|---|---|---|
| §1.5 `SANDBOX-DEMO-TOKEN` universal bypass | **VERIFIED FIXED** | With `TRADEFLOW_API_KEY` set: header `X-API-Key: SANDBOX-DEMO-TOKEN` → **401** (pre-fix: 200). |
| §1.5 no `.env` loader (dotenv) | **VERIFIED FIXED** | `load_dotenv()` wired in `main.py`; `python-dotenv` + `regex` added to requirements (placed as instructed; `re2` deliberately not added). |
| §4.7 TCO dual formula (2.5× apart) | **VERIFIED FIXED** | `INVENTORY_HOLDING_DAYS` shared by both paths; `tests/test_tco_consistency.py` (8 tests) passes; `tests/test_tco.py` threshold corrected (it had encoded the buggy 2.5× value). |
| §6.3 `secret_scan.py` vacuous in CI | **VERIFIED FIXED** | no `.env` + no flag → **exit 1** "Refusing to report PASS"; with `.env` → real scan (358 files, 3 credential needles); `--allow-missing-env` tolerated but prints "NOT a security verification". Covered by `tests/test_secret_scan.py` (7 tests). |
| §6.3 `make test` ran 4/23 files | **VERIFIED FIXED** | `make test` → `$(PY) -m pytest tests -q`; `test-legacy` retained; CI gained a guard-tests step. |
| §6.2 no `.dockerignore` | **VERIFIED FIXED** | Added (`.env`, `data/sessions.db*`, `sessions_json/`, etc.). |
| §1.8 SSE `except: pass` | **VERIFIED FIXED** | error surfaced as `"error"` field + logged; singleton manager shared with main; 300s lifetime cap; `try/finally` teardown. |
| §6.1 `.env` never reached `os.environ` | **VERIFIED FIXED** (with caveat R1) | dotenv loaded; `TRADEFLOW_ENV` now effective when started per README. |
| §6.4 tests write real `data/sessions.db` | **VERIFIED FIXED** | `TRADEFLOW_DB_PATH/TRADEFLOW_STORE_DIR` isolation; Lead additionally isolated the last two writers (`test_transport_std.py`, and telemetry tests via `TRADEFLOW_ENV_PATH`). Proof: row count **202 → 202** across `test_transport_std + test_api_v4` runs; `.env` hash unchanged across full security suite. |
| §3.3 `_backend_name` read from wrong object | **VERIFIED FIXED** | `get_summary()["backend"]` now sourced from solver. |
| §3.4/3.7 "Pareto" terminology (weighted-sum argmax) | **VERIFIED FIXED** | real non-dominated frontier added (`pareto_frontier` keys added); claim wording removed with "舊版稱為" disclosure lines, enforced by a test. |
| §3.5 degenerate opening search | **PARTIALLY FIXED (honestly disclosed)** | anchoring-reaction term added; interior peak moved index 5→6; residual collapse documented in `model.strategy_grid`. Not claimed solved. |
| §4.5 stale P99 latency | **VERIFIED FIXED** | nearest-rank off-by-one corrected; threshold tightened 15ms→5ms; P99 reported as a measured range. |
| §4.6 real-LLM denominators | **VERIFIED FIXED** | denominator-convention table added to LIMITATIONS §2.3 (paid benchmark correctly NOT re-run). |
| §1.7/§2.5/C3 `exec()` + `assert` guardrail (dies under `-O`) | **VERIFIED FIXED** | both AST guardrails assert-free (explicit raises) + AST test enforcing absence of `assert`; `webhook_hub`/`manager` `assert`-as-control-flow replaced with raises. |
| task-9 A Infinity→500 + persisted poisoning | **VERIFIED FIXED** | raw `{"target_price":Infinity}` → **422**; `GET /api/v1/sessions` → **200** afterwards; read path also tolerates a manually poisoned row. |
| task-9 B path traversal in snapshot path | **VERIFIED FIXED** | `..%2F..%2Fmodels%2Fvocab` → **404**; whitelist `^[A-Za-z0-9._-]{1,128}$` + realpath containment check. |
| task-9 C CAS bypass on omitted version | **VERIFIED FIXED** | `append_turn` always CAS now; genuine two-writer contention test added (old stress test gave each thread a distinct id — Lead confirmed that limitation was real). |
| task-9 F `detail=str(e)` leaks | **VERIFIED FIXED** | logged server-side; test asserts no path/credential in 500 body. |
| §4.7(arena) violation_rate tautological (min-clip) | **VERIFIED FIXED** | baseline unclipped → measurable (proxy 20%/40% in artefact); engine keeps clip; documented in LIMITATIONS §2. |
| §4.3 `run_arena_benchmark.py` hardcoded "750" header | **VERIFIED FIXED** | header now interpolated from real args; AST guard test added; artefact regenerated (100 matches). |
| `scripts/make_cover.py` hand-typed figures labelled "measured" | **VERIFIED FIXED (Lead)** | all four chips now computed at build time (`robustness.report()`, `bench.json`, `CorporateMandate`); failure → "unavailable", never a hardcoded fallback. Regenerated: shows `6.7% vs 28.3%`, `+¥0.40 / unit`, `8.7M→6.3M / sec`, `Mandate ≤ 50%`. |
| `src/cli.py --benchmark` KeyError crash | **VERIFIED FIXED (Lead)** | exit 0; prints "Bandwidth ratio … NOT a speedup prediction". |
| §附录 ReDoS: `requirements.txt` lacked `regex`/`re2`; Tier-3 silent | **VERIFIED FIXED (Lead)** | `regex>=2024.0.0` added → Tier-2 timeout guard on clean installs; `backend_is_guarded` + `assert_guard_or_warn()` added; degradation no longer silent; `transport/base.extract_offer` bounded (`MAX_TEXT_LEN=2000`, 64k-input case 43s→<1s). |
| §H3 truncation defeats escalation (1000-char prepend) | **VERIFIED FIXED** | full-text security gate added before length-truncated classification (w2 tested: 1314-char padded divert now detected). |
| §6.6 `robustness.report` on webhook path (4s/request) | **VERIFIED FIXED** | no longer computed on the request path (cache + `computed=False`); `TRADEFLOW_ROBUSTNESS_ON_WEBHOOK=1` restores old behaviour. |

---

## 3. Residual risks / accepted limitations (not "fixed", disclosed)

- **R1 — sandbox is fail-open by design.** With `TRADEFLOW_API_KEY` unset in sandbox mode, every gated
  route accepts unauthenticated requests (by design, so judges can run a zero-config demo). In that
  state section A's probes legitimately return 200. This is only safe because production mode
  fail-closes — verified in §1 above. The warning is logged. **Documented choice, but a deployment
  that forgets to set the key in sandbox runs open.**
- **R2 — sandbox webhook token also fail-open** (logged warning). Same rationale, same caveat.
- **R3 — no rate limiting / no global body-size middleware** (task-9 E): field-level bounds closed the
  demonstrated amplification vectors; per-request rate limiting remains a deliberate, flagged non-goal.
- **R4 — no hard SSE connection cap** (task-9 H): lifetime cap + cleanup done; a cap needs middleware.
- **R5 — engine still trails fixed-5% baseline on Hard-Bargainer** (8.8% vs 9.2%): deliberately NOT
  tuned; tuning to win every cell would be benchmark gaming.
- **R6 — `walk_away_ceiling` remains an input to the object its own AST guardrail validates**
  (internal-consistency only). An independent-mandate cross-check was explicitly documented as absent
  rather than implied. Cross-checking against `SessionSpec.ceiling_price` is main.py-side follow-up.
- **R7 — synthetic-vs-real gap unchanged**: all arena numbers are C-level; the A-level real-LLM
  replication (0/94, 3.1% upper bound) is the honest counterweight, and §4.5 of LIMITATIONS now
  records that the joint-objective superiority **depends on λ=1**, an uncalibrated parameter.

## 4. Could NOT verify / not attempted

1. **True concurrency contention under multi-process writers**: tests cover threads in-process;
   cross-process SQLite locking was not exercised.
2. **Real network paths**: Stage-2 LLM endpoint, Evolus API, telemetry pushes, and the paid benchmark
   were **not** called (cost / offline). Their code paths are reviewed only.
3. **Chrome MV3 runtime behaviour** (service-worker revival, clipboard permission): static review +
   `node --check` only; no browser was driven.
4. **Non-vacuity of every new regression test**: implementers (w1–w5) each supplied before/after proofs
   (e.g. 20/24 engine tests fail pre-fix; XSS guard flags 30 pre-fix sinks; 65→0 train/test overlaps).
   The Lead re-ran non-vacuity checks for the split-disjointness, XSS guard, auth gating, and HITL
   cases directly; the rest rest on implementer evidence + suite green.

## 5. New defects discovered during verification

- **N1 (mine, fixed)** — telemetry tests wrote to the **repo's real `.env`**: after w1's suite ran,
  the real `.env` contained a leaked test row `TELEGRAM_CHAT_ID=100200300`. Root cause: `ENV_PATH` was
  a module constant with no override. Fixed by `TRADEFLOW_ENV_PATH` + isolating the test; verified by
  **sha256-identical `.env` before/after the full security suite**, and the row was removed.
- **N2 (mine, fixed)** — `tests/test_transport_std.py` was the **last un-isolated writer** of the real
  DB (w1's final report flagged it; nobody's scope). Isolated; proven `202 → 202` rows across runs.
- **N3 (tooling, not product)** — my first verification harness crashed (`KeyError: autopilot_state`);
  traced to **my own probe** leaking `TRADEFLOW_API_KEY` from an earlier section (causing 401 with an
  error body). Re-ran clean: 200 + `AWAITING_HUMAN_DECISION`. Not a product defect — recorded for
  honesty.
- **N4 (doc, fixed by Lead)** — `results/cover_1280x720.png` displayed figures its docstring claimed
  were "from robustness.report()" while the code hand-typed them; after the solver fix they were stale
  (28.4% vs real 28.3%, ¥1.95 vs real ¥0.40). Now computed at build time.
- **N5 (new, unclaimed)** — `robustness.report()` reveals **fixed 15%/rd beats the engine on margin**
  (0.253 vs 0.247, gain −0.006). Documented with the joint-objective analysis in LIMITATIONS §4.5
  rather than hidden. Not a code bug; a claim-strength issue now disclosed.

## 6. Overall verdict

**All 6 Critical and all 11 High audit items are VERIFIED FIXED** by direct execution. Of the Medium
set, every item was either fixed and verified, or explicitly converted into a documented, tested
residual (R1–R7). No `REGRESSION INTRODUCED` verdicts were found; two implementer-side integration
failures were caught and corrected during the session (the bridge-`ignored` bug — found by the Lead and
fixed by w1; and two stale tests encoding pre-fix behaviour — corrected by the Lead).

**Caveat on independence**: because the dedicated verifier never completed, this report is Lead-executed.
For an unbiased final pass, re-run task-6 with a fresh verifier against this report — its attacks
should be chosen to *defeat* §1 and §2 above, not to confirm them.
