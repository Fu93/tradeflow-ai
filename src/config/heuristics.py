"""Chosen modelling constants.

Every assignment below is a heuristic, a unit conversion, or a published
statistical identity. None of them is a factory quote, a benchmark result,
or a measurement from this repository. Scenario prices and simulated
supplier parameters live in ``src.sim``, not here.

``scripts/check_no_fake_literals.py`` allowlists this file. A new float in
any other production module has to move here with a ``Rationale:`` comment,
or be derived from a file, or be removed.
"""
from __future__ import annotations

# Rationale: top of the normalised strategy grid. 1.0 is the buyer's target.
# 1.35 means the search stops 35% above that target. Not a market ceiling.
YOUR_CEILING = 1.35

# Rationale: inset so the highest opening sits just under YOUR_CEILING.
GRID_EDGE_INSET = 0.02

# Rationale: safety gate. At this breakdown probability the engine stops
# proposing and waits for a person. Not fitted to factory data.
BREAKDOWN_TRIP_THRESHOLD = 0.15

# Rationale: display band for a mild walk-away hazard. It sits just above
# the zero-gap prior (the logistic intercept) and below the trip cap, so a
# small opening gap is labelled caution. Not a measured rate and not a
# second trip. The old 0.08 band was chosen for 1 - p_deal, which sat higher.
CAUTION_RISK_THRESHOLD = 0.06

# Rationale: neutral opening gap (normalised) for the anchoring reaction.
ANCHORING_REF = 0.10

# Rationale: how strongly an opening gap changes the opponent's concession.
# 1.0 means a gap one full target-unit away scales the concession by that
# amount. Setting this to 0 restores the pre-anchoring payoff. Not fitted.
ANCHORING_BETA = 1.0

# Rationale: clip on the anchoring multiplier so a wild gap cannot zero
# or explode the concession rate. Chosen bounds, not a fit.
ANCHORING_CLIP = (0.50, 1.50)

# Rationale: a final gap at or under 2% of the target counts as a deal.
DEAL_TOLERANCE = 0.02

# Rationale: exponential scale of deal probability. Each extra 5% of gap
# multiplies acceptance by 1/e. Chosen, not fitted.
DEAL_DECAY_SCALE = 0.05

# Rationale: an opening gap above this is treated as an aggressive open
# and adds break probability. Normalised to the target.
OPENING_GAP_BREAK = 0.32

# Rationale: extra break probability when the opening gap is large.
OPENING_BREAK_BASE = 0.20

# Rationale: bluff weight on that extra break probability.
OPENING_BREAK_BLUFF = 0.30

# Rationale: payoff penalty subtracted once per unit of break probability.
BREAK_PENALTY = 0.10

# Rationale: fraction of the remaining gap the opponent is assumed to eat,
# before bluff scales it down. Chosen, not fitted.
OPPONENT_GAP_SHARE = 0.45

# Rationale: cap on an effective per-round concession rate.
CONCESSION_RATE_CAP = 0.95

# Rationale: default belief that the supplier's reserve sits 8% above the
# buyer's target, before any offer is observed. Not a market prior.
PRIOR_RESERVE_MEAN = 1.08

# Rationale: default width of that belief. Not a measured standard deviation.
PRIOR_RESERVE_STD = 0.10

# Rationale: noise used when sampling a reserve around its type centre.
SAMPLE_NOISE = 0.08

# Rationale: noise on urgency, bluff, and concession when sampling opponents.
SAMPLE_AXIS_NOISE = 0.10

# Rationale: lower clip for sampled urgency and concession.
PARAM_FLOOR = 0.05

# Rationale: upper clip for a sampled concession rate.
CONC_CAP = 0.95

# Rationale: belief-meter map. raw = BASE + VAR * variance_reduction - CV * cv,
# then clipped. A display score, not a confidence interval.
BELIEF_CONF_BASE = 0.75
BELIEF_CONF_VAR = 0.35
BELIEF_CONF_CV = 0.80
BELIEF_CONF_LO = 0.50
BELIEF_CONF_HI = 0.98

# Rationale: Bayesian observation noise, prior that a quote is genuine,
# and the normalised domain of a plausible quote. Chosen, not fitted.
BAYES_OBS_NOISE = 0.06
BAYES_PRIOR_CLEAN = 0.85
BAYES_DOMAIN_MIN = 0.80
BAYES_DOMAIN_MAX = 2.50

# Rationale: markup subtracted from an observed offer before the reserve
# update. A modelling assumption, not a supplier's stated margin.
BAYES_MARKUP = 0.08

# Rationale: standard-normal quantile for a two-sided 95% interval
# (Phi^{-1}(0.975)). A published identity, not a fitted coefficient.
Z_95 = 1.95996

# Rationale: flat concession rates on the five-round search grid.
CONCESSION_FLAT_RATES = (0.05, 0.10, 0.15, 0.20)

# Rationale: (start, end) linear concession ramps on that same grid.
CONCESSION_RAMPS = (
    (0.03, 0.27),
    (0.27, 0.03),
    (0.06, 0.20),
    (0.20, 0.06),
)

# Rationale: shaped five-round curves so the grid contains front-loaded
# and back-loaded concessions. Not transcripts.
CONCESSION_FIXED_ROWS = (
    (0.08, 0.10, 0.12, 0.14, 0.16),
    (0.16, 0.14, 0.12, 0.10, 0.08),
    (0.10, 0.08, 0.12, 0.10, 0.10),
    (0.12, 0.14, 0.10, 0.12, 0.10),
)

# Rationale: buyer planning defaults for logrolling when a session does not
# override them. Shadow prices and rates are not supplier quotes.
LEAD_DAY_SHADOW = 0.15
BASELINE_DEPOSIT_PCT = 0.30
MAX_DEPOSIT_PCT = 0.50
BUYER_CAPITAL_RATE = 0.08
SUPPLIER_RELIEF_RATE = 0.22
EXCESS_HOLDING_RATE = 0.05
# Rationale: planning figure used only inside the buyer's own surplus math
# when nobody has quoted a mold fee. It must not be described as the
# supplier's price. Uncalibrated.
PLANNING_MOLD_FEE = 3000.0
VOLUME_DISCOUNT_RATIO = 0.08
SUPPLIER_COST_RATIO = 0.82
RUSH_COST_COEFFICIENT = 0.08
RUSH_COST_EXPONENT = 1.25
# Rationale: share of an unquoted planning mold fee counted as buyer surplus.
# Not a received waiver.
MOLD_SURPLUS_SHARE = 0.50
# Rationale: volume-trade stretch offered against the supplier's stated MOQ.
MOQ_STRETCH = 1.50

# Rationale: assumed days of inventory when turning an annual holding rate
# into a unit cost. Not this buyer's measured turnover.
INVENTORY_HOLDING_DAYS = 90.0
DAILY_LEAD_PENALTY = 0.003
TCO_MARKDOWN_RATE = 0.08
TCO_MARKDOWN_EXP = 1.2
TCO_UNFAVORABLE_PREMIUM = 1.05
TCO_CRISIS_RATE = 0.015
TCO_CRISIS_EXPONENT = 1.75
# Rationale: delay, in days, after which the crisis term starts, and the
# scale of that term. Chosen shape, not a contract clause.
TCO_CRISIS_DELAY_START = 5.0
TCO_CRISIS_DELAY_SCALE = 10.0

# Rationale: a new offer at or under 97% of the previous one ends a silence
# hold. A 3% move was chosen as "a real concession". Not fitted.
SILENCE_CONCESSION_RATIO = 0.97

# Rationale: a supervisor numeric override below half the target is refused.
SUPERVISOR_MIN_PRICE_RATIO = 0.50

# Rationale: hard safety cap on a deposit fraction, stricter than the
# business default on the mandate. An input bound, not a quote.
DEPOSIT_HARD_CAP = 0.60

# Rationale: upper bounds on API numeric fields so one request cannot ask
# the solver to treat an unbounded price as real.
API_NORM_CAP = 100.0
API_EXTRACTED_PRICE_CAP = 1000.0
API_PRICE_CAP = 1_000_000.0
API_MARKUP_CAP = 10.0

# Rationale: ratios used when comparing an offer to the target. 1.05 is
# "a little above target", 1.02 is "close enough to call a deal".
STAGNATION_PREMIUM = 1.05
NEAR_DEAL_RATIO = 1.02
PRICE_MATCH_RATIO = 0.02

# Rationale: clamps for a price or fee mentioned in text, so a parsed
# token cannot become an unbounded float. Not a market range.
MIN_MENTIONED_PRICE = 0.1
MAX_MENTIONED_PRICE = 50000.0
MAX_MENTIONED_MOLD_FEE = 100_000_000.0

# Rationale: pattern-ladder scores. A regex hit is not a calibrated
# probability. The number only ranks which rule fired.
REGEX_CONF_OFFLINE = 0.95
REGEX_CONF_SPEC = 0.90
REGEX_CONF_IDENTITY = 0.92
REGEX_CONF_DEAL = 0.90
REGEX_CONF_POVERTY = 0.85
REGEX_CONF_VOLUME = 0.85
REGEX_CONF_TAX = 0.85
REGEX_CONF_UNRECOGNIZED = 0.30
ONNX_ACCEPT_CONFIDENCE = 0.70
LLM_DEFAULT_CONFIDENCE = 0.85
FIELD_DEFAULT_CONFIDENCE = 0.80
POSTERIOR_STD_PLACEHOLDER = 0.10

# Rationale: relative tolerance when two parsed prices are treated as the
# same mention.
PRICE_MENTION_TOLERANCE = 0.05

# Rationale: a parsed number within one percent of a deposit is treated as
# that deposit, not as a lead time in days.
MENTION_CONFUSION_TOLERANCE = 0.01

# Rationale: guardrail ceiling used when a tactical object is built without
# a session ceiling. 1.5 times the authorised price. A safety multiple,
# not a quote.
GUARDRAIL_CEILING_MULTIPLE = 1.50

# Rationale: ladder label spacing, as a fraction of the target-to-ceiling
# span, so tick labels do not collide with the two end labels.
LADDER_EDGE_LO = 0.06
LADDER_EDGE_HI = 0.94
LADDER_STEP_FINE = 0.10
LADDER_STEP_MID = 0.50

# Rationale: concession rates the robustness report sweeps. A sensitivity
# grid, not a claim about a supplier.
ROBUSTNESS_CONCESSIONS = (0.03, 0.05, 0.10, 0.15)
ROBUSTNESS_FACTORS = (0.50, 1.0, 1.50)
ROBUSTNESS_STABLE_BREAK = 0.15
ROBUSTNESS_STABLE_GAP = 0.05
ROBUSTNESS_BREAK_MULTIPLE = 2.0

# Rationale: baseline fixed concession used by the robustness comparison.
FIXED_BASELINE_CONCESSION = 0.05


# Rationale: offers outside this yuan band in the real-model benchmark are
# recorded as parse failures. They are not imputed. The band is a unit check,
# not a market range.
PLAUSIBLE_OFFER_MIN = 5.0
PLAUSIBLE_OFFER_MAX = 30.0

# Rationale: when the real-model buyer is within this normalised step of the
# supplier, the benchmark records the supplier's price. A scoring rule, not
# a quote.
BENCHMARK_FINAL_STEP = 0.01

# Rationale: normal quantile used by the Wilson interval in benchmark_stats,
# and the 5% tail used by Clopper-Pearson. Published, not fitted to this repo.
WILSON_Z = 1.96
WILSON_ALPHA = 0.05

# Rationale: logistic-regression settings for the on-device intent export.
# Training hyperparameters, not a measured accuracy.
ONNX_LOGREG_C = 3.0
ONNX_TEMPERATURE = 1.20

# Rationale: walk-away hazard logit when our offer is not aggressive.
# Assumption, not a fit. No public dataset was estimated for this build.
# The eBay Best Offer replication (doi:10.7910/DVN/ZOWWO7) was not downloaded.
HAZARD_INTERCEPT = -2.9

# Rationale: logit slope on (quote - offer) / quote. Positive, so a more
# aggressive offer has a higher walk-away hazard. Assumption, not fitted.
HAZARD_QUOTE_GAP = 3.4

# Rationale: logit slope on max(reserve - offer, 0) / reserve. Positive, so
# the same offer is riskier against a higher reserve. Assumption, not fitted.
HAZARD_RESERVE_GAP = 2.2

# Rationale: logit slope on a reciprocity violation. Assumption. With no
# concession history the term is zero.
HAZARD_RECIPROCITY = 0.8

# Rationale: logit slope on the round index. Zero because the sign of time
# pressure is not assumed without observed walk-aways.
HAZARD_ROUND = 0.0

# Rationale: logit slope on the number of better rival quotes. Zero.
# Competition enters through the outside option. A direct hazard sign is
# not assumed.
HAZARD_N_ALT = 0.0

# Rationale: extra logit slope when a sampled bluff multiplies the reserve
# gap. Assumption, not fitted.
HAZARD_BLUFF = 0.6

# Rationale: prior standard deviation of the per-supplier hazard random
# effect. Hierarchical shrinkage before any walk-away is observed.
# Assumption, not estimated.
HAZARD_SUPPLIER_TAU = 0.35

# Rationale: half-width of the random-effect grid. A support for the logit
# shift, not a price.
HAZARD_U_HALF = 2.0

# Rationale: clip on the hazard logit so an extreme gap cannot overflow
# the sigmoid. Not a price.
HAZARD_LOGIT_CLIP = 20.0

# Rationale: tail mass outside the central credible interval. 0.05 leaves
# a 95% interval. The upper end is the conservative prior-driven reading.
HAZARD_CREDIBLE_ALPHA = 0.05

# Rationale: below this many observed continue or walk-away events the
# hazard is labelled prior-driven. A disclosure rule, not a fitted size.
HAZARD_MIN_EVENTS = 30

# Rationale: number of points on the random-effect grid.
HAZARD_U_POINTS = 64

# Rationale: lowest normalised offer on the coupled grid, where 1 is the
# target. A supplier quote below this floor can still be accepted. Not a
# market quote.
OFFER_GRID_FLOOR = 0.50

# Rationale: desk solve budget. Five thousand shared scenarios and a
# 20-point grid match the auction counters on the checked seeds, and the
# reported standard errors sit on the seed-to-seed spread. The auction
# default of 20_000 stays on the auction path. Not a market quote.
DESK_N_SAMPLES = 5000
DESK_N_GRID = 20

# Rationale: weight on CVaR of buyer loss in the coupled objective. It does
# not scale with the hazard cap. Assumption, not fitted.
CVAR_WEIGHT = 0.35

# Rationale: fraction of the cost sample in the upper CVaR tail. Separate
# from the user's hazard cap, so one number does not drive both. A
# conventional tail, not a fitted walk-away rate.
CVAR_TAIL_FRACTION = 0.05

# Rationale: smallest accepted user risk tolerance. Below this the hazard
# cap is not a meaningful policy. A bound, not a measured rate.
RISK_TOLERANCE_FLOOR = 0.01

# Rationale: largest accepted user risk tolerance. A cap of one would
# disable the hazard limit on ordinary openings. A policy bound, not a
# measured rate.
RISK_TOLERANCE_MAX = 0.50

# Rationale: normalised cost of one more exchange in the Weitzman index,
# as a share of the target. An ordering assumption, not a supplier quote.
WEITZMAN_SEARCH_COST = 0.02

# Rationale: bisection steps for the Weitzman reservation. Sixty-four
# halvings put the root well inside a double's spacing on a bracket a few
# units wide. A numerical budget, not a price.
WEITZMAN_BISECTION_ITERS = 64

# Rationale: how many belief standard deviations the Weitzman bracket
# extends on each side of the mean. The root sits below the mean when the
# search cost is smaller than the expected improvement at the mean. A
# bracket width, not a price.
WEITZMAN_BRACKET_SIGMAS = 8

# Rationale: default NumPy seed when a coupled solve is not given one.
# Reproducibility only. Not a price.
DEFAULT_SOLVER_SEED = 20261013

# Rationale: subtracted from the coupled score per unit of offer so equal
# surplus prefers the lower offer. A numerical epsilon, not a price.
OFFER_TIE_BREAK = 1e-6

# Rationale: quotes within this gap of the cheapest quote share the
# lean-in label. A display band, not a price.
TACTIC_PRICE_TIE = 1e-4

# Rationale: a quote above this multiple of the cheapest quote is labelled
# sideline. Display band carried forward from the previous hand-set rule.
# Not a price and not a fitted threshold.
TACTIC_SIDELINE_RATIO = 1.12

# Rationale: prior correlation of reserve shocks across suppliers. Zero is
# the independence prior. A shared factor is still drawn. Not estimated.
SHARED_FACTOR_RHO = 0.0

# Rationale: passes of the coupled offer search. The outside option is the
# rival quote, which does not move inside the pass, so a second pass would
# repeat the same decision and inflate the cell count. One pass is the
# search budget.
COUPLED_SWEEPS = 1


def public_payload() -> dict:
    """Numbers the dashboard and the extension are allowed to display."""
    return {
        "documented": True,
        "source": "src/config/heuristics.py",
        "breakdown_trip_threshold": BREAKDOWN_TRIP_THRESHOLD,
        "risk_tolerance_default": BREAKDOWN_TRIP_THRESHOLD,
        "risk_tolerance_floor": RISK_TOLERANCE_FLOOR,
        "risk_tolerance_max": RISK_TOLERANCE_MAX,
        "cvar_tail_fraction": CVAR_TAIL_FRACTION,
        "caution_risk_threshold": CAUTION_RISK_THRESHOLD,
        "hazard_status": "prior-driven",
        "hazard_note": (
            "Walk-away hazard uses assumed logistic coefficients in "
            "src/config/heuristics.py. No public dataset was fitted. "
            "The default cap is user policy, not an estimated rate."
        ),
        "ladder_edge_lo": LADDER_EDGE_LO,
        "ladder_edge_hi": LADDER_EDGE_HI,
        "ladder_step_fine": LADDER_STEP_FINE,
        "ladder_step_mid": LADDER_STEP_MID,
        "note": "Chosen safety bands and display quantisation, not fitted rates.",
    }
