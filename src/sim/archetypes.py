"""Parameterised supplier archetypes.

Columns are reserve (multiple of the buyer's target), urgency, bluff
probability, and concession tendency. These rows are the simulation.
They are not factory measurements.
"""
from __future__ import annotations

# Rationale: four hand-set opponent centres the sampler mixes.
# Stock-pressure, middle, firm, and bluffing. Not estimated from deals.
OPPONENT_TYPE_ROWS = (
    (1.05, 0.90, 0.35, 0.55),
    (1.00, 0.50, 0.50, 0.35),
    (1.18, 0.30, 0.65, 0.25),
    (1.10, 0.70, 0.80, 0.30),
)

OPPONENT_TYPE_NAMES = ("庫存壓力大", "中間型", "不缺單(強硬)", "詐唬型")
