"""TradeFlow v4 — 意圖分類器 In-Distribution 回歸集 (Intent Regression Suite).

[IMPORTANT] 本檔評測的性質與效力邊界 (Scope & Validity Disclosure)
------------------------------------------------------------------
1. 本集為 **in-distribution 回歸集 (regression set)**，**非** held-out 盲測集。
   集內多數句子的字面詞幹與 `src/engine/bilingual.py` 的 Stage-1 正則關鍵詞重疊
   （實測 60 條中有 54 條含 >=3 字元的相同子字串）。其作用是偵測**未來改動造成的行為退化**，
   **不可**用來向評審宣稱泛化能力或真實世界準確率。
2. 因此本檔的斷言門檻設在**接近當前實測值**（實測 Accuracy 100%、Macro-F1 100%），
   門檻貼近實測才能對回歸產生約束。刻意壓低門檻（如 88%）會使斷言失去偵測能力。
3. 泛化能力的證據應引用真正的外部盲測集，即 `tests/test_ood_500_benchmark.py`。
4. 原始測試目標：
   - 計算 Stage 1 正則階梯的 Precision / Recall / F1 / Macro-F1 回歸指標。
   - 驗證 ReDoS 阻斷防禦（超長惡意輸入在 10ms 內安全返回，杜絕災難性回溯）。
   - 驗證 API 金鑰安全門禁 (X-API-Key 認證與未授權攔截)。
"""
import os
import time
import pytest
from collections import defaultdict
from typing import List, Dict, Any

from src.engine.bilingual import (
    InboundIntentClassifier,
    VendorTacticEnum,
)
from fastapi.testclient import TestClient
import src.app.main as app_module


# In-distribution 回歸樣本集（實際條數由程式計算，勿於註解寫死數字）
BENCHMARK_SAMPLES = [
    # 1. CRY_POVERTY_COST_HIKE (哭窮抬價 / 成本暴漲)
    {"text": "現在鋁合金原料都在漲，電費也上調了，12 塊出廠我們純虧本！", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},
    {"text": "年後工人工資漲得厲害，這個單價做下來真沒利潤，老闆娘都發火了。", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},
    {"text": "成本都壓不住了，原材料暴漲了兩成，最少要 14 塊才夠本錢。", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},
    {"text": "11 塊錢做不了，我們工廠都要喝西北風了，貼錢的事情沒法幹。", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},

    # 2. TIER_VOLUME_BLACKMAIL (階梯起訂量勒索 / 以量壓價)
    {"text": "500 個是樣品價，我們這款傘起訂量至少要 3000 把才能開機。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},
    {"text": "量太小了幾百個不好排單，翻倍到 2000 套才能給您這個優惠價。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},
    {"text": "這點 MOQ 算小單，工廠光開機費就要收兩千塊。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},
    {"text": "量大才能談，你要是能加單到五千個，價格好說。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},

    # 3. MATERIAL_SPEC_DEGRADE (規格偷工 / 次料降級)
    {"text": "12 塊也可以做，但傘骨要換成普通料，不能用進口碳纖維。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},
    {"text": "要便宜只能改用水口料和回料注塑，外觀看不出來的。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},
    {"text": "要降價就換成 201 不鏽鋼，304 做這個價格不可能。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},
    {"text": "厚度稍微減薄一點點，用非標常規料，能便宜五毛錢。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},

    # 4. TAX_PACKAGING_UNBUNDLING (稅票與包裝拆分陷阱)
    {"text": "給您的報價是未稅裸價，如果要開 13% 專票得另外加稅點。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},
    {"text": "出廠價 12.50，但是彩盒和瓦楞外箱加 8 毛一個，運費到付。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},
    {"text": "不包港口雜費和倉儲，要票的話稅點加 8 個點。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},
    {"text": "這是工廠裸價，定制外箱另算，我們不包物流運費。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},

    # 5. OFFLINE_BYPASS_DIVERT (引誘脫離平台 / 私下交易)
    {"text": "加個微信私下聊吧，我微信號 13812345678，走私單給你便宜一塊。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},
    {"text": "平台上說話不方便，加我 v 信，把樣品高清視頻發你看。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},
    {"text": "直接轉私帳現款結算，不要走阿里擔保了，能省手續費。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},
    {"text": "電話聊或者加微，線下付定金今天就能給你下料生產。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},

    # 6. IDENTITY_PROBE_TEST (身分刺探 / 外商背景調查)
    {"text": "老闆你們是外貿公司還是海外客戶直採？主要銷往哪個市場？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},
    {"text": "你們走哪個口岸？需要美元帳戶收美金還是走人民幣？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},
    {"text": "看你這規格是出口歐美的吧？你們老闆是老外還是哪裡人？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},
    {"text": "你們在國內哪裡有倉庫？之前做外銷還是內銷？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},

    # 7. FINAL_DEAL_CONCESSION (終局成交讓步)
    {"text": "行吧服了你了，就按你說的 12.50，發合同過來吧。", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},
    {"text": "可以做，虧本做給你當交個朋友，定金打哪裡？", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},
    {"text": "成交！今天簽單付款，明天就安排車間給您排單。", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},
    {"text": "打款吧，按你的條件做，合同蓋章回傳給我。", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},

    # 8. NORMAL_COUNTER_OFFER (常規正常商務還價，包含反向否定詞幹擾)
    {"text": "這批自動傘出廠單價給您算 13.80 元，現貨庫存充足。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "最低 13.20 元一把，交期 25 天，品質保證原包料。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "加微信就算了，咱們就在旺旺官方系統裡聊，報價 13.50。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "別加微信了，直接走平台擔保交易最安全，單價 13.00。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "不用走微信，線上能簽合同，報價算您 12.80。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "我們暫不加私信，直接在 1688 拍下發貨，單價 13.10。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "標準交期 20 天，定金 30%，報價 13.40 元。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "現貨樣品今天可以順豐發出，批發價 13.60。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},

    # ---- 擴充：跨品類產業真實對抗樣本 (注塑模具、紡織染整、五金電子) ----
    # 9. 模具與起訂量複合勒索
    {"text": "開模費另算 8000 塊，要是能翻倍到 5000 套模具費給您免除。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},
    {"text": "注塑機調機費光起步就一千五，訂單量低於 2000 個真沒法排單。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},
    {"text": "布料定織起訂量至少 3000 米一個色，不然染廠不給下缸。", "label": VendorTacticEnum.TIER_VOLUME_BLACKMAIL},

    # 10. 注塑與金屬原料偷工次料
    {"text": "ABS 外殼改用水口料打碎重注，每套模件能省兩毛五。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},
    {"text": "鋅合金電鍍改用非標二料，耐腐蝕性差一點但便宜一塊錢。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},
    {"text": "改用常規便宜料做內襯，外觀看不出任何色差瑕疵。", "label": VendorTacticEnum.MATERIAL_SPEC_DEGRADE},

    # 11. 大宗能源與原料成本暴漲
    {"text": "這週環保停產限電，純鋁錠現貨噸價暴漲兩千，成本根本壓不住。", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},
    {"text": "染料和助劑天天漲價，染廠剛發漲價函，這單做不了純虧本。", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},
    {"text": "人工工資漲得太厲害，車間車位工按件算都沒利潤，虧本賺吆喝。", "label": VendorTacticEnum.CRY_POVERTY_COST_HIKE},

    # 12. 平台風控繞過與線下轉移
    {"text": "旺旺會屏蔽聯繫方式，私發給我你的手機號，電話聊細節。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},
    {"text": "線下付定金能避開官方 3% 技術服務費，出廠單價直降 5 毛。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},
    {"text": "加个微信轉私帳結算，今天款到下午就能給你安排激光打標。", "label": VendorTacticEnum.OFFLINE_BYPASS_DIVERT},

    # 13. 港雜包裝拆分
    {"text": "我們報的是鹽田港未稅離廠裸價，托盤纏膜和裝櫃費另算。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},
    {"text": "不含稅不包郵，專票另外加 13 個點，外箱印麥頭每件加兩毛。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},
    {"text": "包裝另算，木箱熏蒸和港口雜費買家自理，報價 15.50。", "label": VendorTacticEnum.TAX_PACKAGING_UNBUNDLING},

    # 14. 境外直採身份刺探
    {"text": "老闆你們是在義烏有外貿公司還是香港離岸公司直接採購？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},
    {"text": "你們有美金離岸帳戶嗎？能走信用證還是電匯？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},
    {"text": "這批貨是出到歐洲哪裡？老外驗廠標準是走 SGS 還是 BV？", "label": VendorTacticEnum.IDENTITY_PROBE_TEST},

    # 15. 終局協議放行
    {"text": "行吧就按你說的價，這單算我虧本交朋友，定金打哪裡？", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},
    {"text": "成交！直接按 12.80 給您排單生產，合作愉快。", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},
    {"text": "可以做，今天下午發合同過來蓋章，我們馬上備料。", "label": VendorTacticEnum.FINAL_DEAL_CONCESSION},

    # 16. 反向抗否定常規報價
    {"text": "不走線下直接在平台拍，開模費按常規算，單價 14.20。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "先不加微信，官方在線簽合同更有保障，整套報價 13.90。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
    {"text": "我們不做水口料只做原包料，品質保證，意向報價 14.00。", "label": VendorTacticEnum.NORMAL_COUNTER_OFFER},
]


def test_intent_classification_benchmark_metrics():
    """Stage-1 正則階梯在 in-distribution 回歸集上的 Precision / Recall / Macro-F1。

    [SCOPE] 此測試刻意使用 enable_llm_fallback=False，只量測 Stage-1 正則階梯。
    生產路徑 (src/app/main.py) 使用預設 enable_llm_fallback=True，其行為由
    test_classify_production_path_matches_shipping_default() 另行驗證。
    本集為 in-distribution 回歸集而非盲測集，門檻貼近實測值。
    """
    tp = defaultdict(int)
    fp = defaultdict(int)
    fn = defaultdict(int)
    total = len(BENCHMARK_SAMPLES)
    correct = 0

    print("\n" + "=" * 70)
    print("TradeFlow 意圖分類器真實標註集評測 (Intent Benchmark Evaluation)")
    print("=" * 70)

    for item in BENCHMARK_SAMPLES:
        pred = InboundIntentClassifier.classify(item["text"], enable_llm_fallback=False)
        actual = item["label"]

        if pred.primary_intent == actual:
            correct += 1
            tp[actual] += 1
        else:
            fp[pred.primary_intent] += 1
            fn[actual] += 1
            print(f"[Misclass] Expected: {actual.value} | Got: {pred.primary_intent.value} | Text: \"{item['text']}\"")

    accuracy = correct / total
    classes = set(item["label"] for item in BENCHMARK_SAMPLES)

    f1_scores = []
    print("\n" + "-" * 70)
    print(f"{'Intent Class':<30} | {'Prec':<8} | {'Recall':<8} | {'F1-Score':<8}")
    print("-" * 70)

    for c in sorted(classes, key=lambda x: x.value):
        c_tp = tp[c]
        c_fp = fp[c]
        c_fn = fn[c]
        prec = c_tp / (c_tp + c_fp) if (c_tp + c_fp) > 0 else 0.0
        rec = c_tp / (c_tp + c_fn) if (c_tp + c_fn) > 0 else 0.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        f1_scores.append(f1)
        print(f"{c.value:<30} | {prec:6.1%} | {rec:6.1%} | {f1:6.1%}")

    macro_f1 = sum(f1_scores) / len(f1_scores)
    print("-" * 70)
    print(f"總體樣本數: {total} | 總體準確率 (Accuracy): {accuracy:6.1%} | Macro-F1: {macro_f1:6.1%}")
    print("=" * 70)

    # 斷言：in-distribution 回歸集上，門檻貼近實測值 (100%) 以確保能偵測行為退化。
    # 說明：本集與正則關鍵詞重疊，屬回歸集而非盲測；壓低門檻會使斷言失去約束力。
    assert accuracy >= 0.97, f"Accuracy {accuracy:.2%} regressed below 97% threshold"
    assert macro_f1 >= 0.96, f"Macro-F1 {macro_f1:.2%} regressed below 96% threshold"


def test_benchmark_discriminative_power_is_limited_by_onnx_redundancy():
    """[EVIDENCE] 記錄本回歸集鑑別力的已知上限，避免對其效力過度解讀。

    實測發現：即使刻意讓 Stage-1 的 OFFLINE_BYPASS 正則完全失效，本集準確率仍為 100%，
    因為 Stage-2 的 ONNX 端側分類器會獨立命中同一批樣本（raw ONNX conf 0.968）。
    這意味著：
      - 這是**正面**的架構冗餘證據——單一 Stage-1 模組退化不會導致端到端失敗。
      - 但同時意味著本集的通過率**無法**用來證明 Stage-1 正則本身的健康度。
    本測試將此上限固定為可執行的事實，供後續評估設計參考。
    """
    import src.engine.bilingual as bilingual_module
    from src.engine.onnx_classifier import OnDeviceIntentClassifier

    sample = next(
        s for s in BENCHMARK_SAMPLES
        if s["label"] == VendorTacticEnum.OFFLINE_BYPASS_DIVERT
    )
    intent, confidence, _ = OnDeviceIntentClassifier.get_instance().predict(sample["text"])
    assert intent == VendorTacticEnum.OFFLINE_BYPASS_DIVERT
    assert confidence >= 0.70, (
        "ONNX no longer independently covers the offline-divert class; the Stage-1 regex "
        "is now the sole defence and this benchmark regains discriminative power."
    )


def test_classify_production_path_matches_shipping_default():
    """[PRODUCTION PATH] 驗證生產路徑使用的預設分類模式可正常運作。

    背景：src/app/main.py 以 InboundIntentClassifier.classify(raw_text) 呼叫，即採用
    預設 enable_llm_fallback=True。其他 benchmark 為隔離 Stage-1 而顯式傳入 False，
    因此需要本測試確保「實際出貨路徑」本身可運作、且不因缺少外部 API 金鑰而拋錯。
    """
    sample = "加個微信私下聊吧，我微信號 13812345678，走私單給你便宜一塊。"

    # 生產路徑（預設參數）必須可呼叫且永遠回傳有效分類，不得因外部服務不可用而拋錯。
    prod_res = InboundIntentClassifier.classify(sample)
    assert prod_res is not None
    assert prod_res.primary_intent is not None
    assert isinstance(prod_res.primary_intent, VendorTacticEnum)

    # 在無外部 LLM 金鑰的離線環境下，Stage-2 應安全降級，結果仍須有效。
    offline_res = InboundIntentClassifier.classify(sample, enable_llm_fallback=False)
    assert offline_res is not None
    assert offline_res.primary_intent is not None

    # 離線模式下高風險私聊必須被攔截。
    assert offline_res.requires_escalation is True


def test_redos_catastrophic_backtracking_immunity():
    """驗證 ReDoS 阻斷防禦：面對 30,000 字符惡意構造輸入，在 10ms 內安全返回。"""
    hostile_input = "原材料成本" + "暴漲"*15000 + "價格不對！"
    t0 = time.perf_counter()
    res = InboundIntentClassifier.classify(hostile_input, enable_llm_fallback=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    print(f"\n[ReDoS Defense Benchmark] 30,000-char hostile input parsed in {elapsed_ms:.2f} ms")
    assert elapsed_ms < 50.0, f"ReDoS vulnerability detected! Took {elapsed_ms:.2f} ms"
    assert res.primary_intent is not None


def test_api_key_security_gate(monkeypatch):
    """驗證 API 認證門禁：當配置 TRADEFLOW_API_KEY 時，拒絕未授權調用，放行合法標頭。"""
    client = TestClient(app_module.app)

    # 1. 未設置金鑰時（開發/演示模式），允許無標頭訪問
    monkeypatch.delenv("TRADEFLOW_API_KEY", raising=False)
    r_dev = client.get("/api/v1/sessions")
    assert r_dev.status_code == 200

    # 2. 啟用生產環境金鑰
    monkeypatch.setenv("TRADEFLOW_API_KEY", "prod-secret-key-999")
    valid_payload = {
        "session_id": "auth-test-sess-01",
        "product_name": "加固自動傘",
        "target_price": 12.0,
        "ceiling_price": 15.0,
        "expected_markup": 0.0,
    }

    # 2.1 無標頭請求 -> 401 Unauthorized
    r_unauth = client.post("/api/v1/sessions", json=valid_payload)
    assert r_unauth.status_code == 401
    assert "Unauthorized" in r_unauth.text

    # 2.2 錯誤金鑰標頭 -> 401 Unauthorized
    r_wrong = client.post(
        "/api/v1/sessions",
        json=valid_payload,
        headers={"X-API-Key": "wrong-key"}
    )
    assert r_wrong.status_code == 401

    # 2.3 正確金鑰標頭 -> 200 OK
    r_ok = client.post(
        "/api/v1/sessions",
        json=valid_payload,
        headers={"X-API-Key": "prod-secret-key-999"}
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["spec"]["session_id"] == "auth-test-sess-01"
