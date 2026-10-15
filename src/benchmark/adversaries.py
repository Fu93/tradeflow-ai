"""TradeFlow v4 — 5 大客觀非 LLM 策略對抗代理 (Adversaries)。

數學規格：
1. Hard-Bargainer (Boulwarism, kappa=3.5): 前期極端強硬，僅在極限輪次釋放微小讓步。
2. Conceder (急躁甩賣, kappa=0.25): 前兩輪即快速降價，釋放大比例空間。
3. Tit-for-Tat (對等互惠, beta=1.0): 鏡像買方讓步幅度；買方停滯則自身停滯。
4. Bluffing Hardliner (詐唬突變): 前 T-1 輪偽裝高底價，第 T 輪有 70% 機率突變跳水至真實底價。
5. Noisy Seller (市場噪聲): 基礎衰減疊加高斯隨機擾動 N(0, 0.04^2)。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional
import numpy as np


class BaseAdversary(ABC):
    """對抗代理基底類別。"""

    def __init__(
        self,
        name: str,
        init_price: float = 1.30,
        reserve_price: float = 1.08,
        max_rounds: int = 5,
    ):
        self.name = name
        self.init_price = init_price
        self.reserve_price = reserve_price
        self.max_rounds = max_rounds

    @abstractmethod
    def respond(
        self,
        round_idx: int,
        buyer_offer: float,
        buyer_history: List[float],
        supplier_history: List[float],
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        """根據當前輪次與買方出價歷史，計算供應商本輪報價（歸一化，1.0=目標價）。"""
        pass


class HardBargainerAdversary(BaseAdversary):
    """強硬派 (Boulwarism): 讓步指數 kappa=3.5，死守價格。

    誠實說明：Boulwarism 的本質就是「我的價格就是我的價格」——
    賣方給出一個初始立場後**不因買方出價而調整**，只在輪次推進時做象徵性讓步。
    因此本代理刻意不讀取 buyer_offer / buyer_history：這不是 bug，而是該策略
    身份的定義（take-it-or-leave-it，不回應買方）。若需「會回應買方的賣方」，
    請用 TitForTatAdversary。
    """

    def __init__(
        self,
        kappa: float = 3.5,
        init_price: float = 1.30,
        reserve_price: float = 1.15,
        max_rounds: int = 5,
    ):
        super().__init__("Hard-Bargainer", init_price, reserve_price, max_rounds)
        self.kappa = kappa

    def respond(
        self,
        round_idx: int,
        buyer_offer: float,
        buyer_history: List[float],
        supplier_history: List[float],
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        t = min(round_idx, self.max_rounds)
        fraction = (t / self.max_rounds) ** self.kappa
        price = self.init_price - (self.init_price - self.reserve_price) * fraction
        return float(max(price, self.reserve_price))


class ConcederAdversary(BaseAdversary):
    """急躁甩賣型: 讓步指數 kappa=0.25，迅速降價。

    誠實說明：本代理的降價節奏由**輪次推進**驅動（前瞻式時間讓步），
    不隨買方出價即時調整 —— 對應「想在期限內盡快成交」的賣方。它不是
    buyer-responsive 的買方鏡像；若測試需要「反映買方讓步的對手」，
    使用 TitForTatAdversary。
    """

    def __init__(
        self,
        kappa: float = 0.25,
        init_price: float = 1.30,
        reserve_price: float = 1.05,
        max_rounds: int = 5,
    ):
        super().__init__("Conceder", init_price, reserve_price, max_rounds)
        self.kappa = kappa

    def respond(
        self,
        round_idx: int,
        buyer_offer: float,
        buyer_history: List[float],
        supplier_history: List[float],
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        t = min(round_idx, self.max_rounds)
        fraction = (t / self.max_rounds) ** self.kappa
        price = self.init_price - (self.init_price - self.reserve_price) * fraction
        return float(max(price, self.reserve_price))


class TitForTatAdversary(BaseAdversary):
    """對等互惠型: 根據買方讓步步長鏡像調整自身降價。"""

    def __init__(
        self,
        beta: float = 1.0,
        init_price: float = 1.30,
        reserve_price: float = 1.08,
        max_rounds: int = 5,
    ):
        super().__init__("Tit-for-Tat", init_price, reserve_price, max_rounds)
        self.beta = beta

    def respond(
        self,
        round_idx: int,
        buyer_offer: float,
        buyer_history: List[float],
        supplier_history: List[float],
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        if round_idx <= 1 or not supplier_history:
            return float(self.init_price)

        last_supplier_price = supplier_history[-1]
        if len(buyer_history) >= 2:
            buyer_concession = max(0.0, buyer_history[-1] - buyer_history[-2])
        else:
            buyer_concession = 0.02

        supplier_drop = self.beta * buyer_concession
        new_price = max(self.reserve_price, last_supplier_price - supplier_drop)
        return float(new_price)


class BluffingAdversary(BaseAdversary):
    """詐唬突變型: 前期維持高價，第 T 輪有 70% 機率突變跳水至真實底價。"""

    def __init__(
        self,
        bluff_drop_prob: float = 0.70,
        init_price: float = 1.32,
        reserve_price: float = 1.10,
        max_rounds: int = 5,
    ):
        super().__init__("Bluffing Hardliner", init_price, reserve_price, max_rounds)
        self.bluff_drop_prob = bluff_drop_prob

    def respond(
        self,
        round_idx: int,
        buyer_offer: float,
        buyer_history: List[float],
        supplier_history: List[float],
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        if rng is None:
            rng = np.random.default_rng()

        if round_idx < self.max_rounds:
            # 前期僅象徵性微調
            decay = 0.01 * (round_idx - 1)
            return float(max(self.init_price - decay, self.reserve_price + 0.15))

        # 最後一輪面臨破局
        if rng.random() < self.bluff_drop_prob:
            # 詐唬被識破，跳水至真實底價
            return float(self.reserve_price)
        else:
            # 堅持不退讓
            return float(self.init_price - 0.03)


class NoisyAdversary(BaseAdversary):
    """市場噪聲型: 線性讓步疊加高斯噪聲（節奏由輪次驅動，非買方鏡像）。"""

    def __init__(
        self,
        noise_std: float = 0.04,
        init_price: float = 1.28,
        reserve_price: float = 1.08,
        max_rounds: int = 5,
    ):
        super().__init__("Noisy Seller", init_price, reserve_price, max_rounds)
        self.noise_std = noise_std

    def respond(
        self,
        round_idx: int,
        buyer_offer: float,
        buyer_history: List[float],
        supplier_history: List[float],
        rng: Optional[np.random.Generator] = None,
    ) -> float:
        if rng is None:
            rng = np.random.default_rng()

        fraction = (round_idx - 1) / max(1, self.max_rounds - 1)
        base_price = self.init_price - (self.init_price - self.reserve_price) * fraction
        noise = float(rng.normal(0, self.noise_std))
        noisy_price = base_price + noise
        return float(np.clip(noisy_price, self.reserve_price, self.init_price + 0.05))
