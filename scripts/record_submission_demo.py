"""Record an explicitly offline, narrated walkthrough; never uses real provider keys.

Requires development dependencies, Playwright Chromium and FFmpeg with flite/subtitles.
Artifacts live outside the source tree by default. No account publishing is performed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHAPTERS = [
    ('Small sellers compare factory quotes across a language gap. TradeFlow turns a supplier message into a confirmed price, a counter offer and a draft you control. This is an offline walkthrough with fictional suppliers.',
     'TradeFlow AI | A supplier negotiation desk\nOffline walkthrough. Fictional suppliers.'),
    ('Start with one product and four suppliers. The buyer sets quantity, target, ceiling and a walk away risk limit. Sample contacts use a reserved test domain.',
     'One deal. Four suppliers. Buyer-defined limits.'),
    ('This Chinese message mentions a rival at twenty four and the supplier at twenty seven point eight. Live mode uses Nemotron on Nebius Token Factory to label evidence. This recording uses the rule reader, with no paid calls.',
     'A rival price: 24. Supplier offer: 27.80.\nThis recording uses the offline rule reader.'),
    ('I confirm the supplier\'s own figure. Only confirmed prices enter the decision. The desk shows a default counter and an optional push lower counter, with modelled risk clearly labelled.',
     'A person confirms the number.\nThe desk computes the counter.'),
    ('Now a second supplier offers twenty six point five. Confirming it updates the comparison. Complete freight, fees and duty would switch the ranking to landed cost.',
     'A second confirmed quote updates the comparison.\nComplete freight, fees and duty enable landed costs.'),
    ('I choose push lower for the cheapest supplier. The Chinese draft changes to that exact amount. If no price fits the ceiling and risk cap, the desk withholds the counter.',
     'Choose a counter. Review the exact draft.\nInfeasible price and risk limits withhold the counter.'),
    ('Tavily can add cited public references about a supplier. This offline recording shows the unavailable state. Search results never set the counter and do not verify a factory.',
     'Tavily: cited references, separate from pricing.\nOffline here; no search request is made.'),
    ('Approval is a separate human action. On this demo no mail transport is configured, so approval is recorded without sending an email. Chat drafts are copied and sent by the person.',
     'Human approval is required.\nDemo approval sends no email.'),
    ('The audit endpoint verifies the keyed chain. This remains a research prototype: no factory has closed a deal through it. Code, evaluation results and limitations are public. The model reads, the math prices, and you approve.',
     'Verified audit chain. Published code and limitations.\nNo real factory deal is claimed.'),
]


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def duration(path):
    return float(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)]).stdout)


def stamp(seconds):
    ms = round(seconds * 1000)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{ms:03}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('/tmp/tradeflow-video'))
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not shutil.which('ffmpeg'):
        raise SystemExit('FFmpeg with flite and subtitles is required.')
    from playwright.sync_api import sync_playwright

    voices = []
    for index, (narration, _caption) in enumerate(CHAPTERS):
        text = out / f'voice-{index}.txt'
        wav = out / f'voice-{index}.wav'
        text.write_text(narration)
        run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', f'flite=textfile={text}:voice=slt',
             '-af', 'atempo=0.92', str(wav)])
        voices.append((wav, duration(wav)))
    (out / 'narration.txt').write_text('\n\n'.join(text for text, _ in CHAPTERS))

    with tempfile.TemporaryDirectory(prefix='tradeflow-recording-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ)
        for key in ('TRADEFLOW_LLM_API_KEY', 'OPENAI_API_KEY', 'NVIDIA_API_KEY', 'TAVILY_API_KEY',
                    'VLLM_API_KEY', 'EVOLUS_API_KEY', 'IMAP_HOST', 'GMAIL_CLIENT_ID', 'DESK_SMTP_SEND'):
            env.pop(key, None)
        env.update(TRADEFLOW_SKIP_DOTENV='1', TRADEFLOW_ENV='production', TRADEFLOW_LEGACY='0',
                   TRADEFLOW_API_KEY='local-recording-only-key', TRADEFLOW_CHAIN_KEY='local-recording-only-chain',
                   TRADEFLOW_REQUIRE_SIGNED_APPROVAL='0', TRADEFLOW_LLM_DAILY_CALLS='0',
                   TRADEFLOW_WEB_EVIDENCE_OFFLINE='1', TRADEFLOW_MARKET_OFFLINE='1',
                   TRADEFLOW_DESK_DB=str(Path(temp) / 'desk.db'))
        base = f'http://127.0.0.1:{port}'
        with (out / 'server.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'src.app.main:app',
                                     '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        with urllib.request.urlopen(base + '/health', timeout=1):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('Local server did not become healthy; see server.log')
                        time.sleep(.2)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                                  record_video_dir=str(out / 'raw'),
                                                  record_video_size={'width': 1440, 'height': 1000},
                                                  bypass_csp=True)
                    page = context.new_page()
                    started = time.monotonic()
                    page.goto(base + '/desk')
                    page.evaluate('''() => {
                      const tag = document.createElement('div');
                      tag.textContent = 'OFFLINE WALKTHROUGH  /  FICTIONAL SUPPLIERS  /  NO EMAIL SENT';
                      Object.assign(tag.style, {position:'fixed', top:'0', right:'0', zIndex:'9999',
                        background:'#18384a', color:'#fff', padding:'6px 14px', font:'12px sans-serif'});
                      document.body.appendChild(tag);
                    }''')
                    marks = []

                    def hold(index):
                        print(f'Recording chapter {index + 1}/{len(CHAPTERS)}', flush=True)
                        offset = time.monotonic() - started
                        wav, length = voices[index]
                        marks.append({'index': index, 'start': offset, 'end': offset + length,
                                      'caption': CHAPTERS[index][1], 'voice': wav.name})
                        page.wait_for_timeout((length + 1) * 1000)

                    hold(0)
                    page.click('#sample-deal')
                    page.fill('#api-key', 'local-recording-only-key')
                    page.click('#desk-form button[type=submit]')
                    page.locator('#board:not([hidden])').wait_for()
                    hold(1)
                    page.click('#sample-message')
                    page.click('#paste-form button[type=submit]')
                    page.locator('#review [data-confirm-value="27.80"]').wait_for()
                    hold(2)
                    page.get_by_role('button', name='Accept', exact=True).click()
                    page.locator('#drafts textarea').first.wait_for()
                    page.locator('.desk-card').first.evaluate('(node) => node.scrollIntoView({block: "start"})')
                    hold(3)
                    page.locator('#paste-supplier').select_option(index=1)
                    page.fill('#paste-text', '单价 26.50 元/把，起订 1000 把，15 天出货。')
                    page.click('#paste-form button[type=submit]')
                    page.locator('#review [data-confirm-value="26.50"]').wait_for()
                    page.get_by_role('button', name='Accept', exact=True).click()
                    page.locator('.desk-card').filter(has_text='1. Sample Supplier B').wait_for()
                    card = page.locator('.desk-card').filter(has_text='Sample Supplier B')
                    card.evaluate('(node) => node.scrollIntoView({block: "start"})')
                    hold(4)
                    card.get_by_role('button', name='Use push lower in the draft', exact=True).click()
                    card.get_by_text('push lower, chosen by you', exact=False).wait_for()
                    drafts = page.locator('#drafts .desk-event').filter(has_text='Sample Supplier B')
                    drafts.evaluate('(node) => node.scrollIntoView({block: "start"})')
                    hold(5)
                    card.get_by_role('button', name='Check supplier on the web').click()
                    card.locator('.desk-evidence-state').get_by_text('switched off', exact=False).wait_for()
                    card.evaluate('(node) => node.scrollIntoView({block: "start"})')
                    hold(6)
                    drafts.evaluate('(node) => node.scrollIntoView({block: "start"})')
                    page.on('dialog', lambda dialog: dialog.accept())
                    drafts.get_by_role('button', name='Approve and send').click()
                    drafts.get_by_text('approved here, not transmitted', exact=False).wait_for()
                    hold(7)
                    deal_id = page.evaluate("sessionStorage.getItem('tfDeskDeal')")
                    audit = page.request.get(base + f'/api/v1/desk/deals/{deal_id}/audit/verify',
                                             headers={'X-API-Key': 'local-recording-only-key'}).json()
                    assert audit['ok'], audit
                    snapshot = page.request.get(base + f'/api/v1/desk/deals/{deal_id}',
                                                headers={'X-API-Key': 'local-recording-only-key'}).json()
                    approved = [d for d in snapshot['drafts'] if d['status'] == 'approved']
                    assert approved and all(not d['transmitted'] for d in approved)
                    page.locator('#cards').evaluate('(node) => node.scrollIntoView({block: "start"})')
                    page.wait_for_timeout(500)
                    page.screenshot(path=str(out / 'desk-desktop.png'))
                    hold(8)
                    (out / 'recording-evidence.json').write_text(json.dumps({
                        'offline': True, 'paid_api_attempts': 0, 'transmitted': 0,
                        'audit_ok': audit['ok'], 'chapters': marks,
                    }, indent=2))
                    raw = page.video.path()
                    awaitable_video = page.video
                    context.close()
                    # Closing the context flushes the recorded frames.
                    raw = awaitable_video.path()
                    mobile = browser.new_context(viewport={'width': 390, 'height': 844})
                    mp = mobile.new_page()
                    mp.goto(base + '/desk')
                    mp.evaluate('''([id]) => {
                      sessionStorage.setItem('tfDeskDeal', id);
                      sessionStorage.setItem('tfApiKey', 'local-recording-only-key');
                    }''', [deal_id])
                    mp.reload()
                    mp.locator('#board:not([hidden])').wait_for()
                    mp.screenshot(path=str(out / 'desk-mobile.png'), full_page=True)
                    mobile.close()
                    browser.close()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    captions = '\n\n'.join(f"{i+1}\n{stamp(m['start'])} --> {stamp(m['end'])}\n{m['caption']}"
                            for i, m in enumerate(marks))
    (out / 'captions.srt').write_text(captions + '\n')
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(raw)]
    for wav, _ in voices:
        cmd += ['-i', str(wav)]
    filters = []
    for i, mark in enumerate(marks):
        delay = round(mark['start'] * 1000)
        filters.append(f'[{i+1}:a]adelay={delay}:all=1[a{i}]')
    filters.append(''.join(f'[a{i}]' for i in range(len(marks))) +
                   f'amix=inputs={len(marks)}:normalize=0,apad[audio]')
    filters.append(f"[0:v]subtitles={out / 'captions.srt'}:force_style='FontSize=12,Outline=0.6,MarginV=16'[video]")
    total = max(m['end'] for m in marks) + 1
    if total >= 180:
        raise RuntimeError(f'{total:.1f}s exceeds the video target; shorten narration before publishing')
    cmd += ['-filter_complex', ';'.join(filters), '-map', '[video]', '-map', '[audio]',
            '-t', str(total), '-c:v', 'libx264', '-crf', '22', '-preset', 'fast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(out / 'tradeflow-demo.mp4')]
    run(cmd)
    print(json.dumps({'video': str(out / 'tradeflow-demo.mp4'), 'seconds': duration(out / 'tradeflow-demo.mp4'),
                      'offline': True, 'paid_api_attempts': 0}, indent=2))


if __name__ == '__main__':
    main()
