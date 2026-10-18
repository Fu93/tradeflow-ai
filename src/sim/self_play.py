"""Scripted self-play opponents for the auction demo.

These prices are simulated archetypes. They are not factory quotes. The
live solver imports the table so the numbers stay in the simulation
package, which is the only place a scenario price is allowed.
"""
from __future__ import annotations

# Rationale: normalised opening and reserve (1 = the buyer's target) for the
# four scripted archetypes the demo runs when the caller does not paste quotes.
SELF_PLAY_SPECS = (
    {"kind": "conceder", "init_price": 1.28, "reserve_price": 1.05},
    {"kind": "tit_for_tat", "init_price": 1.30, "reserve_price": 1.12},
    {"kind": "hard", "init_price": 1.33, "reserve_price": 1.18},
    {"kind": "bluff", "init_price": 1.40, "reserve_price": 1.15},
)

# Rationale: normalised buyer offer used only on round 1 of the self-play
# demo, before the solver has produced a counter. Not a market quote.
BUYER_OPENING_NORM = 1.05

SELF_PLAY_NAMES = (
    {"name": "Supplier A (Agile Conceder)", "profile": "Agile Conceder"},
    {"name": "Supplier B (Rational Tit-for-Tat)", "profile": "Rational Tit-for-Tat"},
    {"name": "Supplier C (Moderate Hardliner)", "profile": "Moderate Hardliner"},
    {"name": "Supplier D (Stubborn Over-priced)", "profile": "Stubborn Over-priced"},
)
