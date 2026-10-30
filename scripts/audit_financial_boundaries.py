"""Offline Decimal invoice oracle for exact-cent landed-price caps."""

import argparse
from pathlib import Path
import random, sys, json
from decimal import Decimal, ROUND_DOWN

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine.landed import landed_unit_cost
from src.desk.recompute import _landed_consistent_rivals
from src.desk.money import quantise_down

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cases", type=int, default=100000)
parser.add_argument("--seed", type=int, default=301026)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
if args.cases < 1:
    parser.error("--cases must be above zero")
rng = random.Random(args.seed)
unsafe = []
over_conservative = []
for i in range(args.cases):
    # Construct a rival whose exact landed amount is the own invoice at a cent boundary.
    ownask = Decimal(rng.randrange(100, 10000)) / 100
    offer = Decimal(rng.randrange(1, int(ownask * 100) + 1)) / 100
    freight = Decimal(rng.randrange(0, 10000)) / 1000
    fees = Decimal(rng.randrange(0, 1000)) / 1000
    duty = Decimal(rng.randrange(0, 1000)) / 1000
    rival = (offer + freight + fees) * (1 + duty)
    own = landed_unit_cost(unit_price=float(ownask), freight=float(freight), fees=float(fees), duty_rate=float(duty))
    other = landed_unit_cost(unit_price=float(rival), freight=0, fees=0, duty_rate=0)
    inverse = _landed_consistent_rivals(
        "own", {"own": float(ownask), "other": float(rival)}, {"own": own, "other": other}, "landed_cost"
    )[0]
    price = Decimal(str(quantise_down(inverse)))
    # True inputs are the submitted JSON decimals, not a higher precision synthetic rival.
    exactrival = Decimal(str(float(rival)))
    exactcap = (exactrival / (1 + duty)) - freight - fees
    if (price + freight + fees) * (1 + duty) > exactrival:
        unsafe.append(
            {
                "offer": str(offer),
                "rival_input": str(exactrival),
                "freight": str(freight),
                "fees": str(fees),
                "duty": str(duty),
                "inverse": inverse,
                "issued": str(price),
                "true_cap": str(exactcap),
            }
        )
    if price < exactcap.quantize(Decimal(".01"), rounding=ROUND_DOWN):
        over_conservative.append(
            {
                "case": i,
                "ask": str(ownask),
                "rival_input": str(exactrival),
                "freight": str(freight),
                "fees": str(fees),
                "duty": str(duty),
                "inverse": inverse,
                "issued": str(price),
                "true_cap": str(exactcap),
            }
        )

report = {
    "ok": not unsafe and not over_conservative,
    "scope": "offline Decimal invoice boundary audit",
    "seed": args.seed,
    "checked": i + 1,
    "unsafe_count": len(unsafe),
    "unsafe_examples": unsafe[:5],
    "conservative_cases": len(over_conservative),
    "examples": over_conservative[:3],
}
text = json.dumps(report, indent=2) + "\n"
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
print(text, end="")
raise SystemExit(0 if report["ok"] else 1)
