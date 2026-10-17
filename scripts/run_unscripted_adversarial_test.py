"""真實世界非腳本化 (Unscripted / Chaotic) 紅隊對抗測試。

模擬完全不了解系統規則、使用口語化/錯別字/語音轉文字、極端非理性開價與複合商務甩鍋條件的真實工廠業務員。
測試 TradeFlow 是否能在無預設情景下維持：
1. 價格算力約束 (不被工廠帶跑偏)
2. 風控邊界 (定金超標 80%、嚴重溢價)
3. 語意解析與回覆健全性
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
import src.app.main as m
from src.sim.redteam import UNSCRIPTED_SPEC, UNSCRIPTED_TURNS

client = TestClient(m.app)

def run_unscripted_adversarial_test():
    session_id = f"unscripted-test-{int(time.time())}"
    print(f"=== 啟動非腳本化真實世界對抗測試 Session: {session_id} ===")

    # 初始化會話
    spec_payload = {"session_id": session_id, **UNSCRIPTED_SPEC}
    r = client.post("/api/v1/sessions", json=spec_payload)
    assert r.status_code == 200

    chaotic_turns = UNSCRIPTED_TURNS

    log_records = []

    for turn in chaotic_turns:
        r_num = turn["round"]
        print(f"\n--- [Round {r_num}] {turn['title']} ---")
        print(f"工廠口語發言: \"{turn['opponent_text']}\" (報價: ¥{turn['opponent_price']:.2f})")

        t0 = time.perf_counter()
        resp = client.post(
            f"/api/v1/sessions/{session_id}/round",
            json={
                "opponent_raw_text": turn["opponent_text"],
                "opponent_offer_price": turn["opponent_price"],
            },
        )
        dt = (time.perf_counter() - t0) * 1000

        data = resp.json()
        autopilot_state = data.get("autopilot_state")
        tripped = data.get("circuit_tripped", False)
        intent = data.get("inbound_intent") or (data.get("action_object") or {}).get("counterpart_intent")
        reply = data.get("reply_text")

        print(f"系統狀態: {autopilot_state} | 耗時: {dt:.2f} ms")
        print(f"分類意圖: {intent} | 熔斷: {tripped}")

        if tripped:
            print(f"[ALERT] 安全熔斷原因: {data.get('trip_reason')}")
            # 遠端高管介入授權
            print("[主管遠端處置] 接收 WhatsApp 授權指令: 1 (CONCEDE_STEP)")
            client.post(
                f"/api/v1/sessions/{session_id}/command",
                json={"command_text": "1", "source": "whatsapp_mobile"},
            )
        else:
            print(f"系統回應: \"{reply}\"")

        log_records.append({
            "round": r_num,
            "title": turn["title"],
            "input": turn["opponent_text"],
            "price": turn["opponent_price"],
            "detected_intent": intent,
            "circuit_tripped": tripped,
            "trip_reason": data.get("trip_reason"),
            "reply": reply,
            "state": autopilot_state,
            "latency_ms": round(dt, 2),
        })

    return log_records

if __name__ == "__main__":
    records = run_unscripted_adversarial_test()
    os.makedirs("results", exist_ok=True)
    with open("results/unscripted_adversarial_log.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print("\n[OK] 非腳本化紅隊對抗測試完成，結果已寫入 results/unscripted_adversarial_log.json")
