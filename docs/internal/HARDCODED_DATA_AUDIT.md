# TradeFlow — Hardcoded Data & Fabricated-Metric Audit

> **Audit date**: 2026-10-14  
> **Scope**: entire repository (`src/`, `scripts/`, `tests/`, `verify_*.py`, `results/`, docs)  
> **Method**: static search for magic constants, hardcoded inputs, and claim-vs-evidence mismatch;
> every finding below was confirmed by reading the code and, where possible, executing it.

## Why this document exists

This project's central claim is *honesty about its own boundaries* — the
`LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` report deliberately debunks the project's own
marketing (Expectimax vs CFR, 384 MB vs "192 GB", belief metric vs calibrated probability).

That claim is only credible if the **code** matches it. A single fabricated benchmark
number or a hardcoded "PASS" would destroy the whole posture. This audit finds them.

---

## Severity legend

| Level | Meaning |
|---|---|
| **S1 Critical** | Fabricated evidence, or a test that locks in broken behaviour |
| **S2 High** | Real number invented and presented as measured |
| **S3 Medium** | Magic constant that should be configurable, or overstated wording |
| **S4 Low** | Cosmetic / informational |

---

## Findings

### S2 — Invented speedup ceiling presented as a roofline result

**File**: `src/engine/benchmark.py`

```python
effective_bandwidth_speedup = min(THEORETICAL_BANDWIDTH_SPEEDUP * 0.18, 16.5)
...
mode = "ROOFLINE_MODEL_ESTIMATE"
```

`THEORETICAL_BANDWIDTH_SPEEDUP` divided an MI300X datasheet peak by a host DDR figure
this bench file never measured. `0.18` and the cap `16.5` were further human choices.
The result was labelled a "Roofline Model Estimate".

**Risk**: the ratio could not be reproduced from the datasheet plus the measured working set.

**Status**: **REFACTORED & ELIMINATED.** 徹底移除 `PROJECTION_BANDWIDTH_UTILISATION` (0.18)、`PROJECTION_SPEEDUP_CEILING` (16.5) 與 `PROJECTION_MULTI_SPEEDUP` (15.8)。全面改為標準教科書級 **Analytical Roofline 物理頻寬受限下界分析** ($t_{\text{lower\_bound}} = \frac{\text{Working Set}}{\text{Peak HBM3 Bandwidth}}$)，代碼不再包含任何主觀人為倍率注入；若處於真 GPU 環境則實測真實耗時。

---

### S2 — Multi-supplier "auction" has no real input path

**Files**: `src/engine/multi_solver.py`, `src/app/main.py`, `src/web/static/js/app.js`

- 原先供應商名稱為寫死字典，報價序列為手寫固態二維數組 `[[15.20, ...], ...]`。
- 原先 `POST /api/v1/multi-auction/run` 不接收任何 payload。

**Status**: **REFACTORED — 硬編碼數組已徹底拔除。**
1. **新增 `MultiAuctionSession` 互動式逐輪會話**：每一輪獨立傳入各家供應商的即時報價，
   即時計算戰術標籤（`LEAN_IN` / `CROSS_ANCHOR` / `SIDELINE`）、動態 BATNA 影子上限
   與買方對策還價向量。不預設任何未來輪次報價。
2. **`simulate_reverse_auction()` 預設改為真動態多 Agent 自博弈**：不傳入報價時，
   由 4 類非 LLM 策略對抗 Agent（Conceder / TitForTat / HardBargainer / Bluffing）
   依據各自的數學模型（指數讓步曲線、鏡像互惠、Boulwarism 死守、詐唬突變）
   **在每一輪根據買方出價歷史即時算出報價**。全部數字皆為運行時生成。
3. **`DEFAULT_BENCHMARK_ROUNDS_QUOTES` 寫死數組已刪除**，代碼庫中不再存在。
4. `POST /api/v1/multi-auction/run` 開放接收 `MultiAuctionRunRequest`，
   支援外部注入自定義供應商與逐輪報價矩陣（真實產品路徑）。
5. 前端 `app.js` 改為動態渲染後端回傳的廠商名稱與出價。
6. 新增測試 `test_interactive_multi_auction_session`（逐輪交互）與
   `test_reverse_auction_adversary_dynamic_simulation`（動態自博弈）。

**實測動態輸出**（預設參數，seed=42）：
```
Round 1: [13.26, 15.60, 15.92, 16.80] -> tactics [LEAN_IN, SIDELINE, SIDELINE, SIDELINE]
Round 2: [12.87, 15.36, 15.52, 16.68] -> 最低價持續下降（動態收斂）
Round 3: [12.60, 14.76, 14.16, 16.44] -> 贏家 A，節省 ¥3.60/件 (22.2%)
```
上列數字每一輪都由對抗 Agent 的數學模型算出，**沒有任何一個是預先寫死在代碼裡的**。

---

### S1 — A test asserted the broken state, masking real integration

**File**: `tests/test_api.py`

```python
def test_webhook_offline(self):
    ...
    self.assertIn("offline stub", r.json()["draft_email"])
```

This asserted that the Evolus integration **must fail**. When credentials were later
configured correctly, this test would fail — punishing correct configuration and
inverting the signal. It also meant a genuine Evolus regression could not be detected,
because "offline stub" was the expected outcome.

**Status**: **FIXED.** Replaced with `test_webhook_responds_with_draft`, which asserts the
real contract (HTTP 200 + substantive draft + clear labelling when degraded) and passes
whether or not Evolus is reachable.

---

### S1 — Secret-adjacent IDs committed to a tracked document

**File**: `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md`

The Evolus `Space ID` and `Agent ID` were written into the document verbatim.

Not credentials in themselves, but they identify the submission workspace and were not
needed in a public repository.

**Status**: **FIXED.** Replaced with a reference to `.env`.

---

### S1 — A quantified claim that our own real experiment disproved

**Files**: `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §2, `SUBMISSION_DRAFT.md`, `PITCH.md`

The project repeatedly asserted a *specific number* — "Vanilla LLM agents violate walk-away
budget ceilings up to **44%** of the time" — and used it to justify the AST guardrail.
The number came from a parametric simulator, but nothing in the prose marked it as
anything other than a measurement. It was quoted in the pitch, the submission draft, and
the limitations table.

**Status**: **RESOLVED BY DIRECT EXPERIMENT (negative result reported).**
We built `scripts/run_real_llm_benchmark.py` and ran the real AMD-hosted model as the
buyer: **100 matches, 348 real inference calls, $0.108**. Result: **0/94 violations**
(exact one-sided 95% upper bound **3.1%**), which is sufficient to *reject* the simulator's
12% and 44% figures at the 95% level.

Actions taken:
- `LIMITATIONS` §2.3 added, reporting the real measurement and the negative result,
  with exact confidence bounds (`scripts/benchmark_stats.py`) so the 0% is not over-read,
  and the 6 aborted matches (1.7% parse-failure rate) disclosed rather than hidden.
- `SUBMISSION_DRAFT.md` and `PITCH.md` rewritten: the guardrail's justification moved
  from "blocks 44% violations" to **defense-in-depth**, and the real model's actual
  observed weakness (61.1% deal-closure vs Bluffing Hardliner, vs 70% for the solver)
  is now the cited evidence.
- §零 classification gained an **A-level** row for the real measurement.

**Lesson recorded**: a number produced by a simulator must never be quoted in prose
without its provenance attached in the same sentence. The audit caught this only because
we ran the experiment — the simulator's output had survived several review passes.

---

### S3 — `verify_all.py` claimed "100%" and "PRODUCTION READY"

**File**: `verify_all.py`

```python
print("[PASS] 4. AST Invariant Numeric Guardrail: Verified (100% interception ...)")
print("[PASS] 6. ... Verified (Zero leaks, 100% numeric fidelity)")
print("VERDICT: ALL 6 PILLARS HEALTHY - PRODUCTION READY")
```

Each pillar is exercised with **one or two hand-picked inputs**. Percentages derived from
a single case are not percentages. "Production ready" is not supported by a smoke test.

**Status**: **FIXED.** Wording now states what was actually exercised
("single hand-crafted illegal-deposit case intercepted", "verified on this sample"),
and the verdict clarifies this is a smoke check with the `tests/` regression suite (370 tests + 98 subtests) covering the rest.

---

### S3 — Unexplained magic constants in the negotiation engine

| File | Constant | Issue |
|---|---|---|
| `logrolling.py:301` | `discount_ratio = 0.08` | 8% volume discount is asserted with no derivation |
| `logrolling.py:408` | `0.08 * ... ** 1.25` | Two unsourced constants in a rush-cost formula |
| `model.py:47` | `noise=0.08` | Observation noise chosen, not calibrated |
| `solver.py:78`, `multi_solver.py:215` | `tol = 0.02` | Convergence tolerance, undocumented basis |

**Assessment**: These are **calibration parameters, not fabrications** — a prototype
legitimately has chosen priors, and `LIMITATIONS` already states that Expectimax validity
"depends strictly on the prior". Most are surfaced as Pydantic fields with defaults, so
they are inspectable and overridable.

**Status**: **ACCEPTED, documented.** Not a defect per se. The risk is only in
*describing* them as measured. `LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` §3 already states
the system is "a fast heuristic decision solver under coarse priors".

---

### S3 — Pre-recorded demo fixtures

**File**: `src/app/demo_data.py`

```python
"""預錄 demo 情境（穩定錄影用）。"""
SUPPLIER_REPLIES = [1.25, 1.18, 1.12]
```

**Status**: **ACCEPTED.** Explicitly labelled as a pre-recorded demo scenario. This is
normal and honest practice for a recorded demo, provided the video says so.

---

### S4 — Fake data generated for the OOD benchmark

**File**: `scripts/generate_ood_500.py` → `data/benchmark_ood_500.json`

500 items generated from templates. Already disclosed in
`LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` and in the test docstring ("程式化生成之合成對抗
覆蓋集", 132 distinct templates after masking digits).

**Status**: **ACCEPTED, already disclosed.** Consistent with the honesty posture.

---

### S3 — Stale/unverifiable claims in peripheral scripts and strategy docs

**Files**: `benchmark_negotiation_v2.py`, `benchmark_negotiation.py`, `scripts/make_demo_video.py`,
`COMPETITION_STRATEGY.md`, `extension/README.md`

Five peripheral artefacts still carried claims that no longer matched the codebase:

| Location | Old claim | Problem |
|---|---|---|
| `benchmark_negotiation_v2.py` | unsourced MI300X runtime strings | Removed. The script now prints the label from `results/mi300x_roofline.json` |
| `benchmark_negotiation.py` | loop→vectorized "speedup" printed bare | Both sides are CPU; the ratio is not a GPU speedup and could be misquoted as one |
| `scripts/make_demo_video.py::frames_cpu` | `for b in bench`, `row["seconds"]`, `row["mi300x_projected_seconds"]` | **Live bug**: `benchmark.run()` now returns a summary dict, and the key was renamed. Would raise `TypeError` on the old code path. Chart was also labelled "projected on ROCm". |
| `COMPETITION_STRATEGY.md` §4/§6 | Evolus workspace / API integration listed as open gaps | **Already completed** (workspace, 3 AI Tools + 1 Agent, real REST integration) |
| `COMPETITION_STRATEGY.md` §3, §7 | "我們的引擎在 MI300X 上批量 rollout", "在 AMD MI300X 上向量化蒙地卡羅" | **Never ran on MI300X.** No AMD GPU on the dev host. |
| `extension/README.md` | a debounce literal, "精準計算", "校準貝氏後驗", vague 15% attribution | The README now follows the extension debounce constant; §十三 states the TCO parameters are uncalibrated defaults; §一 states the confidence figure is a convergence metric, not a calibrated probability |

**Status**: **FIXED.**
- Both benchmark scripts now report measured CPU time plus the **analytical roofline lower bound**
  (the formula in `results/mi300x_roofline.json`; theoretical roofline lower-bound estimate, not measured), and the loop→vectorized ratio is explicitly labelled "both on CPU,
  not a GPU speedup".
- `frames_cpu` fixed against the current API, switched to a log axis (the lower bound is
  microseconds and was invisible on a linear axis), and relabelled "CPU (measured)" vs
  "MI300X (bound)" with "a physical bound, not a projection".
- `COMPETITION_STRATEGY.md` gap table marked `[DONE]`/`[RESOLVED]` where work is complete; the
  MI300X claim replaced with an explicit "no MI300X measurement exists" boundary; the pitch
  one-liner rewritten and the old version preserved under a **do-not-use** warning.
- `extension/README.md` corrected to the extension debounce constant, and the three overclaiming adjectives replaced with
  pointers to the relevant `LIMITATIONS` sections.
- `docs/archive/` audited: all 8 archived docs carry `SUPERSEDED` banners, the index covers all 8,
  and it already warns that archived test counts / accuracy / hardware figures may be stale.
  **No change needed.**

---

### S1 — A "verbatim transcript" whose numbers did not match its own log

**Files**: `REAL_WORLD_STRESS_TEST_TRANSCRIPT.md`, `results/real_stress_test_log.json`

The document presented itself as an unfaked, verbatim stress-test transcript, quoting
per-round latencies (e.g. `2351.49 ms`) and reply text as measured output. The machine
log it cited contained **different** values (`2373.66 ms`, different reply strings), and a
later re-run produced different values again. The document's "before vs after fix" table
also described the *current* log's correct output as the "broken" prior state.

Two distinct problems:
1. **Unverifiable numbers presented as verbatim.** Any judge re-running the script would
   get different numbers and could not reconcile the document.
2. **An unverifiable "before" state.** The claimed pre-fix failures (FSM illegal transition,
   solver breaching the ceiling, premature deal-close) had **no saved log** at the time they
   were described.

**Status**: **FIXED.**
- Re-ran the script so the log is the single source of truth, then rewrote the document to
  quote the log **verbatim** and to state that the log wins on any conflict.
- Added a provenance statement distinguishing: measured system output (from the log) vs
  hand-written test inputs vs our own analysis.
- The "pre-fix failure" narrative is retained but explicitly labelled as **engineering
  memory without saved evidence**, with a list of what *is* verifiable in current code
  (`VALID_TRANSITIONS[IDLE]` includes `CIRCUIT_TRIPPED_ESCALATED`; `main.py:690` clamps
  `min(raw_recommended_price, ceiling_price)`; the 60% deposit AST assert).
- Corrected a factual error: 60% was described as a *statutory* deposit ceiling. It is an
  **internal** guardrail constant. Corrected throughout.

**Lesson recorded**: a document that quotes machine output must be *generated from* or
*diffed against* that output, not recalled from it. Latency numbers in particular are
irreproducible by nature and should be labelled as such or re-derived on each run.

---

### S2 — Test assertions written against the old fixture, then silently wrong

**File**: `tests/test_multi_demo.py::test_reverse_auction_simulation_lifecycle`

When the auction was refactored from a hardcoded quote array to dynamic multi-agent
self-play, the test still asserted the *old fixture's* outcomes — including tactic labels
that no longer matched computed reality (`CROSS_ANCHOR` where the solver now correctly
returns `SIDELINE`, because the adversary quotes genuinely land above the 1.12× threshold).

**Risk**: A test asserting a stale fixture fails after a legitimate improvement, and the
tempting "fix" is to bend the code back toward the fake numbers. That is precisely how
fabricated data gets re-introduced after being removed.

**Status**: **FIXED.** Assertions were rewritten from the *actual computed* output, and
extended to check genuine invariants instead of fixture echoes:
- `LEAN_IN` for the lowest quote; `SIDELINE` for the one above the walk-away ceiling.
- Non-leaders' BATNA ceilings are cross-anchored below the initial ceiling.
- **The per-round best quote strictly decreases across rounds** — a real convergence
  property of the dynamic negotiation, not a hardcoded sequence.

---

### S4 — Docstring rollout arithmetic inconsistent with actual defaults

**File**: `src/engine/multi_solver.py` (module header)

The header claimed the demo path uses `S=50,000` → 9.6M rollouts/round. The actual
`simulate_reverse_auction` default is `n_samples=20,000`, and the 28.8M figure accumulates
from *that*. `S=50,000` is the `benchmark.py` path — a different call site.

**Status**: **FIXED.** Header now states both paths and attributes each rollout figure to
its actual call site.

---
## Verification checklist

After the fixes above:

| Check | Command | Expected |
|---|---|---|
| Full regression | `python -m pytest tests -q` | 370 passed + 98 subtests |
| Smoke pillars | `python verify_all.py` | 6 pillars, wording qualified |
| Live E2E | `python verify_live.py` | 7 PASS |
| No hardcoded quote array | `git grep -n "DEFAULT_BENCHMARK_ROUNDS_QUOTES"` | none |
| No invented speedup constants | `git grep -n "PROJECTION_BANDWIDTH_UTILISATION"` | none |
| No keys in repo | `git grep -n "evo_\|rc-" -- .` | only `.env.example` placeholders |
| No fake "measured" labels | `git grep -n "ROOFLINE_MODEL_ESTIMATE"` | none |
| No fabricated literature | `git grep -n "2511\.06262"` | none |
| No stale CLI hardware claims | `git grep -n "9\.6M rollouts in ~0\.4s"` | none |
| Stress transcript matches log | `python scripts/run_real_stress_test.py && git diff --stat results/` | only latency fields change |
| Real LLM benchmark reproducible | `python scripts/benchmark_stats.py results/real_llm_benchmark_100.json` | violation 0.0%, upper bound 3.1% |

## Guidance for future work

1. **Never label a projection a measurement.** Derive GPU projections from the standard
   analytical roofline model (working set ÷ peak bandwidth), never from a hand-picked
   multiplier. If no GPU is present, report the CPU measurement and the theoretical
   lower bound — do not invent an "expected" figure.
2. **Never assert a fixture in a test.** Assert invariants the solver must satisfy
   regardless of the random draw. Tests that echo hardcoded numbers actively defend the
   fake data against removal.
3. **Never put credentials or workspace IDs in tracked files.** `.env` only.
4. **Percentages require more than one sample.** If n=1, write "this case passed".
5. **If a number was chosen rather than computed, say so in a comment.** A labelled
   guess is credible; an unlabelled one is not.
6. **When refactoring away fake data, re-derive test expectations from real output.**
   Never "fix" a failing test by restoring the fixture it was checking.

---

*This audit is part of the project's disclosed-boundary programme, alongside
`LIMITATIONS_AND_REAL_WORLD_FINDINGS.md`.*
