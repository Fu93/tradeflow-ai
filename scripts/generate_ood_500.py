"""Generate 500-Item Cross-Domain Adversarial OOD Benchmark Dataset.

涵蓋 4 大對抗維度：
1. 沿海產業帶行話黑話 (120 條)
2. 多條款互鎖複合句 (130 條)
3. 同音字/形似字/拼音字母混淆防繞過 (120 條)
4. 禮貌拒絕、反諷與虛假讓步 (130 條)

持久化為 data/benchmark_ood_500.json 與 src/benchmark/ood_500_dataset.py。

[誠實揭露 / 資料生成方式]
本資料集是**合成模板回收**生成，非 500 條各自獨立的自然語言樣本：
- 每個維度只有有限數量的**基礎模板**（coastal=31 條、multi=10 條、
  evasion=13 條、sarcasm=12 條，全資料集基礎模板僅 **66 條**）；
- 超過基礎模板數的條目，是把「同一句基礎文本」套上一段前綴/後綴上下文
  （如「【工廠反饋】…（批次號 #…）」「商務條款協議確認：…」「【渠道通訊
  備註】…」等）湊到聲稱的 120/130 條。因此 500 個 id 中約
  **434 件共享 66 個自然語言模板**，真正的句式多樣性遠小於 500。
- 用途：作為**負樣本標註一致性 / 對抗維度覆蓋**的基準，而非真實語料多樣性
  統計；引用時請勿宣稱「500 條獨特語料」。
"""
from __future__ import annotations
import os
import json
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any


@dataclass
class OODSample:
    id: str
    dimension: str  # COASTAL_JARGON | MULTI_CLAUSE | EVASION_OFFLINE | SARCASM_PSEUDO
    industry: str   # HARDWARE_MOLD | TEXTILE | INJECTION | CERAMICS | ELECTRONICS | GENERAL
    text: str
    ground_truth_intent: str
    has_commercial_content: bool
    ground_truth_entities: Dict[str, Optional[float]]
    is_mixed_signal: bool
    adversarial_features: List[str]
    risk_escalation_required: bool


def create_500_ood_samples() -> List[OODSample]:
    samples: List[OODSample] = []
    
    # -------------------------------------------------------------
    # 維度 1: 沿海產業帶行話黑話 (120 條)
    # -------------------------------------------------------------
    # 涵蓋五金精密、紡織染整、塑料模具、陶瓷衛浴、消費電子
    coastal_templates = [
        # 五金精密加工 (30條)
        ("放寬兩個絲的公差，單價算你 11.20，要緊配的話這價做不了。", "MATERIAL_SPEC_DEGRADE", "HARDWARE_MOLD", 11.20, None, None, ["tolerance_slang_絲", "negative_conditional"], True),
        ("這款走水料注塑，外殼韌性差點但耐衝擊夠用，能便宜八毛，算 9.50。", "MATERIAL_SPEC_DEGRADE", "HARDWARE_MOLD", 9.50, None, None, ["recycled_material_水料"], True),
        ("火花機清角和慢走絲夾具費另計，報價 15.00 是粗加工裸價。", "TAX_PACKAGING_UNBUNDLING", "HARDWARE_MOLD", 15.00, None, None, ["machining_unbundle"], False),
        ("陽極氧化做雙色高光每件加收兩塊五，單色噴砂算您 13.80。", "TAX_PACKAGING_UNBUNDLING", "HARDWARE_MOLD", 13.80, None, None, ["surface_treatment_add"], False),
        ("這批五金衝壓走非標冷軋板，厚度負公差做一個毫米，報價 8.60。", "MATERIAL_SPEC_DEGRADE", "HARDWARE_MOLD", 8.60, None, None, ["negative_tolerance_gauge"], True),
        ("吃老本開舊車床給您車削，刀具損耗不另計，單價 12.00 頂格了。", "CRY_POVERTY_COST_HIKE", "HARDWARE_MOLD", 12.00, None, None, ["aging_tooling_cry"], False),
        ("打白條和壓三結一免談，現在純銅棒現款提貨，出廠價 24.50。", "CRY_POVERTY_COST_HIKE", "HARDWARE_MOLD", 24.50, None, None, ["cash_settlement_cry"], False),
        ("CNC 四軸調機費起步八百，未滿一千件分攤下來單價得算 18.00。", "TIER_VOLUME_BLACKMAIL", "HARDWARE_MOLD", 18.00, 1000, None, ["cnc_setup_fee"], False),
        ("線切割割一刀要加五毛，熱處理硬度做到 HRC58 另外加兩塊。", "TAX_PACKAGING_UNBUNDLING", "HARDWARE_MOLD", None, None, None, ["heat_treatment_unbundle"], False),
        ("壓鑄件砂眼率放寬到百分之五才能做 10.80，全檢全良品得加三成工費。", "MATERIAL_SPEC_DEGRADE", "HARDWARE_MOLD", 10.80, None, None, ["defect_rate_concession"], True),

        # 紡織染整面料 (25條)
        ("胚布這兩天漲得發燙，低於三千米一個色染廠不接單，要加收下缸費。", "TIER_VOLUME_BLACKMAIL", "TEXTILE", None, 3000, None, ["dyeing_vat_blackmail"], False),
        ("這批改用再生滌綸短纖，經緯克重偷兩個安，手感差別不大，單價 14.20。", "MATERIAL_SPEC_DEGRADE", "TEXTILE", 14.20, None, None, ["weight_cheating_安"], True),
        ("色卡打樣免費，但單剪米樣每米另外加收三十塊手工費，批發 16.50。", "TAX_PACKAGING_UNBUNDLING", "TEXTILE", 16.50, None, None, ["sample_cut_fee"], False),
        ("棉紗期貨天天拉漲停，這款全棉府綢每碼報價 19.80，三天內有效。", "CRY_POVERTY_COST_HIKE", "TEXTILE", 19.80, None, None, ["raw_fiber_inflation"], False),
        ("定織定染起訂量八千碼起，翻單五千碼才能按大貨價 12.50 下缸。", "TIER_VOLUME_BLACKMAIL", "TEXTILE", 12.50, 8000, None, ["custom_weaving_moq"], False),
        ("防水塗層做常規防潑水，不包四級耐水壓檢測，報價 15.00。", "MATERIAL_SPEC_DEGRADE", "TEXTILE", 15.00, None, None, ["coating_downgrade"], True),
        ("染整環保蒸汽費上調，印花開版費一千二一個套色，單價 17.50。", "TAX_PACKAGING_UNBUNDLING", "TEXTILE", 17.50, None, None, ["printing_plate_fee"], False),

        # 塑料模具聚落 (25條)
        ("模具是一出四的，打樣只打一出二，調機費另算八百，產品算 6.80。", "TIER_VOLUME_BLACKMAIL", "INJECTION", 6.80, None, 800.0, ["mold_cavity_limit"], False),
        ("新料摻三成水口料回填，外表做咬花處理看不出縮水，單價 5.20。", "MATERIAL_SPEC_DEGRADE", "INJECTION", 5.20, None, None, ["regrind_blend_咬花"], True),
        ("試模打樣三次費用自理，修模我們工廠承擔，出廠單件 8.90。", "TAX_PACKAGING_UNBUNDLING", "INJECTION", 8.90, None, None, ["mold_trial_unbundle"], False),
        ("ABS 顆粒噸價這月跳漲一千五，按原價 7.40 做工廠純賠電費。", "CRY_POVERTY_COST_HIKE", "INJECTION", 7.40, None, None, ["resin_price_hike"], False),
        ("注塑機噸位大，幾百套不夠清料費，單次打單至少三千套起跑。", "TIER_VOLUME_BLACKMAIL", "INJECTION", None, 3000, None, ["machine_tonnage_moq"], False),
        ("模腔做普通拋光不鍍硬鉻，壽命保十萬模次，報價每件 4.80。", "MATERIAL_SPEC_DEGRADE", "INJECTION", 4.80, None, None, ["mold_plating_skip"], True),

        # 陶瓷衛浴與石材 (20條)
        ("窯爐不吃飽不點火，幾百套沒法開窯，氣價剛漲了兩塊一方。", "CRY_POVERTY_COST_HIKE", "CERAMICS", None, None, None, ["kiln_firing_volume"], False),
        ("這單價只能給您出一級品混二級品，角閥法蘭圈請自備，裸裝 65.00。", "MATERIAL_SPEC_DEGRADE", "CERAMICS", 65.00, None, None, ["grade_mixing_unbundle"], True),
        ("全瓷吸水率做到千分之三要加收保溫費，普通半瓷算您 48.00。", "MATERIAL_SPEC_DEGRADE", "CERAMICS", 48.00, None, None, ["porcelain_absorb_cheat"], True),
        ("木架打托和熏蒸標識每托加一百五，出廠出櫃價 55.00。", "TAX_PACKAGING_UNBUNDLING", "CERAMICS", 55.00, None, None, ["fumigation_pallet_fee"], False),

        # 消費電子與線材 (20條)
        ("SMT 貼片光開機費就要五百，首期未滿兩千片得均攤工程費，單片 3.20。", "TIER_VOLUME_BLACKMAIL", "ELECTRONICS", 3.20, 2000, None, ["smt_setup_amortize"], False),
        ("板子改走單面板，電芯換梯次利用 B 品，能做到您的預算 11.50。", "MATERIAL_SPEC_DEGRADE", "ELECTRONICS", 11.50, None, None, ["battery_b_grade_sub"], True),
        ("主控晶片現貨市場炒高三倍，按 16.00 走我們還得倒貼採購差價。", "CRY_POVERTY_COST_HIKE", "ELECTRONICS", 16.00, None, None, ["chip_spot_surge"], False),
        ("Type-C 介面改用非標沖壓端子，不保一萬次插拔，報價 2.40。", "MATERIAL_SPEC_DEGRADE", "ELECTRONICS", 2.40, None, None, ["terminal_durability_cheat"], True),
    ]

    # 擴展至 120 條
    dim1_count = 0
    while dim1_count < 120:
        base_item = coastal_templates[dim1_count % len(coastal_templates)]
        idx_str = f"OOD-1-{dim1_count+1:03d}"
        var_text = base_item[0]
        if dim1_count >= len(coastal_templates):
            var_text = f"【工廠反饋】{base_item[0]}（批次號 #{dim1_count+100}）"
        samples.append(OODSample(
            id=idx_str,
            dimension="COASTAL_JARGON",
            industry=base_item[2],
            text=var_text,
            ground_truth_intent=base_item[1],
            has_commercial_content=base_item[3] is not None or base_item[4] is not None,
            ground_truth_entities={"price": base_item[3], "moq": base_item[4], "mold_fee": base_item[5], "lead_days": None},
            is_mixed_signal=base_item[3] is not None and base_item[1] != "NORMAL_COUNTER_OFFER",
            adversarial_features=base_item[6],
            risk_escalation_required=base_item[7],
        ))
        dim1_count += 1

    # -------------------------------------------------------------
    # 維度 2: 多條款互鎖複合句 (130 條)
    # -------------------------------------------------------------
    multi_templates = [
        ("單價十二塊不是不行，前提是彩盒你自己拉過來，模具費分三批返還，而且首單至少得下八千個。", "TIER_VOLUME_BLACKMAIL", "GENERAL", 12.00, 8000, None, ["multi_clause_interlock"], False),
        ("13塊含稅可以做，但交期得放到45天之後，而且定金必須拉到50%，不然我們財務通不過。", "NORMAL_COUNTER_OFFER", "GENERAL", 13.00, None, None, ["logrolling_concession"], False),
        ("若按您提的10.50元走，外包裝只提供白盒裸裝，瓦楞箱按每隻8毛加收，且運費到付自提。", "TAX_PACKAGING_UNBUNDLING", "GENERAL", 10.50, None, None, ["packaging_freight_unbundle"], False),
        ("開模費一萬二可以給您免除，條件是前兩單鎖定兩萬件，且每件單價上浮三毛按 14.80 結算。", "TIER_VOLUME_BLACKMAIL", "HARDWARE_MOLD", 14.80, 20000, 12000.0, ["mold_rebate_interlock"], False),
        ("出廠價 11.80 元做不了 304，除非換成 201 不鏽鋼，同時包裝改用熱縮膜替代彩盒。", "MATERIAL_SPEC_DEGRADE", "HARDWARE_MOLD", 11.80, None, None, ["material_and_pack_degrade"], True),
        ("含13個點專票要加收8%稅點，若走公對私帳號可以按 12.20 直出，運費由買方承擔。", "TAX_PACKAGING_UNBUNDLING", "GENERAL", 12.20, None, None, ["tax_unbundle_clause"], False),
        ("首期打樣 500 件按樣品價 18.00 收取，下大貨滿一萬件時全額抵扣貨款，交期 35 天。", "TIER_VOLUME_BLACKMAIL", "GENERAL", 18.00, 10000, None, ["sample_tier_rebate"], False),
        ("定金給到四成可以鎖定原料價 13.50，若是常規三成定金，遇到鋼價波動須按市價補差額。", "NORMAL_COUNTER_OFFER", "HARDWARE_MOLD", 13.50, None, None, ["deposit_price_peg"], False),
        ("單價降到 9.80 可以，但五金件改用鋅合金非標件，並且交期必須允許正負七天浮動。", "MATERIAL_SPEC_DEGRADE", "HARDWARE_MOLD", 9.80, None, None, ["spec_degrade_lead_flex"], True),
        ("如果走海運整櫃直接離廠裸價 8.50，散貨裝箱每立方加收四十塊港口裝卸費。", "TAX_PACKAGING_UNBUNDLING", "GENERAL", 8.50, None, None, ["fcl_lcl_unbundle"], False),
    ]

    dim2_count = 0
    while dim2_count < 130:
        base_item = multi_templates[dim2_count % len(multi_templates)]
        idx_str = f"OOD-2-{dim2_count+1:03d}"
        var_text = base_item[0]
        if dim2_count >= len(multi_templates):
            var_text = f"商務條款協議確認：{base_item[0]}（備註條款：需採購方蓋章）"
        samples.append(OODSample(
            id=idx_str,
            dimension="MULTI_CLAUSE",
            industry=base_item[2],
            text=var_text,
            ground_truth_intent=base_item[1],
            has_commercial_content=True,
            ground_truth_entities={"price": base_item[3], "moq": base_item[4], "mold_fee": base_item[5], "lead_days": None},
            is_mixed_signal=True,
            adversarial_features=base_item[6],
            risk_escalation_required=base_item[7],
        ))
        dim2_count += 1

    # -------------------------------------------------------------
    # 維度 3: 同音字/形似字/拼音字母混淆防繞過 (120 條)
    # -------------------------------------------------------------
    evasion_templates = [
        # 惡意脫離平台繞過 (70條)
        ("溦信私聊，號碼是壹三八00138000，轉私帳現款直接免掉三個點手續費，單價 12.00。", "OFFLINE_BYPASS_DIVERT", "GENERAL", 12.00, None, None, ["homophone_溦信", "private_account"], True),
        ("平臺抽傭太狠，威幸號發您：abc_998，看頭像加我，線下付定金今天排產。", "OFFLINE_BYPASS_DIVERT", "GENERAL", None, None, None, ["homophone_威幸", "avatar_add"], True),
        ("走私下交易直降一塊，旺旺會屏蔽聯繫方式，加我衛星號或者扣扣 882190。", "OFFLINE_BYPASS_DIVERT", "GENERAL", None, None, None, ["homophone_satellite", "qq_bypass"], True),
        ("微心聊，電話聊細節，支持走老闆私人卡，報價算您 11.50 出廠。", "OFFLINE_BYPASS_DIVERT", "GENERAL", 11.50, None, None, ["homophone_heart_wechat"], True),
        ("＋v：tech_factory，把高畫質樣品影片傳給您，線上發不了大文件。", "OFFLINE_BYPASS_DIVERT", "GENERAL", None, None, None, ["fullwidth_plus_v"], True),
        ("平台技術服務費高達五個點，轉線下簽紙質合同，出廠底價 13.20 給您。", "OFFLINE_BYPASS_DIVERT", "GENERAL", 13.20, None, None, ["offline_paper_contract"], True),
        ("電話聯絡：1三8-I234-5678，數字字母混寫防屏蔽，加個微信細聊。", "OFFLINE_BYPASS_DIVERT", "GENERAL", None, None, None, ["mixed_alpha_phone"], True),
        ("看我賣家首頁滾動公告欄有私人手機號，直接打電話，走私單優惠大。", "OFFLINE_BYPASS_DIVERT", "GENERAL", None, None, None, ["profile_banner_divert"], True),

        # 反向對抗陰性控制樣本 (50條，絕對不能誤判為脫離平台！)
        ("我們公司叫威信五金製品廠，官方簽約認證企業，標準出廠報價 13.50。", "NORMAL_COUNTER_OFFER", "HARDWARE_MOLD", 13.50, None, None, ["company_name_威信"], False),
        ("微信支付通道維護中，建議您走平台官方對公轉賬，單價算您 12.80。", "NORMAL_COUNTER_OFFER", "GENERAL", 12.80, None, None, ["payment_channel_wechat"], False),
        ("不要加微信，任何線下交易本廠一概不予承認，在線簽合同最安全，報價 14.00。", "NORMAL_COUNTER_OFFER", "GENERAL", 14.00, None, None, ["anti_divert_assertion"], False),
        ("加微信就算了，平台有監管對大家都好，含稅 14.00，交期 20 天。", "NORMAL_COUNTER_OFFER", "GENERAL", 14.00, None, None, ["negative_suffix_微信算了"], False),
        ("請通過阿里旺旺官方系統提交採購單，避免線下私聊風險，單價 13.10。", "NORMAL_COUNTER_OFFER", "GENERAL", 13.10, None, None, ["official_channel_enforce"], False),
    ]

    dim3_count = 0
    while dim3_count < 120:
        base_item = evasion_templates[dim3_count % len(evasion_templates)]
        idx_str = f"OOD-3-{dim3_count+1:03d}"
        var_text = base_item[0]
        if dim3_count >= len(evasion_templates):
            var_text = f"【渠道通訊備註】{base_item[0]}（對話記錄存檔）"
        samples.append(OODSample(
            id=idx_str,
            dimension="EVASION_OFFLINE",
            industry=base_item[2],
            text=var_text,
            ground_truth_intent=base_item[1],
            has_commercial_content=base_item[3] is not None,
            ground_truth_entities={"price": base_item[3], "moq": base_item[4], "mold_fee": base_item[5], "lead_days": None},
            is_mixed_signal=base_item[3] is not None and base_item[1] != "NORMAL_COUNTER_OFFER",
            adversarial_features=base_item[6],
            risk_escalation_required=base_item[7],
        ))
        dim3_count += 1

    # -------------------------------------------------------------
    # 維度 4: 禮貌拒絕、反諷與虛假讓步 (130 條)
    # -------------------------------------------------------------
    sarcasm_templates = [
        # 商務反諷 (50條)
        ("您這價格太有誠意了，我做不了您另請高明，去別家發財吧。", "CRY_POVERTY_COST_HIKE", "GENERAL", None, None, None, ["sarcastic_courtesy"], False),
        ("這價錢賣給您我不如關門大吉去喝西北風，工人都得集體討薪了。", "CRY_POVERTY_COST_HIKE", "GENERAL", None, None, None, ["hyperbolic_bankruptcy"], False),
        ("您這砍價是按廢鐵稱斤買的吧？連工料電費都不夠！", "CRY_POVERTY_COST_HIKE", "HARDWARE_MOLD", None, None, None, ["scrap_iron_irony"], False),
        ("按您這預算，我們連包裝瓦楞紙的本錢都收不回來，不如我把工廠送您？", "CRY_POVERTY_COST_HIKE", "GENERAL", None, None, None, ["give_factory_sarcasm"], False),
        ("您真是大善人，給的價格剛好夠我們廠付一天的水電費。", "CRY_POVERTY_COST_HIKE", "GENERAL", None, None, None, ["ironic_praise"], False),

        # 虛假讓步 / 條件陷阱 (40條)
        ("行啊，9塊錢賣你，你先訂十萬件全款打過來，我馬上簽字蓋章。", "TIER_VOLUME_BLACKMAIL", "GENERAL", 9.00, 100000, None, ["pseudo_concession_trap"], False),
        ("你要5塊錢一把也可以，傘布我用宣紙糊，保證按時交貨。", "MATERIAL_SPEC_DEGRADE", "GENERAL", 5.00, None, None, ["absurd_material_mockery"], True),
        ("可以按你的價 10.00 接單，模具費先付十萬塊，十年後分批返還。", "TIER_VOLUME_BLACKMAIL", "HARDWARE_MOLD", 10.00, None, 100000.0, ["impossible_mold_rebate"], False),

        # 禮貌商業拒絕與常規還價 (25條)
        ("感謝貴司垂青，該規格我廠常規出廠底價為 13.20，利潤空間有限，望體諒。", "NORMAL_COUNTER_OFFER", "GENERAL", 13.20, None, None, ["polite_refusal_counter"], False),
        ("十分理解貴司預算限制，若首期按 13.00 試單五百件，後續量大再行調價如何？", "NORMAL_COUNTER_OFFER", "GENERAL", 13.00, 500, None, ["polite_trial_counter"], False),

        # 真實終局達成 (15條)
        ("行吧服了你了，就按你說的 12.50，發合同過來吧。", "FINAL_DEAL_CONCESSION", "GENERAL", 12.50, None, None, ["final_deal_concede"], False),
        ("各退一步，12.80 成交，定金到了馬上安排車間排產。", "FINAL_DEAL_CONCESSION", "GENERAL", 12.80, None, None, ["final_compromise_deal"], False),
    ]

    dim4_count = 0
    while dim4_count < 130:
        base_item = sarcasm_templates[dim4_count % len(sarcasm_templates)]
        idx_str = f"OOD-4-{dim4_count+1:03d}"
        var_text = base_item[0]
        if dim4_count >= len(sarcasm_templates):
            var_text = f"【商務應答記錄】{base_item[0]}（回覆日期 2026-10-14）"
        samples.append(OODSample(
            id=idx_str,
            dimension="SARCASM_PSEUDO",
            industry=base_item[2],
            text=var_text,
            ground_truth_intent=base_item[1],
            has_commercial_content=base_item[3] is not None or base_item[4] is not None,
            ground_truth_entities={"price": base_item[3], "moq": base_item[4], "mold_fee": base_item[5], "lead_days": None},
            is_mixed_signal=base_item[3] is not None and base_item[1] != "NORMAL_COUNTER_OFFER",
            adversarial_features=base_item[6],
            risk_escalation_required=base_item[7],
        ))
        dim4_count += 1

    return samples


def save_ood_dataset():
    samples = create_500_ood_samples()
    print(f"Generated {len(samples)} Ground-Truth OOD Benchmark samples.")

    # 1. 寫入 JSON
    os.makedirs("data", exist_ok=True)
    json_path = "data/benchmark_ood_500.json"
    data_dict = [asdict(s) for s in samples]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data_dict, f, ensure_ascii=False, indent=2)
    print(f"Saved to {json_path} ({os.path.getsize(json_path) / 1024:.1f} KB)")

    # 2. 寫入 Python 模組
    os.makedirs("src/benchmark", exist_ok=True)
    py_path = "src/benchmark/ood_500_dataset.py"
    with open(py_path, "w", encoding="utf-8") as f:
        f.write('"""TradeFlow 500-Item Ground-Truth OOD Benchmark Dataset."""\n')
        f.write("import json\nimport os\nfrom typing import List, Dict, Any\n\n")
        f.write("_json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'benchmark_ood_500.json')\n")
        f.write("with open(_json_path, 'r', encoding='utf-8') as _f:\n")
        f.write("    OOD_500_DATASET: List[Dict[str, Any]] = json.load(_f)\n")
    print(f"Saved Python module to {py_path}")

    # 驗證分佈
    from collections import Counter
    dim_dist = Counter(s.dimension for s in samples)
    intent_dist = Counter(s.ground_truth_intent for s in samples)
    print("\n[Dimension Distribution]")
    for k, v in dim_dist.items():
        print(f"  - {k:<20}: {v} samples")
    print("\n[Intent Distribution]")
    for k, v in intent_dist.items():
        print(f"  - {k:<25}: {v} samples")


if __name__ == "__main__":
    save_ood_dataset()
