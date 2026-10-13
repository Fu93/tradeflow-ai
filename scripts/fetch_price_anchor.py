"""Best-effort 價格錨點拉取（UN Comtrade Plus / TradeMap）。

這兩個來源是互動式（要 cookie/session），不能完全靠簡單 GET。
本腳本做三件事，確保 Day 2 有「真實錨點」：
1. 先嘗試 comtradeplus.un.org 的公共 API（HS6601 雨傘，近期年份）。
2. 若拿不到，退回 seed_data/price_anchor.json 的佔位 + 印出手動步驟。
3. 把結論寫回 seed_data/price_anchor.json 的 "market" 欄。

用法：python scripts/fetch_price_anchor.py
"""
from __future__ import annotations
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(HERE, "..", "seed_data", "price_anchor.json")
HS = "6601"  # 雨傘


def try_comtrade(hs: str, period="2023"):
    """嘗試 UN Comtrade Plus 公共端點；失敗回 None。"""
    url = (f"https://comtradeplus.un.org/api/get/HS?freq=A&ps={period}"
           f"&r=all&p=all&rg=2&cc={hs}&max=10")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TradeFlow/0.1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        return data
    except Exception as e:
        return {"error": str(e)}


def main():
    print(f"[price-anchor] 拉取 HS {HS} (auto umbrella) 真實 FOB 區間…")
    data = try_comtrade(HS)
    with open(SEED, encoding="utf-8") as f:
        seed = json.load(f)

    if isinstance(data, dict) and "error" in data:
        seed["market"] = {
            "source": "manual (comtradeplus/trademap)",
            "status": "FETCH_FAILED",
            "note": data["error"],
            "manual_steps": [
                "開 https://comtradeplus.un.org/ → 查 HS 6601, reporter=China, "
                "flow=Import/Export, 取 FOB value ÷ quantity → 單價區間",
                "或 https://www.trademap.org/ → 按 HS 6601 查 CIF/FOB",
                "把 low/high 填進 seed_data/price_anchor.json 的 market.fob_usd_*",
            ],
        }
    else:
        seed["market"] = {"source": "comtradeplus.un.org", "status": "OK", "raw": data}

    with open(SEED, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    print("[price-anchor] 已更新 seed_data/price_anchor.json")


if __name__ == "__main__":
    main()
