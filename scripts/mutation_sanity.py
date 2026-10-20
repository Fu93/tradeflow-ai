"""Plant small bugs in the decision path and require the tests to kill each one.

A mutant that still passes its test is a gap in the suite. The files are
restored even when a mutant survives.
"""
from __future__ import annotations

import os
import subprocess
import sys
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
    ("src/engine/reserve.py", "bound = min(float(self.upper), max(float(ratio), _TINY))", "bound = max(float(ratio), _TINY)", TIGHT),
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
        "src/desk/service.py",
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
        "src/desk/service.py",
        "return same_supplier and same_deal",
        "return same_supplier or same_deal",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_another_suppliers_decision",
    ),
    (
        "src/desk/service.py",
        "return str(latest_id) == str(decision_id)",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_an_older_decision",
    ),
    (
        "src/desk/service.py",
        "return abs(float(stored) - float(confirmed)) <= tolerance",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_an_ask_the_confirmed_quote_does_not_have",
    ),
    (
        "src/desk/service.py",
        "return abs(float(held) - float(previous_counter)) <= tolerance",
        "return True",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_held_counter_the_chain_did_not_issue",
    ),
    (
        "src/desk/service.py",
        "return draft_digest(body) == draft_digest(rendered)",
        "return rendered in body",
        "tests/steps/test_reverify_blockers.py::test_send_refuses_a_draft_that_adds_a_second_price",
    ),
]


def _purge_pyc(path: Path) -> None:
    cache = path.parent / "__pycache__"
    if not cache.is_dir():
        return
    for child in cache.glob(path.stem + "*.pyc"):
        child.unlink(missing_ok=True)


def _run(node: str) -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--tb=no", "-p", "no:cacheprovider"],
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
        file.write_text(source.replace(original, mutant, 1), encoding="utf-8")
        _purge_pyc(file)
        try:
            code = _run(node)
        finally:
            file.write_text(source, encoding="utf-8")
            _purge_pyc(file)
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
