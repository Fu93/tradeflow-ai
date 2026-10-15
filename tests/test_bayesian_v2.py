"""單元測試：Phase 1 高斯-均勻混合似然貝氏推斷引擎 (bayesian_v2)。

驗證目標：
1. 真實商務要約下的正常後驗更新、方差縮減與香農信息增益。
2. 惡意漫天要價/試探性離群報價下的硬拒絕 (w_t -> 0)、均值抗偏離性與方差守恆 (不確定性不虛假縮減)。
3. 標準正態 CDF 計算成交概率之統計校準度。
4. 95% 雙邊貝氏置信區間有效性。
"""
import math
import unittest
from src.engine.bayesian_v2 import (
    RobustBayesianInferenceEngine,
    robust_bayes_update,
    compute_calibrated_deal_prob,
    BayesianBeliefState,
)


class TestBayesianV2(unittest.TestCase):

    def setUp(self):
        self.engine = RobustBayesianInferenceEngine(
            obs_noise_std=0.06,
            prior_clean_prob=0.85,
            domain_min=0.80,
            domain_max=2.50,
        )

    def test_genuine_offer_update(self):
        """測試合理報價（先驗 1.08 + 加成 0.08 = 1.16，觀測到 1.18）。

        應具有高責任度 (w_t >= 0.70)，後驗均值合理靠近信號，方差正常收斂，信息增益 > 0。
        """
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
        # 責任度應顯著為真
        self.assertGreater(res.clean_responsibility, 0.70)
        # 觀測有效，後驗方差應收斂縮小
        self.assertLess(res.posterior_std, prior_std)
        # 均值應有平滑微調
        self.assertGreaterEqual(res.posterior_mean, 1.08)
        self.assertLessEqual(res.posterior_mean, 1.18)
        # 應有正向信息增益
        self.assertGreater(res.info_gain_bits, 0.01)
        # 95% 置信區間應包含均值
        self.assertLess(res.credible_interval_95[0], res.posterior_mean)
        self.assertGreater(res.credible_interval_95[1], res.posterior_mean)

    def test_extreme_outlier_probe_rejection(self):
        """測試極端惡意試探報價（如 2.30，偏離中樞超過 100%）。

        數理要求：
        1. 責任度 w_t 應急劇趨近於 0 (< 0.01)。
        2. 後驗均值漂移 Delta_mu 應極小 (< 0.5%)。
        3. 方差守恆：後驗方差不可縮減，與先驗方差偏差 <= 0.005。
        """
        prior_mean = 1.08
        prior_std = 0.10
        extreme_offer = 2.30  # 惡意試探報價

        res = self.engine.update(
            prior_mean=prior_mean,
            prior_std=prior_std,
            observed_offer=extreme_offer,
            estimated_markup=0.08,
        )

        # 責任度應被壓制至近乎 0
        self.assertLess(res.clean_responsibility, 0.01)
        # 均值漂移小於 0.5%
        drift = abs(res.posterior_mean - prior_mean) / prior_mean
        self.assertLess(drift, 0.005)
        # 方差嚴格守恆，不可自欺欺人地縮小
        self.assertAlmostEqual(res.posterior_std, prior_std, delta=0.005)
        # 信息增益應趨近於 0 bits
        self.assertLess(res.info_gain_bits, 0.02)

    def test_calibrated_normal_cdf(self):
        """測試標準常態分佈累積函數 CDF 計算精確度。"""
        # 均值處成交概率應為 50%
        p_mid = self.engine.normal_cdf(1.0, 1.0, 0.1)
        self.assertAlmostEqual(p_mid, 0.5, places=3)

        # mu + 1.96 * sigma 處應為 97.5%
        p_high = self.engine.normal_cdf(1.0 + 1.95996 * 0.1, 1.0, 0.1)
        self.assertAlmostEqual(p_high, 0.975, places=3)

        # mu - 1.96 * sigma 處應為 2.5%
        p_low = self.engine.normal_cdf(1.0 - 1.95996 * 0.1, 1.0, 0.1)
        self.assertAlmostEqual(p_low, 0.025, places=3)

        # 邊界極端標準差退化安全保護
        p_inf = self.engine.normal_cdf(1.05, 1.0, 0.0)
        self.assertEqual(p_inf, 1.0)
        p_neg = self.engine.normal_cdf(0.95, 1.0, 0.0)
        self.assertEqual(p_neg, 0.0)

    def test_global_helper_functions(self):
        """測試全域便捷函式 robust_bayes_update 與 compute_calibrated_deal_prob。"""
        b = robust_bayes_update(1.08, 0.10, 1.15)
        self.assertIsInstance(b, BayesianBeliefState)
        self.assertGreater(b.clean_responsibility, 0.5)

        prob = compute_calibrated_deal_prob(1.10, 1.08, 0.05)
        self.assertGreater(prob, 0.5)
        self.assertLess(prob, 1.0)


if __name__ == "__main__":
    unittest.main()
