"""TradeFlow — Live market data refresher (real sources, no API keys required).

取代舊的 `scripts/fetch_price_anchor.py`（該腳本使用已失效的
`comtradeplus.un.org` 端點，導致 `seed_data/price_anchor.json` 長期處於
FETCH_FAILED 狀態）。

資料來源（皆實測可用、免費、無需註冊）：
1. UN Comtrade 公開預覽 API —— 中國海關申報之真實出口金額與重量
   https://comtradeapi.un.org/public/v1/preview/C/A/HS
2. open.er-api.com —— 即時 USD/CNY 匯率

[方法論與誠實邊界 — 重要]
--------------------------------
實測後確認下列事項，本腳本的設計即建立在此基礎上：

a) **HS 代碼的數量欄位不可靠，重量欄位可用。**
   HS6601 的 `qty` 回傳 0（unitCode=-1），只有 `netWgt` 有效。
   部分子代碼（660191/660199）雖回傳 qty（unitCode=5），但交叉驗證後發現
   其隱含「每件平均重量」在年度間劇烈跳動（348g → 794g），
   證明這是**申報口徑不一致造成的統計假象，不是真實單價變動**。
   → **因此本腳本只採用 `$/kg`，並明確拒用 `$/pc`。**

b) **重量為估計值，非實測值。**
   API 回傳 `isNetWgtEstimated: true`，代表 netWgt 多為推算而非逐筆申報。
   → 本腳本會將此旗標寫入輸出，不得對外表述為「精確單價」。

c) **這是宏觀貿易錨點，不是工廠報價。**
   $/kg 反映的是「中國出口該品類的整體價格水準」，用途是**驗證供應商
   「原料暴漲」說法的合理性**，而非直接推導某把傘的應付單價。
   BOM 層級的物理成本仍須由領域專家維護（見 knowledge/spec_baseline.md）。

d) **Comtrade 有速率限制（HTTP 429）。** 本腳本內建退避重試。

用法：
    python scripts/refresh_market_data.py              # 更新全部
    python scripts/refresh_market_data.py --years 5    # 指定回溯年數
    python scripts/refresh_market_data.py --offline    # 不連網，僅重建文件

產出：
    knowledge/market_anchor.md      # 供 Evolus knowledge library 使用
    knowledge/market_data.json      # 原始數據（供引擎程式化取用）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KNOWLEDGE_DIR = os.path.join(ROOT, "knowledge")

COMTRADE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
FX_URL = "https://open.er-api.com/v6/latest/USD"
CHINA = 156          # UN M49 code for China
WORLD = 0            # partnerCode 0 = world total

# 觀測品類。`role` 說明該代碼在本專案中的用途。
WATCHLIST: List[Dict[str, str]] = [
    {"hs": "6601", "label": "Umbrellas and sun umbrellas", "role": "成品：主要標的商品"},
    {"hs": "660191", "label": "Umbrellas, telescopic shaft", "role": "成品細類"},
    {"hs": "660199", "label": "Umbrellas, other", "role": "成品細類"},
    {"hs": "7208", "label": "Flat-rolled steel", "role": "BOM：中棒鋼管"},
    {"hs": "5407", "label": "Woven synthetic fabric", "role": "BOM：傘布面料"},
    {"hs": "3903", "label": "Styrene polymers (ABS/PS)", "role": "BOM：手柄頂帽塑料"},
    {"hs": "7601", "label": "Unwrought aluminium", "role": "BOM：五金件原料(進口價)"},
    {"hs": "7318", "label": "Screws, bolts, fasteners", "role": "BOM：五金標準件"},
]

# 若某代碼的 netWgt 為 None，改以進口(CIF)口徑取得原料成本代理值。
IMPORT_FALLBACK = {"7601"}


def _http_json(url: str, timeout: int = 45, retries: int = 4) -> Optional[Dict[str, Any]]:
    """GET + JSON 解析，內建 429 退避重試。失敗回 None（不拋例外）。"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "TradeFlow-MarketData/1.0 (+hackathon research)"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                back = 6 * (attempt + 1)
                print(f"    [429] rate limited, backing off {back}s...", file=sys.stderr)
                time.sleep(back)
                continue
            print(f"    [HTTP {e.code}] {url[:90]}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001 - 網路層任何錯誤都不應中斷整批
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            print(f"    [{type(e).__name__}] {str(e)[:80]}", file=sys.stderr)
            return None
    return None


def fetch_fx() -> Optional[Dict[str, Any]]:
    """取得即時 USD -> CNY 匯率。"""
    d = _http_json(FX_URL, timeout=20)
    if not d:
        return None
    cny = (d.get("rates") or {}).get("CNY")
    if not cny:
        return None
    return {
        "usd_cny": float(cny),
        "source": "open.er-api.com",
        "updated_utc": d.get("time_last_update_utc"),
    }


def fetch_comtrade(hs: str, year: int, flow: str = "X") -> Optional[Dict[str, Any]]:
    """取得中國對世界、指定 HS 代碼與年度的貿易彙總。

    回傳含 fobvalue / netWgt / isNetWgtEstimated，並計算 $/kg。
    **刻意不計算 $/pc** —— 見模組 docstring 的 (a)。
    """
    url = (f"{COMTRADE}?reporterCode={CHINA}&period={year}"
           f"&cmdCode={hs}&flowCode={flow}&partnerCode={WORLD}")
    d = _http_json(url)
    if not d:
        return None
    rows = d.get("data") or []
    if not rows:
        return None
    r = rows[0]

    value = r.get("cifvalue") if flow == "M" else r.get("fobvalue")
    weight = r.get("netWgt")
    if not value or not weight:
        return None

    return {
        "hs": hs,
        "year": year,
        "flow": flow,
        "value_usd": float(value),
        "value_basis": "CIF" if flow == "M" else "FOB",
        "net_weight_kg": float(weight),
        "usd_per_kg": round(float(value) / float(weight), 4),
        "weight_is_estimated": bool(r.get("isNetWgtEstimated")),
        "legacy_estimation_flag": r.get("legacyEstimationFlag"),
    }


def collect(years: List[int]) -> Dict[str, Any]:
    """依 watchlist 蒐集全部數據。"""
    out: Dict[str, Any] = {
        "fetched_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "trade": "UN Comtrade public preview API (China customs declarations)",
            "fx": "open.er-api.com (daily reference rate)",
        },
        "methodology_notes": [
            "Only USD/kg is reported. USD/piece is deliberately excluded: the implied "
            "average weight per piece swings 348g-794g across years, which indicates "
            "inconsistent reporting scope rather than genuine price movement.",
            "net_weight is frequently flagged isNetWgtEstimated=true, i.e. estimated "
            "rather than individually declared. Treat as an order-of-magnitude anchor.",
            "These are macro trade anchors for sanity-checking supplier claims, NOT "
            "quotations. Factory-level BOM costs must be maintained by domain experts.",
        ],
        "fx": fetch_fx(),
        "series": {},
    }

    for item in WATCHLIST:
        hs = item["hs"]
        print(f"  [{hs}] {item['label']} ...")
        entries: List[Dict[str, Any]] = []
        for yr in years:
            rec = fetch_comtrade(hs, yr, flow="X")
            if rec is None and hs in IMPORT_FALLBACK:
                print(f"    {yr}: export weight missing -> trying import (CIF) proxy")
                rec = fetch_comtrade(hs, yr, flow="M")
                if rec:
                    rec["note"] = "export weight unavailable; using China IMPORT CIF as material-cost proxy"
            if rec:
                entries.append(rec)
            time.sleep(4)  # 尊重速率限制
        out["series"][hs] = {"label": item["label"], "role": item["role"], "years": entries}

    return out


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}" if v >= 1 else f"${v:,.4f}"


def _yoy(entries: List[Dict[str, Any]]) -> Optional[str]:
    """計算最近兩年的 $/kg 年增率。"""
    pts = sorted([e for e in entries if e.get("flow") == "X"], key=lambda e: e["year"])
    if len(pts) < 2:
        return None
    a, b = pts[-2], pts[-1]
    if not a["usd_per_kg"]:
        return None
    pct = (b["usd_per_kg"] - a["usd_per_kg"]) / a["usd_per_kg"] * 100
    return f"{a['year']} -> {b['year']}: {pct:+.1f}%"


def render_markdown(data: Dict[str, Any]) -> str:
    """產生供 Evolus knowledge library 使用的 Markdown。"""
    fx = data.get("fx") or {}
    rate = fx.get("usd_cny")
    L: List[str] = []

    L.append("# TradeFlow — Live Market Price Anchor")
    L.append("")
    L.append(f"> **Data fetched (UTC)**: {data['fetched_at_utc']}  ")
    L.append(f"> **Source**: {data['sources']['trade']}  ")
    L.append(f"> **FX**: {data['sources']['fx']}"
             + (f" — USD/CNY = **{rate:.4f}** ({fx.get('updated_utc')})" if rate else ""))
    L.append("")
    L.append("## How to use this document")
    L.append("")
    L.append("This file is **machine-refreshed real customs data**, not a static estimate. "
             "When a supplier claims a raw-material price increase, compare their claim "
             "against the year-over-year movement below.")
    L.append("")
    L.append("**Always state the data date when citing these figures.** "
             "If the fetched date looks stale, the figures should be refreshed by running "
             "`python scripts/refresh_market_data.py`.")
    L.append("")
    L.append("## Critical limitations (do not overstate)")
    L.append("")
    for note in data["methodology_notes"]:
        L.append(f"- {note}")
    L.append("")

    if rate:
        L.append("## Reference FX rate")
        L.append("")
        L.append("| Pair | Rate | As of |")
        L.append("|---|---|---|")
        L.append(f"| USD -> CNY | {rate:.4f} | {fx.get('updated_utc')} |")
        L.append("")
        L.append("Use this single rate for all conversions so figures stay internally consistent.")
        L.append("")

    L.append("## China export unit values (USD per kilogram)")
    L.append("")
    L.append("| HS | Category | Role | Latest | YoY change | Latest year |")
    L.append("|---|---|---|---|---|---|")
    for hs, s in data["series"].items():
        exports = [e for e in s["years"] if e.get("flow") == "X"]
        if not exports:
            continue
        latest = max(exports, key=lambda e: e["year"])
        yoy = _yoy(s["years"]) or "n/a"
        L.append(f"| {hs} | {s['label']} | {s['role']} | "
                 f"**{_fmt_usd(latest['usd_per_kg'])}/kg** | {yoy} | {latest['year']} |")
    L.append("")

    L.append("## Full time series")
    L.append("")
    for hs, s in data["series"].items():
        if not s["years"]:
            continue
        L.append(f"### HS {hs} — {s['label']}")
        L.append("")
        L.append(f"*Role in this project*: {s['role']}")
        L.append("")
        L.append("| Year | Basis | Value (USD) | Net weight (kg) | USD/kg | "
                 "CNY/kg | Weight estimated? |")
        L.append("|---|---|---|---|---|---|---|")
        for e in sorted(s["years"], key=lambda x: (x["year"], x["flow"])):
            cny = f"¥{e['usd_per_kg'] * rate:,.2f}" if rate else "n/a"
            est = "yes" if e.get("weight_is_estimated") else "no"
            L.append(f"| {e['year']} | {e['value_basis']} | {e['value_usd']:,.0f} | "
                     f"{e['net_weight_kg']:,.0f} | {_fmt_usd(e['usd_per_kg'])} | {cny} | {est} |")
        L.append("")
        if any(e.get("note") for e in s["years"]):
            for e in s["years"]:
                if e.get("note"):
                    L.append(f"> Note ({e['year']}): {e['note']}")
            L.append("")

    L.append("## What this document cannot tell you")
    L.append("")
    L.append("These are aggregate national export statistics. They **cannot** give:")
    L.append("")
    L.append("- The should-cost of one specific finished product")
    L.append("- A particular factory's quotation or margin structure")
    L.append("- Live spot prices for a named material grade (e.g. 210T impact fabric)")
    L.append("- Domestic Chinese purchase prices paid by factories")
    L.append("")
    L.append("For product-level should-cost, use the physical BOM decomposition in "
             "`spec_baseline.md`, and treat this file only as a **reasonableness check** "
             "on whether a claimed cost movement is plausible.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*Auto-generated by `scripts/refresh_market_data.py`. "
             "Do not hand-edit — rerun the script instead.*")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh real market anchor data.")
    ap.add_argument("--years", type=int, default=5, help="how many recent years to fetch")
    ap.add_argument("--offline", action="store_true",
                    help="do not fetch; rebuild markdown from existing market_data.json")
    args = ap.parse_args()

    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    json_path = os.path.join(KNOWLEDGE_DIR, "market_data.json")
    md_path = os.path.join(KNOWLEDGE_DIR, "market_anchor.md")

    if args.offline:
        if not os.path.exists(json_path):
            print(f"[error] {json_path} not found; run without --offline first.", file=sys.stderr)
            return 1
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        print("[offline] rebuilt markdown from cached JSON")
    else:
        this_year = _dt.date.today().year
        # Comtrade 年度資料通常落後約一年，故從前一年往回取。
        years = list(range(this_year - 1 - args.years + 1, this_year))
        print(f"[refresh] fetching real trade data for years {years[0]}..{years[-1]}")
        data = collect(years)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[refresh] wrote {json_path}")

    ok = sum(1 for s in data["series"].values() if s["years"])
    print(f"[refresh] {ok}/{len(WATCHLIST)} series have data")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(data))
    print(f"[refresh] wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
