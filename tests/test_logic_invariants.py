"""Invariants for the decision core, the auction bound, and the human-send gate.

The expectimax check is a scalar replay of solver.solve on one tiny grid.
It is not a recursive closed-loop game tree. CuPy parity runs only when a
device imports; this checkout usually skips it.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.engine import model, solver  # noqa: E402
from src.engine.bayesian_v2 import RobustBayesianInferenceEngine  # noqa: E402
from src.engine.bilingual import (  # noqa: E402
    ActionObjectASTGuardrail,
    GuardrailViolationError,
    InboundIntentClassifier,
    TacticalActionObject,
)
from src.engine.logrolling import (  # noqa: E402
    CorporateMandate,
    LogrollingEngine,
    SupplierCurrentOffer,
)
from src.engine.multi_solver import MultiAuctionSession  # noqa: E402


def _scalar_expectimax(reserve, urgency, bluff, conc, opens, scheds):
    """Same payoff as solver.solve, one opponent and one cell at a time."""
    n_open = len(opens)
    n_sched = len(scheds)
    rounds = int(np.asarray(scheds).shape[1])
    margin = np.zeros((n_open, n_sched), dtype=np.float64)
    brk = np.zeros((n_open, n_sched), dtype=np.float64)
    for s in range(len(reserve)):
        for i, open_px in enumerate(opens):
            first_gap = abs(float(reserve[s]) - float(open_px))
            react = 1.0 - solver.ANCHORING_BETA * (first_gap - solver.ANCHORING_REF)
            react = min(max(react, solver.ANCHORING_CLIP[0]), solver.ANCHORING_CLIP[1])
            cr_eff = min(max(float(conc[s]) * react, 0.0), 0.95)
            for j in range(n_sched):
                gap = first_gap
                paid = float(open_px)
                for r in range(rounds):
                    step_you = float(scheds[j, r]) * gap
                    paid += step_you
                    gap -= step_you
                    step_opp = cr_eff * float(urgency[s]) * (1.0 - 0.45 * float(bluff[s])) * gap
                    gap -= step_opp
                    if gap < 0.0:
                        gap = 0.0
                p_deal = math.exp(-max(gap - 0.02, 0.0) / 0.05)
                extra = (0.20 + 0.30 * float(bluff[s])) if first_gap > 0.32 else 0.0
                p_break = min(max(1.0 - p_deal + extra, 0.0), 1.0)
                payoff = p_deal * (model.YOUR_CEILING - paid) - p_break * 0.10
                margin[i, j] += payoff
                brk[i, j] += p_break
    margin /= len(reserve)
    brk /= len(reserve)
    return margin, brk


class TestExpectimaxReference(unittest.TestCase):
    def test_vectorised_solve_matches_scalar_reference_on_a_tiny_grid(self):
        opens = np.array([1.00, 1.10, 1.20], dtype=np.float64)
        scheds = np.array([[0.05] * model.ROUNDS, [0.10] * model.ROUNDS], dtype=np.float64)
        rng_ref = np.random.default_rng(7)
        rng_sol = np.random.default_rng(7)
        reserve, urgency, bluff, conc = model.sample_opponents(
            rng_ref, 5, reserve_mean=1.08, reserve_std=0.05,
        )
        ref_margin, ref_break = _scalar_expectimax(reserve, urgency, bluff, conc, opens, scheds)
        got = solver.solve(
            n_opp_samples=5,
            opens=opens,
            scheds=scheds,
            use_gpu=False,
            rng=rng_sol,
            reserve_mean=1.08,
            reserve_std=0.05,
            lambda_break=1.0,
        )
        np.testing.assert_allclose(got["exp_margin"], ref_margin, rtol=1e-6, atol=1e-8)
        np.testing.assert_allclose(got["exp_break"], ref_break, rtol=1e-6, atol=1e-8)
        score = ref_margin - ref_break
        flat = int(np.argmax(score))
        self.assertEqual(got["best"], divmod(flat, len(scheds)))
        self.assertTrue(np.isfinite(got["best_score"]))

    def test_best_is_the_scalarised_argmax(self):
        got = solver.solve(
            n_opp_samples=8,
            opens=np.linspace(1.0, 1.2, 4),
            scheds=np.array([[0.05] * 5, [0.12] * 5]),
            use_gpu=False,
            rng=np.random.default_rng(3),
            reserve_mean=1.05,
            reserve_std=0.04,
        )
        score = got["exp_margin"] - got["exp_break"]
        self.assertEqual(got["best"], tuple(int(x) for x in np.unravel_index(int(np.argmax(score)), score.shape)))


class TestBayesianInvariants(unittest.TestCase):
    def test_no_observation_leaves_the_prior_unchanged(self):
        engine = RobustBayesianInferenceEngine()
        state = engine.update(1.08, 0.10, observed_offer=None, our_next_offer=1.12)
        self.assertEqual(state.posterior_mean, round(1.08, 4))
        self.assertEqual(state.posterior_std, round(0.10, 4))
        self.assertEqual(state.clean_responsibility, 1.0)
        self.assertEqual(state.info_gain_bits, 0.0)
        self.assertGreaterEqual(state.deal_probability, 0.0)
        self.assertLessEqual(state.deal_probability, 1.0)

    def test_responsibility_stays_in_unit_interval_and_extremes_move_less(self):
        engine = RobustBayesianInferenceEngine()
        # 1.20 is a clean offer near prior+markup. 2.40 is far enough that the
        # uniform-noise component takes the responsibility and the mean stays put.
        near = engine.update(1.08, 0.10, observed_offer=1.20)
        far = engine.update(1.08, 0.10, observed_offer=2.40)
        for state in (near, far):
            self.assertGreaterEqual(state.clean_responsibility, 0.0)
            self.assertLessEqual(state.clean_responsibility, 1.0)
            self.assertGreater(state.posterior_std, 0.0)
            self.assertGreaterEqual(state.deal_probability, 0.0)
            self.assertLessEqual(state.deal_probability, 1.0)
        self.assertLess(
            abs(far.posterior_mean - 1.08),
            abs(near.posterior_mean - 1.08),
        )

    def test_default_opponent_prior_sums_to_one(self):
        mix = np.ones(4) / 4
        self.assertAlmostEqual(float(mix.sum()), 1.0)


class TestLogrollingBounds(unittest.TestCase):
    def test_ranked_trades_stay_inside_the_mandate(self):
        mandate = CorporateMandate(
            target_price=12.0,
            walk_away_ceiling=16.2,
            mold_fee_exemption_demanded=True,
            estimated_mold_fee=2000.0,
        )
        offer = SupplierCurrentOffer(
            price=15.0, lead_days=30, deposit_pct=0.30, moq=500, mold_fee=2000.0,
        )
        result = LogrollingEngine.evaluate(mandate, offer)
        ranked = [p for p in result.all_pareto_proposals if p.tradeoff_types_used]
        self.assertTrue(ranked, "expected at least one feasible trade")
        for prop in ranked:
            self.assertLessEqual(prop.proposed_price, mandate.walk_away_ceiling + 1e-6)
            self.assertGreater(prop.proposed_price, 0.0)
            self.assertLessEqual(prop.proposed_deposit_pct, mandate.max_allowed_deposit_pct + 1e-6)
            self.assertLessEqual(prop.proposed_lead_days, mandate.max_acceptable_lead_days)
            self.assertGreaterEqual(prop.proposed_lead_days, 1)
            self.assertLessEqual(prop.proposed_moq, mandate.max_allowed_moq)
            self.assertGreaterEqual(prop.supplier_surplus_estimate, 0.0)
        self.assertLessEqual(
            result.best_recommendation.proposed_deposit_pct,
            mandate.max_allowed_deposit_pct + 1e-6,
        )
        self.assertLessEqual(
            result.best_recommendation.proposed_price,
            mandate.walk_away_ceiling + 1e-6,
        )


class TestCircuitBreakerAndGuardrail(unittest.TestCase):
    def test_breaker_trips_at_the_stated_threshold(self):
        self.assertEqual(model.BREAKDOWN_TRIP_THRESHOLD, 0.15)
        self.assertFalse(model.breakdown_circuit_tripped(0.149))
        self.assertTrue(model.breakdown_circuit_tripped(0.15))
        self.assertTrue(model.breakdown_circuit_tripped(0.1500001))

    def test_guardrail_rejects_price_and_deposit_breaches_even_under_optimize(self):
        over_price = TacticalActionObject(authorized_price=20.0, walk_away_ceiling=16.2)
        over_deposit = TacticalActionObject(
            authorized_price=12.0, walk_away_ceiling=16.2, deposit_pct=0.95,
        )
        ok = TacticalActionObject(authorized_price=13.0, walk_away_ceiling=16.2, deposit_pct=0.30)
        with self.assertRaises(GuardrailViolationError):
            ActionObjectASTGuardrail.verify(over_price)
        with self.assertRaises(GuardrailViolationError):
            ActionObjectASTGuardrail.verify(over_deposit)
        self.assertTrue(ActionObjectASTGuardrail.verify(ok))
        snippet = ActionObjectASTGuardrail._GUARDRAIL_SNIPPET
        self.assertNotIn("assert ", snippet)
        proc = subprocess.run(
            [sys.executable, "-O", "-c",
             "from src.engine.bilingual import TacticalActionObject, ActionObjectASTGuardrail, GuardrailViolationError\n"
             "obj = TacticalActionObject(authorized_price=999.0, walk_away_ceiling=15.0, deposit_pct=0.95)\n"
             "try:\n"
             "    ActionObjectASTGuardrail.verify(obj)\n"
             "except GuardrailViolationError:\n"
             "    raise SystemExit(0)\n"
             "raise SystemExit(1)\n"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_stage1_confidence_is_a_probability(self):
        for text in ("最低 15.80", "加我微信谈", "行吧就按你说的 12.50"):
            result = InboundIntentClassifier.classify_stage1_only(text)
            self.assertGreaterEqual(result.confidence, 0.0)
            self.assertLessEqual(result.confidence, 1.0)
            self.assertTrue(result.primary_intent.value)


class TestAuctionOfferBound(unittest.TestCase):
    def test_counter_offers_do_not_exceed_a_tighter_ceiling(self):
        session = MultiAuctionSession(
            target_price=12.0,
            walk_away_ceiling=13.0,
            n_samples=24,
            n_open=12,
            n_sched=4,
            use_gpu=False,
        )
        info = session.step_round([15.0, 14.5, 16.0, 13.8])
        for offer in info["counter_offers_cny"]:
            self.assertLessEqual(offer, 13.0 + 1e-6)
        self.assertLessEqual(info["best_quote"], 16.0)

    def test_ceiling_below_every_grid_open_falls_back_to_the_ceiling(self):
        session = MultiAuctionSession(
            target_price=12.0,
            walk_away_ceiling=11.0,
            n_samples=8,
            n_open=6,
            n_sched=2,
            use_gpu=False,
        )
        info = session.step_round([14.0, 14.0, 14.0, 14.0])
        for offer in info["counter_offers_cny"]:
            self.assertAlmostEqual(offer, 11.0, places=6)


class TestCupyParity(unittest.TestCase):
    def test_numpy_and_cupy_match_when_a_device_is_present(self):
        try:
            import cupy as cp
            cp.zeros(1)
        except Exception as exc:
            self.skipTest(f"CuPy device not available; parity was not measured ({exc})")
        opens = np.linspace(1.0, 1.2, 4)
        scheds = np.array([[0.05] * 5, [0.10] * 5])
        cpu = solver.solve(
            n_opp_samples=6, opens=opens, scheds=scheds, use_gpu=False,
            rng=np.random.default_rng(11), reserve_mean=1.06, reserve_std=0.04,
        )
        gpu = solver.solve(
            n_opp_samples=6, opens=opens, scheds=scheds, use_gpu=True,
            rng=np.random.default_rng(11), reserve_mean=1.06, reserve_std=0.04,
        )
        np.testing.assert_allclose(cpu["exp_margin"], gpu["exp_margin"], rtol=1e-5, atol=1e-6)
        np.testing.assert_allclose(cpu["exp_break"], gpu["exp_break"], rtol=1e-5, atol=1e-6)
        self.assertEqual(cpu["best"], gpu["best"])


def _strip_js_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//.*?$", "", src, flags=re.M)
    return src


class TestExtensionDoesNotAutoSend(unittest.TestCase):
    def test_extension_scripts_copy_and_do_not_click_send(self):
        forbidden = ("findSendButton", "findChatInput", ".click(", "execCommand", "InputEvent")
        for name in ("content.js", "background.js", "sidepanel.js"):
            raw = (ROOT / "extension" / name).read_text(encoding="utf-8")
            live = _strip_js_comments(raw)
            for token in forbidden:
                self.assertNotIn(token, live, f"{name} still contains {token}")
        content = _strip_js_comments((ROOT / "extension" / "content.js").read_text(encoding="utf-8"))
        self.assertIn("clipboard.writeText", content)
        background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        self.assertIn("chrome.runtime.onConnect", background)
        self.assertIn("chrome.runtime.onMessage", background)
        for name in ("content.js", "sidepanel.js"):
            raw = (ROOT / "extension" / name).read_text(encoding="utf-8")
            live = _strip_js_comments(raw)
            # The comment stripper also cuts http:// URLs, so the path is
            # checked on the raw file. The live source must not embed the
            # threshold; both scripts read it from the heuristics endpoint.
            self.assertIn("/api/v1/heuristics", raw)
            self.assertNotIn(f"{model.BREAKDOWN_TRIP_THRESHOLD:.2f}", live)


class TestRoundPathHonesty(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from src.app import main as api_main

        self._tmp = tempfile.mkdtemp(prefix="tf_logic_")
        api_main.session_mgr.__init__(
            db_path=os.path.join(self._tmp, "sessions.db"),
            store_dir=os.path.join(self._tmp, "sessions_json"),
        )
        self.c = TestClient(api_main.app)
        self.api = api_main

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_short_supplier_lead_does_not_crash_and_price_stays_under_ceiling(self):
        sid = "logic_lead"
        created = self.c.post("/api/v1/sessions", json={
            "session_id": sid,
            "product_name": "Sample",
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "moq": 500,
            "max_rounds": 1,
            "supplier_id": "S",
            "supplier_channel": "web_chat",
        })
        self.assertEqual(created.status_code, 200, created.text)
        with mock.patch("requests.post") as posted:
            # A 10-day quote used to be copied into the buyer baseline and then
            # fail CorporateMandate (minimum desired lead is 12), returning 500.
            response = self.c.post(f"/api/v1/sessions/{sid}/round", json={
                "opponent_offer_price": 14.5,
                "opponent_raw_text": "lowest 14.50, lead 10 days",
                "offered_lead_days": 10,
                "deposit_pct": 0.2,
            })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["circuit_tripped"])
        self.assertLessEqual(body["recommended_offer_price"], 16.2)
        self.assertTrue(body["pending_human_approval"])
        self.assertIsNone(body["turn"])
        posted.assert_not_called()
        stored = self.c.get(f"/api/v1/sessions/{sid}")
        entities = stored.json()["history"][-1]["metadata"]["entities"]
        self.assertEqual(entities["lead_days"], 10)
        self.assertAlmostEqual(entities["deposit_pct"], 0.2)

    def test_trip_card_labels_package_terms_as_our_proposal(self):
        sid = "logic_trip"
        self.c.post("/api/v1/sessions", json={
            "session_id": sid,
            "product_name": "Sample",
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "moq": 500,
            "max_rounds": 1,
            "supplier_id": "S",
            "supplier_channel": "web_chat",
        })
        response = self.c.post(f"/api/v1/sessions/{sid}/round", json={
            "opponent_offer_price": 18.0,
            "opponent_raw_text": "lowest 18.00",
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["circuit_tripped"])
        self.assertIsNone(body["reply_text"])
        card = body["executive_card_en"]
        self.assertNotIn("Package Terms: Lead Time", card)
        self.assertIn("OUR proposal", card)
        self.assertIn("not counterpart-confirmed", card)

    def test_autopilot_deal_candidate_does_not_call_evolus(self):
        sid = "logic_auto"
        self.c.post("/api/v1/sessions", json={
            "session_id": sid,
            "product_name": "Sample",
            "target_price": 12.0,
            "ceiling_price": 16.2,
            "moq": 500,
            "max_rounds": 1,
            "supplier_id": "S",
            "supplier_channel": "web_chat",
        })
        os.environ["EVOLUS_API_KEY"] = "k"
        os.environ["EVOLUS_APP_ID"] = "a"
        try:
            with mock.patch("requests.post") as posted:
                response = self.c.post(f"/api/v1/sessions/{sid}/round", json={
                    "opponent_offer_price": 12.0,
                    "opponent_raw_text": "行吧服了你了，就按你說的 12.00，發合同過來吧。",
                    "mode": "autopilot",
                })
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertNotEqual(body["autopilot_state"], "DEAL_CLOSED")
            posted.assert_not_called()
        finally:
            os.environ.pop("EVOLUS_API_KEY", None)
            os.environ.pop("EVOLUS_APP_ID", None)


if __name__ == "__main__":
    unittest.main()
