"""Score the desk quote reader on a held-out set of supplier messages.

Readers scored on every message:

* ``rules``: the deterministic parser alone (``src.desk.parse.parse_quote``), inside
  the desk's unit-sanity band (``UNIT_SANITY_MULTIPLE`` around target and ceiling).
* ``<model>``: one Nemotron model on Nebius Token Factory, alone. Its answer is
  the amount read by code from the span it labels as the supplier's own unit
  offer. The model never supplies a number; a price field in its answer is
  discarded by the same code the desk uses.
* ``gate:<model>``: the desk's shadow gate, which accepts only when the rules
  and that model's span agree.

Each answer is scored against ``own_offer`` (the supplier's single current CNY
unit price, or null when a person must decide):

* ``correct``: the right amount.
* ``wrong``: an amount that is not the label. This is the costly error.
* ``missed``: no amount where the label has one (the message goes to review).
* ``held``: no amount where the label is null (correctly left for a person).

The data file was written before any model run and is pinned by its SHA-256 in
the output. Usage::

    python scripts/eval_quote_reader.py --offline           # rules only, no network
    TRADEFLOW_LLM_API_KEY=... python scripts/eval_quote_reader.py
    python scripts/eval_quote_reader.py --models nvidia/nemotron-3-super-120b-a12b

The API key is read from the environment and never written to the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.heuristics import (  # noqa: E402
    EXTRACTOR_BASE_URL,
    EXTRACTOR_FALLBACK_MODEL,
    EXTRACTOR_MODEL,
    EXTRACTOR_TIMEOUT_BUDGET_SECONDS,
    UNIT_SANITY_MULTIPLE,
)
from src.desk import extract  # noqa: E402
from src.desk.parse import parse_quote  # noqa: E402
from src.desk.shadow import evaluate_shadow  # noqa: E402
from src.desk.span_read import read_one, span_in_message  # noqa: E402

DATA = ROOT / "data" / "eval" / "quote_reader_holdout.jsonl"
OUT = ROOT / "results" / "quote_reader_eval.json"
CURRENCY = "CNY"
KEYS = ("TRADEFLOW_LLM_API_KEY", "OPENAI_API_KEY")
OUTCOMES = ("correct", "wrong", "missed", "held")
# Held-out items the reader prompt was revised against on 2026-10-28 (the demo's
# sample message is close to them). They stay in the set and are also scored apart.
EXPOSED = ("zh_rival_then_own", "en_rival_then_own")

Transport = Callable[[dict[str, Any]], Any]


def load(path: Path = DATA) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate id in the held-out set")
    return rows, hashlib.sha256(raw).hexdigest()


def outcome(value: float | None, truth: float | None) -> str:
    if value is None:
        return "held" if truth is None else "missed"
    if truth is not None and abs(float(value) - float(truth)) < 1e-9:
        return "correct"
    return "wrong"


def rule_reading(text: str) -> dict[str, Any]:
    return parse_quote(text, deal_currency=CURRENCY)


def rule_value(reading: dict[str, Any]) -> float | None:
    if reading.get("status") != "accepted":
        return None
    price = (reading.get("parsed") or {}).get("unit_price")
    return None if price is None else float(price)


def model_value(text: str, mentions: list[dict[str, Any]]) -> float | None:
    """The amount in the one span the model calls the supplier's own unit offer."""
    amounts = set()
    for mention in mentions:
        if mention.get("class") != "own_unit_offer":
            continue
        if mention.get("speaker") != "supplier" or mention.get("polarity") != "affirmative":
            continue
        if bool(mention.get("is_question")):
            continue
        span = str(mention.get("evidence_span") or "")
        if not span_in_message(span, text):
            continue
        amount = read_one(span)
        if amount is not None:
            amounts.add(float(amount))
    return amounts.pop() if len(amounts) == 1 else None


def in_band(value: float | None, low: float, high: float) -> float | None:
    """The desk accepts nothing outside its unit-sanity band; neither does any reader here."""
    if value is None or value < low or value > high:
        return None
    return value


def bounds(target: float, ceiling: float) -> tuple[float, float]:
    multiple = float(UNIT_SANITY_MULTIPLE)
    return target / multiple, max(target, ceiling) * multiple


def http_transport(model: str, base: str, timeout: float, usage: dict[str, Any]) -> Transport:
    def send(body: dict[str, Any]) -> Any:
        body = dict(body, model=model)
        decoded = extract._post(base, body, timeout, KEYS)
        if isinstance(decoded, dict) and isinstance(decoded.get("usage"), dict):
            usage.update(decoded["usage"])
        return decoded

    return send


def run_model(
    rows: list[dict[str, Any]],
    model: str,
    *,
    base: str,
    low: float,
    high: float,
    transport_for: Callable[[str, dict[str, Any]], Transport] | None = None,
) -> list[dict[str, Any]]:
    timeout = float(EXTRACTOR_TIMEOUT_BUDGET_SECONDS)
    out = []
    for row in rows:
        usage: dict[str, Any] = {}
        transport = (
            transport_for(model, usage) if transport_for else http_transport(model, base, timeout, usage)
        )
        hop = {"provider": "nebius", "model": model, "base": base, "keys": KEYS, "timeout": timeout}
        started = time.perf_counter()
        result, status, _latency, _digest = extract._execute_hop(row["text"], hop, transport, timeout)
        latency = time.perf_counter() - started
        mentions = result.get("mentions") or [] if status == "ok" else []
        alone = in_band(model_value(row["text"], mentions), low, high)
        reading = rule_reading(row["text"])
        gate = evaluate_shadow(
            row["text"], reading, low=low, high=high, mentions=mentions,
            extractor_configured=status == "ok", currency=CURRENCY,
        )
        gate_value = rule_value(reading) if gate.get("would_accept") else None
        out.append({
            "id": row["id"],
            "status": status,
            "latency_s": round(latency, 3),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "numeric_field_discarded": bool(result.get("numeric_field_discarded")),
            "classes": sorted({str(m.get("class")) for m in mentions}),
            "alone": alone,
            "alone_outcome": outcome(alone, row["own_offer"]),
            "gate": gate_value,
            "gate_outcome": outcome(gate_value, row["own_offer"]),
        })
    return out


def tally(outcomes: list[str]) -> dict[str, int]:
    return {name: sum(1 for item in outcomes if item == name) for name in OUTCOMES}


def latency_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median_s": None, "max_s": None}
    return {"median_s": round(statistics.median(values), 3), "max_s": round(max(values), 3)}


def evaluate(
    rows: list[dict[str, Any]],
    models: list[str],
    *,
    base: str,
    target: float,
    ceiling: float,
    transport_for: Callable[[str, dict[str, Any]], Transport] | None = None,
) -> dict[str, Any]:
    low, high = bounds(target, ceiling)
    rules = []
    for row in rows:
        value = in_band(rule_value(rule_reading(row["text"])), low, high)
        rules.append({"id": row["id"], "value": value, "outcome": outcome(value, row["own_offer"])})
    readers: dict[str, Any] = {"rules": {"tally": tally([item["outcome"] for item in rules])}}
    per_model: dict[str, Any] = {}
    for model in models:
        scored = run_model(rows, model, base=base, low=low, high=high, transport_for=transport_for)
        per_model[model] = scored
        ok = [item for item in scored if item["status"] == "ok"]
        tokens_in = [item["prompt_tokens"] for item in ok if isinstance(item["prompt_tokens"], int)]
        tokens_out = [item["completion_tokens"] for item in ok if isinstance(item["completion_tokens"], int)]
        readers[model] = {
            "tally": tally([item["alone_outcome"] for item in scored]),
            "answered": len(ok),
            "failed": len(scored) - len(ok),
            "latency": latency_summary([item["latency_s"] for item in ok]),
            "prompt_tokens_total": sum(tokens_in) if tokens_in else None,
            "completion_tokens_total": sum(tokens_out) if tokens_out else None,
            "numeric_field_discarded": sum(1 for item in scored if item["numeric_field_discarded"]),
        }
        readers[model]["tally_clean"] = tally(
            [item["alone_outcome"] for item in scored if item["id"] not in EXPOSED]
        )
        readers["gate:" + model] = {
            "tally": tally([item["gate_outcome"] for item in scored]),
            "tally_clean": tally([item["gate_outcome"] for item in scored if item["id"] not in EXPOSED]),
        }
    return {
        "exposed": [row["id"] for row in rows if row["id"] in EXPOSED],
        "rows": len(rows),
        "labelled_offers": sum(1 for row in rows if row["own_offer"] is not None),
        "bounds": {"target": target, "ceiling": ceiling, "low": low, "high": high},
        "readers": readers,
        "per_message": {"rules": rules, **per_model},
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "| Reader | Correct | Wrong | Missed | Held for a person |",
        "|---|---|---|---|---|",
    ]
    for name, block in report["readers"].items():
        t = block["tally"]
        lines.append(f"| {name} | {t['correct']} | {t['wrong']} | {t['missed']} | {t['held']} |")
        clean = block.get("tally_clean")
        if clean:
            lines.append(
                f"| {name} (without the {len(report.get('exposed') or [])} exposed items) | {clean['correct']} | "
                f"{clean['wrong']} | {clean['missed']} | {clean['held']} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--offline", action="store_true", help="score the rules only; no network")
    parser.add_argument(
        "--models", nargs="*", default=list(dict.fromkeys([EXTRACTOR_MODEL, EXTRACTOR_FALLBACK_MODEL])),
        help="model ids to score (default: the desk's models, once each)",
    )
    parser.add_argument("--base", default=EXTRACTOR_BASE_URL)
    parser.add_argument("--target", type=float, default=30)
    parser.add_argument("--ceiling", type=float, default=40)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    rows, sha = load()
    models = [] if args.offline else list(args.models)
    if models and not extract._env(*KEYS):
        print("Set TRADEFLOW_LLM_API_KEY, or pass --offline.", file=sys.stderr)
        return 2
    report = evaluate(rows, models, base=args.base, target=args.target, ceiling=args.ceiling)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(DATA.relative_to(ROOT)),
        "dataset_sha256": sha,
        "endpoint": None if not models else args.base,
        "models": models,
        "note": "Held-out set written on 2026-10-28 before any model run. Labels are the "
                "author-side reading of each message, not supplier ground truth.",
        **report,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(markdown(report))
    print(f"\nWrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
