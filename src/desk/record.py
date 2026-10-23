"""Append-only decision record. The counter is withheld unless every check passes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.config.heuristics import DEAL_CURRENCY, MINOR_SCALE, OPT_TOL
from src.desk.money import is_quantised, to_minor

ROOT = Path(__file__).resolve().parents[2]
_OPTIMISER = "decision.choose_offer"


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


def file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def constants_snapshot() -> dict[str, str]:
    registry = ROOT / "docs" / "constants_registry.json"
    heuristics = ROOT / "src" / "config" / "heuristics.py"
    return {
        "registry_sha256": file_sha256(registry),
        "heuristics_sha256": file_sha256(heuristics),
    }


def hazard_fit_sha256() -> str:
    return file_sha256(ROOT / "data" / "hazard_ebay_bi.json")


@dataclass(frozen=True)
class DecisionRecord:
    deal_id: str
    supplier_id: str
    currency: str
    event_ids: tuple[str, ...]
    prices_minor: tuple[int, ...]
    ask: float | None
    ceiling: float
    offer: float | None
    counter: float | None
    push_counter: float | None
    half: float | None
    double: float | None
    prior_alpha: float | None
    prior_beta: float | None
    prior_n: int
    prior_source: str
    optimiser: str
    hazard_fit_sha256: str
    constants: dict[str, str]
    unconfirmed: int
    checks: tuple[Check, ...]
    extractor_response_sha256: str = ""
    extractor_attempts: tuple[dict[str, Any], ...] = ()
    rival_asks: tuple[float, ...] = ()
    risk_cap: float | None = None
    reserve_upper: float | None = None
    reject_ratios: tuple[float, ...] = ()
    reference_ask: float | None = None
    hazard_intercept: float | None = None
    hazard_gap: float | None = None
    held_previous_counter: float | None = None

    def eligible(self) -> bool:
        return self.counter is not None and self.unconfirmed == 0 and all(item.passed for item in self.checks)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_ids"] = list(self.event_ids)
        payload["prices_minor"] = list(self.prices_minor)
        payload["checks"] = [item.as_dict() for item in self.checks]
        payload["extractor_attempts"] = [dict(item) for item in self.extractor_attempts]
        payload["rival_asks"] = [float(item) for item in self.rival_asks]
        payload["reject_ratios"] = [float(item) for item in self.reject_ratios]
        payload["eligible"] = self.eligible()
        return payload

    def sha256(self) -> str:
        encoded = hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        )
        return encoded.hexdigest()


def _check(name: str, passed: bool, detail: str) -> Check:
    return Check(name=name, passed=passed, detail=detail)


def build_record(
    *,
    deal_id: str,
    supplier_id: str,
    currency: str,
    events: list[dict[str, Any]],
    ask: float | None,
    ceiling: float,
    offer: float | None,
    counter: float | None,
    push_counter: float | None,
    half: float | None,
    double: float | None,
    prior_alpha: float | None,
    prior_beta: float | None,
    prior_n: int,
    prior_source: str,
    unconfirmed: int,
    hazard_within_cap: bool,
    fit_digest: str | None = None,
    optimiser: str = _OPTIMISER,
    rival_asks: tuple[float, ...] = (),
    risk_cap: float | None = None,
    reserve_upper: float | None = None,
    reject_ratios: tuple[float, ...] = (),
    reference_ask: float | None = None,
    hazard_intercept: float | None = None,
    hazard_gap: float | None = None,
    held_previous_counter: float | None = None,
    inputs_stored: bool = False,
) -> DecisionRecord:
    """Pure. A failed check clears the counter."""
    fit = fit_digest if fit_digest is not None else hazard_fit_sha256()
    accepted = [event for event in events if event.get("status") == "accepted"]
    only_accepted = len(accepted) == len(events) and all(
        (event.get("provenance") or {}).get("review_accepted_by") == "human" for event in accepted
    )
    currencies_ok = all(
        str((event.get("parsed") or {}).get("currency") or currency) == currency for event in accepted
    )
    tolerance = float(OPT_TOL) * max(1.0, abs(float(ask or 0.0)), abs(float(ceiling)))
    counter_le_ask = counter is None or ask is None or float(counter) <= float(ask) + tolerance
    counter_le_ceiling = counter is None or float(counter) <= float(ceiling) + tolerance
    # The number on the record must be the number the supplier reads. The draft
    # body renders two decimals, so a counter with sub-cent precision produced a
    # record and a message that disagreed about the price.
    counter_quantised = counter is None or is_quantised(float(counter))
    fit_pinned = bool(fit)
    checks = (
        _check("only_accepted_inputs", only_accepted or not events, "every priced input was accepted by a person"),
        _check("currency_matches_deal", currencies_ok and bool(currency), f"deal currency is {currency}"),
        _check("counter_le_ask", counter_le_ask, "counter is at or below this supplier's ask"),
        _check("counter_le_ceiling", counter_le_ceiling, "counter is at or below the ceiling"),
        _check(
            "counter_quantised",
            counter_quantised,
            f"counter is a whole number of minor units (scale {int(MINOR_SCALE)})",
        ),
        _check("hazard_within_cap", hazard_within_cap, "incremental hazard is inside the cap, or the push is withheld"),
        _check("fit_hash_pinned", fit_pinned, "the hazard file hash is on the record"),
        _check(
            "unconfirmed_clear",
            int(unconfirmed) == 0,
            f"unconfirmed numbers used in decisions: {int(unconfirmed)}",
        ),
        _check(
            "decision_inputs_stored",
            bool(inputs_stored),
            "ask, reserve, rivals, risk cap, and hazard terms are on the record",
        ),
    )
    issued = counter
    if any(not item.passed for item in checks):
        issued = None
    prices = tuple(
        to_minor(float(event["parsed"]["unit_price"]))
        for event in accepted
        if (event.get("parsed") or {}).get("unit_price") is not None
    )
    digests: list[str] = []
    attempts: list[dict[str, Any]] = []
    for event in accepted:
        provenance = event.get("provenance") or {}
        response_digest = str(provenance.get("response_sha256") or "")
        if response_digest and response_digest not in digests:
            digests.append(response_digest)
        for item in provenance.get("extractor_attempts") or []:
            if not isinstance(item, dict):
                continue
            attempts.append(
                {
                    "provider": str(item.get("provider") or ""),
                    "model": str(item.get("model") or ""),
                    "status": str(item.get("status") or ""),
                    "latency_s": float(item.get("latency_s") or 0.0),
                    "response_sha256": str(item.get("response_sha256") or ""),
                }
            )
    return DecisionRecord(
        deal_id=deal_id,
        supplier_id=supplier_id,
        currency=currency or DEAL_CURRENCY,
        event_ids=tuple(str(event["id"]) for event in accepted if event.get("id")),
        prices_minor=prices,
        ask=None if ask is None else float(ask),
        ceiling=float(ceiling),
        offer=None if offer is None else float(offer),
        counter=None if issued is None else float(issued),
        push_counter=None if issued is None or push_counter is None else float(push_counter),
        half=None if issued is None or half is None else float(half),
        double=None if issued is None or double is None else float(double),
        prior_alpha=prior_alpha,
        prior_beta=prior_beta,
        prior_n=int(prior_n),
        prior_source=prior_source,
        optimiser=optimiser,
        hazard_fit_sha256=fit,
        constants=constants_snapshot(),
        unconfirmed=int(unconfirmed),
        checks=checks,
        extractor_response_sha256=",".join(digests),
        extractor_attempts=tuple(attempts),
        rival_asks=tuple(float(item) for item in rival_asks),
        risk_cap=None if risk_cap is None else float(risk_cap),
        reserve_upper=None if reserve_upper is None else float(reserve_upper),
        reject_ratios=tuple(float(item) for item in reject_ratios),
        reference_ask=None if reference_ask is None else float(reference_ask),
        hazard_intercept=None if hazard_intercept is None else float(hazard_intercept),
        hazard_gap=None if hazard_gap is None else float(hazard_gap),
        held_previous_counter=None if held_previous_counter is None else float(held_previous_counter),
    )


def clamp_counter(offer: float, ceiling: float, ask: float) -> float:
    """The send guard recomputes this from the stored inputs."""
    return min(float(offer), float(ceiling), float(ask))
