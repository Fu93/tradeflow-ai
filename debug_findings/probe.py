# Runtime probe for TradeFlow AI (temp verification artifact, not repo source)
import json
import time

import requests

BASE = 'http://localhost:8000'
out = []


def log(s):
    out.append(s)
    print(s, flush=True)


# ---- 1. Wait for startup, measure time to first 200 on /health ----
t0 = time.time()
up = False
for _ in range(240):
    try:
        r = requests.get(BASE + '/health', timeout=2)
        if r.status_code == 200:
            up = True
            break
    except Exception:
        time.sleep(0.5)
log(f'STARTUP_OK={up} ELAPSED_TO_FIRST_200_SECONDS={round(time.time() - t0, 1)}')


def probe(method, path, body=None, label=''):
    url = BASE + path
    try:
        if body is not None:
            r = requests.request(method, url, json=body, timeout=180)
        else:
            r = requests.request(method, url, timeout=180)
        snippet = r.text.replace('\n', ' ')[:220] if r.text else ''
        log(f'[{method} {path}] -> HTTP {r.status_code} | {label} | body: {snippet}')
        return r
    except Exception as e:
        code = getattr(getattr(e, 'response', None), 'status_code', '')
        log(f'[{method} {path}] -> HTTP {code} EXCEPTION: {e}')
        return None


probe('GET', '/health', label='health check')
h = probe('GET', '/', label='web portal dashboard')
if h is not None:
    log(f'HOME_CONTAINS_TRADEFLOW_AI={"TradeFlow AI" in h.text}')
probe('GET', '/static/css/theme.css', label='static css')
probe('GET', '/static/js/app.js', label='static js')
probe('GET', '/api/v1/sessions', label='list sessions')

# ---- unique session ----
sid = 'rtv-' + time.strftime('%Y%m%d%H%M%S')
spec = {
    'session_id': sid,
    'product_name': '自動反開摺疊傘',
    'target_price': 12.0,
    'ceiling_price': 15.0,
    'moq': 1000,
    'max_rounds': 5,
    'supplier_id': 'supplier-zhejiang-01',
}
probe('POST', '/api/v1/sessions', spec, 'create session')

r1 = {
    'opponent_raw_text': '老闆，現在銅和碰擊布原料都在漲，最低報 14.20 元/把，沒利潤！',
    'opponent_offer_price': 14.20,
}
resp1 = probe('POST', f'/api/v1/sessions/{sid}/round', r1, 'round 1 intent')
if resp1 is not None:
    try:
        j = resp1.json()
        log(f"ROUND1_INTENT={j.get('inbound_intent')} CIRCUIT_TRIPPED={j.get('circuit_tripped')}")
    except Exception:
        pass

r2 = {
    'opponent_raw_text': '平台聊天總卡，加個微信 13812345678 私下聊，價格好商量',
    'opponent_offer_price': 13.80,
}
resp2 = probe('POST', f'/api/v1/sessions/{sid}/round', r2, 'round 2 circuit breaker')
if resp2 is not None:
    try:
        j = resp2.json()
        log(f"ROUND2_TRIPPED={j.get('circuit_tripped')} REASON={j.get('trip_reason')}")
    except Exception:
        pass

cmd = {'command': '1', 'session_id': sid}
respC = probe('POST', '/webhook/mobile-command', cmd, 'mobile command 1')
if respC is not None:
    try:
        j = respC.json()
        log(f"CMD_STATUS={j.get('status')} ACTION={j.get('action')}")
    except Exception:
        pass

# Run Auction (multi-auction tensor run)
sw = time.time()
respA = probe('POST', '/api/v1/multi-auction/run', {}, 'multi-auction run')
log(f'MULTI_AUCTION_ELAPSED_SECONDS={round(time.time() - sw, 2)}')
if respA is not None:
    try:
        j = respA.json()
        log(f"AUCTION_WINNER={j.get('winning_supplier')} K={j.get('k_suppliers')} "
            f"ROLLOUTS={j.get('total_rollouts')} BACKEND={j.get('backend')}")
    except Exception:
        pass

probe('POST', '/negotiate', {}, 'legacy negotiate')
probe('POST', '/api/v1/bayesian/update', {'observed_offer': 14.2}, 'bayesian update')
probe('POST', '/api/v1/tco/evaluate', {'raw_price': 14.2}, 'tco evaluate')
probe('GET', '/api/v1/config/telemetry', None, 'telemetry config read')
probe('POST', '/api/v1/classify/semantic',
      {'text': '老闆，原料漲價了，最低 14.2', 'enable_stage2': False},
      'semantic classify stage1 only')
probe('POST', '/webhook/evolus', {'email_body': 'test'}, 'evolus webhook no token')

# ---- SSE immediate response check ----
try:
    t1 = time.time()
    r = requests.get(BASE + '/api/v1/stream/session', stream=True, timeout=15)
    t_first = None
    data = b''
    try:
        for chunk in r.iter_content(chunk_size=64):
            if t_first is None:
                t_first = round(time.time() - t1, 2)
            data += chunk
            if len(data) >= 200:
                break
    except Exception as e:
        log(f'SSE_STREAM_ERROR: {e}')
    r.close()
    ctype = r.headers.get('content-type', '')
    log(f"SSE_HTTP={r.status_code} CONTENT_TYPE={ctype} FIRST_BYTE_SECONDS={t_first} "
        f"BYTES={len(data)} SAMPLE={data[:160]!r}")
except Exception as e:
    log(f'SSE_EXCEPTION: {e}')

with open('debug_findings/probe_results.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
log('PROBE_DONE')
