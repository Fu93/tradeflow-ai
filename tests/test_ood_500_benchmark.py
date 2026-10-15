"""500-Item Synthetic Adversarial Benchmark (test_ood_500_benchmark.py).

[IMPORTANT] 資料集性質揭露 (Dataset Provenance Disclosure)
------------------------------------------------------------------
`src/benchmark/ood_500_dataset.py` 載入的 `data/benchmark_ood_500.json` 是由
`scripts/generate_ood_500.py` **以模板 + 數字變體程式化生成**，並非人工撰寫的
held-out 盲測集。實測特性：
  - 500 條中，遮蔽數字後僅剩 **132 條互異句子**（單一模板最多重複 12 次）。
  - **199/500** 條含 >=4 字元與 `src/engine/bilingual.py` 正則原始碼相同的子字串。
因此本集屬於 **synthetic adversarial coverage set（合成對抗覆蓋集）**，
其價值在於覆蓋廣度與延遲量測，**不等同**於獨立泛化能力的證明。
對外聲稱時應使用「合成對抗覆蓋集」而非「未洩露盲測集」。

評測目標：
1. 合成對抗集上的總體準確率 (Accuracy) 與 Macro-F1。
2. 四大對抗維度各自準確率 (Coastal Jargon, Multi Clause, Evasion Offline, Sarcasm Pseudo)。
3. 端側推斷延遲統計 (Mean, P50, P95, P99)。
"""
from __future__ import annotations
import time
import pytest
from collections import defaultdict
from typing import List, Dict, Any

from src.benchmark.ood_500_dataset import OOD_500_DATASET
from src.engine.bilingual import InboundIntentClassifier, VendorTacticEnum


def test_500_ood_benchmark_metrics():
    """評測 500 條合成對抗樣本之指標與延遲。

    [SCOPE] 使用 enable_llm_fallback=False，僅量測 Stage-1 正則階梯 + Stage-2 ONNX 端側路徑。
    本集為程式化生成之合成對抗覆蓋集，其中 199/500 條與正則原始碼有子字串重疊，
    故門檻貼近實測值以約束回歸，不作為泛化能力之宣稱依據。
    """
    total = len(OOD_500_DATASET)
    assert total == 500, f"Expected 500 samples, got {total}"

    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    dim_total = defaultdict(int)
    dim_correct = defaultdict(int)

    latencies_ms: List[float] = []
    correct = 0

    print("\n" + "=" * 78)
    print("TradeFlow 500 條跨領域 OOD 對抗盲測集評測 (Ground-Truth 500 OOD Benchmark)")
    print("=" * 78)

    for item in OOD_500_DATASET:
        raw_text = item["text"]
        expected_intent = item["ground_truth_intent"]
        dim = item["dimension"]
        dim_total[dim] += 1

        t0 = time.perf_counter()
        res = InboundIntentClassifier.classify(raw_text, enable_llm_fallback=False)
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

        actual_intent = res.primary_intent.value

        if actual_intent == expected_intent:
            correct += 1
            tp[expected_intent] += 1
            dim_correct[dim] += 1
        else:
            fp[actual_intent] += 1
            fn[expected_intent] += 1

    overall_acc = correct / total

    print(f"\n[總體指標] 樣本數: {total} | 總體準確率: {overall_acc * 100:.2f}%")
    print("-" * 78)
    print(f"{'對抗維度 (Dimension)':<25} | {'樣本數':<8} | {'正確數':<8} | {'準確率 (Acc)':<12}")
    print("-" * 78)
    for dim, count in dim_total.items():
        corr = dim_correct[dim]
        acc = corr / count
        print(f"{dim:<25} | {count:<8} | {corr:<8} | {acc * 100:>8.2f}%")

    print("-" * 78)
    print(f"{'意圖類別 (Intent Class)':<28} | {'Prec':<8} | {'Recall':<8} | {'F1-Score':<8}")
    print("-" * 78)
    classes = set(item["ground_truth_intent"] for item in OOD_500_DATASET)
    f1_list = []
    for cls_name in sorted(classes):
        p = tp[cls_name] / (tp[cls_name] + fp[cls_name]) if (tp[cls_name] + fp[cls_name]) > 0 else 0.0
        r = tp[cls_name] / (tp[cls_name] + fn[cls_name]) if (tp[cls_name] + fn[cls_name]) > 0 else 0.0
        f1 = (2 * p * r) / (p + r) if (p + r) > 0 else 0.0
        f1_list.append(f1)
        print(f"{cls_name:<28} | {p*100:>6.1f}% | {r*100:>6.1f}% | {f1*100:>6.1f}%")

    macro_f1 = sum(f1_list) / len(f1_list)
    print("-" * 78)
    print(f"Macro-F1 Score: {macro_f1 * 100:.2f}%")

    # 延遲指標
    latencies_ms.sort()
    mean_lat = sum(latencies_ms) / len(latencies_ms)
    p50_lat = latencies_ms[int(len(latencies_ms) * 0.50)]
    p95_lat = latencies_ms[int(len(latencies_ms) * 0.95)]
    p99_lat = latencies_ms[int(len(latencies_ms) * 0.99)]

    print("\n[端側推斷延遲統計 (On-Device Latency Benchmark)]")
    print(f"  - Mean Latency : {mean_lat:.3f} ms")
    print(f"  - P50 Latency  : {p50_lat:.3f} ms")
    print(f"  - P95 Latency  : {p95_lat:.3f} ms")
    print(f"  - P99 Latency  : {p99_lat:.3f} ms (遠低於 15ms 生產上限！)")
    print("=" * 78 + "\n")

    # 門檻貼近實測值 (Accuracy 92.00%、P99 0.488ms) 以確保能偵測行為退化。
    # 注意：本集為合成對抗覆蓋集，門檻僅用於回歸約束，非泛化能力宣稱。
    assert overall_acc >= 0.90, f"Overall accuracy {overall_acc:.2%} regressed below 90% target"
    assert p99_lat < 15.0, f"P99 latency {p99_lat}ms exceeds 15ms ceiling"


def test_ood_dataset_provenance_is_synthetic_and_undisclosed():
    """[PROVENANCE GUARD] 記錄並約束本資料集的合成性質，防止文件誇大為盲測集。

    此測試將資料集的可測量特性固定下來。若日後有人以人工撰寫的真實 held-out 集
    取代本集，這些特性會改變，測試即會失敗並提醒同步更新對外文件之措辭。
    """
    import re
    from collections import Counter

    total = len(OOD_500_DATASET)
    assert total == 500

    # 模板化程度：遮蔽數字後應遠少於總數（實測 132），證明其為程式化生成。
    templates = Counter(re.sub(r"[0-9]+(\.[0-9]+)?", "N", e["text"]) for e in OOD_500_DATASET)
    assert len(templates) < 200, (
        f"Dataset now has {len(templates)} distinct templates; if it became a genuinely "
        "hand-written held-out set, update the docstrings and external claims."
    )

    # 每條樣本必須具備完整的標註欄位，確保生成器未產生半成品。
    for e in OOD_500_DATASET:
        assert e.get("text") and e.get("ground_truth_intent") and e.get("dimension")
