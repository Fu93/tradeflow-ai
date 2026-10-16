"""Benchmark Integrity Guards (test_benchmark_integrity.py).

針對 `DEEP_DEFECT_AUDIT.md` §4.1–§4.4 的評測誠信缺失，把「不得訓練即評測」從
文件承諾變成**機器強制**的不變量。本檔的四組斷言，每一組都對應一個先前會失敗的
真實缺陷：

1. `test_train_and_test_splits_are_disjoint`
   審計 §4.1：`scripts/export_onnx_intent_model.py` 曾以**整份** OOD-500 訓練
   ONNX 分類頭，而 `tests/test_ood_500_benchmark.py` 又以同一份評測它。
   → 斷言切分的模板層與字串層交集為 0，且與 in-distribution 集亦不相交。

2. `test_reported_accuracy_is_computed_on_the_held_out_split`
   審計 §4.1：頭條 92.00% 是訓練集分數（關掉 ONNX 後 46.60%）。
   → 斷言評測集身分是 TEST 切分，且訓練集分數**不得**低於 held-out 分數
      （若相等，代表沒有記憶成分或切分失效，兩種情況都要人看）。

3. `test_reproducible_split_matches_committed_artifacts`
   → 斷言落盤的 `data/ood_train.json` / `data/ood_test.json` 與重算結果一致，
      且 manifest 記錄的 overlap 為 0（防止手改切分檔）。

4. `test_onnx_head_training_corpus_excludes_held_out_texts`
   → 直接讀取**模型實際訓練語料**（`BENCHMARK_SAMPLES + OOD_TRAIN_DATASET`），
      斷言它與 held-out 測試集零交集 —— 這是污染回歸的真正守門測試。

並附帶 provenance 與「750 場」標題的守門斷言（審計 §4.2/§4.3）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter

import pytest

from src.benchmark.ood_500_dataset import (
    OOD_500_DATASET,
    OOD_TEST_DATASET,
    OOD_TRAIN_DATASET,
    SPLIT_SEED,
    TRAIN_PINNED_TEXTS,
    build_ood_split,
    template_key,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _texts(rows):
    return {r["text"] for r in rows}


# ---------------------------------------------------------------------------
# 1. Train / eval disjointness
# ---------------------------------------------------------------------------
def test_train_and_test_splits_are_disjoint():
    """模板層與字串層交集必須為 0（審計 §4.1 的核心回歸守門）。"""
    train_keys = {template_key(r["text"]) for r in OOD_TRAIN_DATASET}
    test_keys = {template_key(r["text"]) for r in OOD_TEST_DATASET}

    assert train_keys & test_keys == set(), (
        "Train/test template overlap detected: "
        f"{sorted(train_keys & test_keys)[:3]}"
    )
    assert _texts(OOD_TRAIN_DATASET) & _texts(OOD_TEST_DATASET) == set(), (
        "Exact-text overlap between train and test splits."
    )


def test_splits_partition_the_full_dataset():
    """兩側合起來必須恰好是完整 500 條，且每條只出現一次。"""
    train_ids = [r["id"] for r in OOD_TRAIN_DATASET]
    test_ids = [r["id"] for r in OOD_TEST_DATASET]
    full_ids = [r["id"] for r in OOD_500_DATASET]

    assert len(train_ids) + len(test_ids) == len(full_ids) == 500
    assert len(set(train_ids)) == len(train_ids), "Duplicate ids in train split"
    assert len(set(test_ids)) == len(test_ids), "Duplicate ids in test split"
    assert set(train_ids) | set(test_ids) == set(full_ids)
    assert set(train_ids) & set(test_ids) == set()


def test_split_is_reproducible_under_fixed_seed():
    """相同 seed 必須位元級重現；不同 seed 一般會產生不同切分。"""
    a_train, a_test = build_ood_split(OOD_500_DATASET, seed=SPLIT_SEED)
    b_train, b_test = build_ood_split(OOD_500_DATASET, seed=SPLIT_SEED)
    assert [r["id"] for r in a_train] == [r["id"] for r in b_train]
    assert [r["id"] for r in a_test] == [r["id"] for r in b_test]

    c_train, c_test = build_ood_split(OOD_500_DATASET, seed=SPLIT_SEED + 1)
    assert [r["id"] for r in c_test] != [r["id"] for r in a_test], (
        "Different seeds produced an identical test split; the algorithm is not seed-sensitive."
    )


def test_held_out_split_covers_every_intent():
    """held-out 切分必須覆蓋所有 7 個 ground-truth 意圖，否則部分類別無法評測。"""
    train_intents = {r["ground_truth_intent"] for r in OOD_TRAIN_DATASET}
    test_intents = {r["ground_truth_intent"] for r in OOD_TEST_DATASET}
    assert train_intents == test_intents, (
        f"Held-out split is missing intents: {sorted(train_intents - test_intents)}"
    )
    assert len(test_intents) == 7


def test_onnx_head_training_corpus_excludes_held_out_texts():
    """直接檢驗模型**實際訓練語料**與 held-out 評測集零交集。

    這是審計 §4.1 真正的污染回歸測試：只要有人把 held-out 樣本再加回訓練，
    此斷言立刻失敗。
    """
    from tests.test_intent_benchmark import BENCHMARK_SAMPLES

    training_corpus = [s["text"] for s in BENCHMARK_SAMPLES] + [
        r["text"] for r in OOD_TRAIN_DATASET
    ]
    held_out = _texts(OOD_TEST_DATASET)

    assert set(training_corpus) & held_out == set(), (
        "Training corpus overlaps the held-out evaluation set — train-on-test contamination."
    )
    train_template_keys = {template_key(t) for t in training_corpus}
    held_out_template_keys = {template_key(t) for t in held_out}
    assert train_template_keys & held_out_template_keys == set(), (
        "Training corpus shares a base template with the held-out set."
    )


def test_train_pin_covers_all_cross_set_text_collisions():
    """`TRAIN_PINNED_TEXTS` 必須完整涵蓋 in-distribution / OOD-500 之間的字串衝突。

    若 `tests/test_intent_benchmark.py` 新增了與 OOD 集完全相同的句子，而
    `TRAIN_PINNED_TEXTS` 未同步更新，切分就可能把該模板放進測試側而重新引入重疊。
    本測試把 pin 清單變成可驗證的契約。
    """
    from tests.test_intent_benchmark import BENCHMARK_SAMPLES

    ood_texts = _texts(OOD_500_DATASET)
    actual_collisions = {
        s["text"] for s in BENCHMARK_SAMPLES if s["text"] in ood_texts
    }
    assert actual_collisions == set(TRAIN_PINNED_TEXTS), (
        "TRAIN_PINNED_TEXTS is out of date. Actual in-distribution/OOD text collisions: "
        f"{sorted(actual_collisions)}; declared pins: {sorted(TRAIN_PINNED_TEXTS)}"
    )
    # 每個 pinned 樣本所屬模板必須整組落在訓練側。
    pinned_keys = {template_key(t) for t in TRAIN_PINNED_TEXTS}
    test_keys = {template_key(r["text"]) for r in OOD_TEST_DATASET}
    assert pinned_keys & test_keys == set()


# ---------------------------------------------------------------------------
# 2. Reported accuracy must be held-out
# ---------------------------------------------------------------------------
def test_reported_accuracy_is_computed_on_the_held_out_split():
    """宣稱的評測集必須是 held-out 切分，且訓練集分數必須**高於**它。

    若兩者相等，代表 (a) 切分未生效、或 (b) 任務過易而無法分辨記憶與泛化 ——
    任一情況都需人工介入，故此斷言失敗是**有意義的**。
    """
    from src.engine.bilingual import InboundIntentClassifier

    def _acc(rows, field):
        ok = 0
        for r in rows:
            res = InboundIntentClassifier.classify(r["text"], enable_llm_fallback=False)
            if res.primary_intent.value == r[field]:
                ok += 1
        return ok / len(rows)

    held_out_acc = _acc(OOD_TEST_DATASET, "ground_truth_intent")
    train_acc = _acc(OOD_TRAIN_DATASET, "ground_truth_intent")

    assert held_out_acc < train_acc, (
        f"held-out accuracy ({held_out_acc:.2%}) is not below the train-split accuracy "
        f"({train_acc:.2%}); either the split is not effective or the evaluation is not held-out."
    )
    # held-out 分數必須是「真的有用」的下界，而非隨機猜測。
    assert held_out_acc > 0.30, f"held-out accuracy collapsed to {held_out_acc:.2%}"


def test_documented_held_out_accuracy_matches_measurement():
    """`tests/test_ood_500_benchmark.MEASURED_HELD_OUT_ACCURACY` 必須對得上實測值。"""
    from src.engine.bilingual import InboundIntentClassifier
    from tests.test_ood_500_benchmark import (
        HELD_OUT_ACCURACY_FLOOR,
        MEASURED_HELD_OUT_ACCURACY,
    )

    ok = 0
    for r in OOD_TEST_DATASET:
        res = InboundIntentClassifier.classify(r["text"], enable_llm_fallback=False)
        if res.primary_intent.value == r["ground_truth_intent"]:
            ok += 1
    measured = ok / len(OOD_TEST_DATASET)

    assert abs(measured - MEASURED_HELD_OUT_ACCURACY) < 0.005, (
        f"Documented held-out accuracy {MEASURED_HELD_OUT_ACCURACY:.4f} no longer matches "
        f"the measurement {measured:.4f}; update the constant and the docs that cite it."
    )
    assert HELD_OUT_ACCURACY_FLOOR < measured, (
        "The regression floor must sit strictly below the measured held-out accuracy."
    )


def test_no_document_still_claims_the_contaminated_92_percent_as_generalisation():
    """審計 §4.1：對外文件不得再把 92.00% 當門面數字呈現。

    允許兩種合法寫法：(a) 明確標為訓練集分數／歷史污染值，(b) 已被 held-out 數字取代。
    禁止的是**無標註的頭條引用**。
    """
    doc_files = [
        "README.md",
        "TARGET_ARCHITECTURE_AND_ROADMAP.md",
        "PITCH.md",
        "SUBMISSION_DRAFT.md",
    ]
    offenders = []
    for rel in doc_files:
        path = os.path.join(_REPO_ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for lineno, line in enumerate(lines, start=1):
            if not re.search(r"92\.00|92\.0%", line):
                continue
            # 該行必須同時出現「訓練集」或「held-out」之類的限定語，才算誠實標註。
            context = "".join(lines[max(0, lineno - 3):lineno + 3])
            if not re.search(
                r"訓練集|train[- ]?set|held[- ]?out|污染|contaminat|記憶|memoris|先前|舊版|prior",
                context,
            ):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:110]}")

    assert not offenders, (
        "Unqualified 92.00% claim(s) still present:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 3. Committed split artifacts
# ---------------------------------------------------------------------------
def test_reproducible_split_matches_committed_artifacts():
    """落盤切分檔與 manifest 必須與演算法輸出逐條一致。"""
    train_path = os.path.join(_REPO_ROOT, "data", "ood_train.json")
    test_path = os.path.join(_REPO_ROOT, "data", "ood_test.json")
    manifest_path = os.path.join(_REPO_ROOT, "data", "ood_split_manifest.json")

    for path in (train_path, test_path, manifest_path):
        assert os.path.exists(path), (
            f"Missing split artifact {path}; regenerate with "
            "`python -c \"from src.benchmark.ood_500_dataset import materialize_split; materialize_split()\"`."
        )

    with open(train_path, "r", encoding="utf-8") as f:
        on_disk_train = json.load(f)
    with open(test_path, "r", encoding="utf-8") as f:
        on_disk_test = json.load(f)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert [r["id"] for r in on_disk_train] == [r["id"] for r in OOD_TRAIN_DATASET]
    assert [r["id"] for r in on_disk_test] == [r["id"] for r in OOD_TEST_DATASET]
    assert [r["text"] for r in on_disk_test] == [r["text"] for r in OOD_TEST_DATASET]

    assert manifest["seed"] == SPLIT_SEED
    assert manifest["template_overlap"] == 0
    assert manifest["text_overlap"] == 0
    assert manifest["train_count"] == len(OOD_TRAIN_DATASET)
    assert manifest["test_count"] == len(OOD_TEST_DATASET)

    # 模組內部的 artifact 一致性檢查亦須通過。
    from src.benchmark.ood_500_dataset import _artifact_ok

    assert _artifact_ok(OOD_TRAIN_DATASET, OOD_TEST_DATASET), (
        "Committed split artifacts do not match the recomputed split (possible hand-edit)."
    )


# ---------------------------------------------------------------------------
# 4. Other provenance guards (audit §4.2 / §4.3)
# ---------------------------------------------------------------------------
def test_dataset_is_template_recycled_not_500_distinct_samples():
    """審計 §4.2：500 條不得被描述為 500 條互異自然語言樣本。"""
    masked = Counter(re.sub(r"[0-9]+(\.[0-9]+)?", "N", e["text"]) for e in OOD_500_DATASET)
    groups = Counter(template_key(e["text"]) for e in OOD_500_DATASET)

    assert len(masked) == 132, f"Masked-distinct sentence count changed: {len(masked)}"
    assert len(groups) == 66, f"Base template count changed: {len(groups)}"
    assert max(groups.values()) == 13, f"Max template repetition changed: {max(groups.values())}"
    # 500 個 id，但真正的句式多樣性遠小於 500。
    assert len(groups) < len(OOD_500_DATASET) / 5


@pytest.mark.parametrize("rel", ["README.md", "PITCH.md", "SUBMISSION_DRAFT.md"])
def test_docs_do_not_claim_a_blind_or_distinct_dataset(rel):
    """對外文件不得把本集宣稱為「盲測集」或「500 條獨特語料」。"""
    path = os.path.join(_REPO_ROOT, rel)
    if not os.path.exists(path):
        pytest.skip(f"{rel} not present")
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    forbidden = [
        r"500 條跨領域盲測",
        r"500 條獨特語料",
        r"500\s*distinct natural[- ]language samples",
        r"blind held[- ]out set",
    ]
    hits = [pat for pat in forbidden if re.search(pat, text, re.IGNORECASE)]
    assert not hits, f"{rel} still claims: {hits}"


def test_arena_header_is_not_hardcoded():
    """審計 §4.3：`scripts/run_arena_benchmark.py` 曾寫死
    'Matches per Archetype: 50 (Total 750 match simulations)'，與實際參數無關。

    本測試以 AST 檢查該腳本的**執行期字串**不得再出現寫死的 header，且 header 必須由
    實際傳入的參數插值產生（即為 f-string 且含 format placeholder）。
    docstring／註解中對舊字串的引用不算違規（那正是「更正紀錄」）。
    """
    import ast

    path = os.path.join(_REPO_ROOT, "scripts", "run_arena_benchmark.py")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    # 收集模組層與函式層的 docstring 節點，稍後排除。
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if re.search(r"Matches per Archetype:\s*\d+\s*\(Total\s+\d+", node.value):
            offenders.append(node.value)

    assert not offenders, (
        "Hardcoded arena header string(s) still present in executable code: "
        f"{offenders}; the header must interpolate the real argument."
    )

    # 正向要求：header 必須由 f-string（JoinedStr）構建，且含 "Matches per Archetype"。
    joined = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        and any(
            isinstance(v, ast.Constant)
            and isinstance(v.value, str)
            and "Matches per Archetype" in v.value
            for v in node.values
        )
    ]
    assert joined, (
        "No interpolated f-string header containing 'Matches per Archetype' found in "
        "scripts/run_arena_benchmark.py; the header must be built from the actual argument."
    )


def test_generator_does_not_clobber_the_split_aware_loader():
    """`scripts/generate_ood_500.py` 曾以 `f.write(...)` 覆寫
    `src/benchmark/ood_500_dataset.py`，把它降級成 8 行純載入器。

    那會**靜默刪除**本任務加入的 train/test 切分邏輯，使訓練即評測污染復辟。
    本測試以 AST 斷言該腳本不得再對 ood_500_dataset.py 進行寫入。
    """
    import ast

    path = os.path.join(_REPO_ROOT, "scripts", "generate_ood_500.py")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    # 不得出現 `open("src/benchmark/ood_500_dataset.py", "w" ...)` 之類的寫入。
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            args = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if args and "ood_500_dataset.py" in args[0].value:
                if len(node.args) >= 2:
                    mode = node.args[1]
                    if isinstance(mode, ast.Constant) and "w" in str(mode.value):
                        offenders.append(ast.unparse(node)[:120])
    assert not offenders, (
        "scripts/generate_ood_500.py still writes to src/benchmark/ood_500_dataset.py, "
        f"which would destroy the split logic: {offenders}"
    )

    # 正向要求：必須存在讓切分檔落盤的路徑。
    src = ast.unparse(tree)
    assert "materialize_split" in src, (
        "scripts/generate_ood_500.py no longer regenerates the split artifacts after "
        "recreating the dataset; add a materialize_split() call so the held-out split "
        "stays in sync with the data."
    )


def test_self_play_benchmark_artifact_header_matches_code_default_label():
    """審計 §4.3：`results/self_play_benchmark.md` 的 header 必須反映真實參數，
    且 README 的連結標題不得宣稱與 artefact 不符的場次數。"""
    artifact = os.path.join(_REPO_ROOT, "results", "self_play_benchmark.md")
    if os.path.exists(artifact):
        with open(artifact, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r"Matches per Archetype:\s*(\d+)", content)
        assert match, "self_play_benchmark.md lost its 'Matches per Archetype' header"
        per_arch = int(match.group(1))
        # 5 個對手原型（arena.py 的固定設計）。
        assert per_arch >= 1
        # 任何宣稱的「總場次」都必須等於 per_arch * 5。
        total_claims = [int(m) for m in re.findall(r"Total (\d+) match", content)]
        for claim in total_claims:
            assert claim == per_arch * 5, (
                f"Artefact header claims {claim} total matches but {per_arch} x 5 = {per_arch * 5}."
            )
