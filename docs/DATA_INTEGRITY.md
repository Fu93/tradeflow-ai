# Data integrity audit

This note is the skeptical check of numbers a judge can read. Simulator and demo scenario generators (`src/sim/`, `extension/mock_chat.html`) are out of scope and stay labeled as simulations. Commands re-run for this note:

- `python scripts/compare_llm_vs_tradeflow.py --out /tmp/llm_vs_tradeflow.json` then a JSON compare against `results/llm_vs_tradeflow.json` (equal).
- `python scripts/regen_mi300x_roofline.py --check` (passed). The CPU wall-clock in `results/bench.json` was not re-timed.
- `python scripts/check_claims.py` (passed).
- `python scripts/benchmark_stats.py` on `results/real_llm_benchmark_100.json` (intervals below).

The paid Qwen log was not re-run. No key is in this environment, and a re-run would rewrite the committed file.

## Claim table

| Claim | Source | Regenerated value | Match? |
|---|---|---|---|
| Model `Qwen3.8-Flash-Next`, endpoint `https://developer.amd.com.cn/radeon/api/v1`, date 2026-10-15T19:03:56+00:00 | `results/real_llm_benchmark_100.json` `meta` | `benchmark_stats.py` reprints the same model, endpoint, and timestamp | Yes. The file stores no HTTP status. |
| 348 calls, average 16.81, cost $0.108444, 94 completed matches, 0 budget violations, 6 parse failures | same file, `totals` | `benchmark_stats.py` prints calls 348 and cost $0.108444. `check_claims.py` formats the latency and the violation sentence from this file | Yes. Not re-spent. |
| Bluffing Hardliner deal rate 61.11% in README and SUBMISSION | `deal_rate` 0.6111, formatted by `check_claims.py` as two decimals | check passed | Yes. |
| LIMITATIONS overall 81.9% [72.9%, 88.4%], bluffing 61.1% [38.6%, 79.7%], violation upper bound 3.1% | `scripts/benchmark_stats.py` one-decimal Wilson and Clopper-Pearson on the same file | script printed those exact strings | Yes, for that formatter. 61.1% and 61.11% are the same 0.6111 at different precisions. |
| Default Stage-2 model `Gemma-4-31B-IT`. No committed Gemma latency | `src/engine/llm_config.py` `DEFAULT_STAGE2_MODEL` | The 348-call log's model field is still Qwen. No Gemma row exists in that file | The default is true. A Gemma chat was not made. |
| Offline comparison, 24 episodes, seed 42, buyer `offline_accept_opening`, `ran_on_amd` false | `results/llm_vs_tradeflow.json` | Fresh JSON equals the committed file. TradeFlow 0.00% / 50.00% / 22.24%. Offline buyer 100.00% / 100.00% / 0.00% | Yes. This was not an AMD call. |
| Auction headline 5.0% (¥6,600.00) vs first quote ¥13.26, settled ¥12.60, versus target −5.0% (¥−6,000.00), secondary ceiling 22.2% (¥36,000.00), MOQ 10,000 | `results/multi_supplier_auction.md` | Yen totals are exact unit gaps times 10,000. Raw headline ratio 6600/132600 = 4.98%, shown as 5.0%. Versus target is exactly −5%. Ceiling ratio is 22.222…%, shown as 22.2% | Arithmetic matches the stored file. The auction generator was not re-run. 22.24% in the comparison file is a different experiment. |
| Held-out 63.08% (195 of 500) | `models/onnx_export_metrics.json` and `data/ood_split_manifest.json` via `check_claims.py` | check passed | Yes. |
| Self-play engine savings 8.8% to 20.0%, Hard-Bargainer 8.8% vs fixed 9.2%, 20 matches, 5 archetypes, seed 42 | `results/self_play_benchmark.md` via `check_claims.py` | check passed | Yes, against the stored table. Not re-simulated. The percents are versus the walk-away ceiling in that experiment, not the auction headline. |
| `is_gpu_active: false` | `results/bench.json` | `check_claims.py` | Yes. Wall-clock `cpu_seconds` was not re-timed. |
| HS 6601 $5.01/kg, YoY −5.8%; steel −9.2%; fabric −8.8%; ABS −12.4%; aluminium +3.3%; USD/CNY 6.7050 | `knowledge/market_data.json` fetched 2026-10-14 (`usd_cny` 6.705025; 6601 `usd_per_kg` 5.0138) | YoY recomputed from the stored 2024 and 2025 rows | Yes, against the stored fetch. Not re-fetched. |
| ¥17.91 should-cost | `knowledge/spec_baseline.md` | Line items tagged `[EXPERT]`; the total tagged `[DERIVED]` | Not a factory quote. |
| Deck and form: sourced GBP and HS figures are labelled, no narrated GPU speedup, no factory deal | `scripts/make_pitch.py` and SUBMISSION long description | `tests/test_submission_pack.py` requires the committed pptx to match `build_slides()` and the form fence to match the long description | Yes. The PDF wraps lines onto the widescreen page. It does not cut a line at 110 characters. |
| Dashboard version | `GET /health` field `version` `0.4.0` | The masthead used to show a canned `v5.0` | Fixed. The masthead now shows `API 0.4.0` from `/health`. |
| Local round timings | `results/real_stress_test_log.json` five `latency_ms` values: 2606.97, 2252.06, 2281.22, 2294.83, 2273.78 | Mean 2.342 s, median 2.281 s | The limitations note previously cited three rounds and called the mean about 2.4 s. It now cites all five. Micro-step notes (0.6 ms and similar) have no result file and are labeled as such. |

## What ran on AMD, and what did not

| Statement | Status |
|---|---|
| Tokenfactory calls logged in `results/real_llm_benchmark_100.json` | Ran. Model `Qwen3.8-Flash-Next`. 348 calls. The file has no HTTP status field. |
| `results/real_llm_benchmark.json` | A different, earlier log: 30 completed matches, 104 calls, average 19.73, cost $0.031826, same model and host, generated 2026-10-15T16:48:51+00:00. It is not the submission source. |
| Gemma-4-31B-IT | Configured default for a new Stage-2 call and for live quote reading when `VLLM_MODEL` is unset. No committed Gemma latency. Not in the 348-call log. |
| Live quote reading | Configured. AMD live mode calls `POST /api/v1/quotes/read`. With a real `VLLM_API_KEY` and no `VLLM_BASE_URL` or `VLLM_MODEL`, that call uses the AMD tokenfactory and `Gemma-4-31B-IT`. The model does not set the solver price. `results/amd_endpoint_evidence.json` is absent from git. That file is generated by the script and is not committed unless present. `status: ok` means HTTP 200 on one short chat (`Reply with the single word ok.`). That probe is not a quote extraction and does not prove which GPU served the request. No Gemma quote extraction is recorded. |
| Live dashboard reading | Opt-in. Offline fallback is the default. A failed, timed-out, or unparsable live call stays `status` `failed` or `timeout` and the display starts with `Offline fallback`. `status: ok` and the `AMD GPU Cloud ·` line are set only after HTTP 200 and parsable JSON. The UI adds the success tone only when both are true. |
| GPU solve on the accelerator named in the roofline note | Not run. `is_gpu_active` is false. |
| Public hosted URL, factory transaction, unsourced market-size dollar total | Not run. Sourced GBP totals and the Comtrade snapshot are labelled in docs/MARKET_ANALYSIS.md. |

The roofline column is a theoretical roofline lower-bound estimate, not measured. `python scripts/regen_mi300x_roofline.py --check` passed against `results/bench.json` working sets and `results/mi300x_roofline.json`. This audit does not restate a bandwidth figure. No AMD GPU measurement is claimed.

## Literals outside the simulator

| Item | Treatment |
|---|---|
| Masthead `v5.0` | Removed. Replaced by the `/health` version. `v5.0-Final` remains only as a heading inside `docs/internal/`, which is not the judge pack. |
| Solver `default_rng(20261013)` | Reproducibility seed inside the engine. It is not shown as a factory quote. Comparison runs pass their own seed (42). |
| Heuristic floats in `src/config/heuristics.py` | Config with a rationale comment. `scripts/check_no_fake_literals.py` enforces that. |
| Quote samples `quote_01`–`quote_03` | Files contain `SIMULATION`. The UI says the picture is generated in this repo. |
| `docs/DESIGN.md` ladder (16.20, ¥15, 1.7%) | Labeled as a layout sketch, not a measured quote. |
| `docs/REAL_WORLD_STRESS_TEST_TRANSCRIPT.md` | Header says no real supplier took part. Spot-checked ¥14.80 and ¥13.02 against `results/real_stress_test_log.json`. |
| `extension/mock_chat.html` scripted prices | Labeled sandbox. Left unchanged (scenario generator). |
| AMD failure labels | `OFFLINE_LABEL`, `TIMEOUT_LABEL`, and `FAILED_LABEL` all start with `Offline fallback` and do not start with `AMD GPU Cloud`. |

## Exceptions that remain

- `results/bench.json` wall-clock was not re-timed. Re-timing would replace `cpu_seconds` and `working_set_mb`. Derived roofline fields were checked in place.
- The Qwen log was not re-run. Gemma was not called.
- `knowledge/market_data.json` was not re-fetched. The cited percents match that 2026-10-14 file.
- §10.3 and §10.4 of `docs/LIMITATIONS_AND_REAL_WORLD_FINDINGS.md` still contain historical latency notes (including 4.6). No file regenerates them. They are labeled as notes, not as the committed log.
- `docs/internal/` and `docs/archive/` are not the judge pack. They were not rewritten.
- The single-supplier phone alert still reports the walk-away ceiling. The auction headline is a different baseline.
- Docker was not executed in this environment. No public URL exists.
