"""Checks for the nine findings on the coupled-negotiation review."""
from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

from src.config.heuristics import (  # noqa: E402
    CVAR_TAIL_FRACTION,
    CVAR_WEIGHT,
    PRIOR_RESERVE_MEAN,
    WEITZMAN_SEARCH_COST,
)
from src.engine.coupled import (  # noqa: E402
    _cvar_se,
    _cvar_upper,
    _expected_improvement,
    evaluate_vector,
    solve_coupled,
    weitzman_reservation,
)
from src.engine.hazard import HazardBelief  # noqa: E402
from src.engine.multi_solver import MultiAuctionSession  # noqa: E402
from src.market.official_refs import fetch_ecb_rates, fx_to_base  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "frankfurter_ecb_v2_rates.json"


def _reference_root(mean: float, sigma: float, cost: float) -> float:
    """Independent bisection, wider and finer than the solver's."""
    lo = mean - 64.0 * sigma - abs(cost) - 8.0
    hi = mean + 64.0 * sigma + abs(cost) + 8.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _expected_improvement(mid, mean, sigma) > cost:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


class TestWeitzmanRoot(unittest.TestCase):
    def test_root_sits_below_the_mean_and_matches_a_finer_bisection(self):
        mean, sigma, cost = 1.08, 0.10, float(WEITZMAN_SEARCH_COST)
        root = weitzman_reservation(mean, sigma, cost)
        reference = _reference_root(mean, sigma, cost)
        self.assertAlmostEqual(root, reference, places=8)
        self.assertLess(root, mean)
        self.assertAlmostEqual(root, 1.0307, places=3)
        self.assertAlmostEqual(_expected_improvement(root, mean, sigma), cost, places=8)

    def test_wide_sigma_root_is_below_one(self):
        root = weitzman_reservation(1.0, 0.2, float(WEITZMAN_SEARCH_COST))
        reference = _reference_root(1.0, 0.2, float(WEITZMAN_SEARCH_COST))
        self.assertAlmostEqual(root, reference, places=8)
        self.assertLess(root, 1.0)
        self.assertAlmostEqual(root, 0.8195, places=3)

    def test_reservation_uses_std_times_mean_and_includes_kappa(self):
        result = solve_coupled(
            np.array([0.80]),
            np.array([0.50]),
            np.array([0.10]),
            ceiling=1.35,
            kappas=np.array([0.25]),
            n_samples=30,
            n_grid=4,
            rng=np.random.default_rng(3),
        )
        expected = weitzman_reservation(0.50 + 0.25, abs(0.10 * 0.50))
        clamped = weitzman_reservation(0.50 + 0.25, 0.10 * max(0.50, 1.0))
        self.assertAlmostEqual(result["weitzman_index"][0], expected, places=6)
        self.assertGreater(abs(result["weitzman_index"][0] - clamped), 1e-3)


class TestLandedBasis(unittest.TestCase):
    def test_missing_landed_data_does_not_mix_a_zero_adder(self):
        session = MultiAuctionSession(
            suppliers=[
                {"name": "A", "freight": 0.5, "duty_rate": 0.0, "fees": 0.2},
                {"name": "B"},
            ],
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=40,
            n_open=6,
            seed=5,
        )
        info = session.step_round([13.0, 14.0])
        self.assertEqual(info["ranking_basis"], "unit_price")
        self.assertEqual(info["landed_status"][1], "no data")
        self.assertIsNone(info["landed_costs"][1])
        self.assertEqual(info["kappas_norm"], [0.0, 0.0])
        self.assertIn("no data", info["kappa_note"])
        self.assertIn("not treated as a zero adder", info["kappa_note"])
        # B's outside option is A's unit price. A's landed figure is 13.7 and
        # must not become B's ceiling.
        self.assertAlmostEqual(info["effective_ceilings_cny"][1], 13.0, places=6)
        summary = session.get_summary()
        self.assertIn("no data", summary["kappa_note"])


class TestFrankfurterList(unittest.TestCase):
    def test_recorded_v2_list_parses_and_a_bad_payload_is_no_data(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, list)
        self.assertGreaterEqual(len(payload), 3)

        def getter(url, timeout):
            del url, timeout
            return payload

        parsed = fetch_ecb_rates(["USD", "CNY", "GBP"], getter=getter)
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["as_of"], "2026-10-16")
        self.assertAlmostEqual(parsed["value"]["CNY"], 7.524)
        self.assertAlmostEqual(parsed["value"]["GBP"], 0.84385)
        self.assertAlmostEqual(parsed["value"]["USD"], 1.1253)
        self.assertFalse(parsed["used_in_solver"])
        self.assertAlmostEqual(fx_to_base(parsed["value"], "USD", "CNY"), 7.524 / 1.1253)

        def broken(url, timeout):
            del url, timeout
            return ["not-a-rate"]

        failed = fetch_ecb_rates(["USD"], getter=broken)
        self.assertEqual(failed["status"], "no data")
        self.assertIsNone(failed["value"])


class TestAuctionValidation(unittest.TestCase):
    def test_missing_fx_target_above_ceiling_and_risk_cap(self):
        from fastapi.testclient import TestClient
        from src.app.main import app

        client = TestClient(app)
        missing_fx = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 20,
            "n_rounds": 1,
            "target_price": 12.0,
            "walk_away_ceiling": 16.2,
            "suppliers": [{"name": "Overseas", "currency": "USD", "quote": 2.0}],
            "rounds_quotes": [[2.0]],
        })
        self.assertEqual(missing_fx.status_code, 422, missing_fx.text)
        self.assertIn("FX", missing_fx.json()["detail"])

        upside_down = client.post("/api/v1/multi-auction/run", json={
            "target_price": 20.0,
            "walk_away_ceiling": 16.2,
        })
        self.assertEqual(upside_down.status_code, 422, upside_down.text)
        detail = upside_down.json()["detail"]
        text = detail if isinstance(detail, str) else " ".join(item.get("msg", "") for item in detail)
        self.assertIn("target_price", text)

        too_loose = client.post("/api/v1/multi-auction/run", json={"risk_tolerance": 0.9})
        self.assertEqual(too_loose.status_code, 422, too_loose.text)

        duty = client.post("/api/v1/multi-auction/run", json={
            "suppliers": [{"name": "A", "duty_rate": 5}],
            "rounds_quotes": [[13.0]],
        })
        self.assertEqual(duty.status_code, 422, duty.text)

    def test_duplicate_supplier_names_are_a_readable_422(self):
        from fastapi.testclient import TestClient
        from src.app.main import app

        client = TestClient(app)
        duplicated = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 8,
            "n_rounds": 1,
            "suppliers": [{"name": "Ningbo"}, {"name": "Ningbo "}, {"name": "Shenzhen"}],
            "rounds_quotes": [[13.0, 14.0, 15.0]],
        })
        self.assertEqual(duplicated.status_code, 422, duplicated.text)
        self.assertIn("Duplicate supplier name: Ningbo", duplicated.text)
        self.assertIn("Each supplier needs its own name", duplicated.text)

        same_letters = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 8,
            "n_rounds": 1,
            "suppliers": [{"name": "Ningbo"}, {"name": "ningbo"}],
            "rounds_quotes": [[13.0, 14.0]],
        })
        self.assertEqual(same_letters.status_code, 422, same_letters.text)
        self.assertIn("Duplicate supplier name: ningbo", same_letters.text)

        case_pair = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 8,
            "n_rounds": 1,
            "suppliers": [{"name": "A"}, {"name": "a"}],
            "rounds_quotes": [[13.0, 14.0]],
        })
        self.assertEqual(case_pair.status_code, 422, case_pair.text)
        self.assertIn("Duplicate supplier name: a", case_pair.text)
        self.assertIn("Each supplier needs its own name", case_pair.text)
        self.assertNotIn("Value error,", case_pair.text)

        fullwidth = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 8,
            "n_rounds": 1,
            "suppliers": [{"name": "Ａ"}, {"name": "A"}],
            "rounds_quotes": [[13.0, 14.0]],
        })
        self.assertEqual(fullwidth.status_code, 422, fullwidth.text)
        self.assertIn("Duplicate supplier name", fullwidth.text)
        self.assertNotIn("Value error,", fullwidth.text)

        spaced = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 8,
            "n_rounds": 1,
            "suppliers": [{"name": "A B"}, {"name": "A  B"}],
            "rounds_quotes": [[13.0, 14.0]],
        })
        self.assertEqual(spaced.status_code, 422, spaced.text)
        self.assertIn("Duplicate supplier name", spaced.text)

        dotted = client.post("/api/v1/multi-auction/run", json={
            "n_samples": 8,
            "n_rounds": 1,
            "suppliers": [{"name": "İ"}, {"name": "i"}],
            "rounds_quotes": [[13.0, 14.0]],
        })
        self.assertEqual(dotted.status_code, 422, dotted.text)
        self.assertIn("Duplicate supplier name", dotted.text)

    def test_all_quotes_above_the_ceiling_are_flagged_and_not_actionable(self):
        session = MultiAuctionSession(
            suppliers=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=30,
            n_open=6,
            seed=8,
        )
        info = session.step_round([20.0, 21.0, 22.0])
        self.assertTrue(info["all_quotes_above_ceiling"])
        self.assertTrue(all(figure == "mean" for figure in info["hazard_figure"]))
        self.assertTrue(all(offer is None for offer in info["counter_offers_cny"]))
        self.assertTrue(all(item["blocked"] for item in [{"blocked": flag} for flag in info["blocked"]]))
        summary = session.get_summary()
        self.assertTrue(summary["all_quotes_above_ceiling"])
        self.assertTrue(summary["winner_above_ceiling"])
        self.assertGreater(summary["winning_price"], 16.2)

    def test_a_walked_away_supplier_leaves_the_rival_set(self):
        session = MultiAuctionSession(
            suppliers=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=40,
            n_open=6,
            seed=9,
        )
        first = session.step_round([13.0, 14.0, 15.0])
        session.record_outcomes(["walk_away", "continue", "continue"])
        # A blocked counter must not raise when the outcome is recorded.
        first["counter_offers_cny"][0] = None
        session.trace[-1] = first
        session.record_outcomes(["walk_away", None, None])
        second = session.step_round([13.0, 14.0, 15.0])
        self.assertNotIn("A", second["active_suppliers"])
        self.assertEqual(second["block_reasons"][0], "walked away; removed from the rival set")
        self.assertIsNone(second["counter_offers_cny"][0])
        # B and C do not use A's quote as the rival cap: their ceiling is the
        # other remaining unit price, not A's 13.
        self.assertAlmostEqual(second["effective_ceilings_cny"][1], 15.0, places=6)
        self.assertAlmostEqual(second["effective_ceilings_cny"][2], 14.0, places=6)

    def test_a_blocked_supplier_counter_is_not_actionable(self):
        session = MultiAuctionSession(
            suppliers=[{"name": "A"}, {"name": "B"}],
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=80,
            n_open=6,
            seed=4,
            risk_tolerance=0.02,
        )
        info = session.step_round([15.0, 15.5])
        self.assertTrue(any(info["blocked"]))
        for flag, offer, reason, upper in zip(
            info["blocked"], info["counter_offers_cny"], info["block_reasons"], info["hazard_upper"],
        ):
            if flag:
                self.assertIsNone(offer)
                self.assertIn("not actionable", reason)
                self.assertIn("upper bound", reason)
                self.assertIn("least aggressive", reason)
                self.assertGreaterEqual(upper, 0.02)
        self.assertTrue(all(figure == "upper" for figure, flag in zip(info["hazard_figure"], info["blocked"]) if flag))

    def test_blocked_card_hazard_is_the_upper_bound_that_failed_the_cap(self):
        """The cap is applied to the credible upper bound, not the mean.

        At this tolerance the least-aggressive offer's mean sits under the
        cap while its upper bound does not. The figure a card should show
        is that upper bound.
        """
        cap = 0.08
        session = MultiAuctionSession(
            suppliers=[{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=40,
            n_open=20,
            seed=4,
            risk_tolerance=cap,
        )
        info = session.step_round([13.26, 15.60, 15.92, 16.80])
        under_mean = []
        for flag, mean, upper, reason, offer, figure in zip(
            info["blocked"],
            info["p_break_array"],
            info["hazard_upper"],
            info["block_reasons"],
            info["counter_offers_cny"],
            info["hazard_figure"],
        ):
            self.assertTrue(flag)
            self.assertIsNone(offer)
            self.assertEqual(figure, "upper")
            self.assertIn("upper bound", reason)
            self.assertIn("least aggressive", reason)
            self.assertGreaterEqual(upper, cap)
            if mean < cap:
                under_mean.append((mean, upper))
        self.assertTrue(under_mean)
        mean, upper = under_mean[0]
        self.assertLess(mean, cap)
        self.assertGreaterEqual(upper, cap)
        self.assertAlmostEqual(mean, 0.054897, places=4)
        self.assertAlmostEqual(upper, 0.096797, places=4)
        self.assertEqual(len(info["hazard_figure"]), 4)
        self.assertTrue(all(figure == "upper" for figure in info["hazard_figure"]))

    def test_the_desk_does_not_parse_the_block_reason_for_the_risk_figure(self):
        js = (ROOT / "src/web/static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("hazard_figure", js)
        self.assertNotIn('indexOf("upper bound")', js)
        self.assertNotIn("indexOf('upper bound')", js)
        note = js.index("tail saturated at the ceiling")
        details = js.index('<details class="dev-details">')
        self.assertLess(note, details)
        self.assertIn("tfDealMoney(mc.cvar_cost, data.product_target, \"CNY\")", js)
        self.assertNotIn("String(mc.cvar_cost", js)
        self.assertNotIn('? "tail saturated at ceiling" :', js)


class TestCvarAndCells(unittest.TestCase):
    def test_reported_cvar_se_tracks_the_sampling_sd(self):
        rng = np.random.default_rng(7)
        n = 5000
        tau = float(CVAR_TAIL_FRACTION)
        reps = 120
        estimates = np.empty(reps, dtype=np.float64)
        reported = np.empty(reps, dtype=np.float64)
        naive = np.empty(reps, dtype=np.float64)
        tail_n = max(1, int(math.ceil(tau * n)))
        for i in range(reps):
            sample = rng.standard_normal(n)
            estimates[i] = _cvar_upper(sample, tau)
            reported[i] = _cvar_se(sample, tau)
            worst = np.partition(sample, n - tail_n)[n - tail_n :]
            naive[i] = float(np.std(worst, ddof=1) / math.sqrt(tail_n))
        empirical = float(np.std(estimates, ddof=1))
        ratio = float(np.mean(reported)) / empirical
        self.assertGreater(ratio, 0.80)
        self.assertLess(ratio, 1.25)
        self.assertGreater(float(np.mean(reported)), float(np.mean(naive)) * 1.20)

    def test_tail_pinned_at_the_ceiling_is_flagged(self):
        n = 200
        k = 2
        ceiling = 1.35
        beliefs = [HazardBelief(), HazardBelief()]
        means = np.array([1.08, 1.08])
        failed = {
            "reserves": np.full((k, n), 2.0),
            "uniforms": np.full((k, n), 0.5),
            "bluff": np.full((k, n), 0.2),
        }
        saturated = evaluate_vector(
            np.array([0.5, 0.5]),
            failed,
            np.zeros(k),
            ceiling,
            quotes=np.array([1.2, 1.3]),
            beliefs=beliefs,
            reserve_means=means,
            risk_tolerance=0.15,
            round_index=1.0,
        )
        self.assertTrue(saturated["tail_saturated_at_ceiling"])
        self.assertAlmostEqual(saturated["cvar_cost"], ceiling, places=6)
        self.assertEqual(saturated["cvar_se"], 0.0)

        dealt = {
            "reserves": np.full((k, n), 0.9),
            "uniforms": np.ones((k, n)),
            "bluff": np.zeros((k, n)),
        }
        informative = evaluate_vector(
            np.array([1.0, 1.05]),
            dealt,
            np.zeros(k),
            ceiling,
            quotes=np.array([1.2, 1.3]),
            beliefs=beliefs,
            reserve_means=means,
            risk_tolerance=0.15,
            round_index=1.0,
        )
        self.assertFalse(informative["tail_saturated_at_ceiling"])
        self.assertLess(informative["cvar_cost"], ceiling - 0.05)

        session = MultiAuctionSession(
            suppliers=[{"name": "A"}, {"name": "B"}],
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=400,
            n_open=8,
            seed=1,
        )
        info = session.step_round([13.2, 14.4])
        self.assertTrue(info["mc"]["tail_saturated_at_ceiling"])
        self.assertAlmostEqual(info["mc"]["cvar_cost"], 16.2 / 12.0, places=6)

    def test_tail_fraction_is_not_the_hazard_cap_and_cells_are_one_pass(self):
        quotes = np.array([1.12, 1.20, 1.28, 1.34])
        means = np.full(4, PRIOR_RESERVE_MEAN)
        stds = np.full(4, 0.10)
        tight = solve_coupled(
            quotes, means, stds, ceiling=1.35,
            n_samples=80, n_grid=8, risk_tolerance=0.05,
            rng=np.random.default_rng(2),
        )
        loose = solve_coupled(
            quotes, means, stds, ceiling=1.35,
            n_samples=80, n_grid=8, risk_tolerance=0.20,
            rng=np.random.default_rng(2),
        )
        self.assertAlmostEqual(tight["mc"]["tail_fraction"], CVAR_TAIL_FRACTION)
        self.assertAlmostEqual(loose["mc"]["tail_fraction"], CVAR_TAIL_FRACTION)
        self.assertAlmostEqual(tight["cvar_weight"], CVAR_WEIGHT)
        self.assertAlmostEqual(loose["cvar_weight"], CVAR_WEIGHT)
        self.assertEqual(tight["sweeps"], 1)
        k = 4
        self.assertLessEqual(
            tight["total_rollouts"],
            tight["n_samples"] * (tight["n_grid"] + 1) * k,
        )
        self.assertGreater(tight["total_rollouts"], 0)


if __name__ == "__main__":
    unittest.main()
