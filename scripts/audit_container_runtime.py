"""Exercise the actual image with 0.5 CPU, 512 MB and a disposable volume.

Never uses host .env, real contacts, paid providers or a mail transport.
Requires Docker plus PyNaCl on the caller. No privileged containers.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request

from nacl.signing import SigningKey


def audit(image: str, docker_host: str | None, expected_sha: str | None) -> dict:
    prefix = ["docker"] + (["--host=" + docker_host] if docker_host else [])
    name = "tradeflow-audit-" + secrets.token_hex(6)
    volume = name + "-data"
    api_key, chain_key = secrets.token_hex(24), secrets.token_hex(32)
    signing = SigningKey.generate()
    timings: list[float] = []
    base = ""

    def docker(*args):
        result = subprocess.run(prefix + list(args), text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f"Docker {args[0]} failed: {result.stderr[-2000:]}")
        return result.stdout.strip()

    def request(path, data=None, *, authorized=True, expected=200):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["X-API-Key"] = api_key
        body = None if data is None else json.dumps(data).encode()
        started = time.monotonic()
        req = urllib.request.Request(base + path, data=body, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            result = json.load(response)
            if response.status != expected:
                raise AssertionError(f"{path}: expected HTTP {expected}, got {response.status}: {result}")
        timings.append(time.monotonic() - started)
        return result

    def start():
        nonlocal base
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--memory=512m",
            "--cpus=0.5",
            "--pids-limit=128",
            "-p",
            "127.0.0.1::8000",
            "-v",
            volume + ":/app/data",
        ]
        values = {
            "TRADEFLOW_SKIP_DOTENV": "1",
            "TRADEFLOW_ENV": "production",
            "TRADEFLOW_LEGACY": "0",
            "TRADEFLOW_API_KEY": api_key,
            "TRADEFLOW_CHAIN_KEY": chain_key,
            "TRADEFLOW_REQUIRE_SIGNED_APPROVAL": "1",
            "TRADEFLOW_APPROVAL_PUBKEY": signing.verify_key.encode().hex(),
            "TRADEFLOW_CHAIN_ANCHOR": "/app/data/audit-anchor.json",
            "TRADEFLOW_DESK_DB": "/app/data/desk.db",
            "TRADEFLOW_LLM_DAILY_CALLS": "0",
            "TRADEFLOW_WEB_EVIDENCE_OFFLINE": "1",
            "TRADEFLOW_MARKET_OFFLINE": "1",
        }
        for key, value in values.items():
            args += ["-e", key + "=" + value]
        docker(*args, image)
        base = "http://127.0.0.1:" + docker("port", name, "8000/tcp").rsplit(":", 1)[1]
        deadline = time.monotonic() + 90
        while True:
            try:
                request("/ready")
                return
            except (OSError, AssertionError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Container did not become ready") from None
                time.sleep(0.2)

    docker("volume", "create", volume)
    try:
        # Run the exact image without mounting data: runtime databases must not
        # already be baked into the image by COPY data/.
        scan = docker(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            image,
            "-c",
            "from pathlib import Path; p=[p for p in Path('/app/data').rglob('*') "
            "if p.is_file() and (p.match('*.db*') or p.match('*.sqlite*'))]; "
            "assert not p, 'runtime databases baked into image'; print('clean')",
        )
        assert scan == "clean"
        start()
        version = request("/version")
        if expected_sha and version["git_sha"] != expected_sha:
            raise AssertionError("Container build identity differs from the tested source")
        request("/api/v1/desk/deals/no-such-deal", authorized=False, expected=401)
        deal = request(
            "/api/v1/desk/deals",
            {
                "product": "Offline container audit; fictional suppliers",
                "quantity": 1000,
                "target_price": 24,
                "ceiling_price": 40,
                "risk_tolerance": 0.5,
                "suppliers": [{"name": name, "address": name.lower() + "@example.test"} for name in "ABCD"],
            },
        )
        deal_id = deal["deal"]["id"]
        path = "/api/v1/desk/deals/" + deal_id
        jobs = []
        for row, price in zip(deal["suppliers"], (30, 25, 28, 27), strict=True):
            payload = {
                "source": "paste",
                "raw_text": f"单价 {price:.2f} 元。",
                "supplier_id": row["id"],
                "external_id": "container-" + row["id"],
                "freight": 1 if price == 30 else 0,
                "fees": 1 if price == 30 else 0,
                "duty_rate": 0.1 if price == 30 else 0,
            }
            jobs.extend([(row["id"], price, payload)] * 3)
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda job: request(path + "/quotes", job[2]), jobs))
        unique = {result["event"]["id"]: job for result, job in zip(results, jobs, strict=True)}
        assert len(unique) == 4
        for event_id, (supplier_id, price, _) in unique.items():
            request(
                path + "/review/" + event_id,
                {"action": "accept", "supplier_id": supplier_id, "chosen_token": f"{price:.2f}"},
            )
        snapshot = request(path)
        rows = snapshot["suppliers"]
        asks = dict(zip("ABCD", map(Decimal, ("30", "25", "28", "27")), strict=True))
        costs = {
            name: (ask + (2 if name == "A" else 0)) * (Decimal("1.1") if name == "A" else 1)
            for name, ask in asks.items()
        }
        assert snapshot["ranking_basis"] == "landed_cost"
        assert [r["name"] for r in sorted(rows, key=lambda r: r["rank"])] == sorted(costs, key=costs.get)
        for row in rows:
            assert Decimal(str(row["latest_quote"])) == asks[row["name"]]
            if row["counter"] is not None:
                amount = Decimal(str(row["counter"]))
                invoice = (amount + (2 if row["name"] == "A" else 0)) * (Decimal("1.1") if row["name"] == "A" else 1)
                assert amount <= min(asks[row["name"]], Decimal("40"))
                assert invoice <= min(cost for other, cost in costs.items() if other != row["name"])
        draft = next(row for row in snapshot["drafts"] if row["status"] == "draft")
        digest = hashlib.sha256(draft["body"].encode()).hexdigest()
        send_body = {"human_approved": True, "body_sha256": digest}
        request("/api/v1/desk/drafts/" + draft["id"] + "/send", send_body, expected=422)
        signed = json.dumps(
            {"deal_id": deal_id, "draft_id": draft["id"], "body_sha256": digest},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        send_body["approval"] = {
            "alg": "ed25519",
            "pubkey": signing.verify_key.encode().hex(),
            "sig": signing.sign(signed).signature.hex(),
        }
        approved = request("/api/v1/desk/drafts/" + draft["id"] + "/send", send_body)
        assert approved["status"] == "approved" and approved["transmitted"] == 0
        snapshot = request(path)
        assert request(path + "/audit/verify")["ok"]
        # SIGKILL then remove/recreate the container, not just reopen a Python
        # object: the named volume is the sole source of recovered state.
        docker("kill", "--signal=KILL", name)
        stopped = json.loads(docker("inspect", name))[0]
        assert not stopped["State"]["OOMKilled"]
        docker("rm", name)
        start()
        restored = request(path)
        for field in ("suppliers", "events", "drafts"):
            assert restored[field] == snapshot[field], field
        assert request(path + "/audit/verify")["ok"]
        stats = json.loads(docker("stats", "--no-stream", "--format", "{{json .}}", name))
        return {
            "ok": True,
            "scope": "actual Docker image; local fictional suppliers, not live integrations",
            "image": image,
            "git_sha": version["git_sha"],
            "limits": {"cpu": 0.5, "memory_mb": 512},
            "signed_approval_required": True,
            "unsigned_approval_refused": True,
            "transmitted": False,
            "baked_runtime_databases": False,
            "concurrent_quote_attempts": len(jobs),
            "unique_quotes": len(unique),
            "independent_decimal_invoice_checks": True,
            "container_recreation_preserved_state": True,
            "audit_and_file_anchor_verify": True,
            "oom_killed": False,
            "memory_usage_after_recreation": stats["MemUsage"],
            "requests": len(timings),
            "max_request_seconds": round(max(timings), 4),
        }
    finally:
        subprocess.run(prefix + ["rm", "-f", name], capture_output=True)
        subprocess.run(prefix + ["volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker-host")
    parser.add_argument("--expected-sha")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.image, args.docker_host, args.expected_sha)
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
