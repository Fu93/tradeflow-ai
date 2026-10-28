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
import json
import logging
import math
import os
import re
from enum import Enum
from typing import NamedTuple, Optional, Dict, Any, List, Tuple, Iterable
from pydantic import BaseModel, Field

from src.config.heuristics import (
    FIELD_DEFAULT_CONFIDENCE,
    GUARDRAIL_CEILING_MULTIPLE,
    BASELINE_DEPOSIT_PCT,
    LLM_DEFAULT_CONFIDENCE,
    MAX_MENTIONED_MOLD_FEE,
    MENTION_CONFUSION_TOLERANCE,
    MAX_MENTIONED_PRICE,
    MIN_MENTIONED_PRICE,
    ONNX_ACCEPT_CONFIDENCE,
    POSTERIOR_STD_PLACEHOLDER,
    PRICE_MENTION_TOLERANCE,
    REGEX_CONF_DEAL,
    REGEX_CONF_IDENTITY,
    REGEX_CONF_OFFLINE,
    REGEX_CONF_POVERTY,
    REGEX_CONF_SPEC,
    REGEX_CONF_TAX,
    REGEX_CONF_UNRECOGNIZED,
    REGEX_CONF_VOLUME,
)

from src.engine.safe_regex import SafeLinearRegexEngine, SafeLinearPattern
from src.engine.onnx_classifier import OnDeviceIntentClassifier

logger = logging.getLogger("tradeflow.bilingual")

CURRENT_SCHEMA_VERSION = "4.1.0"

# ---------------------------------------------------------------------------
# 價格掃描器 (單一真相源；供 NumericGuardrailValidator 與品名淨化共用)
#
# [獨立審計修正 C1] 舊版只認 `[¥￥$]|RMB` 作為「前綴」與 `元|塊|/件|/把|/個` 作為
# 「後綴」，下列格式因此全部繞過護欄（皆已實測 PASS）：
#   "…另外也可以做到 USD 15.50 一件。"、"…最高可以給您 15.50 RMB。"、
#   "…可以做到 15.50/pcs。"、"…6.00 元/件"（此為下限缺失，另於 validate_reply 修正）
# 本模式為**無嵌套量詞**的字面交替，對任意長度輸入皆為單次線性掃描。
# ---------------------------------------------------------------------------
PRICE_SCAN_PATTERN = re.compile(
    r"(?:"
    r"(?:[¥￥$€]|RMB|CNY|USD|NTD|HKD)\s*(\d+(?:\.\d{1,2})?)"                     # 貨幣前綴
    r"|(\d+(?:\.\d{1,2})?)\s*(?:元|塊|块|人民币|人民幣|RMB|CNY|USD|NTD|HKD)"       # 貨幣後綴 (N RMB)
    r"|(\d+(?:\.\d{1,2})?)\s*/\s*(?:件|個|个|把|套|支|pcs?|piece|pieces|unit)"   # 每單位後綴
    r"|(\d+(?:\.\d{1,2})?)\s*(?:per|each)\s*(?:pcs?|piece|pieces|unit)"
    r"|(?:每\s*(?:個|个|件|套|把|支))\s*(\d+(?:\.\d{1,2})?)"                     # 每個 N
    r"|(\d+(?:\.\d{1,2})?)\s*每(?:個|个|件|套|把|支)"                           # N 每個
    r")",
    re.IGNORECASE,
)


def scan_draft_prices(text: str) -> List[float]:
    """掃描文本中所有貨幣化單價（支援上游貨幣標記、下游貨幣後綴與每單位後綴）。"""
    if not text:
        return []
    prices: List[float] = []
    for m in PRICE_SCAN_PATTERN.finditer(text):
        for group_val in m.groups():
            if group_val:
                try:
                    prices.append(float(group_val))
                except ValueError:
                    pass
                break
    return prices


def sanitize_product_name(name: Optional[str]) -> str:
    """
    淨化呼叫端傳入之品名 (C2)。

    品名由 `POST /api/v1/sessions` 直接送達，並以 f-string 原樣插值進渲染文本；
    因此 `product_name="核心走量訂單 15.50 RMB 包郵 USD16.90"` 會讓**兩個越界價格**
    搭品名的便車進入話術，舊版護欄只檢查「授權價是否出現」因而放行。
    這裡只中和「貨幣化數字」：304 不銹鋼、LiFePO4 等含數字品名不受影響。
    """
    if not name:
        return "產品"
    cleaned = PRICE_SCAN_PATTERN.sub("[已濾除]", str(name)).strip()
    return cleaned or "產品"


_FULL_TEXT_PATTERNS: dict[tuple[str, int], Any] = {}


def _safety_full_text_pattern(safe_pattern: SafeLinearPattern):
    """
    [H3] 取回**不做長度截斷**的底層編譯樣式，供安全閘門掃描完整原文。

    `SafeLinearPattern.search/finditer/findall` 一律把輸入截到 `MAX_INPUT_LEN`
    （safe_regex.py:196/216/236），因此直接使用它等於保留了「超長輸入可稀釋
    安全訊號」的缺陷 —— 這正是 H3 的核心。

    安全樣式皆為 RE2/`regex` 可編譯**且**內建 `re` 也能安全處理的字面交替
    （多字元字面詞，無嵌套量詞），因此在此以內建 `re` 編譯同一 pattern 並掃描
    完整字串是安全的；同時保留 safe_regex 對 RE2 不相容樣式的降級警告。
    The compiled pattern is kept so the first message does not pay compile again.
    """
    key = (safe_pattern.raw_pattern, int(safe_pattern.flags or 0))
    compiled = _FULL_TEXT_PATTERNS.get(key)
    if compiled is not None:
        return compiled
    import re as _re
    compiled = _re.compile(safe_pattern.raw_pattern, safe_pattern.flags or 0)
    _FULL_TEXT_PATTERNS[key] = compiled
    return compiled


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
    confidence: float = Field(default=FIELD_DEFAULT_CONFIDENCE, ge=0.0, le=1.0)
    mentioned_price: Optional[float] = None
    # Amount as written, before any currency conversion. mentioned_price is the
    # CNY figure the solver may use, and stays None when a non-CNY quote was
    # not converted.
    quoted_amount: Optional[float] = None
    mentioned_currency: Optional[str] = None
    price_currency_note: Optional[str] = None
    demanded_moq: Optional[int] = None
    demanded_mold_fee: Optional[float] = None
    is_mixed_signal: bool = Field(default=False, description="是否同時包含商務報價與非商務試探/雜訊")
    mixed_signal_note: Optional[str] = None
    tactical_rationale: str = ""
    requires_escalation: bool = False


class QuotedPrice(NamedTuple):
    """Amount as written, its currency, and the CNY figure the solver may use."""

    amount: Optional[float]
    currency: Optional[str]
    cny_amount: Optional[float]
    note: Optional[str]


# ---------------------------------------------------------------------------
# 意圖分類器 (Deterministic Pattern Ladder + Stage 2 LLM Fallback)
# ---------------------------------------------------------------------------
class InboundIntentClassifier:
    """供應商對話 7 大核心博弈套路分類器。"""

    # Stage-2 最近一次呼叫的可觀測狀態（供 API 回報真實失敗原因，而不是只回
    # stage2_available=false 讓呼叫端猜）。每次 classify_with_llm 開頭重設。
    last_llm_error: Optional[str] = None
    last_llm_latency_ms: Optional[float] = None

    PATTERNS: List[Tuple[VendorTacticEnum, Any, float, bool]] = [
        (
            VendorTacticEnum.OFFLINE_BYPASS_DIVERT,
            SafeLinearRegexEngine.compile(r"(加個?微信|加个?微信|私下聊|電話聊|电话聊|wx|私聊|轉私帳|转私账|線下付|线下付|v\s*信|微\s*信|\+v|加\s*(?:我)?\s*v|私發|私发|私下交易|走線下|走线下)", re.IGNORECASE),
            REGEX_CONF_OFFLINE,
            True,  # 脫離平台直接觸發風控警報
        ),
        (
            VendorTacticEnum.MATERIAL_SPEC_DEGRADE,
            SafeLinearRegexEngine.compile(r"(換成?便宜|换成?便宜|回料|水口料|減薄|减薄|普通料|換成\s*201|换成\s*201|201\s*不[鏽銹锈]鋼|201\s*不[鏽銹锈]钢|降低規格|降低规格|改用常規|改用常规|非標|非标|換料|换料|非正品|二料|次料)", re.IGNORECASE),
            REGEX_CONF_SPEC,
            True,  # 品質降級需主管確認
        ),
        (
            VendorTacticEnum.IDENTITY_PROBE_TEST,
            SafeLinearRegexEngine.compile(r"(外貿公司|做外貿|外贸公司|做外贸|美金|美元|做外銷|做外销|出口還是內銷|出口还是内销|主要做哪個市場|主要做哪个市场|哪裡人|哪里人|哪個港|哪个港|哪裡的倉庫|哪里的仓库|老外|海外客戶|海外客户|出口到哪|美元帳戶|美元账户|走哪個口岸|走哪个口岸|你們老闆|你们老板|哪裡過來的|哪里过来的)", re.IGNORECASE),
            REGEX_CONF_IDENTITY,
            False,  # 身份試探需以地道本地老採購話術化解
        ),
        (
            VendorTacticEnum.FINAL_DEAL_CONCESSION,
            SafeLinearRegexEngine.compile(r"(行吧|服了你|就按你|發合同|发合同|打款吧|簽單|签单|成交|定金打哪|訂金打哪|虧本做給你|亏本做给你|按你說的|按你说的|可以做|給您排單|给您排单)", re.IGNORECASE),
            REGEX_CONF_DEAL,
            False,
        ),
        (
            VendorTacticEnum.CRY_POVERTY_COST_HIKE,
            # 使用有界量詞 [^\n，。！？]{0,15} 替代 .*，徹底杜絕 ReDoS 災難性回溯
            SafeLinearRegexEngine.compile(r"(原料[^\n，。！？]{0,15}?漲|原料[^\n，。！？]{0,15}?涨|原材料[^\n，。！？]{0,15}?漲|原材料[^\n，。！？]{0,15}?涨|暴漲|暴涨|成本[^\n，。！？]{0,15}?漲|成本[^\n，。！？]{0,15}?涨|人工[^\n，。！？]{0,15}?(?:貴|贵|漲|涨)|虧本|亏本|賠錢|赔钱|喝西北風|喝西北风|沒利潤|没利润|電費|电费|成本都不夠|成本都不够|純虧|纯亏|壓不住|压不住|貼錢|贴钱|沒法開工|没法开工|做不了|利潤太薄|利润太薄)", re.IGNORECASE),
            REGEX_CONF_POVERTY,
            False,
        ),
        (
            VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
            SafeLinearRegexEngine.compile(r"(起訂量|起订量|至少要|量太小|樣品價|样品价|開機費|开机费|加單|加单|翻倍才|起訂|起订|MOQ|小單|小单|幾百個|几百个|量大才能)", re.IGNORECASE),
            REGEX_CONF_VOLUME,
            False,
        ),
        (
            VendorTacticEnum.TAX_PACKAGING_UNBUNDLING,
            # 使用有界量詞替代 .*
            SafeLinearRegexEngine.compile(r"(未稅|未税|不含稅|不含税|專票[^\n，。！？]{0,15}?加|专票[^\n，。！？]{0,15}?加|稅點|税点|包裝另算|包装另算|彩盒[^\n，。！？]{0,15}?加|外箱[^\n，。！？]{0,15}?加|不包[^\n，。！？]{0,15}?港口|港雜費|港杂费|裸價|裸价|要票[^\n，。！？]{0,15}?加)", re.IGNORECASE),
            REGEX_CONF_TAX,
            False,
        ),
    ]

    # Detects a price. Structured currency capture is extract_quoted_price.
    # Linear alternation, no nested quantifiers, so RE2 can compile it.
    # Longer currency words (美元) stay before 元 so a USD quote is not read as CNY.
    _CURRENCY_WORD = (
        r"USD|US\$|RMB|CNY|EUR|HKD|NTD|TWD|GBP|JPY|美元|美金|[¥￥$]"
    )
    _CURRENCY_SUFFIX = (
        r"USD|US\$|RMB|CNY|EUR|HKD|NTD|TWD|GBP|JPY|美元|美金|元|块|塊|dollars?"
    )
    # Thousands separators and a decimal comma. Alternation is left-first and
    # bounded, so "1,000" is not read as 1 and "3,20" is not read as 3.
    _NUMBER = (
        r"(?:\d{1,3}(?:[,，]\d{3})+(?:\.\d{1,2})?"
        r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
        r"|\d+[.,]\d{1,2}"
        r"|\d+)"
    )
    PRICE_PATTERN = SafeLinearRegexEngine.compile(
        r"(?:"
        r"(?:unit\s*price|price|報價|报价|單價|单价|價格|价格|給您|算您|出廠價|出厂价|特價|低至)"
        r"\s*[:：=]?\s*(?:" + _CURRENCY_WORD + r")?\s*" + _NUMBER +
        r"|(?:" + _CURRENCY_WORD + r")\s*" + _NUMBER +
        r"|" + _NUMBER + r"\s*(?:" + _CURRENCY_SUFFIX + r")"
        r"(?:\s*/\s*(?:pc|pcs|piece)|\s+per\s+piece)?"
        r")",
        re.IGNORECASE,
    )
    _PRICE_LABEL = SafeLinearRegexEngine.compile(
        r"(unit\s*price|price|報價|报价|單價|单价|價格|价格|給您|算您|出廠價|出厂价|特價|低至)"
        r"\s*[:：=]?\s*(" + _CURRENCY_WORD + r")?\s*(" + _NUMBER + r")"
        r"(?:\s*(" + _CURRENCY_SUFFIX + r"))?",
        re.IGNORECASE,
    )
    _PRICE_PREFIX = SafeLinearRegexEngine.compile(
        r"(" + _CURRENCY_WORD + r")\s*(" + _NUMBER + r")",
        re.IGNORECASE,
    )
    _PRICE_SUFFIX = SafeLinearRegexEngine.compile(
        r"(" + _NUMBER + r")\s*(" + _CURRENCY_SUFFIX + r")"
        r"(?:\s*/\s*(?:pc|pcs|piece)|\s+per\s+piece)?",
        re.IGNORECASE,
    )
    _CNY_LABELS = frozenset({
        "報價", "报价", "單價", "单价", "價格", "价格", "給您", "算您", "出廠價", "出厂价", "特價", "低至",
    })
    _USD_UNCONVERTED_NOTE = (
        "Non-CNY quote was not converted and is not used as CNY. "
        "Set TRADEFLOW_USD_CNY_RATE to a CNY-per-USD rate you sourced to convert USD."
    )
    _OTHER_UNCONVERTED_NOTE = (
        "Non-CNY quote was not converted and is not used as CNY. "
        "Only a caller-supplied TRADEFLOW_USD_CNY_RATE converts USD."
    )
    _USD_CONVERTED_NOTE = (
        "USD converted to CNY using TRADEFLOW_USD_CNY_RATE from the environment. "
        "This repository does not supply that rate."
    )
    _CURRENCY_MISSING_NOTE = (
        "No currency was stated, so the number was not treated as CNY."
    )
    MOQ_PATTERN = SafeLinearRegexEngine.compile(r"(\d{3,7})\s*(?:個|把|件|套|pcs)", re.IGNORECASE)
    _EXPLICIT_MOQ = SafeLinearRegexEngine.compile(
        r"(?:MOQ|min(?:imum)?\s+order(?:\s+quantity)?|起订量|起訂量)\s*[:：]?\s*(" + _NUMBER + r")",
        re.IGNORECASE,
    )
    _QTY_BEFORE_MOQ_WORD = SafeLinearRegexEngine.compile(
        r"(\d{1,7})\s*(?:個|把|件|套|pcs)\s*(?:起订|起訂)",
        re.IGNORECASE,
    )
    LEAD_DAYS_PATTERN = SafeLinearRegexEngine.compile(r"(\d{1,3})\s*(?:天|工作日|days?)", re.IGNORECASE)
    DEPOSIT_PATTERN = SafeLinearRegexEngine.compile(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:定金|訂金|預付)", re.IGNORECASE)
    MOLD_FEE_PATTERN = SafeLinearRegexEngine.compile(
        r"(?:模具費|开模费|模具费|開模費|打樣費|打样费)\s*(?:另計|另计)?\s*[:：=加另算]?\s*(?:[¥￥$]|RMB)?\s*(\d+(?:\.\d{1,2})?)",
        re.IGNORECASE,
    )
    QUALITY_SPEC_PATTERN = SafeLinearRegexEngine.compile(r"(原包|全新料|回料|水口料|304|316|201|AQL|TDS|公差|檢驗|質檢|驗貨|開模)", re.IGNORECASE)

    # ------------------------------------------------------------------
    # 欄位級夾制 (Field-level clamping)
    #
    # [C5 修正] 這些上下界原本**只**存在於 `_classify_impl` 的 Stage-1 路徑
    # （`:292` 的 `0.1 <= candidate <= 50000.0`）。Stage-2 解析 LLM JSON 時完全沒有
    # 對應夾制，因此單一幻覺欄位就能繞過 Stage-1 已強制的不變量。
    # 現集中為單一真相源，兩條路徑共用同一組邊界。
    # ------------------------------------------------------------------
    MIN_MENTIONED_PRICE = MIN_MENTIONED_PRICE
    MAX_MENTIONED_PRICE = MAX_MENTIONED_PRICE
    MIN_DEMANDED_MOQ = 1
    MAX_DEMANDED_MOQ = 10_000_000
    MIN_DEMANDED_MOLD_FEE = 0.0
    MAX_DEMANDED_MOLD_FEE = MAX_MENTIONED_MOLD_FEE

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    @classmethod
    def _clamp_price(cls, value: Any) -> Optional[float]:
        """將外部/LLM 提供的價格夾制到合理區間；非有限或越界一律回 None（不猜測）。"""
        if value is None or isinstance(value, bool):
            return None
        if not cls._is_finite_number(value):
            return None
        val = float(value)
        if cls.MIN_MENTIONED_PRICE <= val <= cls.MAX_MENTIONED_PRICE:
            return val
        return None

    @staticmethod
    def parse_locale_number(token: Optional[str]) -> Optional[float]:
        """Read 3.20, 3,20, and 1,000. A comma with 1–2 digits is the decimal mark."""
        if token is None:
            return None
        text = str(token).strip().replace("，", ",").replace(" ", "")
        if not text:
            return None
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            parts = text.split(",")
            if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
                text = parts[0] + "." + parts[1]
            elif parts[0] and len(parts[0]) <= 3 and all(len(part) == 3 for part in parts[1:]):
                text = "".join(parts)
            else:
                text = text.replace(",", "")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
            text = text.replace(".", "")
        try:
            value = float(text)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        return value

    @staticmethod
    def _currency_code(token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        compact = token.strip().upper()
        if compact in {"RMB", "CNY", "元", "块", "塊", "¥", "￥"}:
            return "CNY"
        if compact in {"USD", "US$", "$", "美元", "美金", "DOLLAR", "DOLLARS"}:
            return "USD"
        return compact

    @staticmethod
    def configured_usd_cny_rate() -> Optional[float]:
        """CNY per 1 USD from the environment. No rate is shipped in this repo."""
        raw = os.environ.get("TRADEFLOW_USD_CNY_RATE", "").strip()
        if not raw:
            return None
        try:
            rate = float(raw)
        except ValueError:
            return None
        if not math.isfinite(rate) or rate <= 0:
            return None
        return rate

    @classmethod
    def _settle_quote(cls, amount: float, currency: Optional[str]) -> "QuotedPrice":
        if currency == "CNY":
            return QuotedPrice(amount, "CNY", cls._clamp_price(amount), None)
        if currency == "USD":
            rate = cls.configured_usd_cny_rate()
            if rate is None:
                return QuotedPrice(amount, "USD", None, cls._USD_UNCONVERTED_NOTE)
            converted = cls._clamp_price(amount * rate)
            note = cls._USD_CONVERTED_NOTE
            if converted is None:
                note = (
                    note
                    + " The converted amount was outside the price bounds and was not used."
                )
            return QuotedPrice(amount, "USD", converted, note)
        if currency is None:
            return QuotedPrice(amount, None, None, cls._CURRENCY_MISSING_NOTE)
        return QuotedPrice(amount, currency, None, cls._OTHER_UNCONVERTED_NOTE)

    @classmethod
    def extract_quoted_price(cls, raw_text: str) -> "QuotedPrice":
        """First price in the text, with currency kept separate from the CNY figure.

        ``cny_amount`` is what the solver may use. It stays None for a non-CNY
        quote unless ``TRADEFLOW_USD_CNY_RATE`` converts USD. English ``price``
        with no currency is not treated as CNY. Chinese price labels still are.
        """
        if not raw_text:
            return QuotedPrice(None, None, None, None)
        found: list[tuple[int, int, str, Any]] = []
        label = cls._PRICE_LABEL.search(raw_text)
        if label and label.group(3):
            found.append((label.start(), 0, "label", label))
        prefix = cls._PRICE_PREFIX.search(raw_text)
        if prefix and prefix.group(2):
            found.append((prefix.start(), 1, "prefix", prefix))
        suffix = cls._PRICE_SUFFIX.search(raw_text)
        if suffix and suffix.group(1):
            found.append((suffix.start(), 2, "suffix", suffix))
        if not found:
            return QuotedPrice(None, None, None, None)
        found.sort()
        _start, _rank, kind, match = found[0]
        try:
            if kind == "label":
                label_word = match.group(1) or ""
                amount = cls.parse_locale_number(match.group(3))
                token = match.group(2) or match.group(4)
                if token:
                    currency = cls._currency_code(token)
                else:
                    compact = re.sub(r"\s+", " ", label_word).strip().lower()
                    currency = None if compact in {"price", "unit price"} else "CNY"
            elif kind == "prefix":
                amount = cls.parse_locale_number(match.group(2))
                currency = cls._currency_code(match.group(1))
            else:
                amount = cls.parse_locale_number(match.group(1))
                currency = cls._currency_code(match.group(2))
        except (TypeError, ValueError):
            return QuotedPrice(None, None, None, None)
        if amount is None or not math.isfinite(amount):
            return QuotedPrice(None, None, None, None)
        return cls._settle_quote(amount, currency)

    @staticmethod
    def _quoted_kwargs(quote: "QuotedPrice") -> dict:
        return {
            "mentioned_price": quote.cny_amount,
            "quoted_amount": quote.amount,
            "mentioned_currency": quote.currency,
            "price_currency_note": quote.note,
        }

    @staticmethod
    def _mixed_price_phrases(quote: "QuotedPrice") -> list:
        if quote.amount is None:
            return []
        if quote.cny_amount is not None and quote.currency in (None, "CNY"):
            return [f"Price ¥{quote.cny_amount:.2f}"]
        if quote.currency:
            return [f"Price {quote.amount:.2f} {quote.currency}"]
        return [f"Price {quote.amount:.2f}"]

    @classmethod
    def extract_demanded_moq(cls, raw_text: str) -> Optional[int]:
        """Prefer a stated MOQ over an order quantity.

        ``for 1000 pcs, MOQ 500`` is a 500-piece minimum, not a 1000-piece one.
        A bare ``500個`` still counts, because Chinese quotes often omit the word.
        """
        if not raw_text:
            return None
        explicit = cls._EXPLICIT_MOQ.search(raw_text)
        if explicit and explicit.group(1):
            return cls._clamp_moq(cls.parse_locale_number(explicit.group(1)))
        attached = cls._QTY_BEFORE_MOQ_WORD.search(raw_text)
        if attached and attached.group(1):
            return cls._clamp_moq(attached.group(1))
        for match in cls.MOQ_PATTERN.finditer(raw_text):
            prefix = raw_text[max(0, match.start() - 24):match.start()]
            if re.search(
                r"(?:for|order(?:\s+(?:of|qty|quantity))?|订|訂)\s*$",
                prefix,
                re.IGNORECASE,
            ):
                continue
            return cls._clamp_moq(match.group(1))
        return None

    @classmethod
    def _clamp_moq(cls, value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if not cls._is_finite_number(value):
            return None
        val = float(value)
        if cls.MIN_DEMANDED_MOQ <= val <= cls.MAX_DEMANDED_MOQ:
            return int(val)
        return None

    @classmethod
    def _clamp_mold_fee(cls, value: Any) -> Optional[float]:
        if value is None or isinstance(value, bool):
            return None
        if not cls._is_finite_number(value):
            return None
        val = float(value)
        if cls.MIN_DEMANDED_MOLD_FEE < val <= cls.MAX_DEMANDED_MOLD_FEE:
            return val
        return None

    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        if not cls._is_finite_number(value):
            return 0.0
        return max(0.0, min(1.0, float(value)))

    @classmethod
    def extract_immediate_escalation(cls, raw_text: str) -> bool:
        """[H3] 與長度無關的合規升級閘門：是否需要升級（離線分流或材料降級）。"""
        return cls.full_text_escalation_intent(raw_text) is not None

    @classmethod
    def full_text_escalation_intent(cls, raw_text: str) -> Optional[VendorTacticEnum]:
        """
        [H3 修正] 對**完整**原文（未截斷）判定需要升級的意圖，並回傳正確的意圖枚舉。

        舊版先在 `_classify_impl` 把輸入截斷到 1000 字元，之後才做安全樣式比對，
        因此 1600 字的閒聊後接「加個微信私下聊，走線下轉私帳」不會觸發任何風控
        （實測：intent=NORMAL_COUNTER_OFFER、requires_escalation=False、無熔斷、
         無通知，而**完整原文仍被持久化**為對手回合）。
        knowledge/compliance_policy.md 的「Off-platform diversion → Freeze. Escalate.」
        因此可僅靠長度打敗。

        截斷可以限制**語意**比對器的成本，但不得限制**安全**閘門。本方法對完整
        字串套用 Stage-1 的線性樣式（見 `_safety_full_text_pattern` 為何必須繞過
        `SafeLinearPattern` 的內建截斷）。
        """
        if not raw_text:
            return None
        divert_re = _safety_full_text_pattern(cls.PATTERNS[0][1])
        for m in divert_re.finditer(raw_text):
            prefix = raw_text[max(0, m.start() - 8):m.start()]
            suffix = raw_text[m.end():min(len(raw_text), m.end() + 12)]
            if re.search(r"(?:不用走|不走|走|不用|別|别|不|免|無須|无需|不加|非|免了|不需|暫不|暂不)\s*$", prefix):
                continue
            if re.search(r"^\s*(?:就算了|免了|不用|就不必|不考慮|先不加|暫不)", suffix):
                continue
            return VendorTacticEnum.OFFLINE_BYPASS_DIVERT
        if cls.extract_material_degrade_signal(raw_text):
            return VendorTacticEnum.MATERIAL_SPEC_DEGRADE
        return None

    @classmethod
    def extract_material_degrade_signal(cls, raw_text: str) -> bool:
        """對完整字串（不截斷）偵測材料降級訊號，含否定語境豁免。"""
        if not raw_text:
            return False
        degrade_re = _safety_full_text_pattern(cls.PATTERNS[1][1])
        for m in degrade_re.finditer(raw_text):
            prefix = raw_text[max(0, m.start() - 6):m.start()]
            if re.search(r"(?:不做|不用|不採用|不采用|非|不收|排除|拒絕|拒绝)\s*$", prefix):
                continue
            return True
        return False

    # ------------------------------------------------------------------
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

        # 憑證必須與端點配對。VLLM_*（AMD tokenfactory / 自架 vLLM）優先；
        # Evolus 金鑰只打 evolus.ai。__FILL_ME__ 視為未配置，避免離線 demo 外呼。
        from .llm_config import (
            body_overrides,
            model_calls_paused,
            note_model_refusal,
            resolve_chat_endpoint,
            take_model_call,
        )

        cls.last_llm_error = None
        cls.last_llm_latency_ms = None
        _t0 = None

        endpoint = resolve_chat_endpoint()
        if endpoint is None:
            return None
        if model_calls_paused() is not None:
            cls.last_llm_error = "model calls are paused: the service refused the key or its credit is spent"
            return None
        if not take_model_call():
            cls.last_llm_error = "today's live-model budget on this server is spent"
            return None
        base_url = endpoint["base_url"]
        api_key = endpoint["api_key"]

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
            import time as _time
            import requests
            headers = {"Content-Type": "application/json"}
            headers["Authorization"] = f"Bearer {api_key}"
            # [P0 REGRESSION] 舊版 timeout=30.0。實測同一端點（Qwen3.8-Flash-Next，
            # reasoning 模型）對本提示需 30–48 秒，於是請求必定在 30 秒逾時，
            # 例外被 `except Exception: pass` 吞掉，API 在 31 秒後回 200 +
            # stage2_available=false —— 看起來像「模型判斷不需要 Stage-2」，
            # 實際上是每次都被客戶端自己切斷。現在 timeout 可配置（預設 90 秒），
            # 且失敗原因會記錄並可經 last_llm_error 回報。
            payload = {
                "model": endpoint["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": int(os.getenv("VLLM_MAX_TOKENS", "500")),
            }
            payload.update(body_overrides(endpoint))
            _t0 = _time.perf_counter()
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=float(os.getenv("VLLM_TIMEOUT_SECONDS", "90")),
            )
            cls.last_llm_latency_ms = round((_time.perf_counter() - _t0) * 1000, 1)
            if resp.status_code != 200:
                note_model_refusal(resp.status_code)
                cls.last_llm_error = (
                    f"Stage-2 endpoint returned HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:200]}"
                )
                logger.warning("Stage-2 LLM call failed: %s", cls.last_llm_error)
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            if not content or not content.strip():
                cls.last_llm_error = (
                    "Stage-2 endpoint returned an empty message content "
                    "(reasoning models may exhaust max_tokens before answering; "
                    "raise VLLM_MAX_TOKENS)"
                )
                logger.warning("Stage-2 LLM call produced empty content")
                return None
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                intent_str = data.get("primary_intent", "NORMAL_COUNTER_OFFER")
                intent_enum = getattr(VendorTacticEnum, intent_str, VendorTacticEnum.NORMAL_COUNTER_OFFER)
                # [C5 修正] Stage-2 的 LLM 欄位過去直接流入決策輸入，未套用
                # Stage-1 在 `_classify_impl` 中對 `mentioned_price` 早已施行的
                # 0.1..50000 夾制。單一幻覺欄位因此足以製造出「看起來已成交」
                # 的狀態（實測 negative / 超界值原樣存入並觸發成交通知）。
                # LLM 輸出不得繞過 Stage-1 已強制的不變量。
                return InboundTacticalClassification(
                    primary_intent=intent_enum,
                    confidence=cls._clamp_confidence(data.get("confidence", LLM_DEFAULT_CONFIDENCE)),
                    mentioned_price=cls._clamp_price(data.get("mentioned_price")),
                    demanded_moq=cls._clamp_moq(data.get("demanded_moq")),
                    demanded_mold_fee=cls._clamp_mold_fee(data.get("demanded_mold_fee")),
                    tactical_rationale=data.get("tactical_rationale", "LLM zero-shot inference"),
                    requires_escalation=bool(data.get("requires_escalation", False)),
                )
            cls.last_llm_error = "Stage-2 endpoint returned no parsable JSON object"
            logger.warning(
                "Stage-2 LLM returned non-JSON content (first 200 chars): %r",
                (content or "")[:200],
            )
        except Exception as exc:
            # 絕不無聲吞錯：記錄型別與訊息（不含憑證），並留下可供 API 回報的狀態。
            import time as _time
            if _t0 is None:
                _t0 = _time.perf_counter()
            cls.last_llm_latency_ms = round((_time.perf_counter() - _t0) * 1000, 1)
            cls.last_llm_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Stage-2 LLM call raised: %s", cls.last_llm_error)
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

        [PERFORMANCE] Stage-2 是同步網路呼叫。已記錄的 AMD tokenfactory 延遲
        在十秒級（見 results/real_llm_benchmark_100.json 的 llm_avg_latency_s，
        以及後來 15–45 秒的複測筆記），不適合放在同步 HTTP 路徑上。請改用
        `classify_stage1_only()`，需要時再以 `classify_with_llm()` 非同步補強。
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

        # 0. 安全防護：[H3] 合規安全閘門必須先看**完整原文**，之後才做長度截斷。
        #    舊版順序相反（先截斷再比對），使超長閒聊可稀釋並打敗升級閘門。
        full_text = raw_text
        escalation_intent = cls.full_text_escalation_intent(full_text)
        if escalation_intent is not None:
            # [假資料修正 §5.5] 即使走合規安全閘門，也把對方報出的模具費一併
            # 抽出並回傳 —— 否則這條「先攔截」的高優先路徑會把數字丟掉，
            # 下游取不到金額就退回舊版的寫死值。安全攔截與數值保真是兩件事。
            gate_mold_fee = None
            try:
                _mf = cls.MOLD_FEE_PATTERN.search(full_text[:1000])
                if _mf and _mf.group(1):
                    _v = float(_mf.group(1))
                    gate_mold_fee = _v if _v > 0 else None
            except (ValueError, TypeError):
                gate_mold_fee = None
            return InboundTacticalClassification(
                primary_intent=escalation_intent,
                confidence=REGEX_CONF_OFFLINE if escalation_intent == VendorTacticEnum.OFFLINE_BYPASS_DIVERT else REGEX_CONF_SPEC,
                mentioned_price=None,
                demanded_mold_fee=gate_mold_fee,
                tactical_rationale=(
                    "Full-length compliance gate matched before semantic classification "
                    "(length-independent escalation); original text length "
                    f"{len(full_text)} exceeds the semantic truncation limit"
                ),
                requires_escalation=True,
            )

        # 1. 語意分類的成本上限：截斷超長輸入，杜絕 ReDoS 潛在回溯與內存佔用
        MAX_INPUT_LEN = 1000
        if len(raw_text) > MAX_INPUT_LEN:
            raw_text = raw_text[:MAX_INPUT_LEN]

        # 1. 抽取報價。mentioned_price 只放可給求解器的人民幣；外幣另記。
        quote = cls.extract_quoted_price(raw_text)
        quoted = cls._quoted_kwargs(quote)

        # 2. 抽取 MOQ 需求。明示的 MOQ / min order 優先於「for N pcs」的訂購量。
        moq_val = cls.extract_demanded_moq(raw_text)

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
                demanded_moq=moq_val,
                demanded_mold_fee=mold_fee_val,
                is_mixed_signal=False,
                tactical_rationale="Matched hard offline divert pattern in Stage 1",
                requires_escalation=True,
                **quoted,
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
            if onnx_conf >= ONNX_ACCEPT_CONFIDENCE and onnx_intent != VendorTacticEnum.NORMAL_COUNTER_OFFER:
                is_mixed = (quote.amount is not None or mold_fee_val is not None) and onnx_intent in (
                    VendorTacticEnum.IDENTITY_PROBE_TEST,
                    VendorTacticEnum.CRY_POVERTY_COST_HIKE,
                    VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
                )
                mixed_parts = cls._mixed_price_phrases(quote)
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
                    demanded_moq=moq_val,
                    demanded_mold_fee=mold_fee_val,
                    is_mixed_signal=is_mixed,
                    mixed_signal_note=mixed_note,
                    tactical_rationale=f"On-Device ONNX semantic inference (conf={onnx_conf:.2f})",
                    requires_escalation=escalate,
                    **quoted,
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
                is_mixed = (quote.amount is not None or mold_fee_val is not None) and intent in (
                    VendorTacticEnum.IDENTITY_PROBE_TEST,
                    VendorTacticEnum.CRY_POVERTY_COST_HIKE,
                    VendorTacticEnum.TIER_VOLUME_BLACKMAIL,
                )
                mixed_parts = cls._mixed_price_phrases(quote)
                if mold_fee_val:
                    mixed_parts.append(f"Mold Fee ¥{mold_fee_val:.0f}")
                mixed_note = (
                    f"Extracted {', '.join(mixed_parts)} while deflecting {intent.value}"
                    if is_mixed else None
                )
                return InboundTacticalClassification(
                    primary_intent=intent,
                    confidence=conf,
                    demanded_moq=moq_val,
                    demanded_mold_fee=mold_fee_val,
                    is_mixed_signal=is_mixed,
                    mixed_signal_note=mixed_note,
                    tactical_rationale=rationale,
                    requires_escalation=escalate,
                    **quoted,
                )

        # 4. Stage 2: 若正則未命中且啟用 LLM，進行深度語意推理
        if enable_llm_fallback:
            llm_res = cls.classify_with_llm(raw_text)
            if llm_res:
                # A model figure must not become the CNY price when stage-1
                # already saw a quote it refused to convert.
                if quote.amount is not None and quote.cny_amount is None:
                    llm_res.mentioned_price = None
                elif quote.cny_amount is not None and llm_res.mentioned_price is None:
                    llm_res.mentioned_price = quote.cny_amount
                llm_res.quoted_amount = quote.amount
                llm_res.mentioned_currency = quote.currency
                llm_res.price_currency_note = quote.note
                return llm_res

        # 5. 常規報價對話保底
        # [誠實揭露] 此分支表示「正則階梯全未命中 且 (無 LLM 或 LLM 未回)」——我們並未
        # 真正識別出這是正常報價。舊版以 confidence=0.70 固定回 NORMAL_COUNTER_OFFER，
        # 會在沒有任何判定信號時偽裝成「高把握的正常報價」。現改為低把握預設，
        # 讓下游可藉 confidence 區分「確信的正常報價」與「未識別、暫按正常處理」。
        return InboundTacticalClassification(
            primary_intent=VendorTacticEnum.NORMAL_COUNTER_OFFER,
            confidence=REGEX_CONF_UNRECOGNIZED,
            demanded_moq=moq_val,
            # [假資料修正 §5.5] 舊版此處**漏傳** demanded_mold_fee，於是「未識別、
            # 暫按正常處理」這條最高頻路徑會把已成功抽取的模具費靜默丟棄 ——
            # 造成下游（logrolling）永遠取不到對方報出的金額。
            # 這正是「幻燈片上有數字、實際不可用」的同類問題：抽取成功了卻沒傳出去。
            demanded_mold_fee=mold_fee_val,
            tactical_rationale="No pattern/LLM matched; unclassified message treated as normal",
            requires_escalation=False,
            **quoted,
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
    posterior_reserve_std: float = Field(default=POSTERIOR_STD_PLACEHOLDER, description="對手歸一化底價後驗標準差")
    bayesian_confidence: float = Field(default=LLM_DEFAULT_CONFIDENCE, ge=0.0, le=1.0, description="底價估計置信度")
    estimated_floor_price: Optional[float] = Field(default=None, description="估算對手硬底價 (元)")

    # 戰術維度與條款
    tactical_mode: TacticalModeEnum = Field(default=TacticalModeEnum.LEAN_IN, description="主戰術模式")
    product_name: str = Field(default="項目", description="標的品名")
    supplier_contact_name: str = Field(default="老闆", description="對手工廠稱呼")
    lead_days: Optional[int] = Field(default=None, ge=1, description="承諾交期天數。未設定時渲染為 not set。")
    deposit_pct: float = Field(default=BASELINE_DEPOSIT_PCT, ge=0.0, le=1.0, description="承諾定金比例")
    moq: Optional[int] = Field(default=None, ge=1, description="採購起訂批量。未設定時渲染為 not set。")
    mold_fee_exempted: bool = Field(default=False, description="是否要求減免模具/打樣費")
    waive_mold_fee_amount: float = Field(default=0.0, description="要求減免之模具費金額")

    # 博弈訊號與意圖反擊
    counterpart_intent: VendorTacticEnum = Field(
        default=VendorTacticEnum.NORMAL_COUNTER_OFFER, description="辨識出之對手套路"
    )
    counterpart_offered_price: Optional[float] = Field(default=None, description="對手當前報價")
    competing_lead_price: Optional[float] = Field(
        default=None,
        description=(
            "備選供應商錨定價。僅在使用者/呼叫端明確提供有憑據的比價來源時填值；"
            "不得由 target_price / walk_away_ceiling / 對手報價等內部價格公式推算，"
            "否則渲染器會以「合格樣品已給到 ¥X」的既成事實語氣對外送出一個"
            "不存在的報價。未提供真實比價來源時保持 None，走「同規格同行報價」"
            "的不帶金額話術。"
        ),
    )
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
            if MIN_MENTIONED_PRICE <= val <= MAX_MENTIONED_PRICE:
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
                dep_val = float(dep_match.group(1)) / 100
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
        for m in re.finditer(
            # [FIX] 價格掃描：移除範圍介系詞 to、加詞邊界與量詞負向斷言，
            # 修「can you do 5000 units at 13.8」把數量 5000 當報價的誤判。
            r"(?<![\w.])(?:do|at|offer|price|\$|¥)\s*\$?\s*(\d+(?:\.\d{1,2})?)"
            r"(?!\d)(?!\s*(?:units?|pcs|pieces|sets?|k\b|件|把|个|個|只|隻))",
            text,
            re.IGNORECASE,
        ):
            try:
                p_cand = float(m.group(1))
                if p_cand >= 100_000:
                    continue  # 量級 sanity：單價不可能上萬；多為數量/型號雜訊
                if p_cand != found_days and (found_dep is None or abs(p_cand - found_dep * 100) > MENTION_CONFUSION_TOLERANCE):
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
    """基於 Python AST 抽象語法樹編譯之策略對象數值不變量硬護欄。

    [C3 修正] 舊版以 `assert` 敘述實作，並在 `except AssertionError` 中轉譯為
    `GuardrailViolationError`。`assert` 是**可被直譯器最佳化移除**的敘述：
    `python -O` 底下 assert 全數剝除，`verify()` 對任何荒謬對象都回傳 True
    （實測 `authorized_price=999.0, walk_away_ceiling=15.0, deposit_pct=0.95`）。
    安全不變量不得依賴直譯器的 `-O` 旗標，故改用顯式 `if ...: raise`。
    """

    _GUARDRAIL_SNIPPET = (
        "def _ast_check(auth_p, ceiling, dep, lead):\n"
        "    if not auth_p > 0.0:\n"
        "        raise ValueError('Authorized price must be positive')\n"
        "    if not auth_p <= ceiling + 1e-4:\n"
        "        raise ValueError('Authorized price exceeds walk-away ceiling')\n"
        "    if not dep <= 0.6001:\n"
        "        raise ValueError('Deposit percentage exceeds 60% system safety threshold')\n"
        "    if lead is not None and not lead >= 1:\n"
        "        raise ValueError('Lead time must be at least 1 day')\n"
        "    return True\n"
    )

    @classmethod
    def verify(cls, action_obj: TacticalActionObject) -> bool:
        """
        以 AST 編譯檢查樹驗證策略對象之硬性不變量：
        1. 授權出價必須嚴格小於等於最大容忍上限 (加上浮點微小容差)。
        2. 授權出價必須大於零。
        3. 定金比例不得突破 60% 物理極限。
        4. 交期天數必須大於等於 1 天。

        [已知邊界/誠實揭露] `walk_away_ceiling` 本身是**被驗證對象的輸入欄位**，
        因此任何能建構 `TacticalActionObject` 的呼叫端都能順帶寫入自己的上限。
        本護欄僅檢查「對象自身的內部一致性」，無法證明該上限等於獨立授權來源
        （例如 `NegotiationSession.spec.ceiling_price` 或 `CorporateMandate`）。
        需要獨立交叉驗證的呼叫端應另外比對，不應把本護欄當成授權來源的證明。
        """
        tree = ast.parse(cls._GUARDRAIL_SNIPPET)
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
        except ValueError as exc:
            raise GuardrailViolationError(f"AST Invariant Violation: {exc}") from exc
        return True


def _term(value, suffix: str = "") -> str:
    """Render a term the caller set. An omitted term stays omitted."""
    if value is None:
        return "not set"
    return f"{value}{suffix}"


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
        # [C2] 品名由呼叫端 (POST /api/v1/sessions) 提供且以 f-string 原樣插值，
        # 因此必須先中和其中夾帶的貨幣化數字，否則會繞過文本層數值護欄。
        product = sanitize_product_name(obj.product_name)
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
                    f"首批 {_term(obj.moq)} 件試單看品控，跑順了下半年翻單一萬起。¥{p_val:.2f} 出廠價，"
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
                f"首批 {_term(obj.moq)} 件試單預算嚴格卡在 ¥{p_val:.2f}，現款現結走平台擔保，能排單我們即刻立項。"
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
            # [5.2 修正] Logrolling 引擎實際交換的正是「定金比例」與「起訂批量」，
            # 舊版話術只提交期與模具費，忠實度不足（決策與話術不一致）。
            mold_clause = f"，且首批免除模具費 ¥{obj.waive_mold_fee_amount:.0f}" if obj.mold_fee_exempted else ""
            return (
                f"{contact}，理解貴廠在製程與排單成本上的考量。若單價落定在 ¥{p_val:.2f}，"
                f"我方展現充分誠意：首批訂單批量拉升至 {_term(obj.moq)} 件，今天特批預付 {deposit_pct_str} 定金現款秒結排單。"
                f"但作為對等條件，交期必須壓縮至 {_term(obj.lead_days)} 天內交付{mold_clause}。"
                f"若貴方確認，今天立即蓋章回傳 PO；否則我方只得啟動備案產能。"
            )

        elif obj.tactical_mode == TacticalModeEnum.CROSS_ANCHOR:
            # [P1 REGRESSION] 舊版在沒有真實比價來源（competing_lead_price=None）時
            # 把佔位字串塞進同一個句子，產出「合格樣品已給到 同規格同行報價。」
            # 這是語法破碎的中文，而且「已給到」仍暗示我方已收到一份不存在的報價。
            # 現在分成兩個完整句子：有真實來源才陳述金額；沒有來源只說「正在評估」，
            # 不宣稱已取得任何樣品或報價（保留測試所需的「同規格同行報價」片語）。
            if obj.competing_lead_price:
                anchor_sentence = (
                    f"我們同步評估了同規格同行，合格樣品已給到 ¥{obj.competing_lead_price:.2f}。"
                )
            else:
                anchor_sentence = "我們正同步評估同規格同行報價，但目前不便引用未經確認的金額。"
            return (
                f"你們工藝我們很看好，但報價確實超出採購預算。{anchor_sentence}"
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


def supplier_message_language(text: str) -> str:
    """English when the supplier wrote in Latin; Chinese when they wrote in Han characters."""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    if latin == 0 and cjk == 0:
        return "zh"
    return "en" if latin > cjk else "zh"


def render_supplier_reply(obj: TacticalActionObject, inbound_text: str) -> str:
    """Draft the buyer would paste. The price is the solver's authorized price.

    The wording follows the supplier's language. It does not invent a second price.
    """
    price = float(obj.authorized_price)
    if supplier_message_language(inbound_text) == "en":
        return (
            f"Thank you. Our offer is CNY {price:.2f} per piece. "
            "That figure is the engine price in CNY. "
            "It is not a conversion of your quote unless a rate was configured. "
            "A buyer still has to approve it before anything is sent."
        )
    return ChineseSurfaceRenderer.render(obj)


def draft_for_approval(
    price: float,
    *,
    language: str,
    inbound_text: str,
    stored_reply: Optional[str],
    stored_price: Optional[float],
    product_name: str,
    ceiling: float,
    moq: int,
) -> str:
    """The paste-back draft after a human approves.

    The language is the one stored with the supplier message. When the approved
    figure is the stored engine price, the stored draft is returned as-is.
    """
    lang = language if language in {"en", "zh"} else supplier_message_language(inbound_text)
    if (
        isinstance(stored_reply, str)
        and stored_reply.strip()
        and stored_price is not None
        and f"{float(stored_price):.2f}" == f"{float(price):.2f}"
    ):
        return stored_reply
    if lang == "en":
        probe = "Thank you for the quote."
    else:
        probe = inbound_text if supplier_message_language(inbound_text) == "zh" else "最低出廠價"
    obj = TacticalActionObject(
        authorized_price=float(price),
        walk_away_ceiling=float(ceiling),
        product_name=product_name or "Item",
        moq=int(moq) if moq else 500,
        source_round_id="APPROVAL",
    )
    return render_supplier_reply(obj, probe)


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
            f"• Inbound Offer: {offer_str} vs Recommended Counter: {t_price_str}\n"
            f"• Walk-Away Ceiling: {ceiling_str}\n"
            f"• Bayesian Belief: Estimated Floor: {floor_str} (Confidence: {obj.bayesian_confidence:.1%})\n"
            f"• Risk Telemetry: Breakdown Risk: {obj.breakdown_risk:.1%} | Expected Margin: ¥{obj.expected_margin:.2f}\n"
            f"• Active Tactical Mode: {obj.tactical_mode.value}\n"
            f"• Inbound Counterpart Intent: {obj.counterpart_intent.value}\n"
            f"{mixed_note}"
            f"• Package Terms (OUR proposal, not counterpart-confirmed): "
            f"Lead Time: {_term(obj.lead_days, 'd')} | Deposit: {obj.deposit_pct*100:.0f}% | MOQ: {_term(obj.moq)}"
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
        # [假資料修正] lead_days / deposit_pct 是「我方條款」，不是對方報出的條件。
        # 對方從未提交期或定金時，這兩個欄位只是模型預設／我方規劃值。舊版把它
        # 們與已確定的 Settled Price 並列為 "Package Terms"，讓主管以為這些數字
        # 都已經對方確認過。現在明確標示為「我方提案條款」，未經對方確認。
        package_terms = (
            f"• Package Terms (OUR proposal, not counterpart-confirmed): "
            f"Lead Time: {_term(obj.lead_days, 'd')} | Deposit: {obj.deposit_pct*100:.0f}% | MOQ: {_term(obj.moq)}"
        )
        card = (
            f"=== [TradeFlow Action Execution Confirmation] ===\n"
            f"• Source Round ID: {obj.source_round_id or 'N/A'}\n"
            f"• Executed Directive: {command_executed}\n"
            f"• Target Product: {obj.product_name}\n"
            f"• Settled Authorized Price: ¥{obj.authorized_price:.2f}\n"
            f"{package_terms}\n"
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
            f"Quote: {quote_str} vs Recommended Counter: ¥{obj.authorized_price:.2f} | "
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
                "confidence": REGEX_CONF_OFFLINE,
                "stiff_phrases": stiff_hits,
                "leak_phrases": leak_hits,
                "audit_mode": "static_deterministic",
                "critique": "Detected stiff translation syntax or foreign buyer leak words.",
            }

        # Stage 2 LLM 對抗性語意審計。佔位符金鑰不外呼；金鑰與主機必須配對。
        import os
        from .llm_config import body_overrides, note_model_refusal, resolve_chat_endpoint, take_model_call
        endpoint = resolve_chat_endpoint(default_model="Qwen/Qwen2.5-14B-Instruct") if enable_llm else None
        if endpoint is not None and not take_model_call():
            endpoint = None
        if endpoint is not None:
            import json
            import requests
            base_url = endpoint["base_url"]
            api_key = endpoint["api_key"]
            prompt = (
                "You are an experienced, shrewd factory sales manager in Ningbo/Shenzhen. "
                "Analyze the following procurement reply to determine if it sounds like an authentic domestic Chinese buyer "
                "or if it sounds like a translated foreigner / suspicious outsider.\n"
                f"Procurement Reply: \"{chinese_text}\"\n"
                "Output STRICT JSON:\n"
                "{\"is_native\": bool, \"confidence\": float (0.0-1.0), \"critique\": string}"
            )
            try:
                headers = {"Content-Type": "application/json"}
                headers["Authorization"] = f"Bearer {api_key}"
                payload = {
                    "model": endpoint["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 120,
                }
                payload.update(body_overrides(endpoint))
                resp = requests.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=float(os.getenv("VLLM_TIMEOUT_SECONDS", "90")),
                )
                note_model_refusal(resp.status_code)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    m = re.search(r"\{.*\}", content, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        return {
                            "is_native": bool(data.get("is_native", True)),
                            "confidence": float(data.get("confidence", REGEX_CONF_SPEC)),
                            "stiff_phrases": [],
                            "leak_phrases": [],
                            "audit_mode": "llm_adversarial",
                            "critique": data.get("critique", "LLM validated authentic buyer phrasing"),
                        }
            except Exception as exc:
                logger.warning(
                    "Semantic-nativeness LLM audit failed (%s); using the static result.",
                    type(exc).__name__,
                )

        return {
            "is_native": True,
            "confidence": REGEX_CONF_IDENTITY,
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
    """確保生成文本中包含且僅包含法定授權數值，禁止擅自承諾或妥協。

    [獨立審計修正 2.1/2.2/C1] 舊版本質上只是一個「上限檢查 + 7 個字面子字串」：
      * 只拒絕 `p > walk_away_ceiling + tolerance`，因此「授權價旁再附一個更低承諾價」
        完全放行（實測："¥13.00 出廠價可以，另外我們願意 ¥9.50 出廠價成交" → PASSED）。
      * 價格正則只認貨幣**前綴**與 `元|塊|/件|/把|/個` **後綴**，USD 前綴以外的
        貨幣後綴（`15.50 RMB`）、`/pcs`、`/套` 等格式一律漏檢。
      * 禁用詞只比對 7 個確切字串，改一個字即繞過（"私底下打款"、"走私人帳戶"、
        "不開票"、"用二料" 皆實測 PASS）。

    本版改為：上下界雙向夾制 + 更完整的貨幣格式掃描 + 可擴充的禁用片語集，
    並明確區分「我方承諾價」與「非承諾語境下的數字」（成本費目、對手報價、遙測欄位），
    後者不納入授權價位移的判定。
    """

    # 向後相容：原 7 個字面字串仍然存在並繼續以子字串比對。
    FORBIDDEN_COMPROMISE_WORDS = [
        "那我們再退一步", "再便宜點", "私下轉帳", "不開發票", "換次級料", "用回料", "私聊"
    ]

    # [2.2] 片語級禁用模式集（確定性/離線，不使用 LLM）。
    # 覆蓋原文的近義改寫：私底下/私人帳戶/私人帳號/不開票/用二料/二料/回料/次料/
    # 走私單/私下打款/線下付款 等。
    #
    # [效能] 這些模式**刻意不含 `\s*`**：相鄰的可空 `\s*` 會造成歧義回溯，
    # 對超長空白草稿呈二次成長（本護欄正是被要求不得成為 DoS 向量的元件）。
    # 字元間空白改由 `_scan_forbidden_phrases` 先壓縮掉再比對，
    # 因此英文高管卡上的「privately / off the books」間隔寫法仍無法規避。
    FORBIDDEN_COMPROMISE_PATTERNS: List[Any] = [
        re.compile(r"那?我們?再退一步"),
        re.compile(r"再(?:便宜|讓|让|低|減|减)(?:一)?(?:點|点|些|步)"),
        re.compile(r"私(?:下|底|自|人|聊)"),
        re.compile(r"(?:轉|转|走|匯|汇)?(?:入)?私(?:人|下|底)?(?:帳|账|戶|户|號|号)?"),
        re.compile(r"(?:線|线|走)下(?:付|打|結|结|走)"),
        re.compile(r"(?:私下|線下|线下|線|线)(?:付|打款|轉帳|转账|走帳|走账)"),
        re.compile(r"不(?:開|开)(?:發|发)?(?:票|發票|发票)"),
        re.compile(r"(?:換|换|用|走)(?:次|二|回)(?:料|貨|货)"),
        re.compile(r"(?:次|二|回)料"),
        re.compile(r"走私單|走私单|私單|私单"),
        re.compile(r"私(?:訊|讯|信)"),
    ]

    # 出現於價格**之前**時，代表該數字是成本費目 / 對手報價 / 遙測欄位 / 範例，
    # 而非我方主動承諾的成交單價。刻意**不**包含「給到 / 報價 / 單價」等承諾動詞，
    # 否則「我方願意給到 ¥9.50」會被誤判為非承諾而繞過下界。
    NON_COMMITMENT_PRE_CONTEXT = re.compile(
        r"(?:模具費|開模費|打樣費|樣品費|運費|物流費|加工費|模具|開模"
        r"|已給到|同行|對手|對方|貴廠報|工廠報|對手報價|對方報價"
        r"|Floor|Margin|Offer|Target|Ceiling|Inbound|Estimated|Expected"
        r"|Authorized|Quote|Risk|fee|tooling|Reply|example|e\.g)\s*[:：=,]?\s*[¥￥$]?\s*$",
        re.IGNORECASE,
    )

    # 片語被「拒絕」的語境。片語級黑名單無法理解語意，因此以同子句內的
    # **強拒絕**標記豁免，讓確定性防禦不會誤傷自家合規話術
    # （例如「私下微信恕不方便」、「驗收嚴檢拒收次料」）。
    #
    # [刻意保守] 只採認「強拒絕」詞，不採認弱否定（不用/不走/不加/不做/非）：
    # 攻擊字串本身常以弱否定開頭（"可以私下打款嗎？不用走系統"），若納入豁免
    # 反而製造新的繞過。誤傷弱否定的代價僅是降級到安全模板（fail closed）。
    STRONG_REFUSAL = re.compile(
        r"(?:拒收|拒絕|拒绝|嚴禁|严禁|禁止|杜絕|杜绝|恕不|不便|不予|不接受|不允許|不允许|勿)"
    )
    # 子句邊界（用於把豁免限制在同一語氣單位內，避免跨句誤豁免）。
    CLAUSE_BOUNDARY = re.compile(r"[。！？，；!?,;\n]")

    # ------------------------------------------------------------------
    @classmethod
    def _iter_price_spans(cls, text: str):
        """於文本中尋找貨幣化價格，回傳 (值, 起點, 終點)。"""
        if not text:
            return
        for m in PRICE_SCAN_PATTERN.finditer(text):
            for idx in range(1, (m.re.groups or 0) + 1):
                raw = m.group(idx)
                if raw:
                    try:
                        yield float(raw), m.start(), m.end()
                    except ValueError:
                        pass
                    break

    @classmethod
    def _is_commitment_price(cls, text: str, start: int, end: int) -> bool:
        """判斷此數字是否為「我方主動承諾的單價」，而非成本費目 / 對手報價 / 遙測欄位。"""
        pre = text[max(0, start - 10):start]
        if cls.NON_COMMITMENT_PRE_CONTEXT.search(pre):
            return False
        return True

    @classmethod
    def _scan_forbidden_phrases(cls, text: str, compact: str | None = None) -> None:
        """片語級禁用詞掃描（含拒絕語境豁免）。

        先在**正規化文字**（移除所有空白）上比對，讓「私 下 打 款」與
        「私底下打款」等效；同時因為模式本身不含 `\\s*`，對超長空白草稿仍為線性。
        拒絕語境豁免以「片語所屬子句」為範圍，且**只採認強拒絕詞**。
        ``compact`` 由呼叫端傳入以免重複壓縮；未傳入時在此計算。
        """
        if not text:
            return
        if compact is None:
            compact = re.sub(r"\s+", "", text)
        for pattern in cls.FORBIDDEN_COMPROMISE_PATTERNS:
            for m in pattern.finditer(compact):
                hit = m.group(0)
                # 以正規化座標定位所屬子句，並在其中尋找強拒絕標記。
                clause_start = 0
                for boundary in cls.CLAUSE_BOUNDARY.finditer(compact):
                    if boundary.end() <= m.start():
                        clause_start = boundary.end()
                    else:
                        break
                clause_end = len(compact)
                for boundary in cls.CLAUSE_BOUNDARY.finditer(compact):
                    if boundary.start() >= m.end():
                        clause_end = boundary.start()
                        break
                clause = compact[clause_start:clause_end]
                if cls.STRONG_REFUSAL.search(clause):
                    continue
                raise GuardrailViolationError(
                    f"Forbidden compromise phrase detected: '{hit}'"
                )

    # ------------------------------------------------------------------
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
        tolerance: float = PRICE_MENTION_TOLERANCE,
        authorized_floor: Optional[float] = None,
        allowed_values: Optional[Iterable[float]] = None,
    ) -> bool:
        """
        驗證草稿中是否含有非授權報價或敏感妥協。
        - 授權報價必須在文字中明確體現。
        - 文本中出現的所有**承諾單價**均不得高於上限 walk_away_ceiling。
        - [2.1 修正] 亦不得**低於**授權下限 `authorized_floor`；未提供時預設為
          `authorized_price`，因此「授權價旁再附一個更低承諾價」預設即被拒絕。
        - [2.1 修正] 不得出現第二個未授權的承諾單價（例如授權 13.00 卻另承諾 14.00）。
        - 不得含有私下/線下妥協片語（含近義改寫）。
        - `allowed_values` 可列舉非承諾語境仍需放行的已知數字（成本費目、遙測欄位）。
        """
        floor = authorized_price if authorized_floor is None else float(authorized_floor)
        tolerated = tuple(float(v) for v in (allowed_values or ()))

        # 1. 片語級禁用詞（原字面清單保底 + 近義模式集）
        # [SECURITY 2026-10-23] 保底層與近義層掃同一份**壓縮文字**。此前保底層
        # 掃原文，「換 次 級 料」靠字元間空白即可同時逃過兩層（近義模式刻意
        # 不含「次級料」）。壓縮一次、兩層共用，插入空白不再是繞過手段。
        compact = re.sub(r"\s+", "", draft_text)
        for word in cls.FORBIDDEN_COMPROMISE_WORDS:
            if word in compact:
                raise GuardrailViolationError(f"Forbidden compromise phrase detected: '{word}'")
        cls._scan_forbidden_phrases(draft_text, compact)

        # 2. 貨幣化價格掃描（更完整的格式覆蓋：C1）
        spans = list(cls._iter_price_spans(draft_text))
        prices_found = [v for v, _, _ in spans]

        def _is_allowed(v: float) -> bool:
            return any(abs(v - a) <= tolerance for a in (authorized_price,) + tolerated)

        has_authorized = any(abs(p - authorized_price) <= tolerance for p in prices_found)
        if not has_authorized:
            raise GuardrailViolationError(
                f"Draft lacks authorized target price ¥{authorized_price:.2f}"
            )

        for p, start, end in spans:
            if not cls._is_commitment_price(draft_text, start, end):
                continue
            if _is_allowed(p):
                continue
            if p > walk_away_ceiling + tolerance:
                raise GuardrailViolationError(
                    f"Price ¥{p:.2f} violates walk-away ceiling ¥{walk_away_ceiling:.2f}"
                )
            if p < floor - tolerance:
                raise GuardrailViolationError(
                    f"Price ¥{p:.2f} is below the authorized floor ¥{floor:.2f} "
                    f"(unauthorized concession)"
                )
            # 在 [floor, ceiling] 內但不等於授權價 -> 第二個未授權承諾單價
            raise GuardrailViolationError(
                f"Unauthorized secondary commitment price ¥{p:.2f} "
                f"(authorized target is ¥{authorized_price:.2f})"
            )

        return True

    @classmethod
    def validate_bilingual(
        cls,
        zh_text: str,
        en_text: str,
        authorized_price: float,
        walk_away_ceiling: float,
        tolerance: float = PRICE_MENTION_TOLERANCE,
        authorized_floor: Optional[float] = None,
        allowed_values: Optional[Iterable[float]] = None,
    ) -> bool:
        """
        [2.3 修正] 雙表面數值護欄入口點。

        舊版 `main.py` 只對中文 `auto_reply_text` 呼叫 `validate_reply`，而推送給
        主管手機的 `EnglishExecutiveRenderer.render_executive_card` 產物
        （`src/transport/telemetry.py:55-57` 的實際決策依據）完全未受檢。
        本方法讓中英文兩張表面使用同一組不變量，呼叫端一次驗證兩者。

        英文卡上的成本/遙測欄位（Estimated Floor / Expected Margin / Inbound Offer /
        Walk-Away Ceiling / Reply 範例）由 `NON_COMMITMENT_PRE_CONTEXT` 歸類為
        非承諾數字，不納入授權價位移判定；真正的承諾單價仍受上下界夾制。
        """
        cls.validate_reply(
            zh_text,
            authorized_price=authorized_price,
            walk_away_ceiling=walk_away_ceiling,
            tolerance=tolerance,
            authorized_floor=authorized_floor,
            allowed_values=allowed_values,
        )
        cls.validate_reply(
            en_text,
            authorized_price=authorized_price,
            walk_away_ceiling=walk_away_ceiling,
            tolerance=tolerance,
            authorized_floor=authorized_floor,
            allowed_values=allowed_values,
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
        """根據戰術類型、意圖反擊槓桿與鎖死價格生成地道國產採購話術。

        注意：`competing_lead_price` 僅在使用者傳入**真實比價來源**時才會
        渲染成金額；預設 None，話術走「同規格同行報價」的不帶金額表述。
        本方法絕不自行由 `authorized_price` 等內部價格反推該數字。
        """
        t_mode = (
            TacticalModeEnum(tactic)
            if tactic in TacticalModeEnum.__members__
            else TacticalModeEnum.LEAN_IN
        )
        action_obj = TacticalActionObject(
            authorized_price=authorized_price,
            walk_away_ceiling=authorized_price * GUARDRAIL_CEILING_MULTIPLE,
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
