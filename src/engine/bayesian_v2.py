"""TradeFlow v5 — 嚴謹高斯-均勻混合似然貝氏推斷引擎。

數理本質：
1. 雙模態生成模型：區分「真實商務要約 (Z=1)」與「試探性/漫天要價離群噪聲 (Z=0)」。
2. 責任度權重 (w_t)：依據先驗分佈與觀測噪聲計算真實要約的後驗概率；惡意試探報價使 w_t -> 0。
3. 封閉矩匹配 (Closed-Form Moment Matching)：結合高斯共軛更新，並在離群噪聲下嚴格守恆方差，杜絕虛假信息增益。
4. 校準概率與置信區間：標準常態分佈累積函數 CDF 計算精確成交概率，給出 95% 雙邊貝氏置信區間。
"""
from __future__ import annotations
import math
from typing import Tuple, Dict, Any, Optional
from pydantic import BaseModel, Field


class BayesianBeliefState(BaseModel):
    posterior_mean: float = Field(..., description="後驗底價均值乘數")
    posterior_std: float = Field(..., description="後驗底價不確定性標準差")
    clean_responsibility: float = Field(..., description="本次觀測屬於真實商務報價的後驗概率 w_t")
    credible_interval_95: Tuple[float, float] = Field(..., description="95% 貝氏置信區間 [Lower, Upper]")
    info_gain_bits: float = Field(..., description="香農信息增益 (Kullback-Leibler 散度，單位: bits)")
    # [P0 修正] 允許 None：當本輪沒有新報價可評估、且呼叫端也未提供 our_next_offer
    # 時，成交概率是「未計算」而非 0。顯示層應渲染為 "—"，不可捏造數值。
    deal_probability: Optional[float] = Field(
        default=None,
        description="在當前出價下對手接受的校準概率 P(Reserve <= Offer)；無法評估時為 None",
    )


class RobustBayesianInferenceEngine:
    """高斯-均勻雙模態混合似然推斷引擎。"""

    def __init__(
        self,
        obs_noise_std: float = 0.06,       # 觀測噪聲標準差 sigma_epsilon
        prior_clean_prob: float = 0.85,     # 正常商務報價的先驗概率 pi_0
        domain_min: float = 0.80,          # 合理價格域下界 L_min
        domain_max: float = 2.50,          # 合理價格域上界 L_max
    ):
        """[誠實揭露] 下列為編譯時固定的啟發式常數，非資料擬合：
        obs_noise_std=0.06、prior_clean_prob=0.85、域 [0.80, 2.50]。
        全域單例 _default_engine 以這些預設建立，但 update() 逐次接受
        estimated_markup，可依需求覆寫。」"""
        self.obs_noise_std = obs_noise_std
        self.prior_clean_prob = prior_clean_prob
        self.domain_min = domain_min
        self.domain_max = domain_max
        self.uniform_density = 1.0 / max(1e-4, (domain_max - domain_min))

    @staticmethod
    def _gaussian_pdf(x: float, mean: float, std: float) -> float:
        """計算一維常態分佈概率密度值 N(x | mean, std^2)。"""
        if std <= 1e-6:
            return 1e6 if abs(x - mean) < 1e-4 else 0.0
        var = std * std
        denom = math.sqrt(2.0 * math.pi * var)
        diff = x - mean
        return math.exp(-0.5 * (diff * diff) / var) / denom

    @classmethod
    def normal_cdf(cls, x: float, mean: float, std: float) -> float:
        """標準常態分佈累積函數 CDF: Phi((x - mean) / std)。"""
        if std <= 1e-7:
            return 1.0 if x >= mean else 0.0
        z = (x - mean) / (std * math.sqrt(2.0))
        return 0.5 * (1.0 + math.erf(z))

    def update(
        self,
        prior_mean: float,
        prior_std: float,
        observed_offer: Optional[float] = None,
        estimated_markup: float = 0.08,
        our_next_offer: Optional[float] = None,
    ) -> BayesianBeliefState:
        """執行混合似然推斷與矩匹配。

        Args:
            prior_mean: 先驗底價均值乘數 (如 1.08)
            prior_std: 先驗底價不確定性標準差 (如 0.10)
            observed_offer: 觀測到的供應商報價乘數 (如 1.25)。
                **[P0 修正]** 可為 None —— 代表「本輪沒有新證據」。呼叫端若
                已經自行把本輪報價更新進 prior_mean/prior_std（例如 /round
                第 2 步的 bayes_update），就必須傳 None，否則同一筆證據會被
                更新兩次，導致同一個回應裡出現兩個不同的後驗。None 時：
                  - 不做似然更新，後驗 = 先驗；
                  - 責任度 w_t 記為 1.0（無異常證據可判別）；
                  - 信息增益為 0 bit；
                  - 成交概率改用 our_next_offer 評估（若未提供則為 None）。
            estimated_markup: 預估供應商當前輪次的加成利潤率 m_t (如 0.08)
            our_next_offer: 我方擬出的下一輪出價乘數 (若提供則計算該出價下的成交概率)
        """
        # [P0 修正] 無新證據：後驗 = 先驗，只計算依賴後驗的欄位。
        if observed_offer is None:
            z_95 = 1.95996
            ci_lower = max(0.0, prior_mean - z_95 * prior_std)
            ci_upper = prior_mean + z_95 * prior_std
            deal_prob = (
                self.normal_cdf(our_next_offer, prior_mean, prior_std)
                if our_next_offer is not None else None
            )
            return BayesianBeliefState(
                posterior_mean=round(prior_mean, 4),
                posterior_std=round(prior_std, 4),
                clean_responsibility=1.0,
                credible_interval_95=(round(ci_lower, 4), round(ci_upper, 4)),
                info_gain_bits=0.0,
                deal_probability=None if deal_prob is None else round(deal_prob, 4),
            )

        # 1. 計算真實要約假設下的邊際似然 (Marginal Likelihood for Genuine Offer)
        pred_mean = prior_mean + estimated_markup
        pred_var = (prior_std ** 2) + (self.obs_noise_std ** 2)
        pred_std = math.sqrt(pred_var)

        p_y_given_clean = self._gaussian_pdf(observed_offer, pred_mean, pred_std)
        p_y_given_noise = self.uniform_density

        # 2. 計算責任度 w_t = P(Z_t = 1 | Y_t = y_t)
        num = self.prior_clean_prob * p_y_given_clean
        denom = num + (1.0 - self.prior_clean_prob) * p_y_given_noise
        w_t = num / denom if denom > 1e-12 else 0.0

        # 3. 計算真信號高斯-高斯共軛更新量 (Kalman Gain)
        y_tilde = observed_offer - estimated_markup
        kalman_gain = (prior_std ** 2) / pred_var
        mu_clean = prior_mean + kalman_gain * (y_tilde - prior_mean)
        var_clean = (1.0 - kalman_gain) * (prior_std ** 2)

        # 4. 混合分佈的一階與二階矩匹配 (Moment Matching)
        post_mean = (1.0 - w_t) * prior_mean + w_t * mu_clean
        post_var = (
            (1.0 - w_t) * (prior_std ** 2)
            + w_t * var_clean
            + w_t * (1.0 - w_t) * ((mu_clean - prior_mean) ** 2)
        )
        post_std = math.sqrt(max(post_var, 1e-8))

        # 5. 香農信息增益 KL 散度 D_KL(Posterior || Prior) in bits
        # 兩常態分佈之 KL 散度: ln(s0/s1) + (s1^2 + (m1-m0)^2)/(2*s0^2) - 0.5
        kl_nats = (
            math.log(prior_std / post_std)
            + (post_var + ((post_mean - prior_mean) ** 2)) / (2.0 * (prior_std ** 2))
            - 0.5
        )
        info_gain_bits = max(0.0, kl_nats / math.log(2.0))

        # 6. 計算 95% 貝氏置信區間 CI_0.95 = [mu - 1.95996*sigma, mu + 1.95996*sigma]
        z_95 = 1.95996
        ci_lower = max(0.0, post_mean - z_95 * post_std)
        ci_upper = post_mean + z_95 * post_std

        # 7. 成交概率 P(Reserve <= Offer)
        eval_offer = our_next_offer if our_next_offer is not None else observed_offer
        deal_prob = self.normal_cdf(eval_offer, post_mean, post_std)

        return BayesianBeliefState(
            posterior_mean=round(post_mean, 4),
            posterior_std=round(post_std, 4),
            clean_responsibility=round(w_t, 4),
            credible_interval_95=(round(ci_lower, 4), round(ci_upper, 4)),
            info_gain_bits=round(info_gain_bits, 4),
            deal_probability=round(deal_prob, 4),
        )


# 全域單例便利用法
_default_engine = RobustBayesianInferenceEngine()


def robust_bayes_update(
    prior_mean: float,
    prior_std: float,
    observed_offer: Optional[float] = None,
    estimated_markup: float = 0.08,
    our_next_offer: Optional[float] = None,
) -> BayesianBeliefState:
    """提供全域便利用法執行高斯-均勻混合似然推斷。

    observed_offer=None 表示「本輪沒有新證據」——後驗等於傳入的先驗。
    """
    return _default_engine.update(
        prior_mean=prior_mean,
        prior_std=prior_std,
        observed_offer=observed_offer,
        estimated_markup=estimated_markup,
        our_next_offer=our_next_offer,
    )


def compute_calibrated_deal_prob(offer: float, reserve_mean: float, reserve_std: float) -> float:
    """計算嚴格的累積常態分佈成交概率 P(Reserve <= Offer)。"""
    return RobustBayesianInferenceEngine.normal_cdf(offer, reserve_mean, reserve_std)
