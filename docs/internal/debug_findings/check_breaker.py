# Temp verification script (not repo source): reproduce /round offline-divert breaker path
import sys, os, json
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from src.app.main import app

c = TestClient(app)
sid = 'chkbreaker-001'
spec = {
    'session_id': sid,
    'product_name': 'test umbrella',
    'target_price': 12.0,
    'ceiling_price': 15.0,
    'moq': 1000,
    'max_rounds': 5,
    'supplier_id': 'supplier-check-01',
}
r = c.post('/api/v1/sessions', json=spec)
print('CREATE:', r.status_code)

texts = [
    ('R1 normal', '老闆，現在銅和碰擊布原料都在漲，最低報 14.20 元/把，沒利潤！', 14.20),
    ('R2 offline-divert (probe wording)', '平台聊天總卡，加個微信 13812345678 私下聊，價格好商量', 13.80),
    ('R3 offline-divert (canonical wording)', '老闆，平台聊天太卡了，我們加個微信私下聊，走線下轉私帳，價格好說', 13.50),
]
for label, text, price in texts:
    rr = c.post(f'/api/v1/sessions/{sid}/round', json={'opponent_raw_text': text, 'opponent_offer_price': price})
    if rr.status_code != 200:
        print(f'{label}: HTTP {rr.status_code} {rr.text[:200]}')
        continue
    j = rr.json()
    intent = j.get('inbound_intent')
    tripped = j.get('circuit_tripped')
    reason = j.get('trip_reason')
    esc = None
    ii = j.get('inbound_intent') or {}
    if isinstance(ii, dict):
        esc = ii.get('requires_escalation')
        intent = ii.get('primary_intent')
    print(f'{label}: intent={intent} tripped={tripped} reason={reason} escalation={esc} state={j.get("autopilot_state")}')
