"""真實世界紅隊壓力測試腳本 (Real-World Stress Test against Live TradeFlow Engine)

模擬一個老辣、刁鑽、富有多種博弈套路的中國沿海五金/雨傘工廠業務員「陳經理」，
對 TradeFlow Live 引擎發起 5 輪硬碰硬的真實壓力測試：
- Round 1: 原材料暴漲哭窮 + 試探買方預算底線
- Round 2: 複合信號 (詢問進出口權外貿背景 + 拋出模具費 ¥3500)
- Round 3: 誘惑私下加微信私聊 (測試安全熔斷與狀態機掛起)
- Round 4: 偷換次級料 (改用 201 料/普通料) 測試品控防禦
- Round 5: 終局拉鋸逼近成交
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
import src.app.main as m
from src.sim.redteam import STRESS_SPEC, STRESS_TURNS

client = TestClient(m.app)

def run_stress_test():
    session_id = f"stress-test-{int(time.time())}"
    print(f"=== 啟動真實世界紅隊壓力測試 Session: {session_id} ===")

    # 1. 建立會話
    spec_payload = {"session_id": session_id, **STRESS_SPEC}
    r = client.post("/api/v1/sessions", json=spec_payload)
    assert r.status_code == 200, f"Init failed: {r.text}"
    print(f"[INIT] 會話已建立 | 目標價: ¥{spec_payload['target_price']:.2f} | 上限: ¥{spec_payload['ceiling_price']:.2f}\n")

    test_turns = STRESS_TURNS

    transcript = []

    for turn in test_turns:
        r_num = turn["round"]
        print(f"--- [Round {r_num}] {turn['title']} ---")
        print(f"工廠業務員發言: \"{turn['opponent_text']}\" (報價: ¥{turn['opponent_price']:.2f})")

        t0 = time.perf_counter()
        resp = client.post(
            f"/api/v1/sessions/{session_id}/round",
            json={
                "opponent_raw_text": turn["opponent_text"],
                "opponent_offer_price": turn["opponent_price"],
            },
        )
        dt = (time.perf_counter() - t0) * 1000  # ms

        data = resp.json()
        autopilot_state = data.get("autopilot_state")
        tripped = data.get("circuit_tripped", False)
        intent = data.get("inbound_intent") or (data.get("action_object") or {}).get("counterpart_intent")
        reply = data.get("reply_text")

        print(f"系統內部狀態: {autopilot_state} | 耗時: {dt:.2f} ms")
        print(f"識別意圖: {intent} | 安全熔斷: {tripped}")

        if tripped:
            print(f"熔斷原因: {data.get('trip_reason')}")
            print(f"發送英文主管決策卡:")
            card_lines = (data.get("executive_card_en") or "").strip().split("\n")
            for cl in card_lines[:4]:
                print(f"  > {cl}")
            # 模擬主管在 WhatsApp 上按下 1 授權繼續 (concede_step)
            print("[主管介入] 主機端收到 WhatsApp 授權指令: 1 (CONCEDE_STEP)，解鎖會話...")
            client.post(
                f"/api/v1/sessions/{session_id}/command",
                json={"command_text": "1", "source": "whatsapp_mobile"},
            )
        else:
            print(f"系統自動回覆: \"{reply}\"")

        print()

        transcript.append({
            "round": r_num,
            "scenario": turn["title"],
            "opponent_input": turn["opponent_text"],
            "opponent_price": turn["opponent_price"],
            "latency_ms": round(dt, 2),
            "detected_intent": intent,
            "circuit_tripped": tripped,
            "trip_reason": data.get("trip_reason"),
            "reply_text": reply,
            "autopilot_state": autopilot_state,
        })

    return transcript

if __name__ == "__main__":
    t = run_stress_test()
    with open("results/real_stress_test_log.json", "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, indent=2)
    print("真實壓力測試執行完畢，結果已寫入 results/real_stress_test_log.json")
