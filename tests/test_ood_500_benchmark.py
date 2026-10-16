"""Held-Out Synthetic Adversarial Benchmark (test_ood_500_benchmark.py).

[IMPORTANT] 評測效力揭露 (Evaluation Validity Disclosure)
==================================================================
本檔評測的是 `src/benchmark/ood_500_dataset.OOD_TEST_DATASET` —— 由
`data/benchmark_ood_500.json`（500 條）切出的 **held-out 測試切分（195 條）**，
其模板與字串**與 ONNX 訓練集完全不相交**（見 `tests/test_benchmark_integrity.py`）。

* **先前對外引用的 92.00% 是訓練集分數**：舊版 `scripts/export_onnx_intent_model.py`
  把整份 OOD-500 併入訓練，再用同一份評測；以當前（held-out）切分重訓後，
  **訓練切分上實測 92.79%**，但 **held-out 測試切分只有 63.08%** —— 兩者差距即為記憶成分。
* 現行可對外引用的數字一律來自 held-out 切分，且本檔門檻即依該實測值設定。

[資料集性質揭露 / Dataset Provenance Disclosure]
------------------------------------------------------------------
`data/benchmark_ood_500.json` 由 `scripts/generate_ood_500.py` **以模板 + 前綴/後綴
填充程式化生成**，並非人工撰寫的盲測集。可測量特性：
  - 500 條由 **66 個基礎模板**回收；遮蔽數字後僅 **132 條互異句子**（最常見模板重複 13 次）。
  - **與 `src/engine/bilingual.py` 分類正則原始碼共享 >=4-gram 子字串者僅 31/500**
    （獨立重算；另 116/500 命中 docstring/散文/f-string 等非正則字面量，353/500 無任何
    4-gram 重疊）。舊版本檔曾宣稱「199/500 含與正則原始碼相同的子字串」——該數字實為
    「與整個模組原始碼文字重疊」的計數，把正則關鍵詞洩漏誇大了約 6 倍，已更正。
  - 3-gram 判準則給出 452/500，說明短 n-gram 幾無鑑別力，不宜作為洩漏證據。
因此本集屬 **synthetic adversarial coverage set（合成對抗覆蓋集）**，價值在於覆蓋廣度
與延遲量測，**不等同**於獨立泛化能力的證明。對外應稱「合成對抗覆蓋集的 held-out 切分」，
而非「未洩露盲測集」。

(Prompted by the deep defect audit §4.1/§4.2 and the benchmark-integrity remediation.)
"""
from __future__ import annotations
import math
import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List

from src.benchmark.ood_500_dataset import (
    OOD_500_DATASET,
    OOD_TEST_DATASET,
    OOD_TRAIN_DATASET,
    template_key,
)
from src.engine.bilingual import InboundIntentClassifier

# 由 held-out 切分實測錨定（見 `models/onnx_export_metrics.json` 與
# `scripts/export_onnx_intent_model.py` 的 [held-out] 輸出）。
# 每次重訓後若此值變動，請同步更新本常數與對外文件數字。
MEASURED_HELD_OUT_ACCURACY = 0.6308  # 123/195，held-out，與訓練集模板/字串不相交
# 門檻設在實測值下方約 3.1pp —— 足以攔截真實回歸，且不因明顯的機器/版本擾動誤判。
HELD_OUT_ACCURACY_FLOOR = 0.60


def _nearest_rank(sorted_vals: List[float], pct: float) -> float:
    """最近秩 (nearest-rank) 百分位。

    [修正] 舊版用 `latencies[int(len*0.99)]`；對 n=500 這是第 496 個值（99.2 分位），
    **偏高一格**。最近秩定義為 rank = ceil(pct * n)，索引 = rank - 1。
    """
    n = len(sorted_vals)
    if n == 0:
        return float("nan")
    rank = max(1, min(n, math.ceil(pct * n)))
    return sorted_vals[rank - 1]


def _evaluate(dataset: List[Dict[str, Any]]):
    """回傳 (accuracy, latency_ms_list, per_dim, per_intent_counts, route_counts)。"""
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    dim_total = defaultdict(int)
    dim_correct = defaultdict(int)
    route_counts = Counter()
    latencies_ms: List[float] = []
    correct = 0

    for item in dataset:
        raw_text = item["text"]
        expected = item["ground_truth_intent"]
        dim = item["dimension"]
        dim_total[dim] += 1

        t0 = time.perf_counter()
        res = InboundIntentClassifier.classify(raw_text, enable_llm_fallback=False)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

        rationale = res.tactical_rationale or ""
        if "hard offline divert" in rationale:
            route_counts["divert-regex"] += 1
        elif "ONNX" in rationale:
            route_counts["onnx"] += 1
        elif "unclassified" in rationale.lower():
            route_counts["unclassified"] += 1
        else:
            route_counts["regex-ladder"] += 1

        actual = res.primary_intent.value
        if actual == expected:
            correct += 1
            tp[expected] += 1
            dim_correct[dim] += 1
        else:
            fp[actual] += 1
            fn[expected] += 1

    return correct / len(dataset), latencies_ms, (dim_total, dim_correct), (tp, fp, fn), route_counts


def test_held_out_ood_benchmark_metrics():
    """在 **held-out 測試切分** 上量測準確率、Macro-F1 與延遲。

    [SCOPE] `enable_llm_fallback=False`：僅量測 Stage-1（正則階梯 + 端側 ONNX）路徑。
    報告的準確率是 held-out 分數；同一份模型在訓練切分上的分數遠高於此（見 docstring）。
    """
    total = len(OOD_TEST_DATASET)
    assert total == 195, f"Expected held-out split of 195 samples, got {total}"
    assert len(OOD_TRAIN_DATASET) == 305, f"Expected train split of 305, got {len(OOD_TRAIN_DATASET)}"

    overall_acc, latencies_ms, (dim_total, dim_correct), (tp, fp, fn), routes = _evaluate(
        OOD_TEST_DATASET
    )

    print("\n" + "=" * 78)
    print("TradeFlow HELD-OUT 合成對抗切分評測 (195 條，與訓練集不相交)")
    print("=" * 78)
    print(f"\n[總體指標] 樣本數: {total} | held-out 準確率: {overall_acc * 100:.2f}%")
    print(f"[路由分佈] {dict(routes)}")
    print("-" * 78)
    print(f"{'對抗維度 (Dimension)':<25} | {'樣本數':<8} | {'正確數':<8} | {'準確率 (Acc)':<12}")
    print("-" * 78)
    for dim, count in dim_total.items():
        corr = dim_correct[dim]
        print(f"{dim:<25} | {count:<8} | {corr:<8} | {corr / count * 100:>8.2f}%")

    classes = sorted(set(item["ground_truth_intent"] for item in OOD_TEST_DATASET))
    print("-" * 78)
    print(f"{'意圖類別 (Intent Class)':<28} | {'Prec':<8} | {'Recall':<8} | {'F1-Score':<8}")
    f1_list = []
    for cls_name in classes:
        p = tp[cls_name] / (tp[cls_name] + fp[cls_name]) if (tp[cls_name] + fp[cls_name]) else 0.0
        r = tp[cls_name] / (tp[cls_name] + fn[cls_name]) if (tp[cls_name] + fn[cls_name]) else 0.0
        f1 = (2 * p * r) / (p + r) if (p + r) else 0.0
        f1_list.append(f1)
        print(f"{cls_name:<28} | {p * 100:>6.1f}% | {r * 100:>6.1f}% | {f1 * 100:>6.1f}%")
    macro_f1 = sum(f1_list) / len(f1_list)
    print("-" * 78)
    print(f"Macro-F1 Score: {macro_f1 * 100:.2f}%")

    latencies_ms.sort()
    n = len(latencies_ms)
    mean_lat = sum(latencies_ms) / n
    p50_lat = _nearest_rank(latencies_ms, 0.50)
    p95_lat = _nearest_rank(latencies_ms, 0.95)
    p99_lat = _nearest_rank(latencies_ms, 0.99)
    print("\n[端側推斷延遲統計 (On-Device Latency Benchmark)]")
    print(f"  - Mean Latency : {mean_lat:.3f} ms")
    print(f"  - P50 Latency  : {p50_lat:.3f} ms")
    print(f"  - P95 Latency  : {p95_lat:.3f} ms")
    print(f"  - P99 Latency  : {p99_lat:.3f} ms (nearest-rank；多次執行區間 0.27–1.14ms)")
    print("=" * 78 + "\n")

    # 門檻依 **held-out 實測** 錨定。
    # [修正] 舊版 `overall_acc >= 0.90` 是對訓練集分數 92.00% 設的，根本無法發現污染；
    # 且延遲只斷言 `p99 < 15.0ms`（比實測寬 30 倍以上），測不出任何回歸。
    assert overall_acc >= HELD_OUT_ACCURACY_FLOOR, (
        f"held-out accuracy {overall_acc:.2%} regressed below the "
        f"{HELD_OUT_ACCURACY_FLOOR:.0%} floor (measured baseline {MEASURED_HELD_OUT_ACCURACY:.2%})"
    )
    # 延遲門檻收緊至實測值 ~18–75 倍以下之物裡上限，仍留機器負載餘裕。
    assert mean_lat < 1.0, f"Mean latency {mean_lat:.3f}ms exceeds 1.0ms guard"
    assert p99_lat < 5.0, (
        f"P99 latency {p99_lat:.3f}ms exceeds 5.0ms guard "
        "(observed spread across 12 runs: 0.27–1.14ms)"
    )


def test_ood_dataset_provenance_is_synthetic_and_undisclosed():
    """[PROVENANCE GUARD] 記錄並約束本資料集的合成性質，防止文件誇大為盲測集。

    此測試將資料集的可測量特性固定下來。若日後有人以人工撰寫的真實 held-out 集
    取代本集，這些特性會改變，測試即會失敗並提醒同步更新對外文件之措辭。
    """
    total = len(OOD_500_DATASET)
    assert total == 500

    # 模板化程度：遮蔽數字後應遠少於總數（實測 132），證明其為程式化生成。
    masked = Counter(re.sub(r"[0-9]+(\.[0-9]+)?", "N", e["text"]) for e in OOD_500_DATASET)
    assert len(masked) < 200, (
        f"Dataset now has {len(masked)} distinct masked sentences; if it became a genuinely "
        "hand-written held-out set, update the docstrings and external claims."
    )
    # 基礎模板數（剝除生成器前綴/後綴後）應為 66，且最常見模板重複 <= 13。
    template_groups = Counter(template_key(e["text"]) for e in OOD_500_DATASET)
    assert len(template_groups) == 66, (
        f"Expected 66 base templates, found {len(template_groups)}; "
        "update the provenance disclosure in tests/test_ood_500_benchmark.py and README."
    )
    assert max(template_groups.values()) <= 13, (
        f"Most-repeated base template now appears {max(template_groups.values())} times; "
        "the 'template recycling' disclosure needs updating."
    )

    # 每條樣本必須具備完整的標註欄位，確保生成器未產生半成品。
    for e in OOD_500_DATASET:
        assert e.get("text") and e.get("ground_truth_intent") and e.get("dimension")


def test_regex_keyword_overlap_is_disclosed_at_the_correct_magnitude():
    """[PROVENANCE GUARD] 把「與分類正則原始碼重疊」的實際量級固定下來。

    舊版 docstring 宣稱 199/500 條「含與 `src/engine/bilingual.py` 正則原始碼相同的子字串」。
    獨立重算顯示該數字實為「與整個模組**原始碼文字**共享 >=4-gram」的計數（含 docstring、
    散文與 f-string），真正的分類正則字面量重疊只有 **31/500**。本測試以模組自己宣告的
    `SafeLinearRegexEngine.compile(...)` 字面量為準，斷言其實際量級，避免揭露被誇大或低估。
    """
    import ast
    import inspect

    import src.engine.bilingual as bilingual

    def ngrams(s: str, n: int) -> set:
        return {s[i:i + n] for i in range(len(s) - n + 1)}

    tree = ast.parse(inspect.getsource(bilingual))
    regex_sources = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    assert len(regex_sources) >= 10, "Failed to recover classifier regex literals from the module AST"

    regex_ngrams = set()
    for src in regex_sources:
        regex_ngrams |= ngrams(src, 4)

    overlap = sum(1 for e in OOD_500_DATASET if ngrams(e["text"], 4) & regex_ngrams)
    # 實測 31/500。給定 5 條餘裕以容忍正則未來的小幅調整，但杜絕回到 199 的誇大值。
    assert 20 <= overlap <= 45, (
        f"4-gram overlap with classifier regex literals is {overlap}/500; "
        "if it moved, update the provenance disclosure in tests/test_ood_500_benchmark.py, "
        "README.md and TARGET_ARCHITECTURE_AND_ROADMAP.md."
    )
