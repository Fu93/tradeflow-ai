"""Simulated mandates and the scripted CLI replies.

The only prices in this module are scenario inputs. A dashboard that shows
one of them has to say it is a simulated archetype. Nothing here is a
1688 quote or a benchmark result.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SimulatedMandate:
    key: str
    name: str
    target: float
    ceiling: float
    moq: int
    lead_label: str
    deposit_label: str
    tooling_label: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "target_price": self.target,
            "ceiling_price": self.ceiling,
            "moq": self.moq,
            "lead_label": self.lead_label,
            "deposit_label": self.deposit_label,
            "tooling_label": self.tooling_label,
            "simulated": True,
        }


# Rationale: fictional umbrella case used when a caller asks for a simulated
# mandate and does not pass a price. Not a market quote.
UMBRELLA = SimulatedMandate(
    key="umbrella",
    name="Custom Carbon Auto Umbrella",
    target=12.0,
    ceiling=16.2,
    moq=5000,
    lead_label="12 - 35 days",
    deposit_label="30% - 50%",
    tooling_label="Exemption demanded, no amount assumed",
)

# Rationale: second fictional commodity so the desk can show that the
# engine follows the mandate the operator picked. Not a market quote.
CNC_CASE = SimulatedMandate(
    key="cnc_case",
    name="Aerospace CNC Metal Case",
    target=28.0,
    ceiling=36.5,
    moq=2000,
    lead_label="15 - 40 days",
    deposit_label="25% - 45%",
    tooling_label="Tooling waiver requested (no amount assumed)",
)

# Rationale: third fictional commodity, same purpose as CNC_CASE.
BATTERY = SimulatedMandate(
    key="battery",
    name="Industrial LiFePO4 Battery Pack",
    target=185.0,
    ceiling=240.0,
    moq=500,
    lead_label="20 - 45 days",
    deposit_label="30% - 60%",
    tooling_label="Certification cost: to be quoted, not assumed",
)

MANDATES = (UMBRELLA, CNC_CASE, BATTERY)
MANDATES_BY_KEY = {item.key: item for item in MANDATES}

# Rationale: scripted normalised replies (1.0 = target) for the offline CLI
# walkthrough. Not a supplier, and not used by the dashboard as a live chat.
SUPPLIER_REPLIES = (1.25, 1.18, 1.12)


def public_payload() -> dict:
    return {
        "kind": "simulated_archetype",
        "note": "Simulated mandates, not factory quotes. Pick one only to open a labelled sandbox deal.",
        "mandates": [item.as_dict() for item in MANDATES],
    }
