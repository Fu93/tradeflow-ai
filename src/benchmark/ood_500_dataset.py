"""TradeFlow 500-Item Synthetic Adversarial OOD Benchmark Dataset + REPRODUCIBLE SPLIT.

[資料集性質揭露 / Dataset Provenance Disclosure]
--------------------------------------------------------------
`data/benchmark_ood_500.json` 是由 `scripts/generate_ood_500.py` **以模板 + 前綴/後綴
填充程式化生成**，並非人工撰寫的 held-out 盲測集：
  - 500 條由 **66 個基礎模板**回收而成；最常見模板重複 13 次。
  - 遮蔽數字後僅 **132 條互異句子**。
  - 因此它是 **synthetic adversarial coverage set（合成對抗覆蓋集）**，價值在於
    覆蓋廣度與延遲量測，**不等同**於獨立泛化能力的證明。

[訓練/評測分離 / Train-Eval Disjointness]
--------------------------------------------------------------
本模組額外提供一個**可重現的模板級切分**，用以修復原本的「訓練即評測」污染：

  * `OOD_TRAIN_DATASET` — 供 `scripts/export_onnx_intent_model.py` 訓練 ONNX 分類頭。
  * `OOD_TEST_DATASET`  — 供 `tests/test_ood_500_benchmark.py` 評測 **held-out** 準確率。

切分演算法（`build_ood_split`）以 **基礎模板為最小單位** 隨機分配，因此
**測試集與訓練集在模板層與字串層皆完全不相交（overlap = 0）**：同一模板的所有
前綴/後綴變體只會整體落在一側，不會出現「同句不同包裝」跨集洩漏。
固定 `SPLIT_SEED = 42`，任何人重跑都會得到位元相同的切分。

先前 README 引用的 92.00% 是 **ONNX 分類頭在同一份 OOD-500 上訓練後再評同一份** 的
訓練集分數；現行對外數字一律取自 `OOD_TEST_DATASET`（held-out）。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_DATA_DIR = os.path.join(_REPO_ROOT, "data")

_FULL_PATH = os.path.join(_DATA_DIR, "benchmark_ood_500.json")
_TRAIN_PATH = os.path.join(_DATA_DIR, "ood_train.json")
_TEST_PATH = os.path.join(_DATA_DIR, "ood_test.json")
_MANIFEST_PATH = os.path.join(_DATA_DIR, "ood_split_manifest.json")


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 完整資料集（向後相容：既有呼叫端 OOD_500_DATASET 不受影響）
# ---------------------------------------------------------------------------
OOD_500_DATASET: List[Dict[str, Any]] = _load_json(_FULL_PATH)


# ---------------------------------------------------------------------------
# 切分演算法
# ---------------------------------------------------------------------------
SPLIT_SEED: int = 42
SPLIT_TEST_RATIO: float = 0.30
SPLIT_METHOD: str = "template-group-stratified-by-intent-v1"

# `scripts/generate_ood_500.py` 施展的上下文填充，切分前必須先剝除，
# 否則「同模板不同包裝」會被誤認為不同模板。
_GENERATOR_PREFIXES: Tuple[str, ...] = (
    "【工廠反饋】",
    "商務條款協議確認：",
    "【渠道通訊備註】",
    "【商務應答記錄】",
)
_GENERATOR_SUFFIX_RE = re.compile(
    r"（(?:批次號 #\d+|備註條款：需採購方蓋章|對話記錄存檔|回覆日期 [\d\-]+)）"
)
_DIGIT_RE = re.compile(r"[0-9]+(\.[0-9]+)?")

# [train-pin] 與 `tests/test_intent_benchmark.BENCHMARK_SAMPLES` 有**完全相同字串**
# 的樣本所屬模板，必須整組留在訓練側 —— 因為 ONNX 分類頭同時以
# `BENCHMARK_SAMPLES` + `OOD_TRAIN_DATASET` 訓練，若該模板落入測試側，測試集就會
# 與訓練集字串重疊。
# `tests/test_benchmark_integrity.py` 會重算實際交集並斷言其等於本常數，
# 一旦 `tests/test_intent_benchmark.py` 新增/修改樣本造成新交集，該測試即失敗，
# 迫使重建切分。
TRAIN_PINNED_TEXTS: Tuple[str, ...] = (
    "行吧服了你了，就按你說的 12.50，發合同過來吧。",
)


def template_key(text: str) -> str:
    """還原生成器包裝後、再遮蔽數字，得到「基礎模板」識別鍵。

    同一模板的所有前綴/後綴變體，以及數字變體，會得到相同鍵。
    """
    t = text
    for prefix in _GENERATOR_PREFIXES:
        if t.startswith(prefix):
            t = t[len(prefix):]
    t = _GENERATOR_SUFFIX_RE.sub("", t)
    return _DIGIT_RE.sub("N", t)


def build_ood_split(
    dataset: Sequence[Dict[str, Any]],
    seed: int = SPLIT_SEED,
    test_ratio: float = SPLIT_TEST_RATIO,
    pinned_train_texts: Iterable[str] = TRAIN_PINNED_TEXTS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """以基礎模板為單位、按 ground-truth 意圖分層，回傳 (train, test)。

    保證：
      1. 模板層不相交 —— 不存在任何 template_key 同時出現在兩側；
      2. 字串層不相交 —— 因此不存在任何完全相同 text 跨側；
      3. `pinned_train_texts` 所屬模板整組留在 train，確保與
         `tests/test_intent_benchmark.BENCHMARK_SAMPLES` 無交集；
      4. 對每個意圖，只要該意圖有 >= 2 個模板，測試側就至少有 1 個模板；
      5. 完全決定性 —— 相同 seed 與輸入必得相同切分。
    """
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in dataset:
        groups[template_key(item["text"])].append(item)
    canonical = {k: sorted(v, key=lambda x: x["id"]) for k, v in groups.items()}

    pinned = set(pinned_train_texts)
    rng = random.Random(seed)

    by_intent: Dict[str, List[str]] = defaultdict(list)
    for key, items in canonical.items():
        by_intent[items[0]["ground_truth_intent"]].append(key)

    train: List[Dict[str, Any]] = []
    test: List[Dict[str, Any]] = []

    for intent in sorted(by_intent):
        keys = sorted(by_intent[intent])  # 決定性基準序（shuffle 前）
        free = [k for k in keys if not any(i["text"] in pinned for i in canonical[k])]
        rng.shuffle(free)

        total = sum(len(canonical[k]) for k in keys)
        test_target = total * test_ratio
        must_take_one = len(keys) >= 2  # 讓測試側保有該意圖的模板

        chosen: List[str] = []
        acc = 0
        for key in free:
            if must_take_one and not chosen:
                chosen.append(key)
                acc += len(canonical[key])
                continue
            if acc < test_target:
                chosen.append(key)
                acc += len(canonical[key])

        chosen_set = set(chosen)
        for key in keys:
            (test if key in chosen_set else train).extend(canonical[key])

    by_id = lambda x: x["id"]  # noqa: E731
    return sorted(train, key=by_id), sorted(test, key=by_id)


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _artifact_ok(train: List[Dict[str, Any]], test: List[Dict[str, Any]]) -> bool:
    """確認落盤的切分檔與重算結果一致（防止落盤檔被手改而不知）。"""
    try:
        on_disk_train = _load_json(_TRAIN_PATH)
        on_disk_test = _load_json(_TEST_PATH)
    except (OSError, json.JSONDecodeError):
        return False
    if len(on_disk_train) != len(train) or len(on_disk_test) != len(test):
        return False
    if [i["id"] for i in on_disk_train] != [i["id"] for i in train]:
        return False
    if [i["id"] for i in on_disk_test] != [i["id"] for i in test]:
        return False
    # 內容層亦需相同（防止同 id 不同文本被偷換）
    if [i["text"] for i in on_disk_train] != [i["text"] for i in train]:
        return False
    return [i["text"] for i in on_disk_test] == [i["text"] for i in test]


def materialize_split(seed: int = SPLIT_SEED, test_ratio: float = SPLIT_TEST_RATIO) -> Dict[str, Any]:
    """由完整資料集重算切分、寫入 `data/ood_train.json` / `data/ood_test.json` / manifest。

    回傳 manifest dict。可重複執行（idempotent）。
    """
    train, test = build_ood_split(OOD_500_DATASET, seed=seed, test_ratio=test_ratio)

    os.makedirs(_DATA_DIR, exist_ok=True)
    for path, rows in ((_TRAIN_PATH, train), (_TEST_PATH, test)):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
            f.write("\n")

    train_keys = {template_key(i["text"]) for i in train}
    test_keys = {template_key(i["text"]) for i in test}
    manifest: Dict[str, Any] = {
        "method": SPLIT_METHOD,
        "seed": seed,
        "test_ratio": test_ratio,
        "source": "data/benchmark_ood_500.json",
        "source_sha256": _sha256_file(_FULL_PATH),
        "train_count": len(train),
        "test_count": len(test),
        "train_template_count": len(train_keys),
        "test_template_count": len(test_keys),
        "template_overlap": len(train_keys & test_keys),
        "text_overlap": len({i["text"] for i in train} & {i["text"] for i in test}),
        "test_intent_counts": _counts(test, "ground_truth_intent"),
        "test_dimension_counts": _counts(test, "dimension"),
        "train_intent_counts": _counts(train, "ground_truth_intent"),
        "train_pinned_texts": list(TRAIN_PINNED_TEXTS),
        "train_sha256": _sha256_file(_TRAIN_PATH),
        "test_sha256": _sha256_file(_TEST_PATH),
    }
    with open(_MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return manifest


def _counts(rows: Sequence[Dict[str, Any]], field: str) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for r in rows:
        out[r[field]] += 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# 對外曝露：訓練集 / 評測集
# ---------------------------------------------------------------------------
def _resolve_split() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """重算切分（決定性）。

    刻意**不**依賴落盤的 `data/ood_*.json`，讓匯入本模組的結果永遠等於演算法輸出；
    落盤檔只是這份輸出的可稽核快照，由 `tests/test_benchmark_integrity.py`
    比對兩者是否一致。如此可防止「切分檔被手改」而模組仍照舊運作。
    """
    return build_ood_split(OOD_500_DATASET)


_resolved_train, _resolved_test = _resolve_split()
OOD_TRAIN_DATASET: List[Dict[str, Any]] = _resolved_train
OOD_TEST_DATASET: List[Dict[str, Any]] = _resolved_test

# 完整 500 條（供覆蓋廣度/延遲量測；**不得**作為 held-out 準確率來源）
OOD_EVAL_DATASET = OOD_TEST_DATASET

__all__ = [
    "OOD_500_DATASET",
    "OOD_TRAIN_DATASET",
    "OOD_TEST_DATASET",
    "OOD_EVAL_DATASET",
    "SPLIT_SEED",
    "SPLIT_TEST_RATIO",
    "SPLIT_METHOD",
    "TRAIN_PINNED_TEXTS",
    "template_key",
    "build_ood_split",
    "materialize_split",
]
