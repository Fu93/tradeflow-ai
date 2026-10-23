"""Concurrent negotiation across suppliers.

The offer to each supplier maximises expected surplus of the accept-or-reject
model. The best rival landed quote is an outside option inside that search.
A quote is not treated as a reserve unless the caller sets a markup or a
prior. Demo self-play freezes the scripted reserve. Breakdown risk on this
path is the walk-away hazard.

Self-play runs only when the caller passes ``demo=True`` and a labelled
self-play bundle. This module does not import scenario prices.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from src.config.heuristics import (
    BAYES_OBS_NOISE,
    DEFAULT_SOLVER_SEED,
    ONE_HALF,
    SEARCH_STEP,
)
from src.legacy import model
from src.engine.backend import resolve_use_gpu
from src.legacy.coupled import (
    classify_tactics as _classify_tactics,
    solve_coupled,
)
from src.legacy.hazard import HazardBelief
from src.engine.landed import landed_unit_cost
from src.legacy.bayesian_v2 import robust_location
from src.legacy.solver import bayes_update


def _get_xp(use_gpu: bool):
    """Return the array backend that will actually run, not the one requested."""
    if use_gpu:
        try:
            import cupy as cp  # noqa: F401
            return cp, "GPU (CuPy/ROCm)"
        except Exception:
            return np, "CPU (NumPy Vectorized; GPU requested but CuPy unavailable)"
    return np, "CPU (NumPy Vectorized)"


class MultiSupplierSolver:
    """Backward-compatible wrapper around the coupled expectimax."""

    def __init__(
        self,
        n_opp_samples: int = 50_000,
        n_open: int = 40,
        n_sched: int = 12,
        use_gpu: bool | None = None,
        walk_away_ceiling: float | None = None,
        rounds: int | None = None,
        risk_tolerance: float | None = None,
    ):
        self.n_opp_samples = int(n_opp_samples)
        self.n_open = int(n_open)
        self.n_sched = int(n_sched)
        if walk_away_ceiling is None:
            raise ValueError("walk_away_ceiling is required")
        self.use_gpu = resolve_use_gpu(use_gpu)
        self.walk_away_ceiling = float(walk_away_ceiling)
        self.rounds = int(model.ROUNDS if rounds is None else rounds)
        self.risk_tolerance = risk_tolerance
        _, self._backend_name = _get_xp(self.use_gpu)

    def compute_dynamic_batna(
        self,
        current_offers: np.ndarray,
        deal_probs: np.ndarray | None = None,
        risk_lambda: float | None = None,
    ) -> np.ndarray:
        """Ceiling each supplier faces: the walk-away, or the best rival quote.

        ``deal_probs`` and ``risk_lambda`` are accepted so older callers still
        run. They do not invent a risk-adjusted price. The rival quote is the
        recallable outside option.
        """
        del deal_probs, risk_lambda
        offers = np.asarray(current_offers, dtype=np.float64)
        k = int(offers.shape[0])
        ceilings = np.empty(k, dtype=np.float64)
        for i in range(k):
            others = [float(offers[j]) for j in range(k) if j != i]
            rival = min(others) if others else float(self.walk_away_ceiling)
            ceilings[i] = min(float(self.walk_away_ceiling), rival)
        return ceilings

    def apply_cross_anchoring_update(
        self,
        prior_means: np.ndarray,
        prior_stds: np.ndarray,
        supplier_offers: np.ndarray,
        best_lead_price: float,
        eta: float | None = None,
        gamma: float | None = None,
    ):
        """Update each reserve from that supplier's own quote.

        The lead price is not a likelihood. ``eta`` and ``gamma`` are ignored.
        Competition changes the outside option in ``solve_coupled``, not this posterior.
        """
        del best_lead_price, eta, gamma
        post_means = []
        post_stds = []
        for i in range(len(supplier_offers)):
            mean, std = bayes_update(
                float(prior_means[i]),
                float(prior_stds[i]),
                float(supplier_offers[i]),
            )
            post_means.append(mean)
            post_stds.append(std)
        return np.asarray(post_means, dtype=np.float64), np.asarray(post_stds, dtype=np.float64)

    def classify_tactics(self, supplier_offers: np.ndarray, post_means: np.ndarray | None = None) -> List[str]:
        del post_means
        return _classify_tactics(np.asarray(supplier_offers, dtype=np.float64), self.walk_away_ceiling)

    def solve_reverse_auction_tensor(
        self,
        supplier_offers: np.ndarray,
        prior_means: np.ndarray,
        prior_stds: np.ndarray,
        rng: Optional[np.random.Generator] = None,
        kappas: np.ndarray | None = None,
        beliefs: list[HazardBelief] | None = None,
        round_index: int = 1,
        credible_alpha: float | None = None,
        reserves: list | None = None,
    ) -> Dict[str, Any]:
        """Coupled offer vector. ``total_rollouts`` is the number of scenario-offer cells scored."""
        offers = np.asarray(supplier_offers, dtype=np.float64)
        result = solve_coupled(
            offers,
            np.asarray(prior_means, dtype=np.float64),
            np.asarray(prior_stds, dtype=np.float64),
            ceiling=float(self.walk_away_ceiling),
            kappas=kappas,
            n_samples=self.n_opp_samples,
            n_grid=self.n_open,
            risk_tolerance=self.risk_tolerance,
            beliefs=beliefs,
            rng=rng,
            round_index=round_index,
            credible_alpha=credible_alpha,
            reserves=reserves,
        )
        k = int(offers.shape[0])
        flat = [0.0] * self.rounds
        return {
            "total_rollouts": int(result["total_rollouts"]),
            "elapsed_seconds": round(float(result["elapsed_seconds"]), 4),
            "backend": self._backend_name,
            "k_suppliers": k,
            "best_offers": result["offers"],
            "best_sched_curves": [list(flat) for _ in range(k)],
            "schedule_note": "Receding horizon: the decision is the next offer, not a five-round script.",
            "p_break_array": result["hazards"],
            "hazard_upper": result["hazard_upper"],
            "exp_margin_array": result["expected_surplus_by_supplier"],
            "tactics": result["tactics"],
            "effective_ceilings": result["effective_ceilings"],
            "circuit_tripped": bool(result["circuit_tripped"]),
            "blocked": result["blocked"],
            "push_order": result["push_order"],
            "push_first": result["push_first"],
            "weitzman_index": result["weitzman_index"],
            "stop": result["stop"],
            "stop_reason": result["stop_reason"],
            "push_limits": result["push_limits"],
            "mc": result["mc"],
            "risk_tolerance": result["risk_tolerance"],
            "hazard_provenance": result["hazard_provenance"],
            "prior_driven": result["prior_driven"],
            "p_accept": result["p_accept"],
            "counter_relation": result.get("counter_relation"),
            "push_offers": result.get("push_offers"),
            "push_half": result.get("push_half"),
            "push_double": result.get("push_double"),
            "heuristic": result.get("heuristic"),
        }


class MultiAuctionSession:
    """One concurrent negotiation. Quotes arrive round by round."""

    def __init__(
        self,
        suppliers: Optional[List[Dict[str, Any]]] = None,
        target_price: float | None = None,
        walk_away_ceiling: float | None = None,
        moq: int | None = None,
        n_samples: int = 20000,
        n_open: int = 20,
        n_sched: int = 6,
        use_gpu: bool | None = None,
        risk_tolerance: float | None = None,
        seed: int = DEFAULT_SOLVER_SEED,
        base_currency: str = "CNY",
        prior_mean: float | None = None,
        prior_std: float | None = None,
        expected_markup: float | None = None,
        frozen_reserves: list[float] | None = None,
        hazard_intercept: float | None = None,
        hazard_tau: float | None = None,
        hazard_quote_gap: float | None = None,
        hazard_reserve_gap: float | None = None,
        bayes_obs_noise: float | None = None,
        hazard_credible_alpha: float | None = None,
    ):
        if suppliers is None or target_price is None or walk_away_ceiling is None:
            raise ValueError(
                "suppliers, target_price, and walk_away_ceiling are required. "
                "The labelled demo path passes them explicitly."
            )
        self.suppliers = [dict(item) for item in suppliers]
        self.k = len(self.suppliers)
        self.target_price = float(target_price)
        self.walk_away_ceiling = float(walk_away_ceiling)
        self.moq = None if moq is None else int(moq)
        self.n_samples = int(n_samples)
        self.use_gpu = resolve_use_gpu(use_gpu)
        self.base_currency = base_currency
        self.risk_tolerance = risk_tolerance
        self.seed = int(seed)
        self._rng = np.random.default_rng(self.seed)
        ceiling_norm = self.walk_away_ceiling / self.target_price
        self.solver = MultiSupplierSolver(
            n_opp_samples=n_samples,
            n_open=n_open,
            n_sched=n_sched,
            use_gpu=use_gpu,
            walk_away_ceiling=ceiling_norm,
            risk_tolerance=risk_tolerance,
        )
        self.user_prior = prior_mean is not None
        self.expected_markup = None if expected_markup is None else float(expected_markup)
        self.freeze_reserve = frozen_reserves is not None
        if prior_std is not None:
            width = float(prior_std)
        elif bayes_obs_noise is not None:
            width = float(bayes_obs_noise)
        else:
            width = float(BAYES_OBS_NOISE)
        self.obs_noise = float(BAYES_OBS_NOISE if bayes_obs_noise is None else bayes_obs_noise)
        self.hazard_credible_alpha = hazard_credible_alpha
        self._hazard_intercept = hazard_intercept
        self._hazard_tau = hazard_tau
        self._hazard_quote_gap = hazard_quote_gap
        self._hazard_reserve_gap = hazard_reserve_gap
        if frozen_reserves is not None:
            if len(frozen_reserves) != self.k:
                raise ValueError("frozen reserves must name one reserve per supplier")
            self.prior_means = np.asarray(frozen_reserves, dtype=np.float64)
            self.prior_stds = np.full(self.k, width, dtype=np.float64)
            self.reserve_source = "simulated"
        elif self.user_prior:
            self.prior_means = np.full(self.k, float(prior_mean), dtype=np.float64)
            self.prior_stds = np.full(self.k, width, dtype=np.float64)
            self.reserve_source = "user"
        elif self.expected_markup is not None:
            self.prior_means = np.full(self.k, np.nan, dtype=np.float64)
            self.prior_stds = np.full(self.k, width, dtype=np.float64)
            self.reserve_source = "markup"
        else:
            self.prior_means = np.full(self.k, np.nan, dtype=np.float64)
            self.prior_stds = np.full(self.k, width, dtype=np.float64)
            self.reserve_source = "ask_only"
        self.reserve_note = ""
        self._observed: List[List[float]] = [[] for _ in range(self.k)]
        self.beliefs = [
            HazardBelief(
                intercept=self._hazard_intercept,
                tau=self._hazard_tau,
                quote_gap_coef=self._hazard_quote_gap,
                reserve_gap_coef=self._hazard_reserve_gap,
            )
            for _ in range(self.k)
        ]
        self.inactive: set[int] = set()
        self.current_round = 0
        self.trace: List[Dict[str, Any]] = []
        self.landed_rows: List[Dict[str, Any]] = []
        self.total_rollouts_accum = 0
        self.total_compute_seconds = 0.0
        self._last_offers_norm: List[float] = []

    def record_outcomes(self, outcomes: List[Optional[str]]) -> None:
        """Update hazard beliefs with what already happened. Silence is not a walk-away."""
        if self.current_round <= 0 or not self.trace:
            return
        previous = self.trace[-1]
        quotes = previous["quotes"]
        offers = previous["counter_offers_cny"]
        for i, outcome in enumerate(outcomes):
            if outcome not in ("continue", "walk_away"):
                continue
            if i >= self.k:
                break
            if outcome == "walk_away":
                self.inactive.add(i)
            offer = offers[i] if i < len(offers) else None
            if offer is None:
                continue
            reserve = float(self.prior_means[i]) * self.target_price
            self.beliefs[i].update(
                walked=outcome == "walk_away",
                quote=float(quotes[i]),
                offer=float(offer),
                reserve=reserve,
                round_index=float(self.current_round),
            )

    def _components(self, quotes: List[float]) -> tuple[np.ndarray, np.ndarray, List[dict], List[bool], str, Optional[str]]:
        quote_norm = []
        rows = []
        comparable = []
        for i, quote in enumerate(quotes):
            spec = self.suppliers[i]
            row = landed_unit_cost(
                unit_price=float(quote),
                freight=spec.get("freight"),
                duty_rate=spec.get("duty_rate"),
                fees=spec.get("fees"),
                fx_to_base=spec.get("fx_to_base"),
                currency=spec.get("currency"),
                base_currency=self.base_currency,
            )
            rows.append(row)
            if row.get("unit_price_base") is None:
                comparable.append(False)
                quote_norm.append(float(quote) / self.target_price)
                continue
            comparable.append(True)
            quote_norm.append(float(row["unit_price_base"]) / self.target_price)
        active = [
            i for i in range(self.k)
            if comparable[i] and i not in self.inactive
        ]
        missing_landed = any(rows[i].get("status") != "complete" for i in active)
        kappas = []
        for i in range(self.k):
            if i not in active or missing_landed:
                # A missing adder is not a measured zero. If any comparable
                # supplier lacks landed data, every supplier is scored on
                # the unit price so a real adder is not mixed with a blank.
                kappas.append(0.0)
                continue
            adder = rows[i].get("adder")
            if rows[i].get("status") == "complete" and adder is not None:
                kappas.append(float(adder) / self.target_price)
            else:
                kappas.append(0.0)
        if not active or missing_landed:
            basis = "unit_price"
        else:
            basis = "landed_cost"
        note = None
        if missing_landed:
            note = (
                "Landed-cost data is missing for at least one comparable supplier. "
                "The solve uses unit prices for every supplier. Missing components "
                "are reported as no data and are not treated as a zero adder."
            )
        return (
            np.asarray(quote_norm, dtype=np.float64),
            np.asarray(kappas, dtype=np.float64),
            rows,
            comparable,
            basis,
            note,
        )

    def _sensitivity_counters(self, offers_norm, kappas, identified, quotes):
        """Counters if the reserve prior or the markup is halved and doubled.

        The analytic mesh chooses the offer. The sample count does not.
        """
        blank = [None] * self.k
        if not identified:
            return list(blank), list(blank)
        tiny = float(np.finfo(np.float64).tiny)
        offers = [float(offers_norm[i]) for i in identified]
        if self.freeze_reserve:
            half = [max(float(self.prior_means[i]) * float(ONE_HALF), tiny) for i in identified]
            double = [max(float(self.prior_means[i]) / float(ONE_HALF), tiny) for i in identified]
        elif self.expected_markup is not None:
            markup = float(self.expected_markup)
            half = [max(quote - markup * float(ONE_HALF), tiny) for quote in offers]
            double = [max(quote - markup / float(ONE_HALF), tiny) for quote in offers]
        elif all(np.isfinite(self.prior_means[i]) for i in identified):
            half = [max(float(self.prior_means[i]) * float(ONE_HALF), tiny) for i in identified]
            double = [max(float(self.prior_means[i]) / float(ONE_HALF), tiny) for i in identified]
        else:
            return list(blank), list(blank)
        stds = np.asarray([float(self.prior_stds[i]) for i in identified], dtype=np.float64)
        belief_rows = [self.beliefs[i] for i in identified]
        kappa_rows = np.asarray([float(kappas[i]) for i in identified], dtype=np.float64)

        def priced(means):
            result = self.solver.solve_reverse_auction_tensor(
                supplier_offers=np.asarray(offers, dtype=np.float64),
                prior_means=np.asarray(means, dtype=np.float64),
                prior_stds=stds,
                rng=np.random.default_rng(self.seed),
                kappas=kappa_rows,
                beliefs=belief_rows,
                round_index=self.current_round,
                credible_alpha=self.hazard_credible_alpha,
            )
            out = [None] * self.k
            for local, supplier_index in enumerate(identified):
                if result["blocked"][local]:
                    continue
                relation = result.get("counter_relation") or []
                if local < len(relation) and relation[local] == "withheld":
                    continue
                raw = float(result["best_offers"][local]) * self.target_price
                own = float(quotes[supplier_index])
                out[supplier_index] = min(raw, float(self.walk_away_ceiling), own)
            return out

        return priced(half), priced(double)

    def step_round(self, quotes: List[float]) -> Dict[str, Any]:
        """Take this round's quotes and return counters, hazards, and the push order."""
        if len(quotes) != self.k:
            raise ValueError(f"Expected {self.k} quotes for {self.k} suppliers, got {len(quotes)}")
        self.current_round += 1
        offers_norm, kappas, rows, comparable, basis, kappa_note = self._components(quotes)
        self.landed_rows = rows
        active = [
            i for i in range(self.k)
            if comparable[i] and i not in self.inactive
        ]
        for i in active:
            observed = float(offers_norm[i])
            self._observed[i].append(observed)
            if self.freeze_reserve:
                continue
            if self.user_prior and self.expected_markup is not None:
                mean, std = bayes_update(
                    float(self.prior_means[i]),
                    float(self.prior_stds[i]),
                    observed,
                    estimated_markup=float(self.expected_markup),
                )
                self.prior_means[i] = mean
                self.prior_stds[i] = max(std, float(np.finfo(np.float64).tiny))
            elif self.user_prior:
                continue
            elif self.expected_markup is not None:
                centred = [quote - float(self.expected_markup) for quote in self._observed[i]]
                mean, std = robust_location(centred, noise_floor=self.obs_noise)
                self.prior_means[i] = mean
                self.prior_stds[i] = max(std, float(np.finfo(np.float64).tiny))
            else:
                self.prior_means[i] = np.nan
        if (
            not self.freeze_reserve
            and not self.user_prior
            and self.expected_markup is None
        ):
            from src.engine.reserve import ReserveState

            tiny = float(np.finfo(np.float64).tiny)
            for i in active:
                state = ReserveState.policy()
                observed = self._observed[i]
                reference = float(observed[0]) if observed and float(observed[0]) > 0.0 else float(offers_norm[i])
                state = state.with_reference(reference)
                if len(observed) >= 2 and reference > 0.0:
                    for later in observed[1:]:
                        state = state.tighten(float(later) / reference)
                self.prior_means[i] = reference * float(state.mean())
                self.prior_stds[i] = max(reference * float(state.std()), tiny)
            self.reserve_source = "labelled_policy"
            self.reserve_note = (
                "Each supplier's reserve is their own ask times D. "
                "D is a labelled Beta prior until that supplier has responded. "
                "Another supplier's ask is not used, and a markup is not applied."
            )
        elif self.freeze_reserve:
            self.reserve_note = (
                "Reserve is the scripted self-play reserve. "
                "It is labelled simulated, not a fitted margin."
            )
        elif self.expected_markup is not None:
            self.reserve_note = (
                "A typed markup is not applied. The reserve is each supplier's "
                "own ask times a labelled Beta prior."
            )
        elif self.user_prior:
            self.reserve_note = (
                "A typed prior is not the reserve. Each supplier's reserve is "
                "their own ask times a labelled Beta prior."
            )
        if not active:
            if self.inactive and len(self.inactive) >= self.k:
                raise ValueError("Every supplier has walked away, so there is no rival left to compare.")
            raise ValueError("No supplier quote could be compared without inventing an FX rate.")
        identified = [i for i in active if np.isfinite(self.prior_means[i])]
        # A quote that cannot be converted is left out of the rival set.
        # Using the unconverted number would invent a comparison.
        res_active = None
        if identified:
            from src.engine.reserve import ReserveState

            states = []
            for supplier_index in identified:
                state = ReserveState.policy()
                observed = self._observed[supplier_index]
                reference = float(observed[0]) if observed and float(observed[0]) > 0.0 else float(offers_norm[supplier_index])
                state = state.with_reference(reference)
                if len(observed) >= 2 and reference > 0.0:
                    for later in observed[1:]:
                        state = state.tighten(float(later) / reference)
                states.append(state)
            res_active = self.solver.solve_reverse_auction_tensor(
                supplier_offers=offers_norm[identified],
                prior_means=self.prior_means[identified],
                prior_stds=self.prior_stds[identified],
                rng=self._rng,
                kappas=kappas[identified],
                beliefs=[self.beliefs[i] for i in identified],
                round_index=self.current_round,
                credible_alpha=self.hazard_credible_alpha,
                reserves=states,
            )

        def _expand(values, fill=None):
            out = [fill] * self.k
            for local, supplier_index in enumerate(identified):
                out[supplier_index] = values[local]
            return out

        if res_active is None:
            best_offers = [None] * self.k
            solver_blocked = [False] * self.k
            relations = ["withheld"] * self.k
            rollouts = 0
            elapsed = 0.0
            push_order: list[int] = []
            tactics_local: list[str] = []
            ceilings_local: list[float | None] = []
            hazards_local: list[float | None] = []
            upper_local: list[float | None] = []
        else:
            best_offers = _expand(res_active["best_offers"])
            solver_blocked = _expand(res_active["blocked"], fill=False)
            relations = _expand(res_active.get("counter_relation"), fill="withheld")
            rollouts = int(res_active["total_rollouts"])
            elapsed = float(res_active["elapsed_seconds"])
            push_order = list(res_active["push_order"])
            tactics_local = list(res_active["tactics"])
            ceilings_local = list(res_active["effective_ceilings"])
            hazards_local = list(res_active["p_break_array"])
            upper_local = list(res_active["hazard_upper"])
            self.total_rollouts_accum += rollouts
            self.total_compute_seconds += elapsed
        unit_bases = [
            None if rows[i].get("unit_price_base") is None else float(rows[i]["unit_price_base"])
            for i in range(self.k)
        ]
        active_units = [
            unit_bases[i] if unit_bases[i] is not None else float(quotes[i])
            for i in active
        ]
        all_above = bool(active_units) and all(
            float(unit) > float(self.walk_away_ceiling) for unit in active_units
        )
        counters = []
        block_reasons: List[Optional[str]] = []
        blocked_out: List[bool] = []
        # "upper" means the card should show hazard_upper. "mean" means the
        # card should show p_break_array. The desk reads this field; it does
        # not infer the choice from the wording of block_reasons.
        hazard_figure: List[str] = []
        above_reason = "every quote is above the walk-away ceiling"
        hazard_reason = (
            "not actionable: the upper bound on walk-away hazard for every "
            "tested offer, including the least aggressive, exceeds the risk-tolerance cap"
        )
        for i in range(self.k):
            if i not in active:
                counters.append(None)
                blocked_out.append(False)
                hazard_figure.append("mean")
                if i in self.inactive:
                    block_reasons.append("walked away; removed from the rival set")
                elif not comparable[i]:
                    block_reasons.append("no data: this quote has no FX rate, so it was not compared")
                else:
                    block_reasons.append("no data")
                continue
            if i not in identified:
                counters.append(None)
                blocked_out.append(True)
                hazard_figure.append("mean")
                block_reasons.append(
                    getattr(self, "reserve_note", None)
                    or (
                        "Insufficient data: one ask and no closed deals on this item. "
                        "No markup was assumed. A markup is an optional override."
                    )
                )
                continue
            if all_above:
                counters.append(None)
                blocked_out.append(True)
                hazard_figure.append("mean")
                block_reasons.append(above_reason)
                continue
            if solver_blocked[i]:
                counters.append(None)
                blocked_out.append(True)
                hazard_figure.append("upper")
                block_reasons.append(hazard_reason)
                continue
            raw = float(best_offers[i]) * self.target_price
            own = unit_bases[i]
            own_cap = float(own) if own is not None else float(quotes[i])
            cap = min(self.walk_away_ceiling, own_cap)
            counter = min(raw, cap)
            counters.append(counter)
            blocked_out.append(False)
            hazard_figure.append("mean")
            if relations[i] != "at_ask" and abs(counter - own_cap) <= self.target_price * float(SEARCH_STEP):
                relations[i] = "at_ask"
            block_reasons.append(
                "at the ask: this supplier is the cheapest confirmed quote, so the default is their ask"
                if relations[i] == "at_ask"
                else (
                    "matches the best other confirmed landed quote"
                    if relations[i] == "match_rival"
                    else None
                )
            )
        actionable = [counter / self.target_price for counter in counters if counter is not None]
        self._last_offers_norm = actionable
        mean_norm = float(np.mean(actionable)) if actionable else None
        lead_pool = list(active)
        if basis == "landed_cost":
            lead = min(lead_pool, key=lambda i: float(rows[i]["landed_cost"]))
        else:
            lead = min(lead_pool, key=lambda i: float(rows[i]["unit_price_base"]))
        if res_active is None:
            names = []
            tactics = ["ASK_ONLY"] * self.k
            ceilings = [None] * self.k
            hazard_values = [None] * self.k
            upper_values = [None] * self.k
            push_first_name = None
            weitzman = [None] * self.k
            stop = True
            stop_reason = "ask only: set a reserve prior or a markup before a counter is issued"
            mc = None
            push_limits = [None] * self.k
            prior_driven = True
            hazard_provenance = None
            risk_value = self.risk_tolerance
        else:
            names = [self.suppliers[identified[i]]["name"] for i in push_order]
            tactics = _expand(tactics_local, fill="NO_DATA")
            ceilings = _expand(ceilings_local)
            hazard_values = _expand(hazards_local)
            upper_values = _expand(upper_local)
            push_first_name = self.suppliers[identified[int(res_active["push_first"])]]["name"]
            weitzman = _expand(res_active["weitzman_index"])
            stop = res_active["stop"]
            stop_reason = res_active["stop_reason"]
            mc = res_active["mc"]
            push_limits = _expand(res_active["push_limits"])
            prior_driven = res_active["prior_driven"]
            hazard_provenance = res_active["hazard_provenance"]
            risk_value = res_active["risk_tolerance"]
        def _priced_push(key: str) -> list:
            blank = [None] * self.k
            if res_active is None or not res_active.get(key):
                return blank
            out = [None] * self.k
            for local, supplier_index in enumerate(identified):
                raw = float(res_active[key][local]) * self.target_price
                own = unit_bases[supplier_index]
                own_cap = float(own) if own is not None else float(quotes[supplier_index])
                out[supplier_index] = min(raw, float(self.walk_away_ceiling), own_cap)
            return out

        sensitivity_half = _priced_push("push_half")
        sensitivity_double = _priced_push("push_double")
        push_offers_cny = _priced_push("push_offers")
        round_info = {
            "round": self.current_round,
            "quotes": [float(q) for q in quotes],
            "best_quote": float(min(float(rows[i]["unit_price_base"]) for i in lead_pool)),
            "lead_supplier": self.suppliers[lead]["name"],
            "tactics": tactics,
            "effective_ceilings_cny": [
                None if c is None else float(c) * self.target_price for c in ceilings
            ],
            "p_break_array": [None if p is None else float(p) for p in hazard_values],
            "hazard_upper": [None if p is None else float(p) for p in upper_values],
            "hazard_figure": hazard_figure,
            "counter_offers_cny": counters,
            "counter_relation": relations,
            "reserve_source": self.reserve_source,
            "reserve_note": getattr(self, "reserve_note", ""),
            "markup_used": None,
            "push_offers_cny": push_offers_cny,
            "sensitivity_half": sensitivity_half,
            "sensitivity_double": sensitivity_double,
            "counter_offers_mean_norm": mean_norm,
            "rollouts": rollouts,
            "elapsed_seconds": elapsed,
            "unit_prices": unit_bases,
            "landed_costs": [None if rows[i].get("landed_cost") is None else float(rows[i]["landed_cost"]) for i in range(self.k)],
            "landed_status": [rows[i].get("status") for i in range(self.k)],
            "landed_missing": [list(rows[i].get("missing") or []) for i in range(self.k)],
            "ranking_basis": basis,
            "kappa_basis": basis,
            "kappa_note": kappa_note,
            "kappas_norm": [
                None if i not in active else float(kappas[i]) for i in range(self.k)
            ],
            "all_quotes_above_ceiling": all_above,
            "push_order": names,
            "push_first": push_first_name,
            "weitzman_index": weitzman,
            "stop": stop,
            "stop_reason": stop_reason,
            "mc": mc,
            "blocked": blocked_out,
            "block_reasons": block_reasons,
            "active_suppliers": [self.suppliers[i]["name"] for i in active],
            "push_limits": push_limits,
            "prior_driven": prior_driven,
            "hazard_provenance": hazard_provenance,
            "risk_tolerance": risk_value,
            "breakdown_definition": "walk-away hazard of the next offer",
            "comparable": comparable,
        }
        self.trace.append(round_info)
        return round_info

    def get_summary(self) -> Dict[str, Any]:
        """Settle on the last round's best comparable quote and label every baseline."""
        if not self.trace:
            raise ValueError("No rounds have been executed yet.")
        last_round = self.trace[-1]
        basis = last_round.get("ranking_basis") or "unit_price"
        unit_bases = last_round.get("unit_prices") or []
        if unit_bases and any(item is not None for item in unit_bases):
            winning_idx = int(np.argmin([item if item is not None else np.inf for item in unit_bases]))
            final_quote = float(unit_bases[winning_idx])
        else:
            winning_idx = int(np.argmin(last_round["quotes"]))
            final_quote = float(last_round["quotes"][winning_idx])
        if basis == "landed_cost":
            landed = last_round["landed_costs"]
            winning_idx = int(np.argmin([item if item is not None else np.inf for item in landed]))
            final_quote = float(landed[winning_idx])
        winning_supplier = self.suppliers[winning_idx]["name"]
        winner_above_ceiling = bool(final_quote > float(self.walk_away_ceiling))
        first_round = self.trace[0]
        first_best_quote = float(first_round["best_quote"])
        savings_vs_first_quote_pct = (
            (first_best_quote - final_quote) / first_best_quote if first_best_quote else 0.0
        )
        quantity = self.moq
        savings_vs_first_quote_cny = None if quantity is None else (first_best_quote - final_quote) * quantity
        savings_vs_target_pct = (
            (self.target_price - final_quote) / self.target_price if self.target_price else 0.0
        )
        savings_vs_target_cny = None if quantity is None else (self.target_price - final_quote) * quantity
        savings_vs_ceiling_pct = (
            (self.walk_away_ceiling - final_quote) / self.walk_away_ceiling
            if self.walk_away_ceiling
            else 0.0
        )
        savings_vs_ceiling_cny = None if quantity is None else (self.walk_away_ceiling - final_quote) * quantity
        return {
            "success": True,
            "k_suppliers": self.k,
            "suppliers": self.suppliers,
            "backend": getattr(self.solver, "_backend_name", "CPU (NumPy Vectorized)"),
            "product_target": self.target_price,
            "walk_away_ceiling": self.walk_away_ceiling,
            "moq": self.moq,
            "winning_supplier": winning_supplier,
            "winning_price": final_quote,
            "winning_unit_price": float(last_round["quotes"][winning_idx]),
            "winning_landed_cost": last_round["landed_costs"][winning_idx],
            "ranking_basis": basis,
            "kappa_note": last_round.get("kappa_note"),
            "all_quotes_above_ceiling": bool(last_round.get("all_quotes_above_ceiling")),
            "winner_above_ceiling": winner_above_ceiling,
            "total_savings_pct": savings_vs_first_quote_pct,
            "total_savings_cny": savings_vs_first_quote_cny,
            "total_savings_baseline": "first_round_best_quote",
            "total_savings_baseline_value": first_best_quote,
            "first_round_best_quote": first_best_quote,
            "savings_vs_first_quote_pct": savings_vs_first_quote_pct,
            "savings_vs_first_quote_cny": savings_vs_first_quote_cny,
            "savings_vs_target_pct": savings_vs_target_pct,
            "savings_vs_target_cny": savings_vs_target_cny,
            "savings_vs_ceiling_pct": savings_vs_ceiling_pct,
            "savings_vs_ceiling_cny": savings_vs_ceiling_cny,
            "total_rollouts": self.total_rollouts_accum,
            "total_rollouts_accum": self.total_rollouts_accum,
            "total_compute_seconds": round(self.total_compute_seconds, 4),
            "trace": self.trace,
            "counter_offers_mean_norm": self.trace[-1]["counter_offers_mean_norm"],
            "mc": last_round.get("mc"),
            "push_order": last_round.get("push_order"),
            "push_first": last_round.get("push_first"),
            "stop": last_round.get("stop"),
            "stop_reason": last_round.get("stop_reason"),
            "prior_driven": last_round.get("prior_driven"),
            "reserve_source": last_round.get("reserve_source"),
            "reserve_note": last_round.get("reserve_note"),
            "markup_used": last_round.get("markup_used"),
            "sensitivity_half": last_round.get("sensitivity_half"),
            "sensitivity_double": last_round.get("sensitivity_double"),
            "hazard_provenance": last_round.get("hazard_provenance"),
            "risk_tolerance": last_round.get("risk_tolerance"),
            "breakdown_definition": "walk-away hazard of the next offer",
        }


def _self_play_agents(n_rounds: int, specs):
    from ..benchmark.adversaries import (
        BluffingAdversary,
        ConcederAdversary,
        HardBargainerAdversary,
        TitForTatAdversary,
    )

    kinds = {
        "conceder": ConcederAdversary,
        "tit_for_tat": TitForTatAdversary,
        "hard": HardBargainerAdversary,
        "bluff": BluffingAdversary,
    }
    agents = []
    for spec in specs:
        cls = kinds[spec["kind"]]
        agents.append(
            cls(
                max_rounds=n_rounds,
                init_price=spec["init_price"],
                reserve_price=spec["reserve_price"],
            )
        )
    return agents


def simulate_reverse_auction(
    target_price: float | None = None,
    walk_away_ceiling: float | None = None,
    moq: int | None = None,
    n_samples: int = 20000,
    use_gpu: bool | None = None,
    suppliers: Optional[List[Dict[str, Any]]] = None,
    rounds_quotes: Optional[List[List[float]]] = None,
    n_rounds: int = 3,
    seed: int = 42,
    risk_tolerance: float | None = None,
    base_currency: str = "CNY",
    round_outcomes: Optional[List[List[Optional[str]]]] = None,
    demo: bool = False,
    self_play: Optional[Dict[str, Any]] = None,
    prior_mean: float | None = None,
    prior_std: float | None = None,
    expected_markup: float | None = None,
    hazard_intercept: float | None = None,
    hazard_tau: float | None = None,
    hazard_quote_gap: float | None = None,
    hazard_reserve_gap: float | None = None,
    bayes_obs_noise: float | None = None,
    hazard_credible_alpha: float | None = None,
) -> Dict[str, Any]:
    """Run a concurrent round.

    ``user_supplied`` requires the caller to pass the target, the ceiling,
    and the quotes. Self-play runs only with ``demo=True`` and a labelled
    ``self_play`` bundle, and that result stays ``simulated``.
    """
    if target_price is None or walk_away_ceiling is None:
        raise ValueError("target_price and walk_away_ceiling are required")
    user_quotes = rounds_quotes is not None
    suppliers_from_bundle = suppliers is None and demo and self_play is not None
    if suppliers is None:
        if not demo or not self_play:
            raise ValueError(
                "Name the suppliers, or set demo=True and pass the labelled self-play bundle."
            )
        suppliers = [dict(item) for item in self_play["names"]]
    elif not user_quotes:
        embedded = [item.get("quote") for item in suppliers]
        if embedded and all(item is not None for item in embedded):
            rounds_quotes = [[float(item) for item in embedded]]
            user_quotes = True
    # Frozen reserves describe the bundle's own simulated suppliers, one per
    # supplier. When the caller names their own supplier list, the bundle's
    # reserves do not describe those suppliers and must not be applied.
    frozen = None
    if suppliers_from_bundle and prior_mean is None and expected_markup is None:
        frozen = [float(item["reserve_price"]) for item in self_play["specs"]]
    session = MultiAuctionSession(
        suppliers=suppliers,
        target_price=target_price,
        walk_away_ceiling=walk_away_ceiling,
        moq=moq,
        n_samples=n_samples,
        use_gpu=use_gpu,
        risk_tolerance=risk_tolerance,
        seed=seed,
        base_currency=base_currency,
        prior_mean=prior_mean,
        prior_std=prior_std,
        expected_markup=expected_markup,
        frozen_reserves=frozen,
        hazard_intercept=hazard_intercept,
        hazard_tau=hazard_tau,
        hazard_quote_gap=hazard_quote_gap,
        hazard_reserve_gap=hazard_reserve_gap,
        bayes_obs_noise=bayes_obs_noise,
        hazard_credible_alpha=hazard_credible_alpha,
    )
    if rounds_quotes is not None:
        for index, quotes in enumerate(rounds_quotes):
            if round_outcomes is not None and index < len(round_outcomes) and session.current_round > 0:
                session.record_outcomes(round_outcomes[index])
            session.step_round(quotes)
    else:
        if not demo or not self_play:
            raise ValueError(
                "Quotes are required. Self-play is only the labelled demo path."
            )
        agents = _self_play_agents(n_rounds, self_play["specs"])
        opening = float(self_play["opening"])
        rng = np.random.default_rng(seed)
        buyer_histories: List[List[float]] = [[] for _ in range(session.k)]
        supp_histories: List[List[float]] = [[] for _ in range(session.k)]
        round_info = None
        for r in range(1, n_rounds + 1):
            cur_round_quotes = []
            for i in range(session.k):
                adv = agents[i % len(agents)]
                b_offer = round_info["counter_offers_mean_norm"] if r > 1 else opening
                if b_offer is None:
                    b_offer = opening
                buyer_histories[i].append(b_offer)
                offer_norm = adv.respond(r, b_offer, buyer_histories[i], supp_histories[i], rng)
                supp_histories[i].append(offer_norm)
                cur_round_quotes.append(round(float(offer_norm * session.target_price), 2))
            round_info = session.step_round(cur_round_quotes)
    summary = session.get_summary()
    simulated = not user_quotes
    summary["data_class"] = "simulated" if simulated else "user_supplied"
    summary["simulated"] = simulated
    summary["solver"] = "analytic expected surplus of the one-step accept-or-reject model"
    return summary
