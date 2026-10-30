"""Liveness and readiness probes: /health and /ready.

[ARCHITECTURE 2026-10-24] Moved verbatim from main.py. Registered on the bare
app (no /api/v1 gate): a probe that needs a key cannot answer a load balancer.
"""
from __future__ import annotations
import os
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

        from src.app.security import MIN_API_KEY_LEN, env_mode

        production = env_mode() != "sandbox"
        configured_key = os.environ.get("TRADEFLOW_API_KEY", "").strip()
        auth_ok = len(configured_key) >= MIN_API_KEY_LEN
        checks["api_auth"] = {
            "ok": auth_ok,
            "detail": (
                "API authentication configured" if auth_ok else "API authentication not configured for production"
            ),
            "required": production,
        }

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

        # [OPS 2026-10-25] A database written by a NEWER build must stop this
        # process from serving: migrations refuse to run against a newer
        # schema, and a stale binary reading newer rows would corrupt them.
        # Readiness reports the mismatch instead of discovering it on a write.
        schema_ok = False
        schema_detail = "not probed"
        try:
            import sqlite3

            from src.desk.migrations import CURRENT_SCHEMA_VERSION, user_version
            from src.desk.store import db_path

            conn = sqlite3.connect(str(db_path()))
            try:
                version = user_version(conn)
            finally:
                conn.close()
            if version > CURRENT_SCHEMA_VERSION:
                schema_detail = (
                    f"database schema {version} is newer than this build "
                    f"({CURRENT_SCHEMA_VERSION}); deploy the newer build"
                )
            else:
                schema_ok = True
                schema_detail = f"schema {version} <= build {CURRENT_SCHEMA_VERSION}"
        except Exception as exc:  # pragma: no cover - defensive: probe must not 500
            schema_detail = f"schema probe failed: {type(exc).__name__}"
        checks["desk_schema"] = {"ok": schema_ok, "detail": schema_detail, "required": True}

        policy = approval_policy()
        pubkey_present = required_approval_pubkey() is not None
        pubkey_valid = False
        if pubkey_present:
            from nacl.signing import VerifyKey
            from nacl.bindings import crypto_core_ed25519_is_valid_point

            try:
                raw_key = bytes.fromhex(required_approval_pubkey() or "")
                VerifyKey(raw_key)
                pubkey_valid = bool(crypto_core_ed25519_is_valid_point(raw_key))
            except (ValueError, TypeError):
                pass
        approval_ok = policy != "required" or pubkey_valid
        checks["signed_approval"] = {
            "ok": approval_ok,
            "detail": (
                "policy=required, public key pinned"
                if policy == "required" and pubkey_valid
                else "policy=required, public key missing or malformed (fail-closed: sends are refused)"
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

    @app.get("/version")
    def version():
        """Build identity, read-only. Answers "what am I talking to".

        [OPS 2026-10-24] Health answers "is it up"; this answers "which build".
        A dashboard masthead, a bug report, or a post-restore check can pin the
        exact code and schema without shell access. ``git_sha`` comes from the
        TRADEFLOW_GIT_SHA environment variable — a container does not carry the
        git history, so the deploy pipeline stamps it (and prints "unknown"
        when nobody did). No secret material: the schema version and the build
        tag are public facts about a public service.
        """
        import sys

        from src.desk.migrations import CURRENT_SCHEMA_VERSION

        return {
            "version": "0.4.0",
            "schema_version": int(CURRENT_SCHEMA_VERSION),
            # TRADEFLOW_GIT_SHA wins; Render stamps RENDER_GIT_COMMIT on every
            # deploy, so the live demo names its commit without a manual step.
            # The Dockerfile defaults TRADEFLOW_GIT_SHA to the word "unknown",
            # which is no stamp at all, so it does not hide RENDER_GIT_COMMIT.
            "git_sha": next(
                (
                    value
                    for value in (
                        os.environ.get("TRADEFLOW_GIT_SHA", "").strip(),
                        os.environ.get("RENDER_GIT_COMMIT", "").strip(),
                    )
                    if value and value.lower() != "unknown"
                ),
                "unknown",
            ),
            "python": sys.version.split()[0],
        }


