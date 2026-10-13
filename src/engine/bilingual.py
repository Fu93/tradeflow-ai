"""TradeFlow v4 — 雙語跨語言認知橋樑、供應商意圖分類器與數值護欄。

特色：
1. 嚴格禁止 Emoji（Zero Emoji 政策）。
2. 解耦原則：MCTS 決策核心決定價格，表面表達生成器不得隨意出價。
3. 數值硬不變量護欄 (NumericGuardrailValidator)：防止幻覺或未經授權的讓步。
4. 供應商 6 大核心套路分類器 (InboundIntentClassifier)。
5. 中英雙向認知轉譯：英語買家規格與指令 -> 專業國產金牌買家中文談判話術。
"""
from __future__ import annotations
import re
from enum import Enum
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field


class VendorTacticEnum(str, Enum):
    CRY_POVERTY_COST_HIKE = "CRY_POVERTY_COST_HIKE"      # 哭窮/原料暴漲
    TIER_VOLUME_BLACKMAIL = "TIER_VOLUME_BLACKMAIL"      # 以量壓價/要求加單
    MATERIAL_SPEC_DEGRADE = "MATERIAL_SPEC_DEGRADE"      # 偷換規格/換便宜料
    TAX_PACKAGING_UNBUNDLING = "TAX_PACKAGING_UNBUNDLING"  # 稅率拆分/包裝加價
    OFFLINE_BYPASS_DIVERT = "OFFLINE_BYPASS_DIVERT"      # 要求加微信/線下私聊
    FINAL_DEAL_CONCESSION = "FINAL_DEAL_CONCESSION"      # 同意條款/準備簽單
    NORMAL_COUNTER_OFFER = "NORMAL_COUNTER_OFFER"        # 常規還價


class InboundTacticalClassification(BaseModel):
    primary_intent: VendorTacticEnum
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    mentioned_price: Optional[float] = None
    demanded_moq: Optional[int] = None
    tactical_rationale: str = ""
    requires_escalation: bool = False


# ---------------------------------------------------------------------------
# 意圖分類器 (Deterministic Pattern Ladder)
# ---------------------------------------------------------------------------
class InboundIntentClassifier:
    """供應商對話 6 大核心博弈套路分類器。"""

    PATTERNS: List[Tuple[VendorTacticEnum, re.Pattern, float, bool]] = [
        # (Intent, Regex Pattern, Default Confidence, Requires Escalation)
        (
            VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
            re.compile(r"(加個?微信|私下聊|電話聊|wx|私聊|轉私帳|線下付|1[3-9]\d{9})", re.IGNORECASE),
            0.95,
            True,  # 脫離平台直接觸發風控警報
        ),
        (
            VendorTacticEnum.MATERIAL_SPEC_DEGRADE,
            re.compile(r"(換成?便宜|回料|水口料|減薄|普通料|換成201|降低規格|改用常規|非標)", re.IGNORECASE),
            0.90,
            True,  # 品質降級需主管確認
        ),
        (
            VendorTacticEnum.CRY_POVERTY_COST_HIKE,
            re.compile(r"(原料.*漲|人工.*貴|虧本|賠錢|喝西北風|沒利潤|電費|成本都不夠|純虧)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
            re.compile(r"(起訂量|至少要|量太小|樣品價|開機費|加單|翻倍才|起訂|MOQ)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.TAX_PACKAGING_UNBUNDLING,
            re.compile(r"(未稅|不含稅|專票.*加|稅點|包裝另算|彩盒.*加|外箱.*加|不包.*港口|港雜費)", re.IGNORECASE),
            0.85,
            False,
        ),
        (
            VendorTacticEnum.FINAL_DEAL_CONCESSION,
            re.compile(r"(行吧|服了你|就按你|發合同|打款吧|簽單|成交|定金打哪|虧本做給你)", re.IGNORECASE),
            0.90,
            False,
        ),
    ]

    PRICE_PATTERN = re.compile(r"(?:[¥￥$]|RMB|元|塊)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元|塊|/件|/把|/個)?")
    MOQ_PATTERN = re.compile(r"(\d{3,7})\s*(?:個|把|件|套|pcs)", re.IGNORECASE)

    @classmethod
    def classify(cls, raw_text: str) -> InboundTacticalClassification:
        """分析工廠訊息，提取數值並識別博弈套路。"""
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
                candidate = float(p_match.group(1))
                if candidate > 0.1:  # 排除誤判
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

        # 3. 匹配套路特徵
        for intent, pattern, conf, escalate in cls.PATTERNS:
            if pattern.search(raw_text):
                rationale = f"Matched pattern '{pattern.pattern}' in inbound text"
                return InboundTacticalClassification(
                    primary_intent=intent,
                    confidence=conf,
                    mentioned_price=price_val,
                    demanded_moq=moq_val,
                    tactical_rationale=rationale,
                    requires_escalation=escalate,
                )

        return InboundTacticalClassification(
            primary_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
            confidence=0.70,
            mentioned_price=price_val,
            demanded_moq=moq_val,
            tactical_rationale="Regular pricing dialogue",
            requires_escalation=False,
        )


# ---------------------------------------------------------------------------
# 數值 AST 硬不變量護欄 (Numeric Guardrails)
# ---------------------------------------------------------------------------
class GuardrailViolationError(ValueError):
    """觸發數值或合規安全不變量違規異常。"""
    pass


class NumericGuardrailValidator:
    """確保生成文本中包含且僅包含法定授權數值，禁止擅自承諾或妥協。"""

    FORBIDDEN_COMPROMISE_WORDS = [
        "那我們再退一步", "再便宜點", "私下轉帳", "不開發票", "換次級料", "用回料", "私聊"
    ]

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
# 跨語言戰術生成器 (Bilingual Cognitive Bridge)
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
    ) -> str:
        """根據戰術類型、意圖反擊槓桿與鎖死價格生成地道國產採購話術。"""
        # 針對特殊套路的特化反擊
        if supplier_intent == VendorTacticEnum.OFFLINE_BYPASS_DIVERT:
            return (
                f"老闆，公司財務合規審計明確要求走系統留痕，私下微信恕不方便。"
                f"首批 {moq} 件試單預算嚴格卡在 ¥{authorized_price:.2f}，現款現結走平台擔保，能排單我們即刻立項。"
            )

        if supplier_intent == VendorTacticEnum.MATERIAL_SPEC_DEGRADE:
            return (
                f"配件必須按標準 TDS 技術規範走 100% 原包全新料，AQL 1.0 驗收嚴檢拒收次料。"
                f"我們看重貴廠製程管控，¥{authorized_price:.2f} 是經過核算的合理出廠價，這批能做我們現在鎖定配額。"
            )

        if supplier_intent == VendorTacticEnum.CRY_POVERTY_COST_HIKE:
            return (
                f"大宗原料現貨波動我們有跟蹤，攤到單件成本影響可控。"
                f"這批出口項目總量大，意向價 ¥{authorized_price:.2f} 出廠價大家都有合理利潤，合適的話儘快出樣排產。"
            )

        # 標准三階戰術範本 (LEAN_IN / CROSS_ANCHOR / SIDELINE)
        if tactic == "LEAN_IN":
            return (
                f"老闆誠意我們認可，我們是歐美專案採購，這批傘是核心訂單。"
                f"首批試單給到 ¥{authorized_price:.2f} 出廠價，現款現結 30% 定金見單走帳，週五前出簽字封樣，合適的話現在走合同。"
            )
        elif tactic == "CROSS_ANCHOR":
            lead = f"¥{competing_lead_price:.2f}" if competing_lead_price else "同規格同行報價"
            return (
                f"你們工藝我們很看好，但報價確實超出採購預算。我們同步評估了其他大廠，同規格合格樣品已給到 {lead}。"
                f"我們更傾向與你們配合，意向價 ¥{authorized_price:.2f} 能做我們就敲定，否則只能按採購流程選備選庫了。"
            )
        else:  # SIDELINE
            return (
                f"若貴司成本暫時難以優化，我們不強求。該批單核價嚴格，超出預算線我們暫不佔用貴司檔期。"
                f"我方暫時以 ¥{authorized_price:.2f} 基準線安排備用供應商。後續如有產能餘量或報價調整歡迎隨時同步。"
            )

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
