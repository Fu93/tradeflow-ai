"""TradeFlow v4 — 語言中立策略決策核心與雙向表面渲染器。

架構特色：
1. 嚴格禁止 Emoji（Zero Emoji 政策）。
2. 解耦原則：策略語言中立核心 (Language-Agnostic Decision Core)
   - MCTS、貝氏更新與 Logrolling 求解器僅輸出純數字、多維約束與戰術枚舉之 TacticalActionObject。
   - 單一真相源：決策不依賴自然語言反向正則解析。
3. 抽象語法樹硬不變量護欄 (ActionObjectASTGuardrail & NumericGuardrailValidator)：
   - AST 編譯驗證策略對象之數值不變量，物理阻斷越界與未經授權之妥協。
4. 供應商 6 大核心博弈套路分類器 (InboundIntentClassifier)。
5. 雙向表面渲染器 (Dual Surface Renderers)：
   - 中文表面渲染器 (ChineseSurfaceRenderer)：沿海地道金牌買家商務行話，動態插值，嚴禁外商溢價洩漏。
   - 英文高管渲染器 (EnglishExecutiveRenderer)：西方 CPO 決策卡片與即時手機遙測推播。
"""
from __future__ import annotations
import ast
import re
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field


class VendorTacticEnum(str, Enum):
    CRY_POVERTY_COST_HIKE = "CRY_POVERTY_COST_HIKE"          # 哭窮/原料暴漲
    TIER_VOLUME_BLACKMAIL = "TIER_VOLUME_BLACKMAIL"          # 以量壓價/要求加單
    MATERIAL_SPEC_DEGRADE = "MATERIAL_SPEC_DEGRADE"          # 偷換規格/換便宜料
    TAX_PACKAGING_UNBUNDLING = "TAX_PACKAGING_UNBUNDLING"    # 稅率拆分/包裝加價
    OFFLINE_BYPASS_DIVERT = "OFFLINE_BYPASS_DIVERT"          # 要求加微信/線下私聊
    FINAL_DEAL_CONCESSION = "FINAL_DEAL_CONCESSION"          # 同意條款/準備簽單
    NORMAL_COUNTER_OFFER = "NORMAL_COUNTER_OFFER"            # 常規還價


class TacticalModeEnum(str, Enum):
    LEAN_IN = "LEAN_IN"                  # 常規出廠價進攻 (高誠意、定金誘餌、封樣排產)
    CROSS_ANCHOR = "CROSS_ANCHOR"        # 備選大廠比價錨定 (威懾轉向備選庫)
    SIDELINE = "SIDELINE"                # 靠邊觀望/產能冷卻 (預算卡死、不佔檔期)
    LOGROLL = "LOGROLL"                  # 企業授權多維條款置換 (定金換交期/模具費減免)
    FIRM_HOLD = "FIRM_HOLD"              # 鎖死底線價 (寸步不讓)
    WALK_AWAY = "WALK_AWAY"              # 終止談判/啟動離場


class InboundTacticalClassification(BaseModel):
    primary_intent: VendorTacticEnum
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    mentioned_price: Optional[float] = None
    demanded_moq: Optional[int] = None
    tactical_rationale: str = ""
    requires_escalation: bool = False


# ---------------------------------------------------------------------------
# 意圖分類器 (Deterministic Pattern Ladder + Stage 2 LLM Fallback)
# ---------------------------------------------------------------------------
class InboundIntentClassifier:
    """供應商對話 6 大核心博弈套路分類器。"""

    PATTERNS: List[Tuple[VendorTacticEnum, re.Pattern, float, bool]] = [
        (
            VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
            re.compile(r"(加個?微信|私下聊|電話聊|wx|私聊|轉私帳|線下付|1[3-9]\d{9}|v信|微\s*信|\+v|加v|私發|私下交易)", re.IGNORECASE),
            0.95,
            True,  # 脫離平台直接觸發風控警報
        ),
        (
            VendorTacticEnum.MATERIAL_SPEC_DEGRADE,
            re.compile(r"(換成?便宜|回料|水口料|減薄|普通料|換成201|降低規格|改用常規|非標|換料|非正品|二料)", re.IGNORECASE),
            0.90,
            True,  # 品質降級需主管確認
        ),
        (
            VendorTacticEnum.CRY_POVERTY_COST_HIKE,
            re.compile(r"(原料.*漲|原材料.*漲|暴漲|成本.*漲|人工.*貴|虧本|賠錢|喝西北風|沒利潤|電費|成本都不夠|純虧|壓不住|貼錢|沒法開工|做不了)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
            re.compile(r"(起訂量|至少要|量太小|樣品價|開機費|加單|翻倍才|起訂|MOQ|小單|幾百個)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.TAX_PACKAGING_UNBUNDLING,
            re.compile(r"(未稅|不含稅|專票.*加|稅點|包裝另算|彩盒.*加|外箱.*加|不包.*港口|港雜費|裸價|要票.*加)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.FINAL_DEAL_CONCESSION,
            re.compile(r"(行吧|服了你|就按你|發合同|打款吧|簽單|成交|定金打哪|虧本做給你|按你說的|可以做)", re.IGNORECASE),
            0.90,
            False,
        ),
    ]

    # 嚴格價格正則：必須具備貨幣標識 (¥/￥/$/RMB)、單價前綴 (報價/單價/出廠價) 或單位後綴 (元/塊/件)
    PRICE_PATTERN = re.compile(
        r"(?:(?:[¥￥$]|RMB)\s*(\d+(?:\.\d{1,2})?)|(?:報價|單價|價格|給您|算您|出廠價|特價|低至)\s*[:：=]?\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:元|塊)(?:/件|/把|/個|/套|/pcs)?)",
        re.IGNORECASE,
    )
    MOQ_PATTERN = re.compile(r"(\d{3,7})\s*(?:個|把|件|套|pcs)", re.IGNORECASE)

    @classmethod
    def classify_with_llm(cls, raw_text: str) -> Optional[InboundTacticalClassification]:
        """
        Stage 2: 結構化語意 AI 推理分類器 (LLM Semantic Parser)。
        當 Stage 1 正則無法高確信度識別或文本存在口語反諷、隱晦暗示時觸發。
        """
        import os
        import json

        api_key = os.getenv("EVOLUS_API_KEY") or os.getenv("VLLM_API_KEY")
        base_url = os.getenv("VLLM_BASE_URL", "https://api.evolus.ai/v1")
        if not api_key:
            return None

        prompt = (
            "Analyze the B2B Chinese supplier message below and output STRICT JSON format with keys:\n"
            "\"primary_intent\": one of [\"CRY_POVERTY_COST_HIKE\", \"TIER_VOLUME_BLACKMAIL\", \"MATERIAL_SPEC_DEGRADE\", \"TAX_PACKAGING_UNBUNDLING\", \"OFFLINE_BYPASS_DIVERT\", \"FINAL_DEAL_CONCESSION\", \"NORMAL_COUNTER_OFFER\"],\n"
            "\"confidence\": float between 0.0 and 1.0,\n"
            "\"mentioned_price\": float or null,\n"
            "\"demanded_moq\": int or null,\n"
            "\"tactical_rationale\": short explanation string,\n"
            "\"requires_escalation\": boolean (true if attempts off-platform or material degradation).\n"
            f"Supplier message: \"{raw_text}\"\n"
            "JSON:"
        )

        try:
            import requests
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-14B-Instruct"),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 150,
            }
            resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=3.0)
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
    def classify(cls, raw_text: str, enable_llm_fallback: bool = True) -> InboundTacticalClassification:
        """
        兩段式意圖解析管線 (Two-Stage Intent Pipeline)：
        - Stage 1: 微秒級確定性正則階梯 (Deterministic Regex Ladder)
        - Stage 2: 複雜語意與長文本語意 LLM 意圖推理 (Semantic LLM Fallback)
        """
        if not raw_text:
            return InboundTacticalClassification(
                primary_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
                tactical_rationale="Empty input"
            )

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

        # 3. Stage 1: 匹配確定性套路特徵
        for intent, pattern, conf, escalate in cls.PATTERNS:
            if pattern.search(raw_text):
                rationale = "Matched deterministic pattern in inbound text"
                return InboundTacticalClassification(
                    primary_intent=intent,
                    confidence=conf,
                    mentioned_price=price_val,
                    demanded_moq=moq_val,
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
        return InboundTacticalClassification(
            primary_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
            confidence=0.70,
            mentioned_price=price_val,
            demanded_moq=moq_val,
            tactical_rationale="Regular pricing dialogue",
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
    circuit_tripped: bool = Field(default=False, description="是否觸發安全熔斷")
    trip_reason: Optional[str] = Field(default=None, description="熔斷原因")


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
    1. 將語言中立之 TacticalActionObject 渲染為符合中國沿海（義烏、慈溪、深圳、泉州）老買家專業商務行話。
    2. 動態插值品類名稱與聯絡人稱呼，嚴禁硬編碼。
    3. 嚴格杜絕「歐美專案」、「外商」、「美元」等外商溢價洩漏詞彙。
    """

    @classmethod
    def render(cls, obj: TacticalActionObject) -> str:
        p_val = obj.authorized_price
        contact = obj.supplier_contact_name or "老闆"
        product = obj.product_name or "產品"
        deposit_pct_str = f"{int(round(obj.deposit_pct * 100))}%"

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
            return (
                f"大宗原料現貨波動我們有跟蹤，攤到單件成本影響可控。"
                f"這批{product}總量大，意向價 ¥{p_val:.2f} 出廠價大家都有合理利潤，合適的話儘快出樣排產。"
            )

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
                f"我方預算上限已由採購會審定，¥{p_val:.2f} 是首批排單的底線價。"
                f"該報價具備充足同行競爭力，請貴司慎重評估。"
            )

        elif obj.tactical_mode == TacticalModeEnum.WALK_AWAY:
            return (
                f"因雙方商務條件分歧超出容忍範圍，本次{product}採購談判終止。"
                f"感謝貴司報價，期待未來其他專案合作。"
            )

        else:  # LEAN_IN (預設)
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
    """

    @classmethod
    def render_executive_card(cls, obj: TacticalActionObject) -> str:
        floor_str = f"¥{obj.estimated_floor_price:.2f}" if obj.estimated_floor_price else "Estimating..."
        offer_str = f"¥{obj.counterpart_offered_price:.2f}" if obj.counterpart_offered_price else "Pending"
        t_price_str = f"¥{obj.authorized_price:.2f}"
        ceiling_str = f"¥{obj.walk_away_ceiling:.2f}"

        circuit_warning = ""
        if obj.circuit_tripped:
            circuit_warning = (
                f"\n*** [SAFETY CIRCUIT BREAKER TRIPPED] ***\n"
                f"Reason: {obj.trip_reason or 'Risk parameter ceiling breached'}\n"
            )

        card = (
            f"=== [TradeFlow CPO Executive Telemetry] ===\n"
            f"• Target Product: {obj.product_name}\n"
            f"• Inbound Offer: {offer_str} vs Authorized Target: {t_price_str}\n"
            f"• Walk-Away Ceiling: {ceiling_str}\n"
            f"• Bayesian Belief: Estimated Floor: {floor_str} (Confidence: {obj.bayesian_confidence:.1%})\n"
            f"• Risk Telemetry: Breakdown Risk: {obj.breakdown_risk:.1%} | Expected Margin: ¥{obj.expected_margin:.2f}\n"
            f"• Active Tactical Mode: {obj.tactical_mode.value}\n"
            f"• Inbound Counterpart Intent: {obj.counterpart_intent.value}\n"
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
    def render_telemetry_summary(cls, obj: TacticalActionObject) -> str:
        """單行英文遙測摘要。"""
        quote_str = f"¥{obj.counterpart_offered_price:.2f}" if obj.counterpart_offered_price else "N/A"
        floor_str = f"¥{obj.estimated_floor_price:.2f}" if obj.estimated_floor_price else "N/A"
        return (
            f"[SUPPLIER TELEMETRY] Intent: {obj.counterpart_intent.value} | "
            f"Quote: {quote_str} vs Target: ¥{obj.authorized_price:.2f} | "
            f"Floor Est: {floor_str} (Conf: {obj.bayesian_confidence:.1%}) | "
            f"Breakdown Risk: {obj.breakdown_risk:.1%}"
        )


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
        # 1. 敏感妥協詞彙檢查
        for word in cls.FORBIDDEN_COMPROMISE_WORDS:
            if word in draft_text:
                raise GuardrailViolationError(f"Forbidden compromise phrase detected: '{word}'")

        # 2. 抽取所有價格數值 (要求帶貨幣符號或明確價格後綴，避免 30% 定金或數量被誤認為價格)
        prices_found = []
        for m in re.finditer(r"(?:[¥￥$]|RMB)\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:元|塊|/件|/把|/個)", draft_text):
            val_str = m.group(1) or m.group(2)
            if val_str:
                try:
                    val = float(val_str)
                    prices_found.append(val)
                except ValueError:
                    pass

        # 3. 檢查授權價格是否至少出現一次
        has_authorized = any(abs(p - authorized_price) <= tolerance for p in prices_found)
        if not has_authorized:
            raise GuardrailViolationError(
                f"Draft lacks authorized target price ¥{authorized_price:.2f}"
            )

        # 4. 嚴格不變量：所有出現的價位均不得突破買方最大放棄上限
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
        # 轉發至語言中立決策對象與 ChineseSurfaceRenderer
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
            VendorTacticEnum.FINAL_DEAL_CONCESSION: "Terms conceded, ready for binding PI",
            VendorTacticEnum.NORMAL_COUNTER_OFFER: "Routine price negotiation",
        }
        quote_str = f"¥{quoted_price:.2f}" if quoted_price else "N/A"
        desc = intent_labels.get(intent, "Routine negotiation")
        return (
            f"[SUPPLIER TELEMETRY] Intent: {desc} | Quote: {quote_str} vs Target: ¥{target_price:.2f} | "
            f"Breakdown Risk: {p_break:.1%}"
        )
