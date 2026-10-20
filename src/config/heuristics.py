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

# Rationale: payoff penalty subtracted once per unit of break probability.
BREAK_PENALTY = 0.10

# Rationale: fraction of the remaining gap the opponent is assumed to eat,
# before bluff scales it down. Chosen, not fitted.
OPPONENT_GAP_SHARE = 0.45

# Rationale: cap on an effective per-round concession rate.
CONCESSION_RATE_CAP = 0.95

# Rationale: labelled suggestion for a reserve prior, shown in the desk
# before the user saves one. The desk, /round, and the auction do not apply
# it unless the request includes it. It is not a market measurement.
PRIOR_RESERVE_MEAN = 1.08

# Rationale: labelled width that accompanies that suggestion. Applied only
# when the user submits a prior and does not submit a width.
PRIOR_RESERVE_STD = 0.10

# Rationale: noise used when sampling a reserve around its type centre.
SAMPLE_NOISE = 0.08

# Rationale: noise on urgency, bluff, and concession when sampling opponents.
SAMPLE_AXIS_NOISE = 0.10

# Rationale: lower clip for sampled urgency and concession.
PARAM_FLOOR = 0.05

# Rationale: belief-meter map. raw = BASE + VAR * variance_reduction - CV * cv,
# then clipped. A display score, not a confidence interval.
BELIEF_CONF_BASE = 0.75
BELIEF_CONF_VAR = 0.35
BELIEF_CONF_CV = 0.80
BELIEF_CONF_LO = 0.50
BELIEF_CONF_HI = 0.98

# Rationale: scale of the Student-t observation in the reserve update.
# A numerical robustness setting, not a measured quote error.
BAYES_OBS_NOISE = 0.06

# Rationale: degrees of freedom of that Student-t. A heavy-tailed
# robustness setting so a far quote is down-weighted and still moves the
# mean. Not an economic claim about suppliers.
STUDENT_T_DF = 4.0

# Rationale: normal consistency constant for the median absolute deviation,
# 1 / Phi^{-1}(0.75). A published identity used when a scale is estimated
# from the user's own quotes.
MAD_NORMAL_CONSISTENCY = 1.4826

# Rationale: optional markup a user may type, as a fraction of the target.
# The dashboard does not fill it in and does not send it when the field is
# blank. Blank derives the reserve from the other quotes and from closed
# deals. Not a measured margin and not a silent default.
BAYES_MARKUP = 0.08

# Rationale: central credible mass reported with a Student-t reserve belief.
# The conventional 95% reporting level. Not a fitted coverage rate.
CREDIBLE_MASS = 0.95

# Rationale: the one-half in the Student-t incomplete-beta shape and in the
# Gaussian KL. An identity, not a fitted weight.
ONE_HALF = 0.5

# Rationale: bisection bracket for a standard Student-t quantile, in
# standard deviations. A numerical guard, not a price.
STUDENT_T_BRACKET = 1000.0

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

# Rationale: when the offline real-model benchmark is within this
# normalised step of the supplier, it records the supplier's price.
# A scoring rule for that script, not a live-desk quote.
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

# Rationale: prior standard deviation of the per-supplier hazard random
# effect. Hierarchical shrinkage before any walk-away is observed.
# Assumption, not estimated.
HAZARD_SUPPLIER_TAU = 0.35

# Rationale: minimum half-width of the random-effect grid, in logit units.
# The live grid is at least this wide and at least HAZARD_U_TAILS times the
# user's spread, so a spread larger than 2 is still inside the support.
HAZARD_U_HALF = 2.0

# Rationale: how many prior standard deviations of the supplier effect the
# hazard grid covers on each side. Eight standard deviations leave a
# negligible normal tail outside the grid.
HAZARD_U_TAILS = 8.0

# Rationale: spacing of the hazard random-effect grid, in logit units.
# Fine enough that the predictive 97.5% hazard matches the closed form
# sigmoid(eta + Phi^{-1}(0.975) * tau) when no outcomes have been recorded.
HAZARD_U_STEP = 0.05

# Rationale: clip on the hazard logit so an extreme gap cannot overflow
# the sigmoid. Not a price.
HAZARD_LOGIT_CLIP = 20.0

# Rationale: tail mass outside the central credible interval. 0.05 leaves
# a 95% interval. The upper end is the conservative prior-driven reading.
# Moving this level from 0.05 to 0.10 changed the block bit on 28 of 539
# quotes (5.2%) on the seed-19 bodies with markup 0.05. Callers
# of /negotiate and /multi-auction/run can set another level. The desk
# keeps this labelled default.
HAZARD_CREDIBLE_ALPHA = 0.05

# Rationale: below this many observed continue or walk-away events the
# hazard is labelled prior-driven. A disclosure rule, not a fitted size.
HAZARD_MIN_EVENTS = 30

# Rationale: number of points on the random-effect grid.
HAZARD_U_POINTS = 64

# Rationale: lowest point of the legacy concession grid, where 1 is the
# target. The live counter does not read this floor. It searches expected
# surplus from a tiny positive offer up to the quote, the ceiling, and the
# rival cap. Kept for the CLI grid only.
OFFER_GRID_FLOOR = 0.50

# Rationale: step of the analytic surplus search, in the same units as the
# normalised offer (1 is the target). The chosen counter is the mesh point
# with the highest expected surplus. Halving the step must not move that
# counter by more than one step.
SEARCH_STEP = 0.001

# Rationale: largest mesh the analytic search will allocate. A wider domain
# uses a coarser step. Live offers are normalised, so this cap is not the
# decision. A numerical budget, not a price.
SEARCH_MESH_CAP = 20001

# Rationale: a parsed unit price is auto-accepted only from the target
# divided by this multiple up to the larger of the target and the ceiling
# times this multiple. Three still accepts a quote near twice the ceiling,
# and sends a price near an eighth of the target to review. The user can
# set another multiple on the deal. A labelled band, not a market range.
UNIT_SANITY_MULTIPLE = 3.0

# Rationale: how many times the user's MOQ a planning mandate may stretch
# when the user did not set a separate maximum. A labelled volume policy,
# not a factory limit.
MOQ_HEADROOM_MULTIPLE = 2

# Rationale: sample budget for one /round or /negotiate solve. A numerical
# budget, not a price.
ROUND_N_SAMPLES = 2000

# Rationale: Monte Carlo budget for the report attached to a chosen offer.
# The samples do not choose the offer. The offer is the analytic surplus
# maximum. Not a market quote.
DESK_N_SAMPLES = 5000

# Rationale: legacy offer-grid length. The live counter does not read it.
# Doubling it must not move a counter. Kept so an older caller can still
# name a grid. Not a market quote.
DESK_N_GRID = 20

# Rationale: fraction of the cost sample in the upper CVaR tail. The tail
# is reported. It is not a term in the offer score, because on this
# one-step accept-or-reject objective the tail sat on the ceiling for
# almost every candidate and could not change the offer.
# A conventional reporting tail, not a fitted walk-away rate.
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

# Rationale: labelled Beta mean for D, the fraction of a supplier's own
# first ask at which their reserve sits. 0.9 means the policy expects about
# ten percent of room under that ask. It is not fitted. The eBay
# auto-decline ratio is not this number.
D_PRIOR_MEAN = 0.9

# Rationale: pseudo-count strength, alpha plus beta, of that Beta prior.
# Four is a weak prior, so a few responses from this supplier can move it.
# The card shows the push-lower offer at half this strength and at double it.
D_PRIOR_STRENGTH = 4.0

# Rationale: slope of the soft likelihood used when a supplier rejects an
# offer. The rejection raises weight on D above the rejected ratio and
# leaves weight below it. It is not a hard truncation.
REJECT_SOFTNESS = 8.0

# Rationale: quadrature nodes for a censored or reweighted Beta distribution
# function. This integrates the belief. It is not a price grid and it does
# not choose the offer.
QUAD_NODES = 64

# Rationale: iteration budget for the golden-section maximiser of the
# one-dimensional offer. The offer is that maximiser, not a mesh point.
BRENT_ITERS = 80

# Rationale: golden-section conjugate (sqrt(5) − 1) / 2. A mathematical
# identity used by the bounded scalar maximiser.
GOLDEN_CONJUGATE = 0.6180339887498949

# Rationale: relative tolerance of the bounded scalar maximiser. A numerical
# guard, not a price.
OPT_TOL = 1e-12

# Rationale: continued-fraction iterations for the regularized incomplete
# beta function. A numerical guard.
BETA_CF_ITERS = 200

# Rationale: how many resolved shadow accepts, all confirmed unchanged, put
# the two-sided Wilson 95 percent lower bound at 0.98. The desk shows
# progress toward this count. Auto-accept stays off.
GATE_CERTIFY_ACCEPTS = 189

# Rationale: digit-group length the span reader treats as a thousands
# separator. The rule reader does not call this tokenizer.
SPAN_GROUP = 3

# Rationale: timeout, in seconds, for one optional extractor call. A missing
# key leaves the rule prefill only.
LLM_TIMEOUT_SECONDS = 20

# Rationale: default OpenAI-compatible base for the desk span extractor.
# An unset URL uses this host. A call still requires an API key.
EXTRACTOR_BASE_URL = "https://api.tokenfactory.nebius.com/v1"

# Rationale: default chat model for the desk span extractor. It names evidence
# spans and does not set a price. Temperature stays zero.
EXTRACTOR_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"

# Rationale: second Nebius chat model. The desk calls it when the Ultra hop
# times out, gets a server or rate-limit response, or returns a body that is
# not a mentions object. It does not set a price.
EXTRACTOR_FALLBACK_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# Rationale: NVIDIA integrate base used only after Nebius itself fails.
# The Ultra model on this host is not in the chain.
EXTRACTOR_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Rationale: NVIDIA chat model for the third hop. It is the Super model.
# The Ultra model on this host is not called.
EXTRACTOR_NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# Rationale: seconds allowed for the Nebius Ultra extractor hop. A longer
# wait does not start; the desk moves to the next hop or to human review.
EXTRACTOR_TIMEOUT_ULTRA_SECONDS = 12

# Rationale: seconds allowed for the Nebius Super extractor hop. This is
# the second hop, after Ultra, and it is a policy limit rather than a price.
EXTRACTOR_TIMEOUT_SUPER_SECONDS = 15

# Rationale: seconds allowed for the NVIDIA Super extractor hop. This hop
# runs only after Nebius itself fails, and only inside the overall budget.
EXTRACTOR_TIMEOUT_NVIDIA_SECONDS = 30

# Rationale: overall seconds one quote may spend in the extractor chain.
# It equals the Ultra, Super, and NVIDIA hop limits added together, so a
# quote cannot wait longer than the whole chain.
EXTRACTOR_TIMEOUT_BUDGET_SECONDS = 57

# Rationale: maximum completion tokens sent on every extractor hop. A long
# supplier message was truncated when the provider default was left in place.
EXTRACTOR_MAX_OUTPUT_TOKENS = 4096

# Rationale: maximum reasoning tokens sent on every extractor hop. The cap
# leaves room for the JSON answer inside the output limit above.
EXTRACTOR_REASONING_BUDGET = 2048

# Rationale: characters of supplier text counted as one output token when the
# completion cap grows for a long message. A short message stays at the floor.
EXTRACTOR_CHARS_PER_TOKEN = 4

# Rationale: ceiling on extractor completion tokens. A long message may use
# more than the floor, and a truncated completion still fails closed.
EXTRACTOR_OUTPUT_TOKEN_CAP = 32768

# Rationale: how many extractor HTTP calls may be in flight at once. A hop
# that misses its deadline is abandoned, and further calls wait for a free
# slot instead of starting another socket.
EXTRACTOR_MAX_IN_FLIGHT = 4

# Rationale: seconds allowed for one read or write of the external chain
# anchor. It bounds that network call. It is not a price and not a quote.
CHAIN_ANCHOR_TIMEOUT_SECONDS = 5

# Rationale: HTTP status when the desk cannot open a deal because the chain
# key is missing. The process stays up. The create route tells the caller.
HTTP_UNAVAILABLE = 503

# Rationale: HTTP status an anchor store returns when this deal has no head yet.
# The desk treats that as an empty export chain, not as a store that is down.
HTTP_NOT_FOUND = 404

# Rationale: HTTP status that means the extractor was rate limited. That
# status retries the next hop. It is not a price and not a quote amount.
EXTRACTOR_HTTP_TOO_MANY = 429

# Rationale: HTTP status at or above this value is a provider failure. The
# desk retries the next hop. Client errors below it are recorded as well.
EXTRACTOR_HTTP_SERVER = 500

# Rationale: Newton polish step is two to the minus this many bits, times the
# offer scale. Wide enough that curvature is visible in float64, and short
# enough that the uniform and Beta(alpha, 1) closed forms land inside 1e-9.
NEWTON_STEP_BITS = 20

# Rationale: characters of the message kept on each side of an evidence span
# when a currency or unit mark sits beside the number rather than inside it.
NEIGHBORHOOD_CHARS = 24

# Rationale: the 4 in the two-sided Wilson variance, z squared over 4n. An
# identity of the interval, not a price and not a sample size.
WILSON_QUARTIC = 4

# Rationale: quote-gap knot for the small-gap hinge in the eBay walk-away
# logit. Gaps below this knot get an extra term because a straight line
# understated seller-ended walk-away there. It is part of the fitted model,
# not a price.
SMALL_GAP_KNOT = 0.10

# Rationale: ISO code for the yuan. The deal row stores the currency a
# decision uses. This constant is the code the parser recognises for 元,
# and the labelled default when a caller omits the deal currency.
DEAL_CURRENCY = "CNY"

# Rationale: labelled policy for a bare number in a Chinese supplier message
# on a CNY deal. The number is a review candidate either way. It is accepted
# only when this value is 1. The default 0 leaves that confirm to a person.
IMPLICIT_CNY_AUTO_ACCEPT = 0

# Rationale: labelled policy for a pack count the supplier wrote beside a
# bundle price. That count converts into the deal unit only when this value
# is 1. The default 0 records the bundle and does not divide it.
UNIT_BASIS_FROM_MESSAGE = 0

# Rationale: one fen is one hundredth of a yuan. A supplier who writes 分
# is naming that subunit. The price in yuan is the count times this factor.
FEN_IN_YUAN = 0.01

# Rationale: one jiao, also called 毛, is one tenth of a yuan. A supplier
# who writes 角 or 毛 is naming that subunit, and the yuan price is the count
# times this factor.
JIAO_IN_YUAN = 0.1

# Rationale: one li is one thousandth of a yuan. A supplier who writes 厘
# is naming that subunit. The price in yuan is the count times this factor.
LI_IN_YUAN = 0.001

# Rationale: supplier-message length, in characters, above which the extractor
# skips the Ultra hop and calls Super. A longer message was timing out on Ultra.
EXTRACTOR_SUPER_DIRECT_CHARS = 24000

# Rationale: currencies a deal may store. The decision reads the deal row.
# It does not pick one of these itself.
DEAL_CURRENCIES = ["CNY", "USD", "EUR"]

# Rationale: minor units per major unit for the currencies above. Prices in
# a decision record are stored as integers of this scale. Two decimal places.
MINOR_SCALE = 100

# Rationale: largest extractor response, in bytes, that is parsed. A larger
# body is dropped and the quote stays in human review.
EXTRACTOR_MAX_BYTES = 65536

# Rationale: maximum characters stored for one extractor evidence span or a
# caller note. Longer text is rejected by the schema. It is not a price.
FIELD_MAX_CHARS = 500


def _sigmoid(value: float) -> float:
    import math

    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def public_payload() -> dict:
    """Numbers the dashboard and the extension are allowed to display."""
    intercept = float(HAZARD_INTERCEPT)
    gap = float(HAZARD_QUOTE_GAP)
    hazard_status = "fit-missing"
    hazard_note = "The eBay hazard file is not available in this process."
    try:
        from src.engine.hazard_fit import load_hazard_fit

        fit = load_hazard_fit()
        intercept = float(fit.intercept)
        gap = float(fit.gap_coef)
        hazard_status = "ebay_seller_ended"
        caveat = str((fit.provenance or {}).get("transfer_caveat") or "")
        hazard_note = (
            "Walk-away hazard is transferred from eBay Best Offer, 2012–13, "
            "Business & Industrial, seller-ended, with a small-gap hinge. "
            "It is not a measured Chinese-factory rate. A reserve-gap term is "
            "not in the decision. The risk cap applies to the incremental hazard "
            "of our offer. " + caveat
        ).strip()
    except Exception:
        pass
    return {
        "documented": True,
        "source": "src/config/heuristics.py",
        "breakdown_trip_threshold": BREAKDOWN_TRIP_THRESHOLD,
        "risk_tolerance_default": BREAKDOWN_TRIP_THRESHOLD,
        "risk_tolerance_floor": RISK_TOLERANCE_FLOOR,
        "risk_tolerance_max": RISK_TOLERANCE_MAX,
        "cvar_tail_fraction": CVAR_TAIL_FRACTION,
        "caution_risk_threshold": CAUTION_RISK_THRESHOLD,
        "hazard_status": hazard_status,
        "hazard_intercept": intercept,
        "hazard_tau": HAZARD_SUPPLIER_TAU,
        "hazard_quote_gap": gap,
        "hazard_baseline_rate": _sigmoid(intercept),
        "hazard_baseline_low": _sigmoid(intercept - HAZARD_SUPPLIER_TAU),
        "hazard_baseline_high": _sigmoid(intercept + HAZARD_SUPPLIER_TAU),
        "hazard_min_events": HAZARD_MIN_EVENTS,
        "prior_reserve_suggestion": PRIOR_RESERVE_MEAN,
        "prior_reserve_std_suggestion": PRIOR_RESERVE_STD,
        "student_t_df": STUDENT_T_DF,
        "dashboard_expected_markup": None,
        "unit_sanity_multiple": UNIT_SANITY_MULTIPLE,
        "hazard_credible_alpha": HAZARD_CREDIBLE_ALPHA,
        "weitzman_search_cost": WEITZMAN_SEARCH_COST,
        "hazard_note": hazard_note,
        "ladder_edge_lo": LADDER_EDGE_LO,
        "ladder_edge_hi": LADDER_EDGE_HI,
        "ladder_step_fine": LADDER_STEP_FINE,
        "ladder_step_mid": LADDER_STEP_MID,
        "note": "Chosen safety bands and display quantisation, not fitted rates.",
    }
