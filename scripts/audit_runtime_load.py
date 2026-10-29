"""Offline HTTP concurrency/restart drill with a Decimal financial oracle.

Starts a disposable local server. No real supplier, provider or mail account.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
KEY = "local-load-drill-key"


def audit():
    with tempfile.TemporaryDirectory(prefix="tradeflow-http-drill-") as folder:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = dict(os.environ)
        for key in ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY", "VLLM_API_KEY", "TAVILY_API_KEY",
                    "DESK_SMTP_SEND", "IMAP_HOST", "GMAIL_CLIENT_ID", "TRADEFLOW_APPROVAL_PUBKEY"):
            env.pop(key, None)
        env.update(TRADEFLOW_SKIP_DOTENV="1", TRADEFLOW_ENV="production", TRADEFLOW_API_KEY=KEY,
                   TRADEFLOW_CHAIN_KEY="local-load-drill-chain", TRADEFLOW_CHAIN_ANCHOR="",
                   TRADEFLOW_REQUIRE_SIGNED_APPROVAL="0", TRADEFLOW_LLM_DAILY_CALLS="0",
                   TRADEFLOW_WEB_EVIDENCE_OFFLINE="1", TRADEFLOW_MARKET_OFFLINE="1",
                   TRADEFLOW_DESK_DB=str(Path(folder) / "desk.db"))
        timings = []

        def request(path, data=None):
            started = time.monotonic()
            body = None if data is None else json.dumps(data).encode()
            req = urllib.request.Request(base + path, data=body,
                                         headers={"X-API-Key": KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.load(response)
            timings.append(time.monotonic() - started)
            return result

        with (Path(folder) / "server.log").open("w") as log:
            def start():
                proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.app.main:app", "--host", "127.0.0.1",
                                         "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 30
                while True:
                    try:
                        request("/health")
                        return proc
                    except OSError:
                        if proc.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError((Path(folder) / "server.log").read_text()) from None
                        time.sleep(.1)

            proc = start()
            try:
                suppliers = [{"name": n, "address": n.lower() + "@example.test"} for n in "ABCD"]
                deals = [request("/api/v1/desk/deals", {
                    "product": f"offline umbrella {i}", "quantity": 1000, "target_price": 24,
                    "ceiling_price": 40, "risk_tolerance": .5, "suppliers": suppliers,
                }) for i in range(4)]
                jobs = []
                for deal in deals:
                    for row, price in zip(deal["suppliers"], (30, 25, 28, 27), strict=True):
                        payload = {"source": "paste", "raw_text": f"单价 {price:.2f} 元。", "supplier_id": row["id"],
                                   "external_id": "load-" + row["id"], "freight": 1 if price == 30 else 0,
                                   "fees": 1 if price == 30 else 0, "duty_rate": .1 if price == 30 else 0}
                        jobs.extend([(deal["deal"]["id"], row["id"], price, payload)] * 3)

                def submit(job):
                    deal_id, _, _, payload = job
                    return request(f"/api/v1/desk/deals/{deal_id}/quotes", payload)

                with ThreadPoolExecutor(max_workers=8) as workers:
                    submitted = list(workers.map(submit, jobs))
                unique = {}
                for job, result in zip(jobs, submitted, strict=True):
                    unique[result["event"]["id"]] = (job[0], job[1], job[2])
                if len(unique) != 16:
                    raise AssertionError(f"Expected 16 logical quotes, found {len(unique)}")

                def accept(item):
                    event, (deal, supplier, price) = item
                    return request(f"/api/v1/desk/deals/{deal}/review/{event}", {
                        "action": "accept", "supplier_id": supplier, "chosen_token": f"{price:.2f}",
                    })

                with ThreadPoolExecutor(max_workers=8) as workers:
                    list(workers.map(accept, unique.items()))
                snapshots = [request("/api/v1/desk/deals/" + d["deal"]["id"]) for d in deals]
                for snapshot in snapshots:
                    rows = snapshot["suppliers"]
                    costs = {}
                    for row in rows:
                        freight = Decimal("1") if row["name"] == "A" else Decimal("0")
                        fees = freight
                        duty = Decimal("0.1") if row["name"] == "A" else Decimal("0")
                        costs[row["id"]] = (Decimal(str(row["latest_quote"])) + freight + fees) * (1 + duty)
                        if row["counter"] is not None:
                            landed = (Decimal(str(row["counter"])) + freight + fees) * (1 + duty)
                            if landed > 40 or Decimal(str(row["counter"])) > Decimal(str(row["latest_quote"])):
                                raise AssertionError("Issued price violates actual invoice constraints")
                    oracle = sorted(rows, key=lambda row: costs[row["id"]])
                    actual = sorted(rows, key=lambda row: row["rank"])
                    if [r["id"] for r in actual] != [r["id"] for r in oracle]:
                        raise AssertionError("Supplier ranking differs from independent Decimal invoice totals")
                    if snapshot["ranking_basis"] != "landed_cost" or len(snapshot["events"]) != 4:
                        raise AssertionError("Incomplete or duplicated quote state")
                proc.kill()
                proc.wait(timeout=10)
                proc = start()
                for snapshot in snapshots:
                    deal = snapshot["deal"]["id"]
                    restored = request("/api/v1/desk/deals/" + deal)
                    for field in ("suppliers", "events", "drafts"):
                        if restored[field] != snapshot[field]:
                            raise AssertionError(f"Hard restart changed persisted {field}")
                    if not request(f"/api/v1/desk/deals/{deal}/audit/verify")["ok"]:
                        raise AssertionError("Audit failed after restart")
                samples = sorted(timings)
                return {"ok": True, "offline": True, "concurrency": 8, "deals": 4, "quote_attempts": 48,
                        "stored_quotes": 16, "confirmed_quotes": 16, "independent_invoice_oracle": "Decimal",
                        "duplicate_quotes": 0, "hard_restart_preserved_state": True,
                        "request_count": len(samples), "p95_seconds": round(samples[int(len(samples) * .95)], 4),
                        "max_seconds": round(max(samples), 4),
                        "scope": "local disposable server, not production capacity"}
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit()
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
