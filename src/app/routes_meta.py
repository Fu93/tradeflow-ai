"""Liveness and readiness probes: /health and /ready.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. Registered on the bare
app (no /api/v1 gate): a probe that needs a key cannot answer a load balancer.
"""
from __future__ import annotations
from typing import Any

from fastapi.responses import JSONResponse

from src.config.heuristics import HTTP_UNAVAILABLE
from ..engine.backend import describe_solver_backend
from ..engine.llm_config import llm_status


def register_meta_routes(app) -> None:
    @app.get("/health")
    def health():
        from src.desk.audit_chain import chain_key_configured

        language = llm_status()
        desk_line = "desk sends enabled"
        if not chain_key_configured():
            desk_line = "desk sends disabled: chain key missing"
        return {
            "status": "ok",
            "version": "0.4.0",
            "solver": describe_solver_backend(),
            "llm": language,
            "offline_demo": language["offline_demo"],
            "desk": desk_line,
        }


    @app.get("/ready")
    def ready():
        """Readiness probe: the process can actually serve a decision, or it cannot.

        `/health` is a liveness probe — it answers "is this process up" and is
        intentionally cheap and dependency-free. It used to be the only probe, so
        a pod whose SQLite file was unwritable, whose chain key was missing, or
        whose desk store had never been created still reported `ok` and received
        traffic. Readiness is a different question: every dependency a send
        depends on is checked here, and the answer is `503` when one is missing.

        The body never contains a secret or a key value — only whether one is set.
        """
        from src.desk.audit_chain import chain_key_configured
        from src.desk.approval_signing import approval_policy, required_approval_pubkey

        checks: dict[str, dict[str, Any]] = {}

        checks["chain_key"] = {
            "ok": bool(chain_key_configured()),
            "detail": "TRADEFLOW_CHAIN_KEY is set" if chain_key_configured() else "TRADEFLOW_CHAIN_KEY is missing",
            "required": True,
        }

        store_ok = False
        store_detail = "not probed"
        try:
            from src.desk.store import get_store

            probe = get_store().probe_write()
            store_ok = bool(probe.get("ok"))
            store_detail = str(probe.get("detail") or ("writable" if store_ok else "not writable"))
        except Exception as exc:  # pragma: no cover - defensive: a broken store must not 500 the probe
            store_detail = f"store probe failed: {type(exc).__name__}"
        checks["desk_store"] = {"ok": store_ok, "detail": store_detail, "required": True}

        policy = approval_policy()
        pubkey_present = required_approval_pubkey() is not None
        approval_ok = policy != "required" or pubkey_present
        checks["signed_approval"] = {
            "ok": approval_ok,
            "detail": (
                "policy=required, public key pinned"
                if policy == "required" and pubkey_present
                else "policy=required, no public key pinned (fail-closed: sends are refused)"
                if policy == "required"
                else f"policy={policy}"
            ),
            "required": policy == "required",
        }

        blocking = [name for name, item in checks.items() if item["required"] and not item["ok"]]
        body = {
            "status": "ready" if not blocking else "not_ready",
            "blocking": blocking,
            "checks": checks,
            "version": "0.4.0",
        }
        if blocking:
            return JSONResponse(status_code=HTTP_UNAVAILABLE, content=body)
        return body


