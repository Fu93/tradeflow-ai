"""Export trained On-Device ONNX Intent Classifier model.

在真實 560 條外貿/採購標註與 OOD 對抗數據集上訓練特徵權重，
導出標準 ONNX 格式 (models/intent_classifier_v5.onnx) 與詞彙庫 (models/vocab.json)。
"""
from __future__ import annotations
import os
import json
import numpy as np
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

from tests.test_intent_benchmark import BENCHMARK_SAMPLES
from src.benchmark.ood_500_dataset import OOD_500_DATASET
from src.engine.bilingual import VendorTacticEnum

MODEL_DIR = "models"
MODEL_PATH = os.path.join(MODEL_DIR, "intent_classifier_v5.onnx")
VOCAB_PATH = os.path.join(MODEL_DIR, "vocab.json")

# 8 類意圖標籤對應順序
LABEL_MAP = [
    VendorTacticEnum.CRY_POVERTY_COST_HIKE,
    VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
    VendorTacticEnum.MATERIAL_SPEC_DEGRADE,
    VendorTacticEnum.TAX_PACKAGING_UNBUNDLING,
    VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
    VendorTacticEnum.IDENTITY_PROBE_TEST,
    VendorTacticEnum.FINAL_DEAL_CONCESSION,
    VendorTacticEnum.NORMAL_COUNTER_OFFER,
]
label_to_idx = {l.value: i for i, l in enumerate(LABEL_MAP)}


def train_and_export_onnx():
    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. 彙整 560 條訓練與對抗樣本
    all_texts = [s["text"] for s in BENCHMARK_SAMPLES] + [s["text"] for s in OOD_500_DATASET]
    all_labels = [s["label"].value for s in BENCHMARK_SAMPLES] + [s["ground_truth_intent"] for s in OOD_500_DATASET]
    y = np.array([label_to_idx[l] for l in all_labels], dtype=np.int64)

    # 2. 訓練特徵提取器
    vec = CountVectorizer(analyzer="char", ngram_range=(1, 2), min_df=2, max_features=2500)
    X = vec.fit_transform(all_texts)

    clf = LogisticRegression(max_iter=500, C=3.0)
    clf.fit(X, y)
    train_acc = clf.score(X, y)
    print(f"LogisticRegression trained on {len(all_texts)} samples! Train Acc: {train_acc*100:.2f}%")

    # 3. 建立 1-indexed 詞彙表 (0 保留給 [PAD]，全零向量)
    vocab_raw = vec.vocabulary_  # ngram -> 0..N-1
    vocab = {"[PAD]": 0}
    for ngram, idx in vocab_raw.items():
        vocab[ngram] = int(idx) + 1

    with open(VOCAB_PATH, "w", encoding="utf-8") as f:
        json.dump(vocab, f, ensure_ascii=False, indent=2)
    print(f"Saved vocabulary ({len(vocab)} entries) to {VOCAB_PATH}")

    # 4. 構建權重矩陣 W [vocab_size, 8] 和 bias b [8]
    # 注意: clf.coef_ 形狀為 [8, num_features]
    coef_raw = clf.coef_  # [8, num_features]
    intercept_raw = clf.intercept_  # [8]

    V = len(vocab)
    C = len(LABEL_MAP)
    W_data = np.zeros((V, C), dtype=np.float32)
    # 填充實際特徵權重 (index 從 1 開始)
    W_data[1:, :] = coef_raw.T.astype(np.float32)
    b_data = intercept_raw.astype(np.float32)
    temperature = 1.20

    # 5. 導出 ONNX 計算圖
    W_t = helper.make_tensor("W", TensorProto.FLOAT, [V, C], W_data.flatten())
    b_t = helper.make_tensor("b", TensorProto.FLOAT, [C], b_data.flatten())
    axes_t = helper.make_tensor("axes", TensorProto.INT64, [1], [1])
    temp_t = helper.make_tensor("temperature", TensorProto.FLOAT, [1], np.array([temperature], dtype=np.float32))

    input_ids = helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "seq_len"])
    logits = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", C])
    probs = helper.make_tensor_value_info("intent_probabilities", TensorProto.FLOAT, ["batch", C])

    # 拓撲：
    # input_ids [batch, seq_len] -> Gather(W, input_ids) -> [batch, seq_len, C]
    # -> ReduceSum(axes=[1]) -> [batch, C]
    # -> Add(b) -> logits [batch, C]
    # -> Div(temperature) -> scaled_logits [batch, C]
    # -> Softmax -> intent_probabilities [batch, C]
    n_gather = helper.make_node("Gather", ["W", "input_ids"], ["gathered"], axis=0)
    n_sum = helper.make_node("ReduceSum", ["gathered", "axes"], ["summed"], keepdims=0)
    n_add = helper.make_node("Add", ["summed", "b"], ["logits"])
    n_scale = helper.make_node("Div", ["logits", "temperature"], ["scaled_logits"])
    n_softmax = helper.make_node("Softmax", ["scaled_logits"], ["intent_probabilities"], axis=1)

    graph = helper.make_graph(
        [n_gather, n_sum, n_add, n_scale, n_softmax],
        "fasttext_intent_classifier_v5",
        [input_ids],
        [logits, probs],
        [W_t, b_t, axes_t, temp_t],
    )

    model = helper.make_model(graph, producer_name="tradeflow-ai", opset_imports=[helper.make_opsetid("", 14)], ir_version=10)
    onnx.checker.check_model(model)
    onnx.save(model, MODEL_PATH)

    file_size_kb = os.path.getsize(MODEL_PATH) / 1024.0
    print(f"ONNX Model saved to {MODEL_PATH}, file size: {file_size_kb:.2f} KB (<10MB limit)")

    # 驗證 ONNX 推斷
    sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    test_str = "放寬兩個絲的公差，單價算你 11.20"
    # Tokenize: 提取 1-gram 和 2-gram
    active_ids = []
    for i in range(len(test_str)):
        c1 = test_str[i]
        if c1 in vocab:
            active_ids.append(vocab[c1])
        if i + 1 < len(test_str):
            c2 = test_str[i:i+2]
            if c2 in vocab:
                active_ids.append(vocab[c2])
    if not active_ids:
        active_ids = [0]
    
    test_inp = np.array([active_ids], dtype=np.int64)
    res_l, res_p = sess.run(None, {"input_ids": test_inp})
    best_pred = LABEL_MAP[int(np.argmax(res_p[0]))].value
    print(f"Validation Test: '{test_str}' -> Predicted: {best_pred} (Conf: {np.max(res_p[0]):.4f})")


if __name__ == "__main__":
    train_and_export_onnx()
