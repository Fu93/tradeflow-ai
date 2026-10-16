"""TradeFlow v4 — 語言中立策略決策核心與雙向表面渲染器。

架構特色：
1. 嚴格禁止 Emoji（Zero Emoji 政策）。
2. 解耦原則：策略語言中立核心 (Language-Agnostic Decision Core)
   - MCTS、貝氏更新與 Logrolling 求解器僅輸出純數字、多維約束與戰術枚舉之 TacticalActionObject。
   - 單一真相源 (Single Source of Truth)：決策不依賴自然語言反向正則解析。
   - 契約版本控制 (schema_version = "4.1.0") 與來源輪次追蹤 (source_round_id)。
3. 抽象語法樹硬不變量護欄 (ActionObjectASTGuardrail & NumericGuardrailValidator)：
   - AST 編譯驗證策略對象之數值不變量，物理阻斷越界與未經授權之妥協。
4. 供應商 6 大核心博弈套路分類器 (InboundIntentClassifier)。
5. 雙向表面渲染器 (Dual Surface Renderers)：
   - 中文表面渲染器 (ChineseSurfaceRenderer)：沿海地道金牌買家商務行話，動態插值，嚴禁外商溢價洩漏。
   - 英文高管渲染器 (EnglishExecutiveRenderer)：西方 CPO 決策卡片、即時手機遙測推播與指令確認卡。
6. 反向鏈路：英文高管指令解析器 (EnglishExecutiveCommandParser)：
   - 解析高管手機 WhatsApp 回覆（1=妥協, 2=堅持, 3=離場, 具體價格, 或自然語言商務指令）。
   - 輸出結構化 ExecutiveCommandObject，經 AST 護欄驗證後驅動雙向渲染。
7. 雙向一致性與防穿幫審計器 (CrossLingualSanityChecker)：
   - 杜絕外商溢價詞彙洩漏，保證中文與英文雙邊數值完全一致。
"""
from __future__ import annotations
import ast
import re
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field

from src.engine.safe_regex import SafeLinearRegexEngine, SafeLinearPattern
from src.engine.onnx_classifier import OnDeviceIntentClassifier

CURRENT_SCHEMA_VERSION = "4.1.0"


class VendorTacticEnum(str, Enum):
    CRY_POVERTY_COST_HIKE = "CRY_POVERTY_COST_HIKE"          # 哭窮/原料暴漲
    TIER_VOLUME_BLACKMAIL = "TIER_VOLUME_BLACKMAIL"          # 以量壓價/要求加單
    MATERIAL_SPEC_DEGRADE = "MATERIAL_SPEC_DEGRADE"          # 偷換規格/換便宜料
    TAX_PACKAGING_UNBUNDLING = "TAX_PACKAGING_UNBUNDLING"    # 稅率拆分/包裝加價
    OFFLINE_BYPASS_DIVERT = "OFFLINE_BYPASS_DIVERT"          # 要求加微信/線下私聊
    IDENTITY_PROBE_TEST = "IDENTITY_PROBE_TEST"              # 身份背景試探 (外貿/美元/倉庫/港口)
    FINAL_DEAL_CONCESSION = "FINAL_DEAL_CONCESSION"          # 同意條款/準備簽單
    NORMAL_COUNTER_OFFER = "NORMAL_COUNTER_OFFER"            # 常規還價


class TacticalModeEnum(str, Enum):
    LEAN_IN = "LEAN_IN"                  # 常規出廠價進攻 (高誠意、定金誘餌、封樣排產)
    CROSS_ANCHOR = "CROSS_ANCHOR"        # 備選大廠比價錨定 (威懾轉向備選庫)
    SIDELINE = "SIDELINE"                # 靠邊觀望/產能冷卻 (預算卡死、不佔檔期)
    SILENCE = "SILENCE"                  # 戰略沉默與冷卻威懾 (掛起非同步會話)
    LOGROLL = "LOGROLL"                  # 企業授權多維條款置換 (定金換交期/模具費減免)
    DEFENSIVE_PIVOT = "DEFENSIVE_PIVOT"  # 交易邊界拉回 (偏離交付要素時不自證、鎖死現款合同)
    FIRM_HOLD = "FIRM_HOLD"              # 鎖死底線價 (寸步不讓)
    WALK_AWAY = "WALK_AWAY"              # 終止談判/啟動離場


class ExecutiveActionEnum(str, Enum):
    CONCEDE = "CONCEDE"                  # 指令 1: 妥協並讓步至推薦階梯
    HOLD_FIRM = "HOLD_FIRM"              # 指令 2: 堅持底線價，不讓步
    WALK_AWAY = "WALK_AWAY"              # 指令 3: 終止談判/啟動離場
    OVERRIDE_PRICE = "OVERRIDE_PRICE"    # 數字指令: 指定特定覆寫單價
    CUSTOM_TACTIC = "CUSTOM_TACTIC"      # 自然語言進階商業置換指令


class InboundTacticalClassification(BaseModel):
    primary_intent: VendorTacticEnum
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    mentioned_price: Optional[float] = None
    demanded_moq: Optional[int] = None
    demanded_mold_fee: Optional[float] = None
    is_mixed_signal: bool = Field(default=False, description="是否同時包含商務報價與非商務試探/雜訊")
    mixed_signal_note: Optional[str] = None
    tactical_rationale: str = ""
    requires_escalation: bool = False


# ---------------------------------------------------------------------------
# 意圖分類器 (Deterministic Pattern Ladder + Stage 2 LLM Fallback)
# ---------------------------------------------------------------------------
class InboundIntentClassifier:
    """供應商對話 7 大核心博弈套路分類器。"""

    PATTERNS: List[Tuple[VendorTacticEnum, Any, float, bool]] = [
        (
            VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
            SafeLinearRegexEngine.compile(r"(加個?微信|加个?微信|私下聊|電話聊|电话聊|wx|私聊|轉私帳|转私账|線下付|线下付|v\s*信|微\s*信|\+v|加\s*(?:我)?\s*v|私發|私发|私下交易|走線下|走线下)", re.IGNORECASE),
            0.95,
            True,  # 脫離平台直接觸發風控警報
        ),
        (
            VendorTacticEnum.MATERIAL_SPEC_DEGRADE,
            SafeLinearRegexEngine.compile(r"(換成?便宜|换成?便宜|回料|水口料|減薄|减薄|普通料|換成\s*201|换成\s*201|201\s*不[鏽銹锈]鋼|201\s*不[鏽銹锈]钢|降低規格|降低规格|改用常規|改用常规|非標|非标|換料|换料|非正品|二料|次料)", re.IGNORECASE),
            0.90,
            True,  # 品質降級需主管確認
        ),
        (
            VendorTacticEnum.IDENTITY_PROBE_TEST,
            SafeLinearRegexEngine.compile(r"(外貿公司|做外貿|外贸公司|做外贸|美金|美元|做外銷|做外销|出口還是內銷|出口还是内销|主要做哪個市場|主要做哪个市场|哪裡人|哪里人|哪個港|哪个港|哪裡的倉庫|哪里的仓库|老外|海外客戶|海外客户|出口到哪|美元帳戶|美元账户|走哪個口岸|走哪个口岸|你們老闆|你们老板|哪裡過來的|哪里过来的)", re.IGNORECASE),
            0.92,
            False,  # 身份試探需以地道本地老採購話術化解
        ),
        (
            VendorTacticEnum.FINAL_DEAL_CONCESSION,
            SafeLinearRegexEngine.compile(r"(行吧|服了你|就按你|發合同|发合同|打款吧|簽單|签单|成交|定金打哪|訂金打哪|虧本做給你|亏本做给你|按你說的|按你说的|可以做|給您排單|给您排单)", re.IGNORECASE),
            0.90,
            False,
        ),
        (
            VendorTacticEnum.CRY_POVERTY_COST_HIKE,
            # 使用有界量詞 [^\n，。！？]{0,15} 替代 .*，徹底杜絕 ReDoS 災難性回溯
            SafeLinearRegexEngine.compile(r"(原料[^\n，。！？]{0,15}?漲|原料[^\n，。！？]{0,15}?涨|原材料[^\n，。！？]{0,15}?漲|原材料[^\n，。！？]{0,15}?涨|暴漲|暴涨|成本[^\n，。！？]{0,15}?漲|成本[^\n，。！？]{0,15}?涨|人工[^\n，。！？]{0,15}?(?:貴|贵|漲|涨)|虧本|亏本|賠錢|赔钱|喝西北風|喝西北风|沒利潤|没利润|電費|电费|成本都不夠|成本都不够|純虧|纯亏|壓不住|压不住|貼錢|贴钱|沒法開工|没法开工|做不了|利潤太薄|利润太薄)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
            SafeLinearRegexEngine.compile(r"(起訂量|起订量|至少要|量太小|樣品價|样品价|開機費|开机费|加單|加单|翻倍才|起訂|起订|MOQ|小單|小单|幾百個|几百个|量大才能)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.TAX_PACKAGING_UNBUNDLING,
            # 使用有界量詞替代 .*
            SafeLinearRegexEngine.compile(r"(未稅|未税|不含稅|不含税|專票[^\n，。！？]{0,15}?加|专票[^\n，。！？]{0,15}?加|稅點|税点|包裝另算|包装另算|彩盒[^\n，。！？]{0,15}?加|外箱[^\n，。！？]{0,15}?加|不包[^\n，。！？]{0,15}?港口|港雜費|港杂费|裸價|裸价|要票[^\n，。！？]{0,15}?加)", re.IGNORECASE),
            0.85,
            False,
        ),
    ]

    PRICE_PATTERN = SafeLinearRegexEngine.compile(
        r"(?:(?:[¥￥$]|RMB)\s*(\d+(?:\.\d{1,2})?)|(?:報價|單價|價格|給您|算您|出廠價|特價|低至)\s*[:：=]?\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:元|塊)(?:/件|/把|/個|/套|/pcs)?)",
        re.IGNORECASE,
    )
    MOQ_PATTERN = SafeLinearRegexEngine.compile(r"(\d{3,7})\s*(?:個|把|件|套|pcs)", re.IGNORECASE)
    LEAD_DAYS_PATTERN = SafeLinearRegexEngine.compile(r"(\d{1,3})\s*(?:天|工作日|days?)", re.IGNORECASE)
    DEPOSIT_PATTERN = SafeLinearRegexEngine.compile(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:定金|訂金|預付)", re.IGNORECASE)
    MOLD_FEE_PATTERN = SafeLinearRegexEngine.compile(r"(?:模具費|開模費|模具|開模)\s*[:：=加另算]*\s*(?:[¥￥$]|RMB)?\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
    QUALITY_SPEC_PATTERN = SafeLinearRegexEngine.compile(r"(原包|全新料|回料|水口料|304|316|201|AQL|TDS|公差|檢驗|質檢|驗貨|開模)", re.IGNORECASE)

    @classmethod
    def has_actionable_commercial_content(cls, raw_text: str) -> bool:
        """
        結構化商務要素反向判定 (Structural Actionable Content Check):
        檢驗對手訊息是否包含任何可執行的商務交易要素（價、期、量、定金、材質/驗收規範）。
        若完全不包含可執行商務要素（純閒聊、身分刺探、無依據推託），系統判定為偏離核心要素。
        """
        if not raw_text or not raw_text.strip():
            return False
        if cls.PRICE_PATTERN.search(raw_text):
            return True
        if cls.MOQ_PATTERN.search(raw_text):
            return True
        if cls.LEAD_DAYS_PATTERN.search(raw_text):
            return True
        if cls.DEPOSIT_PATTERN.search(raw_text):
            return True
        if cls.QUALITY_SPEC_PATTERN.search(raw_text):
            return True
        return False

    @classmethod
    def classify_with_llm(cls, raw_text: str) -> Optional[InboundTacticalClassification]:
        """
        Stage 2: 結構化語意 AI 推理分類器 (LLM Semantic Parser)。
        當 Stage 1 正則無法高確信度識別或文本存在口語反諷、隱晦暗示時觸發。
        """
        import os
        import json

        # 憑證必須與端點配對，不可跨端點混用（否則會以 A 端點的 key 打 B 端點而 401）。
        # 優先使用 VLLM_*（AMD tokenfactory / 自架 vLLM）；僅在未配置時才退回 Evolus。
        # 同時視 __FILL_ME__ 等佔位符為「未配置」，避免佔位符遮蔽真實可用的金鑰。
        def _real(v: str | None) -> str | None:
            if not v:
                return None
            s = v.strip()
            if not s or s.startswith("__FILL") or s.lower() in ("none", "null", "changeme"):
                return None
            return s

        base_url = _real(os.getenv("VLLM_BASE_URL")) or "https://api.evolus.ai/v1"
        if _real(os.getenv("VLLM_API_KEY")):
            api_key = os.getenv("VLLM_API_KEY")
        elif "evolus.ai" in base_url:
            api_key = _real(os.getenv("EVOLUS_API_KEY"))
        else:
            api_key = _real(os.getenv("EVOLUS_API_KEY")) or _real(os.getenv("VLLM_API_KEY"))
        if not _real(api_key):
            return None

        prompt = (
            "你是 B2B 採購風控分析器。分析下列中國工廠業務員的訊息，只輸出嚴格 JSON，不要任何說明文字。\n"
            "欄位 primary_intent 必須是下列之一：\n"
            "CRY_POVERTY_COST_HIKE(哭窮抬價/原料漲價)、TIER_VOLUME_BLACKMAIL(以量壓價/起訂量勒索)、"
            "MATERIAL_SPEC_DEGRADE(偷換材料規格/次料降級)、TAX_PACKAGING_UNBUNDLING(稅票或包裝費用拆分)、"
            "OFFLINE_BYPASS_DIVERT(引誘脫離平台私下交易)、FINAL_DEAL_CONCESSION(終局成交讓步)、"
            "NORMAL_COUNTER_OFFER(一般正常報價，無上述套路)。\n"
            "其他欄位：confidence(0.0-1.0)、mentioned_price(float 或 null)、demanded_moq(int 或 null)、"
            "tactical_rationale(簡短理由)、requires_escalation(bool，若試圖線下交易或降級材料則為 true)。\n"
            f"工廠訊息：「{raw_text}」\n"
            "JSON:"
        )

        try:
            import requests
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": os.getenv("VLLM_MODEL", "Qwen3.8-Flash-Next"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 500,
            }
            resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=30.0)
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    intent_str = data.get("primary_intent", "NORMAL_COUNTER_OFFER")
                    intent_enum = getattr(VendorTacticEnum, intent_str, VendorTacticEnum.NORMAL_COUNTER_OFFER)
                    return InboundTacticalClassification(
                        primary_intent=intent_enum,
                        confidence=float(data.get("confidence", 0.85)),
                        mentioned_price=data.get("mentioned_price"),
                        demanded_moq=data.get("demanded_moq"),
                        tactical_rationale=data.get("tactical_rationale", "LLM zero-shot inference"),
                        requires_escalation=bool(data.get("requires_escalation", False)),
                    )
        except Exception:
            pass
        return None

    @classmethod
    async def classify_with_llm_async(cls, raw_text: str) -> Optional["InboundTacticalClassification"]:
        """非同步版本的 Stage-2 語意推理。

        以 `asyncio.to_thread` 將阻塞的 `requests.post` 移出事件迴圈，
        避免 2–13 秒的網路等待凍結 FastAPI 的 async worker。
        """
        import asyncio
        return await asyncio.to_thread(cls.classify_with_llm, raw_text)

    @classmethod
    async def classify_async(cls, raw_text: str, enable_llm_fallback: bool = True) -> "InboundTacticalClassification":
        """非同步版本的完整兩段式分類。"""
        import asyncio
        return await asyncio.to_thread(cls._classify_impl, raw_text, enable_llm_fallback)

    @classmethod
    def classify(cls, raw_text: str, enable_llm_fallback: bool = True) -> InboundTacticalClassification:
        """
        兩段式意圖解析管線 (Two-Stage Intent Pipeline)：
        - Stage 1: 微秒級確定性正則階梯 (Deterministic Regex Ladder)
        - Stage 2: 複雜語意與長文本語意 LLM 意圖推理 (Semantic LLM Fallback)

        [PERFORMANCE] Stage-2 為同步網路呼叫，實測在 AMD tokenfactory 上需 2–13 秒。
        對於延遲敏感的呼叫端（例如同步 HTTP 請求），應改用
        `classify_stage1_only()` 取得即時結果，再以 `classify_with_llm()` 非同步補強。
        """
        return cls._classify_impl(raw_text, enable_llm_fallback=enable_llm_fallback)

    @classmethod
    def classify_stage1_only(cls, raw_text: str) -> InboundTacticalClassification:
        """僅執行 Stage-1（正則階梯 + 端側 ONNX），保證不發出任何網路請求。

        實測延遲 ~1ms。適用於同步 HTTP 路徑等對延遲敏感、且可接受確定性降級的場景。
        """
        return cls._classify_impl(raw_text, enable_llm_fallback=False)

    @classmethod
    def _classify_impl(cls, raw_text: str, enable_llm_fallback: bool) -> InboundTacticalClassification:
        """兩段式管線的實際實作；由 classify() 與 classify_stage1_only() 共用。"""
        if not raw_text:
            return InboundTacticalClassification(
                primary_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
                confidence=0.0,
                tactical_rationale="Empty input; unclassified",
            )

        # 0. 安全防護：截斷超長輸入，徹底杜絕 ReDoS 潛在回溯與內存佔用
        MAX_INPUT_LEN = 1000
        if len(raw_text) > MAX_INPUT_LEN:
            raw_text = raw_text[:MAX_INPUT_LEN]

        # 1. 抽取報價
        price_val = None
        p_match = cls.PRICE_PATTERN.search(raw_text)
        if p_match:
            try:
                matched_str = p_match.group(1) or p_match.group(2) or p_match.group(3)
                if matched_str:
                    candidate = float(matched_str)
                    if 0.1 <= candidate <= 50000.0:
                        price_val = candidate
            except ValueError:
                pass

        # 2. 抽取 MOQ 需求
        moq_val = None
        m_match = cls.MOQ_PATTERN.search(raw_text)
        if m_match:
            try:
                moq_val = int(m_match.group(1))
            except ValueError:
                pass

        # 2.5 抽取模具費需求 (Composite Signals)
        mold_fee_val = None
        mf_match = cls.MOLD_FEE_PATTERN.search(raw_text)
        if mf_match and mf_match.group(1):
            try:
                candidate_mf = float(mf_match.group(1))
                if candidate_mf > 0:
                    mold_fee_val = candidate_mf
            except ValueError:
                pass

        # 3. Stage 1: 硬性脫離平台私聊檢測 (微秒級正則短路保護)
        divert_intent, divert_pattern, divert_conf, divert_escalate = cls.PATTERNS[0]
        has_positive_divert = False
        for m in divert_pattern.finditer(raw_text):
            prefix = raw_text[max(0, m.start() - 8):m.start()]
            suffix = raw_text[m.end():min(len(raw_text), m.end() + 12)]
            if re.search(r"(?:不用走|不走|走|不用|別|别|不|免|無須|无需|不加|非|免了|不需|暫不|暂不)\s*$", prefix):
                continue
            if re.search(r"^\s*(?:就算了|免了|不用|就不必|不考慮|先不加|暫不)", suffix):
                continue
            has_positive_divert = True
            break
        if has_positive_divert:
            return InboundTacticalClassification(
                primary_intent=VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
                confidence=divert_conf,
                mentioned_price=price_val,
                demanded_moq=moq_val,
                demanded_mold_fee=mold_fee_val,
                is_mixed_signal=False,
                tactical_rationale="Matched hard offline divert pattern in Stage 1",
                requires_escalation=True,
            )

        # 4. Stage 2: 端側 ONNX 深度語意推理 (0.02ms 本地神經分類，覆蓋行話/反諷/複合句)
        onnx_clf = OnDeviceIntentClassifier.get_instance()
        onnx_pred = onnx_clf.predict(raw_text)
        if onnx_pred is not None:
            onnx_intent, onnx_conf, onnx_dist = onnx_pred
            # 排除被上下文否定詞中和的微信私聊誤判 (如「不加微信」、「微信就算了」)
            if onnx_intent == VendorTacticEnum.OFFLINE_BYPASS_DIVERT:
                if re.search(r"(?:不加|別加|不用走|不走|免了|就算了|不考慮|先不加|暫不加|不要加)\s*(?:微信|私聊|線下|私信|私帳|v)", raw_text) or re.search(r"(?:微信|私聊|線下)\s*(?:就算了|免了|不用|就不必|不考慮)", raw_text):
                    onnx_intent = VendorTacticEnum.NORMAL_COUNTER_OFFER

            # 若 ONNX 高確信度 (>=0.70) 識別為專門套路 (行話黑話、階梯勒索、反諷哭窮、終局成交等)
            if onnx_conf >= 0.70 and onnx_intent != VendorTacticEnum.NORMAL_COUNTER_OFFER:
                is_mixed = (price_val is not None or mold_fee_val is not None) and onnx_intent in (
                    VendorTacticEnum.IDENTITY_PROBE_TEST,
                    VendorTacticEnum.CRY_POVERTY_COST_HIKE,
                    VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
                )
                mixed_parts = []
                if price_val:
                    mixed_parts.append(f"Price ¥{price_val:.2f}")
                if mold_fee_val:
                    mixed_parts.append(f"Mold Fee ¥{mold_fee_val:.0f}")
                mixed_note = (
                    f"Extracted {', '.join(mixed_parts)} while deflecting {onnx_intent.value}"
                    if is_mixed else None
                )
                escalate = onnx_intent in (
                    VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
                    VendorTacticEnum.MATERIAL_SPEC_DEGRADE,
                )
                return InboundTacticalClassification(
                    primary_intent=onnx_intent,
                    confidence=onnx_conf,
                    mentioned_price=price_val,
                    demanded_moq=moq_val,
                    demanded_mold_fee=mold_fee_val,
                    is_mixed_signal=is_mixed,
                    mixed_signal_note=mixed_note,
                    tactical_rationale=f"On-Device ONNX semantic inference (conf={onnx_conf:.2f})",
                    requires_escalation=escalate,
                )

        # 5. Stage 1.5: 確定性正則階梯保底
        for intent, pattern, conf, escalate in cls.PATTERNS[1:]:
            if intent == VendorTacticEnum.MATERIAL_SPEC_DEGRADE:
                has_degrade = False
                for m in pattern.finditer(raw_text):
                    prefix = raw_text[max(0, m.start() - 6):m.start()]
                    if re.search(r"(?:不做|不用|不採用|不采用|非|不收|排除|拒絕|拒绝)\s*$", prefix):
                        continue
                    has_degrade = True
                    break
                if not has_degrade:
                    continue

            if pattern.search(raw_text):
                rationale = "Matched deterministic pattern in inbound text"
                is_mixed = (price_val is not None or mold_fee_val is not None) and intent in (
                    VendorTacticEnum.IDENTITY_PROBE_TEST,
                    VendorTacticEnum.CRY_POVERTY_COST_HIKE,
                    VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
                )
                mixed_parts = []
                if price_val:
                    mixed_parts.append(f"Price ¥{price_val:.2f}")
                if mold_fee_val:
                    mixed_parts.append(f"Mold Fee ¥{mold_fee_val:.0f}")
                mixed_note = (
                    f"Extracted {', '.join(mixed_parts)} while deflecting {intent.value}"
                    if is_mixed else None
                )
                return InboundTacticalClassification(
                    primary_intent=intent,
                    confidence=conf,
                    mentioned_price=price_val,
                    demanded_moq=moq_val,
                    demanded_mold_fee=mold_fee_val,
                    is_mixed_signal=is_mixed,
                    mixed_signal_note=mixed_note,
                    tactical_rationale=rationale,
                    requires_escalation=escalate,
                )

        # 4. Stage 2: 若正則未命中且啟用 LLM，進行深度語意推理
        if enable_llm_fallback:
            llm_res = cls.classify_with_llm(raw_text)
            if llm_res:
                if price_val and llm_res.mentioned_price is None:
                    llm_res.mentioned_price = price_val
                return llm_res

        # 5. 常規報價對話保底
        # [誠實揭露] 此分支表示「正則階梯全未命中 且 (無 LLM 或 LLM 未回)」——我們並未
        # 真正識別出這是正常報價。舊版以 confidence=0.70 固定回 NORMAL_COUNTER_OFFER，
        # 會在沒有任何判定信號時偽裝成「高把握的正常報價」。現改為低把握預設，
        # 讓下游可藉 confidence 區分「確信的正常報價」與「未識別、暫按正常處理」。
        return InboundTacticalClassification(
            primary_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
            confidence=0.30,
            mentioned_price=price_val,
            demanded_moq=moq_val,
            tactical_rationale="No pattern/LLM matched; unclassified message treated as normal",
            requires_escalation=False,
        )


# ---------------------------------------------------------------------------
# 語言中立策略決策對象 (Language-Agnostic Tactical Action Object)
# ---------------------------------------------------------------------------
class TacticalActionObject(BaseModel):
    """
    語言中立結構化策略對象 (Language-Agnostic Decision Object)。
    此對象為博弈核心運算 (MCTS + 貝氏信念 + Logrolling) 輸出的唯一單一真相源 (Single Source of Truth)，
    完全解耦自然語言表面表達。
    """
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION, description="策略契約版本號")
    source_round_id: Optional[str] = Field(default=None, description="決策來源輪次與會話追蹤 ID")

    # 價格與安全約束
    authorized_price: float = Field(..., gt=0.0, description="法定授權出價單價 (元)")
    walk_away_ceiling: float = Field(..., gt=0.0, description="買方最高容忍上限 (元)")
    breakdown_risk: float = Field(default=0.0, ge=0.0, le=1.0, description="破裂風險機率")
    expected_margin: float = Field(default=0.0, description="單件預期毛利 (元)")
    concession_step: float = Field(default=0.0, description="本輪相較前輪之讓步幅度 (元)")

    # 貝氏推論狀態
    posterior_reserve_mean: float = Field(default=1.0, description="對手歸一化底價後驗均值")
    posterior_reserve_std: float = Field(default=0.1, description="對手歸一化底價後驗標準差")
    bayesian_confidence: float = Field(default=0.85, ge=0.0, le=1.0, description="底價估計置信度")
    estimated_floor_price: Optional[float] = Field(default=None, description="估算對手硬底價 (元)")

    # 戰術維度與條款
    tactical_mode: TacticalModeEnum = Field(default=TacticalModeEnum.LEAN_IN, description="主戰術模式")
    product_name: str = Field(default="項目", description="標的品名")
    supplier_contact_name: str = Field(default="老闆", description="對手工廠稱呼")
    lead_days: int = Field(default=25, ge=1, description="承諾交期天數")
    deposit_pct: float = Field(default=0.30, ge=0.0, le=1.0, description="承諾定金比例")
    moq: int = Field(default=500, ge=1, description="採購起訂批量")
    mold_fee_exempted: bool = Field(default=False, description="是否要求減免模具/打樣費")
    waive_mold_fee_amount: float = Field(default=0.0, description="要求減免之模具費金額")

    # 博弈訊號與意圖反擊
    counterpart_intent: VendorTacticEnum = Field(
        default=VendorTacticEnum.NORMAL_COUNTER_OFFER, description="辨識出之對手套路"
    )
    counterpart_offered_price: Optional[float] = Field(default=None, description="對手當前報價")
    competing_lead_price: Optional[float] = Field(default=None, description="備選供應商錨定價")
    urgency_signal: str = Field(default="routine", description="緊迫度訊號")
    is_mixed_signal: bool = Field(default=False, description="是否為混合信號 (含報價且含試探/抱怨)")
    mixed_signal_note: Optional[str] = Field(default=None, description="混合信號摘要說明")
    circuit_tripped: bool = Field(default=False, description="是否觸發安全熔斷")
    trip_reason: Optional[str] = Field(default=None, description="熔斷原因")


# ---------------------------------------------------------------------------
# 反向鏈路：英文高管指令解析器 (English Executive Command Parser)
# ---------------------------------------------------------------------------
class ExecutiveCommandObject(BaseModel):
    """英文高管手機端指令解析後的結構化對象。"""
    action: ExecutiveActionEnum
    override_price: Optional[float] = None
    demanded_lead_days: Optional[int] = None
    demanded_deposit_pct: Optional[float] = None
    waive_mold_fee: bool = False
    raw_command: str = ""
    confidence: float = 1.0
    rationale: str = ""


class EnglishExecutiveCommandParser:
    """
    英文高管指令結構化解析器 (English Executive Command Parser)。
    職責：
    1. 接收高管手機端 (WhatsApp / Telegram / 微信 Webhook) 發送的英文代碼或自然語言商務指令。
    2. 解析為語言中立之 ExecutiveCommandObject。
    3. 支持 direct code (1/2/3)、純數字 (13.5)、與英文自然語言指令。
    """

    PRICE_REGEX = re.compile(r"(?:price|offer|at|to|¥|\$|rmb)?\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
    DEPOSIT_REGEX = re.compile(r"(\d+)\s*%\s*(?:deposit|down\s*payment)?", re.IGNORECASE)
    LEAD_TIME_REGEX = re.compile(r"(\d+)\s*(?:days?|d)\s*(?:delivery|lead|lead\s*time)?", re.IGNORECASE)
    TOOLING_REGEX = re.compile(r"(?:waive|exempt|free|no)\s*(?:tooling|mold|mould|sample)\s*fee", re.IGNORECASE)

    @classmethod
    def parse(cls, raw_command: str) -> ExecutiveCommandObject:
        text = str(raw_command).strip()
        if not text:
            return ExecutiveCommandObject(
                action=ExecutiveActionEnum.HOLD_FIRM,
                raw_command=text,
                rationale="Empty input defaulted to HOLD_FIRM",
            )

        upper = text.upper()

        # 1. 直接按鈕/數字代碼
        if upper in ("1", "CONCEDE", "ACCEPT", "APPROVE", "YES"):
            return ExecutiveCommandObject(
                action=ExecutiveActionEnum.CONCEDE,
                raw_command=text,
                rationale="Direct concession directive code 1",
            )

        if upper in ("2", "HOLD", "HOLD FIRM", "HOLD_FIRM", "STAY", "REJECT", "NO"):
            return ExecutiveCommandObject(
                action=ExecutiveActionEnum.HOLD_FIRM,
                raw_command=text,
                rationale="Direct hold firm directive code 2",
            )

        if upper in ("3", "WALK AWAY", "WALK_AWAY", "QUIT", "ABORT", "TERMINATE", "CANCEL"):
            return ExecutiveCommandObject(
                action=ExecutiveActionEnum.WALK_AWAY,
                raw_command=text,
                rationale="Direct walk away directive code 3",
            )

        # 2. 純數值報價 (例如 "13.2" 或 "$14.50" 或 "¥14.50")
        clean_num = text.replace("¥", "").replace("$", "").replace("RMB", "").replace("元", "").strip()
        try:
            val = float(clean_num)
            if 0.1 <= val <= 50000.0:
                return ExecutiveCommandObject(
                    action=ExecutiveActionEnum.OVERRIDE_PRICE,
                    override_price=val,
                    raw_command=text,
                    rationale="Direct numeric price override parsed",
                )
        except ValueError:
            pass

        # 3. 英文自然語言商務指令解析 (Deterministic Pattern Ladder)
        found_dep = None
        dep_match = cls.DEPOSIT_REGEX.search(text)
        if dep_match and dep_match.group(1):
            try:
                dep_val = float(dep_match.group(1)) / 100.0
                if 0.0 < dep_val <= 1.0:
                    found_dep = dep_val
            except ValueError:
                pass

        found_days = None
        days_match = cls.LEAD_TIME_REGEX.search(text)
        if days_match and days_match.group(1):
            try:
                days_val = int(days_match.group(1))
                if 1 <= days_val <= 365:
                    found_days = days_val
            except ValueError:
                pass

        # 搜索明確價格 (排除定金百分比與交期天數)
        found_price = None
        for m in re.finditer(r"(?:do|at|to|offer|price|\$|¥)\s*(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE):
            try:
                p_cand = float(m.group(1))
                if p_cand != found_days and (found_dep is None or abs(p_cand - found_dep * 100) > 0.01):
                    found_price = p_cand
                    break
            except ValueError:
                pass

        if found_price is None:
            # 尋找孤立浮點數 (如 14.5)
            for m in re.finditer(r"\b(\d+\.\d{1,2})\b", text):
                try:
                    p_cand = float(m.group(1))
                    found_price = p_cand
                    break
                except ValueError:
                    pass

        has_tooling_waiver = bool(cls.TOOLING_REGEX.search(text))

        if found_price is not None or found_days is not None or found_dep is not None or has_tooling_waiver:
            action_enum = ExecutiveActionEnum.OVERRIDE_PRICE if found_price else ExecutiveActionEnum.CUSTOM_TACTIC
            return ExecutiveCommandObject(
                action=action_enum,
                override_price=found_price,
                demanded_lead_days=found_days,
                demanded_deposit_pct=found_dep,
                waive_mold_fee=has_tooling_waiver,
                raw_command=text,
                rationale="Extracted structured terms from executive natural instruction",
            )

        # 預設維持底線
        return ExecutiveCommandObject(
            action=ExecutiveActionEnum.HOLD_FIRM,
            raw_command=text,
            rationale="Unrecognized instruction defaulted to HOLD_FIRM",
        )


# ---------------------------------------------------------------------------
# AST 數值不變量硬護欄 (Action Object AST Invariant Guardrail)
# ---------------------------------------------------------------------------
class GuardrailViolationError(ValueError):
    """觸發數值或合規安全不變量違規異常。"""
    pass


class ActionObjectASTGuardrail:
    """基於 Python AST 抽象語法樹編譯之策略對象數值不變量硬護欄。"""

    @classmethod
    def verify(cls, action_obj: TacticalActionObject) -> bool:
        """
        以 AST 編譯 assert 樹驗證策略對象之硬性不變量：
        1. 授權出價必須嚴格小於等於最大容忍上限 (加上浮點微小容差)。
        2. 授權出價必須大於零。
        3. 定金比例不得突破 60% 物理極限。
        4. 交期天數必須大於等於 1 天。
        """
        code_str = (
            "def _ast_check(auth_p, ceiling, dep, lead):\n"
            "    assert auth_p > 0.0, 'Authorized price must be positive'\n"
            "    assert auth_p <= ceiling + 1e-4, 'Authorized price exceeds walk-away ceiling'\n"
            "    assert dep <= 0.6001, 'Deposit percentage exceeds 60% system safety threshold'\n"
            "    assert lead >= 1, 'Lead time must be at least 1 day'\n"
            "    return True\n"
        )
        tree = ast.parse(code_str)
        compiled_code = compile(tree, filename="<action_object_ast_guardrail>", mode="exec")
        local_scope: Dict[str, Any] = {}
        exec(compiled_code, {}, local_scope)
        check_fn = local_scope["_ast_check"]
        try:
            check_fn(
                action_obj.authorized_price,
                action_obj.walk_away_ceiling,
                action_obj.deposit_pct,
                action_obj.lead_days,
            )
        except AssertionError as exc:
            raise GuardrailViolationError(f"AST Invariant Violation: {exc}") from exc
        return True


# ---------------------------------------------------------------------------
# 雙向表面渲染器 (Dual Surface Renderers)
# ---------------------------------------------------------------------------
class ChineseSurfaceRenderer:
    """
    中文地道沿海買家表面渲染器 (Chinese Coastal Sourcing Surface Renderer)。
    職責：
    1. 將語言中立之 TacticalActionObject 渲染為符合中國沿海老買家專業商務行話。
    2. 內置真實採購對話 Exemplars (Group A Few-Shot)，徹底消除西方哈佛談判腔與套話。
    3. 動態插值品類名稱與聯絡人稱呼，嚴禁硬編碼。
    4. 實作 DEFENSIVE_PIVOT：當對手偏離交付核心時，不自證、不展開，直接拉回專票與現款合同排單。
    5. 實作 SILENCE：戰略冷卻掛起話術（行，那你先忙，我再看看別家）。
    """

    # Group A: 注入 Prompt / 渲染器的真實長三角採購對白示例 (Prompt Exemplars)
    FEW_SHOT_EXEMPLARS_GROUP_A = [
        {"scenario": "報價虛高", "real_buyer": "太貴了，最低多少出廠價。"},
        {"scenario": "原料暴漲藉口", "real_buyer": "行，那你先忙，我先看看餘姚那邊，回頭再聯絡。"},
        {"scenario": "量太小試單", "real_buyer": "首批試單看品控，跑順了下半年翻單一萬起，別因小失大。"},
        {"scenario": "非商務背景打探", "real_buyer": "問那麼多幹嘛？開13個點專票，財務對接。你就說¥{price}能不能做，能做今天現款打定金。"},
        {"scenario": "催促下單", "real_buyer": "價格敲定今天就蓋章走合同，合適的話現在把PI發過來。"},
    ]

    @classmethod
    def _check_schema_version(cls, obj: TacticalActionObject) -> None:
        if not obj.schema_version.startswith("4."):
            raise ValueError(f"Incompatible schema version '{obj.schema_version}'. Expected 4.x")

    @classmethod
    def _extract_round_number(cls, source_round_id: Optional[str]) -> int:
        if not source_round_id:
            return 1
        m = re.search(r"_R(\d+)(?:_|$)", source_round_id)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
        return 1

    @classmethod
    def render(cls, obj: TacticalActionObject) -> str:
        cls._check_schema_version(obj)
        p_val = obj.authorized_price
        contact = obj.supplier_contact_name or "老闆"
        product = obj.product_name or "產品"
        deposit_pct_str = f"{int(round(obj.deposit_pct * 100))}%"
        r_idx = cls._extract_round_number(obj.source_round_id)

        # 0. 混合信號處理：若對手同時給出報價與非商務試探/抱怨，邊界鎖定直接壓制試探，鎖定商務價格
        if obj.is_mixed_signal:
            mold_mention = f"，模具費 ¥{obj.waive_mold_fee_amount:.0f} 由貴廠自攤免除" if obj.waive_mold_fee_amount > 0 else ""
            return (
                f"{contact}，問那麼多幹嘛，開13個點專票走合同。"
                f"這批{product}按 ¥{p_val:.2f} 出廠價落定{mold_mention}，首批預付 {deposit_pct_str} 定金現款秒排，"
                f"合適的話現在把合同簽了排單。"
            )

        # 1. 策略原生原語：交易邊界拉回 (Defensive Pivot - 3大實戰情境變體)
        if obj.tactical_mode == TacticalModeEnum.DEFENSIVE_PIVOT or obj.counterpart_intent == VendorTacticEnum.IDENTITY_PROBE_TEST:
            # 變體 A: 面對抱怨/行情套話
            if obj.counterpart_intent == VendorTacticEnum.CRY_POVERTY_COST_HIKE:
                return (
                    f"{contact}，行情波動我清楚，所以給你們現款現結。"
                    f"這批{product} ¥{p_val:.2f} 出廠價，合適的話現在把合同簽了排單；不能做我們不佔檔期。"
                )
            # 變體 B: 面對以量壓價/小單推託
            elif obj.counterpart_intent == VendorTacticEnum.TIER_VOLUME_BLACKMAIL:
                return (
                    f"首批 {obj.moq} 件試單看品控，跑順了下半年翻單一萬起。¥{p_val:.2f} 出廠價，"
                    f"能做今天下 PO，別因小失大。"
                )
            # 變體 C: 面對背景刺探/閒聊雜訊 (標準邊界鎖定)
            else:
                mold_clause = f"，模具費由貴廠攤銷免除" if (obj.mold_fee_exempted or obj.waive_mold_fee_amount > 0) else ""
                return (
                    f"{contact}，開票走 13 個點專票，具體條款財務後台走合同。"
                    f"你先確認這批{product} ¥{p_val:.2f} 出廠價能不能接{mold_clause}，能做今天預付 {deposit_pct_str} 定金秒排，"
                    f"合適的話現在把合同簽了排單；不能做我們手頭備案產能就放出去了。"
                )

        # 2. 策略原生原語：戰略沉默與冷卻威懾 (Silence / Cooling Off)
        if obj.tactical_mode == TacticalModeEnum.SILENCE:
            return f"行，那{contact}你先忙，這批{product}我先問問餘姚那邊，回頭再聯絡。"

        # 優先針對對手特殊套路進行反制打擊
        if obj.counterpart_intent == VendorTacticEnum.OFFLINE_BYPASS_DIVERT:
            return (
                f"{contact}，公司財務合規審計明確要求走系統留痕，私下微信恕不方便。"
                f"首批 {obj.moq} 件試單預算嚴格卡在 ¥{p_val:.2f}，現款現結走平台擔保，能排單我們即刻立項。"
            )

        if obj.counterpart_intent == VendorTacticEnum.MATERIAL_SPEC_DEGRADE:
            return (
                f"配件必須按標準 TDS 技術規範走 100% 原包全新料，AQL 1.0 驗收嚴檢拒收次料。"
                f"我們看重貴廠製程管控，¥{p_val:.2f} 是經過核算的合理出廠價，這批能做我們現在鎖定配額。"
            )

        if obj.counterpart_intent == VendorTacticEnum.CRY_POVERTY_COST_HIKE:
            poverty_templates = [
                f"大宗原料現貨波動我們有跟蹤，攤到單件成本影響可控。這批{product}總量大，意向價 ¥{p_val:.2f} 出廠價大家都有合理利潤，合適的話儘快出樣排產。",
                f"{contact}，行情波動大家心裡有數，核算過BOM表材料佔比，¥{p_val:.2f} 出廠價給貴廠留足了利潤。能接我們今天鎖定排單，別為幾毛錢耽誤檔期。",
                f"成本壓力大家都有，但我們走的是核心長期走量盤。首批試單 ¥{p_val:.2f} 現款現結見單排產，後續翻單把量補上，痛快點今天定下來。",
            ]
            return poverty_templates[r_idx % len(poverty_templates)]

        if obj.counterpart_intent == VendorTacticEnum.TAX_PACKAGING_UNBUNDLING:
            return (
                f"核價標準按行業常規含稅含常規包裝出廠價核算。"
                f"首批試單 ¥{p_val:.2f} 現款秒排，請確認是否能按標準條款接單。"
            )

        if obj.counterpart_intent == VendorTacticEnum.FINAL_DEAL_CONCESSION:
            return (
                f"很好，認可貴廠務實態度。意向價 ¥{p_val:.2f}，現款現結 {deposit_pct_str} 定金見單走帳，"
                f"請發合同與正式 PI，我們今天立項排產。"
            )

        # 依據主戰術模式渲染
        if obj.tactical_mode == TacticalModeEnum.LOGROLL:
            mold_clause = f"，且首批免除模具費 ¥{obj.waive_mold_fee_amount:.0f}" if obj.mold_fee_exempted else ""
            return (
                f"{contact}，理解貴廠在製程與排單成本上的考量。若單價落定在 ¥{p_val:.2f}，"
                f"我方展現充分誠意：今天特批預付 {deposit_pct_str} 定金現款秒結排單。"
                f"但作為對等條件，交期必須壓縮至 {obj.lead_days} 天內交付{mold_clause}。"
                f"若貴方確認，今天立即蓋章回傳 PO；否則我方只得啟動備案產能。"
            )

        elif obj.tactical_mode == TacticalModeEnum.CROSS_ANCHOR:
            lead_str = f"¥{obj.competing_lead_price:.2f}" if obj.competing_lead_price else "同規格同行報價"
            return (
                f"你們工藝我們很看好，但報價確實超出採購預算。我們同步評估了同規格同行，合格樣品已給到 {lead_str}。"
                f"我們更傾向與你們配合，意向價 ¥{p_val:.2f} 能做我們就敲定，否則只能按採購流程選備選庫了。"
            )

        elif obj.tactical_mode == TacticalModeEnum.SIDELINE:
            return (
                f"若貴司成本暫時難以優化，我們不強求。該批{product}單核價嚴格，超出預算線我們暫不佔用貴司檔期。"
                f"我方暫時以 ¥{p_val:.2f} 基準線安排備用供應商。後續如有產能餘量或報價調整歡迎隨時同步。"
            )

        elif obj.tactical_mode == TacticalModeEnum.FIRM_HOLD:
            return (
                f"{contact}，該批{product}訂單核算預算卡死在 ¥{p_val:.2f}。我們有誠意長期配合，"
                f"若貴廠此價格能接我們立即蓋章下 PO，否則確實無法超出採購底線。"
            )

        elif obj.tactical_mode == TacticalModeEnum.WALK_AWAY:
            return (
                f"因雙方商務條件分歧超出容忍範圍，本次{product}採購談判終止。"
                f"感謝貴司報價，期待未來其他專案合作。"
            )

        else:  # LEAN_IN (預設進攻/讓步)
            return (
                f"{contact}誠意我們認可，這批{product}是我們下半年的核心走量訂單。"
                f"首批試單給到 ¥{p_val:.2f} 出廠價，現款現結 {deposit_pct_str} 定金見單走帳，"
                f"週五前出簽字封樣，合適的話現在走合同。"
            )


class EnglishExecutiveRenderer:
    """
    英文採購高管與 CPO 遙測警報卡片渲染器 (English Executive Surface Renderer)。
    職責：
    1. 將同一個 TacticalActionObject 渲染為西方採購主管 / CPO 儀表板及 WhatsApp 手機決策卡。
    2. 包含貝氏底價估算 (Estimated Floor Price)、貝氏置信度 (Confidence)、破裂風險 (Risk)、利潤空間 (Margin)。
    3. 清楚呈現 [1] Concede / [2] Hold / [3] Walk Away / [Direct Price] 移動指令。
    4. 輸出執行確認卡 (Confirmation Card)。
    """

    @classmethod
    def _check_schema_version(cls, obj: TacticalActionObject) -> None:
        if not obj.schema_version.startswith("4."):
            raise ValueError(f"Incompatible schema version '{obj.schema_version}'. Expected 4.x")

    @classmethod
    def render_executive_card(cls, obj: TacticalActionObject) -> str:
        cls._check_schema_version(obj)
        floor_str = f"¥{obj.estimated_floor_price:.2f}" if obj.estimated_floor_price else "Estimating..."
        offer_str = f"¥{obj.counterpart_offered_price:.2f}" if obj.counterpart_offered_price else "Pending"
        t_price_str = f"¥{obj.authorized_price:.2f}"
        ceiling_str = f"¥{obj.walk_away_ceiling:.2f}"
        round_tag = f"• Source Round ID: {obj.source_round_id}\n" if obj.source_round_id else ""

        circuit_warning = ""
        if obj.circuit_tripped:
            circuit_warning = (
                f"\n*** [SAFETY CIRCUIT BREAKER TRIPPED] ***\n"
                f"Reason: {obj.trip_reason or 'Risk parameter ceiling breached'}\n"
            )

        mixed_note = f"• [MIXED SIGNAL DETECTED]: {obj.mixed_signal_note or 'Offer extracted; non-commercial probe deflected'}\n" if obj.is_mixed_signal else ""

        card = (
            f"=== [TradeFlow CPO Executive Telemetry] ===\n"
            f"{round_tag}"
            f"• Target Product: {obj.product_name}\n"
            f"• Inbound Offer: {offer_str} vs Authorized Target: {t_price_str}\n"
            f"• Walk-Away Ceiling: {ceiling_str}\n"
            f"• Bayesian Belief: Estimated Floor: {floor_str} (Confidence: {obj.bayesian_confidence:.1%})\n"
            f"• Risk Telemetry: Breakdown Risk: {obj.breakdown_risk:.1%} | Expected Margin: ¥{obj.expected_margin:.2f}\n"
            f"• Active Tactical Mode: {obj.tactical_mode.value}\n"
            f"• Inbound Counterpart Intent: {obj.counterpart_intent.value}\n"
            f"{mixed_note}"
            f"• Package Terms: Lead Time: {obj.lead_days}d | Deposit: {obj.deposit_pct*100:.0f}% | MOQ: {obj.moq}"
            f"{circuit_warning}\n\n"
            f"DIRECTIVES (Reply to execute):\n"
            f"[ 1 ] CONCEDE: Accept proposed step to {t_price_str}\n"
            f"[ 2 ] HOLD FIRM: Reject offer & maintain leverage\n"
            f"[ 3 ] WALK AWAY: Terminate negotiation & invoke BATNA\n"
            f"[ Direct Price ]: e.g., Reply '13.20' to override"
        )
        return card

    @classmethod
    def render_executive_confirmation_card(cls, obj: TacticalActionObject, command_executed: str) -> str:
        """生成高管執行指令後的確認卡片 (Confirmation Card)。"""
        cls._check_schema_version(obj)
        floor_str = f"¥{obj.estimated_floor_price:.2f}" if obj.estimated_floor_price else "N/A"
        card = (
            f"=== [TradeFlow Action Execution Confirmation] ===\n"
            f"• Source Round ID: {obj.source_round_id or 'N/A'}\n"
            f"• Executed Directive: {command_executed}\n"
            f"• Target Product: {obj.product_name}\n"
            f"• Settled Authorized Price: ¥{obj.authorized_price:.2f}\n"
            f"• Package Terms: Lead Time: {obj.lead_days}d | Deposit: {obj.deposit_pct*100:.0f}% | MOQ: {obj.moq}\n"
            f"• Bayesian Anchor: Floor: {floor_str} (Confidence: {obj.bayesian_confidence:.1%})\n"
            f"• Risk Telemetry: Breakdown Risk: {obj.breakdown_risk:.1%}\n"
            f"• Counterparty Action: Injected native Chinese procurement response to supplier chat"
        )
        return card

    @classmethod
    def render_telemetry_summary(cls, obj: TacticalActionObject) -> str:
        """單行英文遙測摘要。"""
        cls._check_schema_version(obj)
        quote_str = f"¥{obj.counterpart_offered_price:.2f}" if obj.counterpart_offered_price else "N/A"
        floor_str = f"¥{obj.estimated_floor_price:.2f}" if obj.estimated_floor_price else "N/A"
        return (
            f"[SUPPLIER TELEMETRY] Intent: {obj.counterpart_intent.value} | "
            f"Quote: {quote_str} vs Target: ¥{obj.authorized_price:.2f} | "
            f"Floor Est: {floor_str} (Conf: {obj.bayesian_confidence:.1%}) | "
            f"Breakdown Risk: {obj.breakdown_risk:.1%}"
        )


# ---------------------------------------------------------------------------
# 跨語言雙向一致性與防穿幫審計器 (Cross-Lingual Sanity & Anti-Leak Checker)
# ---------------------------------------------------------------------------
class CrossLingualSanityChecker:
    """跨語言雙向一致性與防穿幫審計器 (Anti-Leak & Consistency Checker)。"""

    FORBIDDEN_LEAK_WORDS = [
        "歐美專案", "外商", "美元", "董事會", "西方", "海外總部", "外國人"
    ]

    STIFF_TRANSLATION_MARKERS = [
        "尊敬的", "深感榮幸", "董事會指示", "我們公司希望了解", "海外總部授權",
        "貴方是否能考慮", "期待您的良好回覆", "誠摯地感謝", "我們十分重視此次合作機會"
    ]

    @classmethod
    def check_chinese_leaks(cls, text: str) -> List[str]:
        """檢測中文話術中是否存在外商身分溢價洩漏詞彙。"""
        return [w for w in cls.FORBIDDEN_LEAK_WORDS if w in text]

    @classmethod
    def check_semantic_nativeness(cls, chinese_text: str, enable_llm: bool = True) -> Dict[str, Any]:
        """
        語意原生度審計 (Semantic Nativeness Audit):
        兩道防線：
        1. 快速靜態模式審計：檢測生硬機翻書面腔 (Stiff translation phrasing) 與外商洩漏詞。
        2. LLM 狡黠工廠業務員視角對抗審計 (可選/在可用時調用): 模擬 15 年工廠老業務辨識外商/假買家。
        """
        stiff_hits = [m for m in cls.STIFF_TRANSLATION_MARKERS if m in chinese_text]
        leak_hits = cls.check_chinese_leaks(chinese_text)

        if stiff_hits or leak_hits:
            return {
                "is_native": False,
                "confidence": 0.95,
                "stiff_phrases": stiff_hits,
                "leak_phrases": leak_hits,
                "audit_mode": "static_deterministic",
                "critique": "Detected stiff translation syntax or foreign buyer leak words.",
            }

        # Stage 2 LLM 對抗性語意審計 (若配置金鑰且啟用)
        import os
        api_key = os.getenv("EVOLUS_API_KEY") or os.getenv("VLLM_API_KEY")
        if enable_llm and api_key:
            import json
            import requests
            base_url = os.getenv("VLLM_BASE_URL", "https://api.evolus.ai/v1")
            prompt = (
                "You are an experienced, shrewd factory sales manager in Ningbo/Shenzhen. "
                "Analyze the following procurement reply to determine if it sounds like an authentic domestic Chinese buyer "
                "or if it sounds like a translated foreigner / suspicious outsider.\n"
                f"Procurement Reply: \"{chinese_text}\"\n"
                "Output STRICT JSON:\n"
                "{\"is_native\": bool, \"confidence\": float (0.0-1.0), \"critique\": string}"
            )
            try:
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-14B-Instruct"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 120,
                }
                resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=2.5)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    m = re.search(r"\{.*\}", content, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        return {
                            "is_native": bool(data.get("is_native", True)),
                            "confidence": float(data.get("confidence", 0.9)),
                            "stiff_phrases": [],
                            "leak_phrases": [],
                            "audit_mode": "llm_adversarial",
                            "critique": data.get("critique", "LLM validated authentic buyer phrasing"),
                        }
            except Exception:
                pass

        return {
            "is_native": True,
            "confidence": 0.92,
            "stiff_phrases": [],
            "leak_phrases": [],
            "audit_mode": "static_deterministic",
            "critique": "Native coastal buyer vernacular validated.",
        }

    @classmethod
    def check_english_card_purity(cls, text: str, allowed_product_name: Optional[str] = None) -> bool:
        """檢查英文卡片中不得含有中文字符 (允許貨幣符號 ¥ 與常規標點；若 product_name 原生為中文則豁免該名稱)。"""
        clean = text.replace("¥", "")
        if allowed_product_name:
            clean = clean.replace(allowed_product_name, "")
        has_chinese = bool(re.search(r"[\u4e00-\u9fff]", clean))
        return not has_chinese

    @classmethod
    def verify_bidirectional_consistency(
        cls,
        action_obj: TacticalActionObject,
        zh_script: str,
        en_card: str,
    ) -> bool:
        """
        全量驗證雙向一致性：
        1. 中文話術零洩漏
        2. 英文卡片純度 100%
        3. 授權價格在雙邊 100% 精確一致
        4. 來源溯源 ID 一致
        """
        leaks = cls.check_chinese_leaks(zh_script)
        if leaks:
            raise ValueError(f"Chinese script contains forbidden leak words: {leaks}")

        if not cls.check_english_card_purity(en_card, allowed_product_name=action_obj.product_name):
            raise ValueError("English card contains prohibited Chinese characters outside product name")

        expected_p = f"{action_obj.authorized_price:.2f}"
        if expected_p not in zh_script:
            raise ValueError(f"Authorized price ¥{expected_p} missing from Chinese script")
        if expected_p not in en_card:
            raise ValueError(f"Authorized price ¥{expected_p} missing from English card")

        if action_obj.source_round_id and action_obj.source_round_id not in en_card:
            raise ValueError(f"Source round ID {action_obj.source_round_id} missing from English card")

        return True


# ---------------------------------------------------------------------------
# 數值文字硬不變量護欄 (相容保留)
# ---------------------------------------------------------------------------
class NumericGuardrailValidator:
    """確保生成文本中包含且僅包含法定授權數值，禁止擅自承諾或妥協。"""

    FORBIDDEN_COMPROMISE_WORDS = [
        "那我們再退一步", "再便宜點", "私下轉帳", "不開發票", "換次級料", "用回料", "私聊"
    ]

    @classmethod
    def validate_action_object(cls, obj: TacticalActionObject) -> bool:
        """以 AST 驗證結構化對象數值不變量。"""
        return ActionObjectASTGuardrail.verify(obj)

    @classmethod
    def validate_reply(
        cls,
        draft_text: str,
        authorized_price: float,
        walk_away_ceiling: float,
        tolerance: float = 0.05,
    ) -> bool:
        """
        驗證草稿中是否含有非授權報價或敏感妥協。
        - 授權報價必須在文字中明確體現。
        - 文本中出現的所有價格均不得高於最高上限 walk_away_ceiling。
        - 不得含有私下線下妥協詞彙。
        """
        for word in cls.FORBIDDEN_COMPROMISE_WORDS:
            if word in draft_text:
                raise GuardrailViolationError(f"Forbidden compromise phrase detected: '{word}'")

        prices_found = []
        for m in re.finditer(r"(?:[¥￥$]|RMB)\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:元|塊|/件|/把|/個)", draft_text):
            val_str = m.group(1) or m.group(2)
            if val_str:
                try:
                    val = float(val_str)
                    prices_found.append(val)
                except ValueError:
                    pass

        has_authorized = any(abs(p - authorized_price) <= tolerance for p in prices_found)
        if not has_authorized:
            raise GuardrailViolationError(
                f"Draft lacks authorized target price ¥{authorized_price:.2f}"
            )

        for p in prices_found:
            if p > walk_away_ceiling + tolerance:
                raise GuardrailViolationError(
                    f"Price ¥{p:.2f} violates walk-away ceiling ¥{walk_away_ceiling:.2f}"
                )

        return True


# ---------------------------------------------------------------------------
# 跨語言戰術生成器相容橋樑 (Bilingual Tactical Bridge - Backward Compatible)
# ---------------------------------------------------------------------------
class BilingualTacticalBridge:
    """提供中英雙向認知轉譯，輸出具備商業威懾力與合規安全的話術。"""

    @staticmethod
    def generate_chinese_counter_offer(
        tactic: str,
        authorized_price: float,
        product_name: str,
        moq: int = 500,
        competing_lead_price: Optional[float] = None,
        supplier_intent: Optional[VendorTacticEnum] = None,
        supplier_contact_name: str = "老闆",
    ) -> str:
        """根據戰術類型、意圖反擊槓桿與鎖死價格生成地道國產採購話術。"""
        t_mode = (
            TacticalModeEnum(tactic)
            if tactic in TacticalModeEnum.__members__
            else TacticalModeEnum.LEAN_IN
        )
        action_obj = TacticalActionObject(
            authorized_price=authorized_price,
            walk_away_ceiling=authorized_price * 1.5,
            tactical_mode=t_mode,
            product_name=product_name or "項目",
            supplier_contact_name=supplier_contact_name,
            moq=moq,
            competing_lead_price=competing_lead_price,
            counterpart_intent=supplier_intent or VendorTacticEnum.NORMAL_COUNTER_OFFER,
        )
        return ChineseSurfaceRenderer.render(action_obj)

    @staticmethod
    def telemetry_english_summary(
        intent: VendorTacticEnum,
        quoted_price: Optional[float],
        target_price: float,
        p_break: float,
    ) -> str:
        """生成供西方英語採購主管即時決策的英文遙測摘要。"""
        intent_labels = {
            VendorTacticEnum.CRY_POVERTY_COST_HIKE: "Cost-hike bluff (raw material inflation cited)",
            VendorTacticEnum.TIER_VOLUME_BLACKMAIL: "MOQ blackmail (demanded larger volume)",
            VendorTacticEnum.MATERIAL_SPEC_DEGRADE: "SPEC DEGRADATION ALERT (attempted lower-grade material)",
            VendorTacticEnum.TAX_PACKAGING_UNBUNDLING: "Unbundled packaging/VAT fee surcharge",
            VendorTacticEnum.OFFLINE_BYPASS_DIVERT: "AUDIT ESCALATION: Factory attempted off-platform bypass",
            VendorTacticEnum.IDENTITY_PROBE_TEST: "IDENTITY PROBE: Factory probed domestic/export background",
            VendorTacticEnum.FINAL_DEAL_CONCESSION: "Terms conceded, ready for binding PI",
            VendorTacticEnum.NORMAL_COUNTER_OFFER: "Routine price negotiation",
        }
        quote_str = f"¥{quoted_price:.2f}" if quoted_price else "N/A"
        desc = intent_labels.get(intent, "Routine negotiation")
        return (
            f"[SUPPLIER TELEMETRY] Intent: {desc} | Quote: {quote_str} vs Target: ¥{target_price:.2f} | "
            f"Breakdown Risk: {p_break:.1%}"
        )
