"""Plant small bugs in the decision path and require the tests to kill each one.

A mutant is written to a temporary copy of the tree. The worktree files are
not rewritten. A mutant that still passes its test is a gap in the suite.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (path, original, mutant, pytest node that must fail)
WALK = "tests/test_pr13_verify.py::test_walk_penalty_is_the_surplus_at_stake_and_a_negative_push_is_withheld"
RIVAL = "tests/test_v2_phase1.py::test_default_counter_matches_the_rival_and_push_is_not_above_it"
FIRST = "tests/test_pr13_verify.py::test_concession_uses_the_first_ask_as_the_reference"
REJECT = "tests/test_pr13_verify.py::test_rejection_is_a_lower_bound_and_never_raises_acceptance"
PRIOR = "tests/test_pr13_verify.py::test_user_prior_mean_and_spread_set_the_beta"
TIGHT = "tests/steps/test_t5_reserve.py::test_later_higher_ask_does_not_raise_the_mean"
SHADOW = "tests/steps/test_t3_shadow.py::test_shadow_never_applies_and_wilson_fails_closed"
WILSON = "tests/test_v2_phase1.py::test_wilson_certification_count_is_189"
VETO = "tests/test_v2_phase1.py::test_instruction_span_is_never_a_shadow_accept"

MUTANTS = [
    ("src/engine/decision.py", "return saving - risk", "return saving + risk", WALK),
    ("src/engine/decision.py", "stake = max(float(outside) - float(offer), 0.0)", "stake = float(outside)", WALK),
    ("src/engine/decision.py", "default = 0.0 if blocked else hi", "default = float(ask)", RIVAL),
    ("src/engine/decision.py", "fraction = 0.0 if ref <= 0.0 else float(offer) / ref", "fraction = 1.0", WALK),
    ("src/engine/decision.py", "return float(recorded)", "return float(latest_ask)", FIRST),
    ("src/engine/decision.py", "risk = incremental_hazard(ask, offer, effect, fit, intercept=intercept, gap_coef=gap_coef) * stake", "risk = 0.0", WALK),
    ("src/engine/decision.py", "saving = state.acceptance(fraction) * stake", "saving = stake", WALK),
    ("src/engine/decision.py", "push_counter=push", "push_counter=float(ask)", "tests/steps/test_t7_optimiser.py::test_desk_decide_matches_choose_offer"),
    ("src/engine/decision.py", "return _clip(found, floor, hi), \"golden_section\"", "return _clip(found, floor, hi), \"mesh\"", "tests/steps/test_t7_optimiser.py::test_desk_decide_matches_choose_offer"),
    ("src/engine/decision.py", "belief = state if state is not None else ReserveState.policy()", "belief = ReserveState.policy()", FIRST),
    ("src/engine/reserve.py", "if bound >= self.upper:", "if False:", TIGHT),
    ("src/engine/reserve.py", "ratios = self.reject_ratios + (min(max(float(ratio), _TINY), 1.0),)", "ratios = self.reject_ratios", REJECT),
    ("src/engine/reserve.py", "lower = max(float(ratio) for ratio in self.reject_ratios)", "lower = 0.0", REJECT),
    ("src/engine/reserve.py", "return self._spawn(reference_ask=float(ask))", "return self._spawn(reference_ask=None)", FIRST),
    ("src/engine/reserve.py", "return self._spawn(upper=bound, n_responses=self.n_responses + 1)", "return self._spawn(upper=1.0, n_responses=self.n_responses)", TIGHT),
    ("src/engine/reserve.py", "return float(alpha) / (float(alpha) + float(beta))", "return float(alpha) / (float(alpha) + float(beta) + float(beta))", TIGHT),
    ("src/engine/reserve.py", "strength = float(D_PRIOR_STRENGTH)\n    mean = min(max(float(D_PRIOR_MEAN), _TINY), 1.0 - _TINY)", "strength = float(D_PRIOR_STRENGTH) + float(D_PRIOR_STRENGTH)\n    mean = min(max(float(D_PRIOR_MEAN), _TINY), 1.0 - _TINY)", TIGHT),
    ("src/engine/reserve.py", "if self.n_responses >= int(HAZARD_MIN_EVENTS):\n            return \"supplier_responses\"", "if self.n_responses >= 0:\n            return \"supplier_responses\"", "tests/test_v2_phase1.py::test_labelled_policy_until_thirty_responses_and_strength_keeps_the_mean"),
    ("src/engine/reserve.py", "return (float(alpha) * float(beta)) / (total * total * (total + 1.0))", "return float(alpha)", TIGHT),
    ("src/engine/reserve.py", "alpha, beta = scale_strength(self.alpha, self.beta, factor)", "alpha, beta = self.alpha, self.beta", "tests/test_v2_phase1.py::test_labelled_policy_until_thirty_responses_and_strength_keeps_the_mean"),
    ("src/desk/shadow.py", '"applied": False', '"applied": True', SHADOW),
    ("src/desk/shadow.py", "if count <= 0:", "if count < 0:", SHADOW),
    ("src/desk/shadow.py", "return max(0.0, centre - margin), min(1.0, centre + margin)", "return 0.0, 1.0", WILSON),
    ("src/desk/shadow.py", "would = True", "would = False", SHADOW),
    ("src/desk/shadow.py", "if index < 0:\n        return \"\"", "if index < 0:\n        return str(message)", SHADOW),
    ("src/desk/shadow.py", "if str(mention.get(\"speaker\") or \"\") != \"supplier\":", "if False:", SHADOW),
    ("src/desk/shadow.py", "if str(mention.get(\"polarity\") or \"\") != \"affirmative\":", "if False:", SHADOW),
    ("src/desk/shadow.py", "if bool(mention.get(\"is_question\")):", "if False:", SHADOW),
    ("src/desk/shadow.py", "if str(mention.get(\"unit\") or \"\") != \"per_piece\" and not (per_piece_marked(span) or per_piece_marked(nearby)):", "if False:", SHADOW),
    ("src/desk/shadow.py", "wins = min(max(int(successes), 0), count)", "wins = 0", WILSON),
    (
        "src/desk/record.py",
        "bool(inputs_stored)",
        "True",
        "tests/steps/test_reverify_blockers.py::test_record_without_decision_inputs_withholds_the_counter",
    ),
    (
        "src/desk/verify.py",
        "if chain_digest != loaded_digest:",
        "if False and chain_digest != loaded_digest:",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_rewritten_counter_whose_row_hash_matches",
    ),
    (
        "src/desk/headline.py",
        "best = min(float(item) for item in first_landed)",
        "best = max(float(item) for item in first_landed)",
        "tests/steps/test_reverify_blockers.py::test_headline_saving_uses_the_lower_landed_price",
    ),
    (
        "src/desk/verify.py",
        "return same_supplier and same_deal",
        "return same_supplier or same_deal",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_another_suppliers_decision",
    ),
    (
        "src/desk/verify.py",
        "return str(latest_id) == str(decision_id)",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_an_older_decision",
    ),
    (
        "src/desk/verify.py",
        "return abs(float(stored) - float(confirmed)) <= tolerance",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_an_ask_the_confirmed_quote_does_not_have",
    ),
    (
        "src/desk/verify.py",
        "return abs(float(held) - float(previous_counter)) <= tolerance",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_held_counter_the_chain_did_not_issue",
    ),
    (
        "src/desk/verify.py",
        "return draft_digest(body) == draft_digest(rendered)",
        "return rendered in body",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_draft_that_adds_a_second_price",
    ),
    (
        "src/desk/verify.py",
        "return decision_currency == chained_currency == body_currency",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_decision_in_another_currency",
    ),
    (
        "src/desk/verify.py",
        "return digest == ingested_hash and digest == chained_raw and price_ok and ask_ok and unit_ok and span_ok",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_an_edited_confirmed_price",
    ),
    (
        "src/desk/verify.py",
        "return same_counter and same_ceiling and same_unconfirmed and same_rivals and same_constants",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_decision_with_forged_rivals",
    ),
    (
        "src/desk/verify.py",
        "return not approved",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_resetting_an_approved_draft_does_not_send_again",
    ),
    (
        "src/desk/extract.py",
        "if response is not None:\n        response.close()",
        "if False:\n        response.close()",
        "tests/steps/test_extractor_failover.py::test_abandoned_hop_stops_and_concurrency_is_bounded",
    ),
    (
        "src/web/static/js/desk.js",
        "const span = mapped || lineContaining(item.raw_text, token);",
        "const span = token;",
        "tests/steps/test_reverify_blockers.py::test_a_non_suggested_token_shows_its_line",
    ),
    (
        "scripts/mutation_sanity.py",
        "dest.write_text(mutated, encoding=\"utf-8\")",
        "live.write_text(mutated, encoding=\"utf-8\")",
        "tests/steps/test_reverify_blockers.py::test_mutation_runner_leaves_the_worktree_untouched",
    ),
    (
        "src/desk/verify.py",
        "live_ids = quoting_supplier_ids(list(asks), outcomes_for)",
        "live_ids = list(asks)",
        "tests/steps/test_reverify_blockers.py::test_send_after_a_rival_walks_away",
    ),
    (
        "src/desk/extract.py",
        "worker.join(timeout)",
        "worker.join()",
        "tests/steps/test_extractor_failover.py::test_slow_drip_stays_inside_the_deadline",
    ),
    (
        "src/desk/audit_chain.py",
        "return hmac.new(chain_key(), canonical(body), hashlib.sha256).hexdigest()",
        "return hashlib.sha256(canonical(body)).hexdigest()",
        "tests/steps/test_reverify_blockers.py::test_rehashed_chain_and_forged_failure_do_not_verify",
    ),
    (
        "src/desk/service.py",
        "raise SendRefused(\"audit chain broken\")",
        "raise SendRefused(\"This draft was already approved.\")",
        "tests/steps/test_reverify_blockers.py::test_rehashed_chain_and_forged_failure_do_not_verify",
    ),
    (
        "src/desk/service.py",
        "if not chain_key_configured():",
        "if False:",
        "tests/steps/test_reverify_blockers.py::test_rehashed_chain_and_forged_failure_do_not_verify",
    ),
    (
        "src/desk/audit_chain.py",
        "return int(anchored[\"seq\"]) == head_seq and str(anchored[\"entry_hash\"]) == head_mac",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_anchor_detects_a_truncated_tail",
    ),
    (
        "src/desk/extract.py",
        "return bool(slot.acquire(timeout=timeout))",
        "return bool(slot.acquire(blocking=False))",
        "tests/steps/test_extractor_failover.py::test_extractions_queue_and_release_a_slow_header",
    ),
    (
        "src/desk/extract.py",
        "_release_http(slot, box)  # abandoned hop",
        "return  # abandoned hop",
        "tests/steps/test_extractor_failover.py::test_extractions_queue_and_release_a_slow_header",
    ),
    (
        "src/desk/verify.py",
        "if _migrated_unconfirmed(opened_for_migration, prior_seq):",
        "if False and _migrated_unconfirmed(opened_for_migration, prior_seq):",
        "tests/steps/test_reverify_blockers.py::test_legacy_deal_needs_a_fresh_confirmation",
    ),
    (
        "src/desk/parse.py",
        "blocked = _mentions_other_currency(line, deal_currency) or _mentions_other_currency(sentence, deal_currency)",
        "blocked = False",
        "tests/steps/test_reverify_blockers.py::test_another_currency_in_the_sentence_blocks_a_bare_number",
    ),
    (
        "src/desk/verify.py",
        "if _transmission_differs(outgoing, chained):",
        "if False and _transmission_differs(outgoing, chained):",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_rewritten_address_subject_or_reply_header",
    ),
    (
        "src/desk/store.py",
        "if audit_chain.legacy_unkeyed(list(rows)):",
        "if False and audit_chain.legacy_unkeyed(list(rows)):",
        "tests/steps/test_reverify_blockers.py::test_pre_key_chain_is_archived_and_reconfirmed",
    ),
    (
        "src/desk/audit_chain.py",
        "if _anchor_is_required():",
        "if False and _anchor_is_required():",
        "tests/steps/test_reverify_blockers.py::test_missing_anchor_fails_closed_and_the_index_is_the_latest_head",
    ),
    (
        "src/desk/audit_chain.py",
        "handle.seek(int(slot[\"offset\"]))",
        "handle.seek(0)",
        "tests/steps/test_reverify_blockers.py::test_missing_anchor_fails_closed_and_the_index_is_the_latest_head",
    ),
    (
        "src/desk/audit_chain.py",
        "_remember_anchor_pending(conn, deal_id, seq, digest)",
        "_clear_anchor_pending(conn, deal_id)",
        "tests/steps/test_reverify_blockers.py::test_anchor_failure_stays_pending_even_if_a_later_head_matches",
    ),
    (
        "src/desk/store.py",
        "if commit:\n            self.conn.commit()  # create_deal",
        "if True:\n            self.conn.commit()  # create_deal",
        "tests/steps/test_reverify_blockers.py::test_create_rolls_back_when_the_chain_write_fails",
    ),
    (
        "src/app/main.py",
        "if not chain_key_configured():",
        "if False:",
        "tests/steps/test_reverify_blockers.py::test_health_and_create_report_a_missing_chain_key",
    ),
    (
        "src/desk/extract.py",
        "return allowance - (time.monotonic() - started)",
        "return allowance",
        "tests/steps/test_extractor_failover.py::test_queue_wait_is_part_of_the_hop_deadline",
    ),
    (
        "src/desk/service.py",
        "max_workers=int(EXTRACTOR_MAX_IN_FLIGHT)",
        "max_workers=None",
        "tests/steps/test_extractor_failover.py::test_extractor_workers_stay_bounded",
    ),
    (
        "src/desk/parse.py",
        "elsewhere = _other_currency_on_another_line(visible, start, deal_currency)",
        "elsewhere = False",
        "tests/steps/test_reverify_blockers.py::test_spelled_currency_on_another_line_blocks_a_bare_number",
    ),
    (
        "src/desk/audit_chain.py",
        "return key_id == current_key_id()",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_a_retired_chain_key_still_verifies",
    ),
]


def _repo_of(path: Path) -> Path:
    for parent in [path.resolve(), *path.resolve().parents]:
        if (parent / "docs" / "constants_registry.json").is_file() and (parent / "src").is_dir():
            return parent
    raise RuntimeError(f"repo root not found from {path}")


def _write_mutant(dest: Path, mutated: str, live: Path) -> None:
    if dest == live:
        raise RuntimeError("mutant copy must not be the worktree file")
    dest.write_text(mutated, encoding="utf-8")


def stage_mutant(path: Path, mutated: str) -> Path:
    """A temp tree. The mutated file is a real copy. Package inits are copies too.

    A symlinked ``__init__.py`` would retarget the package at the worktree, and
    the mutant would never be imported.
    """
    stage = Path(tempfile.mkdtemp(prefix="tf-mutant-"))
    live = path.resolve()
    repo = _repo_of(live)
    src_root = (repo / "src").resolve()
    try:
        live.relative_to(src_root)
        under_src = True
    except ValueError:
        under_src = False
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for name in filenames:
            src_file = Path(dirpath) / name
            dest = stage / src_file.relative_to(repo)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src_file.resolve() == live:
                _write_mutant(dest, mutated, live)
            elif name == "__init__.py":
                shutil.copyfile(src_file, dest)
            else:
                dest.symlink_to(src_file)
    if not under_src:
        dest = stage / live.relative_to(repo)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_mutant(dest, mutated, live)
    for name in ("models", "data", "docs"):
        source = repo / name
        if source.exists():
            (stage / name).symlink_to(source, target_is_directory=True)
    return stage


def _run(node: str, stage: Path) -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    parts = [str(stage), str(ROOT)]
    prior = env.get("PYTHONPATH", "")
    if prior:
        parts.append(prior)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    # conftest inserts the repo at sys.path[0] and then imports src. Preload the
    # staged package so that import is the mutant, not the worktree.
    runner = stage / "_mutant_pytest.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(stage)!r})\n"
        "import src\n"
        "import pytest\n"
        "raise SystemExit(pytest.main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-P", str(runner), node, "-q", "--tb=no", "--import-mode=importlib", "-p", "no:cacheprovider"],
        cwd=ROOT,
        check=False,
        env=env,
    )
    return result.returncode


def main() -> int:
    survived = []
    for path, original, mutant, node in MUTANTS:
        file = ROOT / path
        source = file.read_text(encoding="utf-8")
        if source.count(original) != 1:
            survived.append(f"{path}: pattern not unique ({source.count(original)}): {original}")
            continue
        stage = stage_mutant(file, source.replace(original, mutant, 1))
        try:
            code = _run(node, stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        if code == 0:
            survived.append(f"SURVIVED {path}: {original!r} -> {mutant!r} ({node})")
        else:
            print(f"killed {path}: {original}")
    if survived:
        print("mutation sanity failed:", file=sys.stderr)
        for item in survived:
            print(item, file=sys.stderr)
        return 1
    print(f"mutation sanity ok ({len(MUTANTS)} mutants killed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
