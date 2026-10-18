"""Export trained On-Device ONNX Intent Classifier model.

在真實外貿/採購標註（`tests/test_intent_benchmark.BENCHMARK_SAMPLES`，60 條 in-distribution）
與 **OOD 訓練切分**（`src.benchmark.ood_500_dataset.OOD_TRAIN_DATASET`，305 條）上訓練
特徵權重，導出標準 ONNX 格式 (models/intent_classifier_v5.onnx) 與詞彙庫 (models/vocab.json)。

[訓練/評測分離 / Why this file changed]
--------------------------------------------------------------
舊版第 42-43 行把 **整份** `data/benchmark_ood_500.json`（500 條）併入訓練集，
而 `tests/test_ood_500_benchmark.py` 又拿**同一份** 500 條評測它 —— 於是對外宣稱的
「OOD-500 準確率 92.00%」其實是**訓練集分數**（關掉 ONNX 後同一份資料掉到 46.60%），
其中 ONNX 階段獨力決定了 369/500 條，洩漏就發生在那裡。

現行做法（本檔案與 `src/benchmark/ood_500_dataset.py`）：
  * 以**基礎模板**為最小單位切出 `data/ood_train.json` / `data/ood_test.json`，
    模板層與字串層 overlap 皆為 0（見 manifest `data/ood_split_manifest.json`）；
  * 分類頭只以 `BENCHMARK_SAMPLES + OOD_TRAIN_DATASET` 訓練；
  * 對外數字一律取自 `OOD_TEST_DATASET`（held-out），並由本腳本印出**端到端管線**
    在該集上的實測準確率，供 README / TARGET_ARCHITECTURE 引用。

備註（serving 對齊）：`src/engine/onnx_classifier.py::tokenize` 於推斷時以
`max_tokens=100` 截斷字元 1/2-gram，故長句只取前綴；訓練端 CountVectorizer 見全文。
此為既存的 train/serve 落差，本腳本不改動 `src/engine/*`（非本任務寫入範圍），
但以**真實管線**（`InboundIntentClassifier.classify`）量測 held-out 數字，故對外數字
反映的正是實際出貨行為。
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
from src.benchmark.ood_500_dataset import (
    OOD_TRAIN_DATASET,
    OOD_TEST_DATASET,
    SPLIT_SEED,
    SPLIT_METHOD,
    template_key,
)
from src.config.heuristics import ONNX_LOGREG_C, ONNX_TEMPERATURE
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


def _onnx_head_predict(session, vocab, text: str, max_tokens: int = 100) -> str:
    """以導出後的 ONNX 圖與 vocab 做一次單句推斷（鏡像 onnx_classifier.tokenize）。"""
    active_ids = []
    n = len(text)
    for i in range(n):
        c1 = text[i]
        if c1 in vocab:
            active_ids.append(vocab[c1])
        if i + 1 < n:
            c2 = text[i:i + 2]
            if c2 in vocab:
                active_ids.append(vocab[c2])
        if len(active_ids) >= max_tokens:
            break
    if not active_ids:
        active_ids = [0]
    _, probs = session.run(None, {"input_ids": np.array([active_ids], dtype=np.int64)})
    return LABEL_MAP[int(np.argmax(probs[0]))].value


def evaluate_held_out() -> dict:
    """以**真實端到端管線**量測 held-out（OOD_TEST_DATASET）與 in-distribution 準確率。"""
    from src.engine.bilingual import InboundIntentClassifier

    def _acc(dataset, label_field):
        correct = 0
        for item in dataset:
            res = InboundIntentClassifier.classify(item["text"], enable_llm_fallback=False)
            if res.primary_intent.value == item[label_field]:
                correct += 1
        return correct, len(dataset)

    # 端到端（正則階梯 + ONNX）在 held-out 上的準確率 —— 這是唯一可對外引用的 OOD 數字。
    ood_correct, ood_total = _acc(OOD_TEST_DATASET, "ground_truth_intent")
    # 同一份 held-out 只用 ONNX 分類頭（不經正則階梯）的準確率。
    sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    with open(VOCAB_PATH, "r", encoding="utf-8") as f:
        vocab = json.load(f)
    head_correct = 0
    for item in OOD_TEST_DATASET:
        if _onnx_head_predict(sess, vocab, item["text"]) == item["ground_truth_intent"]:
            head_correct += 1
    # in-distribution 回歸集（**訓練集分數**，僅供對照，不可宣稱泛化）。
    ind_correct = 0
    for s in BENCHMARK_SAMPLES:
        res = InboundIntentClassifier.classify(s["text"], enable_llm_fallback=False)
        if res.primary_intent == s["label"]:
            ind_correct += 1

    return {
        "held_out_ood_accuracy": ood_correct / ood_total,
        "held_out_ood_correct": ood_correct,
        "held_out_ood_total": ood_total,
        "held_out_onnx_head_only_accuracy": head_correct / ood_total,
        "in_distribution_train_set_accuracy": ind_correct / len(BENCHMARK_SAMPLES),
    }


def train_and_export_onnx():
    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. 彙整訓練樣本：in-distribution 標註 (60) + **OOD 訓練切分** (305)。
    #    絕不使用 OOD_TEST_DATASET —— 那是評測集。
    all_texts = [s["text"] for s in BENCHMARK_SAMPLES] + [s["text"] for s in OOD_TRAIN_DATASET]
    all_labels = [s["label"].value for s in BENCHMARK_SAMPLES] + [
        s["ground_truth_intent"] for s in OOD_TRAIN_DATASET
    ]
    y = np.array([label_to_idx[l] for l in all_labels], dtype=np.int64)

    # 訓練集與評測集的模板/字串層隔離斷言（污染的機器可驗證防線）。
    train_keys = {template_key(t) for t in all_texts}
    test_keys = {template_key(s["text"]) for s in OOD_TEST_DATASET}
    assert not (train_keys & test_keys), (
        f"Train/test template overlap detected: {sorted(train_keys & test_keys)[:3]}"
    )
    train_texts = set(all_texts)
    assert not (train_texts & {s["text"] for s in OOD_TEST_DATASET}), (
        "Exact-text overlap between training corpus and held-out test split."
    )

    print(
        f"[split] method={SPLIT_METHOD} seed={SPLIT_SEED} | "
        f"train={len(all_texts)} ({len(BENCHMARK_SAMPLES)} in-dist + "
        f"{len(OOD_TRAIN_DATASET)} OOD-train) | held-out test={len(OOD_TEST_DATASET)} | "
        f"template overlap={len(train_keys & test_keys)}"
    )

    # 2. 訓練特徵提取器
    vec = CountVectorizer(analyzer="char", ngram_range=(1, 2), min_df=2, max_features=2500)
    X = vec.fit_transform(all_texts)

    clf = LogisticRegression(max_iter=500, C=ONNX_LOGREG_C)
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
    temperature = ONNX_TEMPERATURE

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

    file_size_kb = os.path.getsize(MODEL_PATH) / 1024
    print(f"ONNX Model saved to {MODEL_PATH}, file size: {file_size_kb:.2f} KB (<10MB limit)")

    # 6. 驗證 ONNX 推斷（單句煙霧測試）
    sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    test_str = "放寬兩個絲的公差，單價算你 11.20"
    best_pred = _onnx_head_predict(sess, vocab, test_str)
    print(f"Validation Test: '{test_str}' -> Predicted: {best_pred}")

    # 7. 對外數字：**held-out** 實測。此區塊的輸出即為 README / TARGET 可引用的來源。
    metrics = evaluate_held_out()
    print(
        f"[held-out] OOD-TEST acc = {metrics['held_out_ood_accuracy']*100:.2f}% "
        f"({metrics['held_out_ood_correct']}/{metrics['held_out_ood_total']}, disjoint from training)"
    )
    print(
        f"[held-out] ONNX head only (no regex ladder) = "
        f"{metrics['held_out_onnx_head_only_accuracy']*100:.2f}%"
    )
    print(
        f"[train-set] in-distribution regression set acc = "
        f"{metrics['in_distribution_train_set_accuracy']*100:.2f}% (train-set score; NOT a generalisation claim)"
    )

    if os.path.exists("models/onnx_export_metrics.json"):
        os.remove("models/onnx_export_metrics.json")
    with open("models/onnx_export_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Saved held-out metrics to models/onnx_export_metrics.json")


if __name__ == "__main__":
    train_and_export_onnx()
