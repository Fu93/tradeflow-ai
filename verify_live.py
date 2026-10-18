import os
import sys
import tempfile

# [AUDIT FIX — 2026-10-17] 這支指令是 README 教使用者跑的驗證腳本，但它過去直接
# 寫進 repo 的 data/sessions.db：跑完之後儀表板就多出 test-session-live-01
# （以及每次重跑覆蓋掉的歷史）。驗證的是 HTTP 介面，不是持久化資料，所以預設
# 導向臨時目錄；若你就是要寫進真實資料庫（例如要留一份示範資料），自己在環境
# 變數設 TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR 即可，這裡用 setdefault 尊重它。
_SMOKE_DIR = tempfile.mkdtemp(prefix="tf_verify_live_")
os.environ.setdefault("TRADEFLOW_DB_PATH", os.path.join(_SMOKE_DIR, "sessions.db"))
os.environ.setdefault("TRADEFLOW_STORE_DIR", os.path.join(_SMOKE_DIR, "sessions_json"))

from fastapi.testclient import TestClient
import src.app.main as m
from src.sim.smoke import LIVE_CEILING, LIVE_OFFER_R1, LIVE_OFFER_R2, LIVE_TARGET

print(f"[info] isolated session store: {m.session_mgr.db_path}")
client = TestClient(m.app)

# 1. 測試 Web Dashboard 首頁
res_home = client.get('/')
assert res_home.status_code == 200, f'Home failed: {res_home.status_code}'
assert 'TradeFlow AI' in res_home.text
print('[PASS] 1. Web Portal Dashboard GET / (Status: 200)')

# 2. 測試靜態檔案
res_css = client.get('/static/css/theme.css')
res_js = client.get('/static/js/app.js')
assert res_css.status_code == 200, 'CSS failed'
assert res_js.status_code == 200, 'JS failed'
print('[PASS] 2. Static Assets (/static/css/theme.css, /static/js/app.js)')

# 3. 測試建立會話 Session
spec_payload = {
    'session_id': 'test-session-live-01',
    'product_name': '自動反開摺疊傘',
    'target_price': LIVE_TARGET,
    'ceiling_price': LIVE_CEILING,
    'moq': 1000,
    'max_rounds': 5,
    'supplier_id': 'supplier-zhejiang-01'
}
res_sess = client.post('/api/v1/sessions', json=spec_payload)
assert res_sess.status_code == 200, f'Create session failed: {res_sess.text}'
print('[PASS] 3. Session Created: test-session-live-01')

# 4. 測試第 1 輪對手報價與雙語意圖反饋
r1_payload = {
    'opponent_raw_text': '老闆，現在銅和碰擊布原料都在漲，最低報 14.20 元/把，沒利潤！',
    'opponent_offer_price': LIVE_OFFER_R1
}
res_r1 = client.post('/api/v1/sessions/test-session-live-01/round', json=r1_payload)
assert res_r1.status_code == 200, f'Round 1 failed: {res_r1.text}'
r1_data = res_r1.json()
intent_name = r1_data['inbound_intent']
assert intent_name == 'CRY_POVERTY_COST_HIKE'
assert not r1_data['circuit_tripped']
assert '出廠價' in r1_data['reply_text']
print(f'[PASS] 4. Round 1 Cruising -> Intent: {intent_name}')
print(f'         Agent Tactical Reply: {r1_data["reply_text"][:50]}...')

# 5. 測試工廠誘惑私聊引發安全熔斷 (Circuit Breaker Tripped)
r2_payload = {
    'opponent_raw_text': '平台聊天總卡，加個微信 13812345678 私下聊，價格好商量',
    'opponent_offer_price': LIVE_OFFER_R2
}
res_r2 = client.post('/api/v1/sessions/test-session-live-01/round', json=r2_payload)
assert res_r2.status_code == 200
r2_data = res_r2.json()
assert r2_data['circuit_tripped'] is True
assert 'OFFLINE_BYPASS_DIVERT' in r2_data['trip_reason']
print(f'[PASS] 5. Round 2 Circuit Breaker Tripped -> Reason: {r2_data["trip_reason"]}')

# 6. 測試手機端遠端指令解鎖 (指令 1: 妥協推進)
cmd_payload = {'command': '1', 'session_id': 'test-session-live-01'}
res_cmd = client.post('/webhook/mobile-command', json=cmd_payload)
assert res_cmd.status_code == 200
cmd_data = res_cmd.json()
assert cmd_data['status'] == 'success'
print(f'[PASS] 6. Mobile Webhook Command Executed -> Action: {cmd_data["action"]}')

# 7. 測試多供應商張量拍賣模擬端點
res_auc = client.post('/api/v1/multi-auction/run')
assert res_auc.status_code == 200
auc_data = res_auc.json()
assert auc_data['k_suppliers'] == 4
assert auc_data['backend'] is not None
print(
    f'[PASS] 7. Multi-Auction Run -> Winner: {auc_data["winning_supplier"]} '
    f'(Scenario-offer cells: {auc_data["total_rollouts"]:,}; coupled expectimax, not the old K×S×N_open×N_sched product)'
)

print('\nALL INTEGRATION ENDPOINTS TESTED AND VERIFIED!')
