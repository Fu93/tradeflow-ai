"""On-Device ONNX Intent Classifier (OnDeviceIntentClassifier).

基於 Character 1-gram / 2-gram N-gram 特徵聚合與 Softmax 分類，
純本地離線加載 models/intent_classifier_v5.onnx，單次推斷延遲 <0.05ms，體積 <100KB。
"""
from __future__ import annotations
import os
import json
import logging
from typing import Optional, Tuple, Dict, Any, List
import numpy as np

logger = logging.getLogger("tradeflow.onnx_classifier")

# [PERF 2026-10-23] ``onnxruntime`` is imported on first use, not at module
# import. Its C extension costs ~168 ms of import time (723 ms cold) and it was
# pulled in unconditionally by ``engine.bilingual`` → ``desk.parse`` → the app
# entry point, so every process — including short-lived CLI runs and test
# workers that never classify anything — paid for it. The session itself is only
# 7 ms to create once the library is loaded.
ort = None
HAS_ORT: bool | None = None  # None = not probed yet


def _load_ort() -> bool:
    """Import onnxruntime once. Returns False when it is genuinely unavailable."""
    global ort, HAS_ORT
    if HAS_ORT is None:
        try:
            import onnxruntime as _ort

            ort = _ort
            HAS_ORT = True
        except ImportError:
            ort = None
            HAS_ORT = False
    return bool(HAS_ORT)


# 8 類意圖字串標籤
LABEL_STRINGS = [
    "CRY_POVERTY_COST_HIKE",
    "TIER_VOLUME_BLACKMAIL",
    "MATERIAL_SPEC_DEGRADE",
    "TAX_PACKAGING_UNBUNDLING",
    "OFFLINE_BYPASS_DIVERT",
    "IDENTITY_PROBE_TEST",
    "FINAL_DEAL_CONCESSION",
    "NORMAL_COUNTER_OFFER",
]


class OnDeviceIntentClassifier:
    """端側 ONNX 語意分類器包裝器。"""

    _instance: Optional[OnDeviceIntentClassifier] = None

    def __init__(
        self,
        model_path: Optional[str] = None,
        vocab_path: Optional[str] = None,
    ):
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.model_path = model_path or os.path.join(base_dir, "models", "intent_classifier_v5.onnx")
        self.vocab_path = vocab_path or os.path.join(base_dir, "models", "vocab.json")
        self.session: Optional[Any] = None
        self.vocab: Dict[str, int] = {}
        self._initialize()

    @classmethod
    def get_instance(cls) -> OnDeviceIntentClassifier:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _initialize(self):
        if not _load_ort():
            logger.warning("onnxruntime is not installed; on-device ONNX classification is disabled.")
            return

        if not os.path.exists(self.model_path) or not os.path.exists(self.vocab_path):
            logger.warning(f"ONNX model or vocab missing at {self.model_path}; falling back.")
            return

        try:
            with open(self.vocab_path, "r", encoding="utf-8") as f:
                self.vocab = json.load(f)

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])
            logger.info(f"Loaded ONNX Intent Classifier from {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to load ONNX session: {e}")
            self.session = None

    def tokenize(self, text: str, max_tokens: int = 100) -> np.ndarray:
        """提取字元 1-gram 與 2-gram 特徵 ID。"""
        if not text or not self.vocab:
            return np.zeros((1, 1), dtype=np.int64)

        active_ids: List[int] = []
        n = len(text)
        for i in range(n):
            c1 = text[i]
            if c1 in self.vocab:
                active_ids.append(self.vocab[c1])
            if i + 1 < n:
                c2 = text[i:i+2]
                if c2 in self.vocab:
                    active_ids.append(self.vocab[c2])
            if len(active_ids) >= max_tokens:
                break

        if not active_ids:
            active_ids = [0]

        return np.array([active_ids], dtype=np.int64)

    def predict(self, raw_text: str) -> Optional[Tuple[Any, float, Dict[str, float]]]:
        """
        執行端側語意分類推斷。
        返回: (預測意圖枚舉, 置信度概率, 全量類別機率分佈字典)
        """
        if not self.session or not raw_text or not raw_text.strip():
            return None

        try:
            input_ids = self.tokenize(raw_text)
            _, probs = self.session.run(None, {"input_ids": input_ids})
            probs_row = probs[0]
            best_idx = int(np.argmax(probs_row))
            confidence = float(probs_row[best_idx])
            best_label_str = LABEL_STRINGS[best_idx] if best_idx < len(LABEL_STRINGS) else "NORMAL_COUNTER_OFFER"

            # 延遲導入枚舉避免模組級循環依賴
            from src.engine.bilingual import VendorTacticEnum
            best_intent = getattr(VendorTacticEnum, best_label_str, VendorTacticEnum.NORMAL_COUNTER_OFFER)

            prob_dist = {
                LABEL_STRINGS[i]: float(probs_row[i])
                for i in range(min(len(LABEL_STRINGS), len(probs_row)))
            }
            return best_intent, confidence, prob_dist
        except Exception as e:
            logger.error(f"ONNX inference error: {e}")
            return None
