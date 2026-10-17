"""Inputs for the local smoke scripts.

``verify_all.py`` and ``verify_live.py`` check that a mechanism runs. The
prices below are the cases those scripts feed in. They are not factory
quotes and they are not benchmark results.
"""
from __future__ import annotations

from src.sim.scenarios import UMBRELLA

# Rationale: the auction smoke uses the umbrella simulation, so the solver
# sees the same mandate the sandbox uses.
AUCTION_TARGET = UMBRELLA.target
AUCTION_CEILING = UMBRELLA.ceiling

# Rationale: a small positive pair so the persistence smoke can store one
# session. Not a product price.
PERSIST_TARGET = 10.0
PERSIST_CEILING = 15.0

# Rationale: a legal logrolling case (offer under the ceiling) so the smoke
# checks that the solver returns proposals.
LOGROLL_TARGET = 12.0
LOGROLL_CEILING = 16.0
LOGROLL_OFFER = 15.0

# Rationale: a legal proposal body. The smoke blocks the script, not these
# numbers.
GUARD_PRICE = 14.0
GUARD_DEPOSIT = 0.4

# Rationale: one hand-built executive sentence. The parser must return the
# price written in the sentence. Not a negotiation.
EXEC_COMMAND = "offer 13.80 with 50% deposit and waive mold fee"
EXEC_PRICE = 13.80
EXEC_DEPOSIT = 0.50

# Rationale: a tactical object built from that sentence so the renderers have
# something to print. The floor sits under the authorised price, which sits
# under the ceiling.
EXEC_CEILING = 16.00
EXEC_RISK = 0.08
EXEC_CONFIDENCE = 0.95
EXEC_FLOOR = 12.96
EXEC_MOLD = 3000.0
EXEC_PRICE_STEP = 1.11

# Rationale: the live HTTP smoke opens one sandbox deal and posts two
# scripted supplier lines. Not a factory.
LIVE_TARGET = 12.0
LIVE_CEILING = 15.0
LIVE_OFFER_R1 = 14.20
LIVE_OFFER_R2 = 13.80
