"""The round safety gate: the predicate that decides whether a round is blocked.

Why this module exists
----------------------
Stage 4 of ``process_negotiation_round`` is the reason a round does not reach a
supplier: it decides, from six conditions, whether the engine's recommendation
must be withheld and escalated to a person instead. It lived as nineteen inline
lines inside an 877-line handler, which meant the safety decision could not be
tested without standing up a whole session, and could not be read without
scrolling past nine other stages.

It is now a pure function with named inputs and a single ``str | None`` output.
The side effects (notify, state transition) stay in the handler; the *decision*
is here, where it can be asserted directly.

The ordering is deliberate and is part of the contract:

1. **No counter** — the solver found nothing feasible. This is the only branch
   that also triggers the out-of-band notification, because it is the one case
   where there is no price to escalate *about*.
2. **Security escalation** — the inbound message is an attack pattern, not a
   negotiation. Reported ahead of a breakdown trip because an attack is not a
   price problem.
3. **Breakdown risk** — the walk-away probability exceeds the user's tolerance.
4. **Opponent above the ceiling** — they have priced outside the deal.
5. **Engine above the ceiling** — our own number breaches the buyer's cap. The
   last branch is the one that should never fire; if it does, the solver and
   the clamp disagree and that is a defect, not a negotiation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoundSafetyInput:
    """Everything the gate is allowed to look at.

    A frozen dataclass rather than a loose tuple of arguments, so a caller
    cannot silently pass the values in the wrong order, and so the test can
    name what it is asserting on.
    """

    recommended_price: float | None
    requires_escalation: bool
    primary_intent: Any = None
    tactical_rationale: str = ""
    breakdown_probability: float = 0.0
    risk_tolerance: float | None = None
    breakdown_trip_threshold: float = 0.0
    opponent_price: float | None = None
    ceiling_price: float = 0.0


def breakdown_circuit_tripped(breakdown_probability: float, risk_tolerance: float | None, threshold: float) -> bool:
    """True when the walk-away probability exceeds the cap that applies.

    Kept as a call into the engine's own predicate so the definition of
    "tripped" has exactly one home; this wrapper only resolves which cap.
    """
    from src.engine import model

    return bool(model.breakdown_circuit_tripped(breakdown_probability, risk_tolerance))


def circuit_breaker_reason(inp: RoundSafetyInput) -> str | None:
    """Why this round must be blocked and escalated, or ``None`` to proceed.

    Returns a sentence safe to show a person. Never raises: a missing input
    must not turn a safety check into a 500.
    """
    if inp.recommended_price is None:
        return "No counter. Every feasible offer exceeds the walk-away hazard cap."

    if inp.requires_escalation:
        intent = getattr(inp.primary_intent, "value", None) or str(inp.primary_intent or "")
        rationale = (inp.tactical_rationale or "").strip()
        return f"Security escalation: {intent} ({rationale})".rstrip(" ()")

    cap = float(inp.breakdown_trip_threshold if inp.risk_tolerance is None else inp.risk_tolerance)
    if breakdown_circuit_tripped(inp.breakdown_probability, inp.risk_tolerance, cap):
        return (
            f"Breakdown risk {float(inp.breakdown_probability):.1%} exceeds "
            f"{cap:.0%} safety limit."
        )

    if inp.opponent_price and float(inp.opponent_price) > float(inp.ceiling_price):
        return (
            f"Opponent offer ¥{float(inp.opponent_price):.2f} breaches "
            f"ceiling ¥{float(inp.ceiling_price):.2f}."
        )

    if float(inp.recommended_price) > float(inp.ceiling_price):
        return (
            f"Engine recommended price ¥{float(inp.recommended_price):.2f} breaches "
            f"ceiling ¥{float(inp.ceiling_price):.2f}."
        )

    return None
