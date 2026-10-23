"""Student-t reserve update.

A quote near the predictive centre keeps a high weight and shrinks the
scale. A far quote is down-weighted and still moves the mean. The normal
CDF helper stays available for callers that ask for a Gaussian tail.
Deal probability uses the Student-t.
"""
import unittest
from src.legacy.bayesian_v2 import (
    RobustBayesianInferenceEngine,
    robust_bayes_update,
    compute_calibrated_deal_prob,
    BayesianBeliefState,
)


class TestBayesianV2(unittest.TestCase):

    def setUp(self):
        self.engine = RobustBayesianInferenceEngine(obs_noise_std=0.06)

    def test_genuine_offer_update(self):
        """A quote near prior + markup keeps a high weight and shrinks the scale."""
        prior_mean = 1.08
        prior_std = 0.10
        observed_offer = 1.18
        estimated_markup = 0.08

        res = self.engine.update(
            prior_mean=prior_mean,
            prior_std=prior_std,
            observed_offer=observed_offer,
            estimated_markup=estimated_markup,
            our_next_offer=1.12,
        )

        self.assertIsInstance(res, BayesianBeliefState)
        self.assertEqual(res.interval_model, "student_t")
        self.assertGreater(res.clean_responsibility, 0.70)
        self.assertLess(res.posterior_std, prior_std)
        self.assertGreaterEqual(res.posterior_mean, 1.08)
        self.assertLessEqual(res.posterior_mean, 1.18)
        self.assertGreater(res.info_gain_bits, 0.01)
        self.assertLess(res.credible_interval_95[0], res.posterior_mean)
        self.assertGreater(res.credible_interval_95[1], res.posterior_mean)

    def test_far_quote_is_down_weighted_and_still_moves_the_mean(self):
        """A heavy tail down-weights 2.30. It does not discard the quote."""
        prior_mean = 1.08
        prior_std = 0.10
        extreme_offer = 2.30

        res = self.engine.update(
            prior_mean=prior_mean,
            prior_std=prior_std,
            observed_offer=extreme_offer,
            estimated_markup=0.08,
        )

        self.assertLess(res.robust_weight, 0.2)
        self.assertGreater(res.posterior_mean, prior_mean)
        self.assertLess(res.posterior_mean, extreme_offer)
        self.assertGreater(res.clean_responsibility, 0.0)
        self.assertLessEqual(res.clean_responsibility, 1.0)

    def test_calibrated_normal_cdf(self):
        """The Gaussian helper stays calibrated for callers that ask for it."""
        p_mid = self.engine.normal_cdf(1.0, 1.0, 0.1)
        self.assertAlmostEqual(p_mid, 0.5, places=3)

        p_high = self.engine.normal_cdf(1.0 + 1.95996 * 0.1, 1.0, 0.1)
        self.assertAlmostEqual(p_high, 0.975, places=3)

        p_low = self.engine.normal_cdf(1.0 - 1.95996 * 0.1, 1.0, 0.1)
        self.assertAlmostEqual(p_low, 0.025, places=3)

        p_inf = self.engine.normal_cdf(1.05, 1.0, 0.0)
        self.assertEqual(p_inf, 1.0)
        p_neg = self.engine.normal_cdf(0.95, 1.0, 0.0)
        self.assertEqual(p_neg, 0.0)

    def test_global_helper_functions(self):
        b = robust_bayes_update(1.08, 0.10, 1.15)
        self.assertIsInstance(b, BayesianBeliefState)
        self.assertGreater(b.clean_responsibility, 0.5)
        self.assertEqual(b.interval_model, "student_t")

        prob = compute_calibrated_deal_prob(1.10, 1.08, 0.05)
        self.assertGreater(prob, 0.5)
        self.assertLess(prob, 1.0)


if __name__ == "__main__":
    unittest.main()
