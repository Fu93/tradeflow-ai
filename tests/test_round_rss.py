"""Interactive web calls must stay inside a 512 MB Render instance.

The ceiling is the process high-water mark, measured in a fresh interpreter
so earlier tests cannot inflate it. 300 MiB leaves headroom under 512 MB.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 300 MiB. The Starter instance has 512 MB and was OOM-killed at 461 MB.
RSS_CEILING_BYTES = 300 * 1024 * 1024
# Price from the unchunked default round on the umbrella mandate.
RECOMMENDED_COUNTER = 13.523076923076923

_PROBE = r"""
import json, os, resource, tempfile
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="tf_rss_")
os.environ["TRADEFLOW_DB_PATH"] = str(Path(tmp) / "sessions.db")
os.environ["TRADEFLOW_STORE_DIR"] = str(Path(tmp) / "json")
os.environ["TRADEFLOW_ENV"] = "sandbox"
os.environ.pop("TRADEFLOW_USD_CNY_RATE", None)

def rss():
    # Linux: ru_maxrss is kilobytes, the process high-water mark.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024

from fastapi.testclient import TestClient
from src.app.main import app

after_import = rss()
client = TestClient(app)
health = client.get("/health")
after_health = rss()
quote = client.post(
    "/api/v1/quotes/read",
    json={"text": "3.20 USD/pc", "amd_live": False},
)
after_quote = rss()
round_resp = client.post(
    "/api/v1/sessions/rss-round-01/round",
    json={
        "opponent_offer_price": 15.60,
        "opponent_raw_text": "最低 15.60 元/把",
    },
)
after_round = rss()
approve = client.post(
    "/api/v1/sessions/rss-round-01/approve",
    json={"action": "approve"},
)
after_approve = rss()
auction = client.post(
    "/api/v1/multi-auction/run",
    json={"n_samples": 20000, "n_rounds": 3},
)
after_auction = rss()
round_body = round_resp.json()
auction_body = auction.json() if auction.status_code == 200 else {}
quote_body = quote.json()
print(json.dumps({
    "after_import": after_import,
    "after_health": after_health,
    "after_quote": after_quote,
    "after_round": after_round,
    "after_approve": after_approve,
    "after_auction": after_auction,
    "health_status": health.status_code,
    "quote_status": quote.status_code,
    "round_status": round_resp.status_code,
    "approve_status": approve.status_code,
    "auction_status": auction.status_code,
    "price": round_body.get("recommended_offer_price"),
    "quote_price": (quote_body.get("fields") or {}).get("price"),
    "quote_currency": (quote_body.get("fields") or {}).get("currency"),
    "winning_price": auction_body.get("winning_price"),
}))
"""


class TestInteractiveRss(unittest.TestCase):
    def test_round_approve_auction_and_quote_stay_under_300_mib(self):
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"rss probe failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        line = proc.stdout.strip().splitlines()[-1]
        measured = json.loads(line)
        peak = max(
            measured["after_import"],
            measured["after_health"],
            measured["after_quote"],
            measured["after_round"],
            measured["after_approve"],
            measured["after_auction"],
        )
        self.assertLess(
            peak,
            RSS_CEILING_BYTES,
            f"peak RSS {peak} bytes exceeds {RSS_CEILING_BYTES}: {measured}",
        )
        self.assertEqual(measured["health_status"], 200)
        self.assertEqual(measured["quote_status"], 200)
        self.assertEqual(measured["round_status"], 200)
        self.assertEqual(measured["approve_status"], 200)
        self.assertEqual(measured["auction_status"], 200)
        self.assertIsNone(measured["quote_price"])
        self.assertEqual(measured["quote_currency"], "USD")
        self.assertAlmostEqual(measured["price"], RECOMMENDED_COUNTER, places=6)
        self.assertAlmostEqual(measured["winning_price"], 12.6, places=6)


if __name__ == "__main__":
    unittest.main()
