# Phase 1 pass bars

Written before the phase-1 implementation. A bar is met only by a test or a checked-in artefact named here. These bars do not certify automatic acceptance of prices.

The product this phase ships is prefill plus one click. The agreement gate records what it would have accepted. It does not accept anything. No unconfirmed number may enter a decision.

Dates below use the calendar: 23 Oct 2026 and 30 Oct 2026 are Fridays. The Nebius x NVIDIA deadline is Friday 30 Oct 2026, 17:00 London. This phase is the code that has to be complete before an independent check.

## Quote intake

1. A message that the user has not confirmed is stored with status `review` and with `unit_price` empty. The decision reads only events whose status is `accepted`. The audit line on every decision payload is `unconfirmed numbers used in decisions: 0`.
2. An extractor response is evidence only. The allowed classes are `own_unit_offer`, `rival_price`, `our_price_echo`, `refusal`, `question`, `discount_delta`, `fee`, `deposit`, `tier_table`, `instruction`, and `other`. A numeric price field in that response is discarded. The amount, if one is shown, is read by deterministic code from a span that occurs verbatim in the message.
3. A span whose class is `instruction` is never a shadow accept.
4. The shadow gate would accept only when the rule reader and the span reader agree on the value, the currency equals the deal currency, the unit is per piece, the speaker is the supplier's own offer, the polarity is affirmative, the sentence is not a question, and the amount is inside the labelled plausibility band. Phase 1 still leaves the event in `review`.
5. With no extractor endpoint configured, the shadow gate does not accept, and the prefill is the rule reader's token.
6. The rule reader and the span reader do not call the same number tokenizer. They live in different modules and neither imports the other.
7. The desk shows how many shadow accepts the user later confirmed unchanged, out of 189. 189 is the two-sided Wilson sample size at which zero errors put the 95% lower bound at 0.98. The count is of resolved shadow accepts, not of messages in a corpus. Auto-accept stays off.

## Reserve

1. For supplier i, `reserve = ask × D` with `D` in `[0, 1]`. Each supplier has their own Beta prior. A second supplier's ask does not enter the first supplier's posterior.
2. A later ask, an accepted offer, and a closed-deal price are upper bounds on `D` (`D ≤ price / first ask`). They are not treated as draws from `D`, and they are not lower bounds. The posterior mean does not move upward because a closed deal printed a high ratio.
3. A rejection uses a soft likelihood. Probability remains on values of `D` below the rejected offer.
4. The decision does not use a shared cross-supplier reserve and does not use a lowest-ask rule. A typed markup is not applied.
5. On the uncensored special cases, the uniform closed form and the `Beta(α, 1)` closed form match the bounded scalar optimiser to within `1e-9` times the target.
6. Until a supplier has 30 recorded responses, the card says the Beta prior is labelled policy. The card shows the push-lower counter at half the prior strength and at double the prior strength. Strength is `α + β`. The mean is held fixed in that comparison.

## Walk-away hazard

1. The decision hazard has one gap: how far the offer sits below the supplier's ask. A reserve-gap term is not added. Acceptance is the Beta distribution function. It is not multiplied by one minus the hazard.
2. `data/hazard_ebay_bi.json` carries both fitted outcomes, seller-clustered confidence intervals, a seller-grouped hold-out, and a by-time hold-out. The two outcomes are thread ended with no deal (this includes threads the buyer ended) and seller-ended (the seller declined, auto-declined, or let the offer expire). The decision uses the seller-ended fit. The file records the data URL, the date, and the sample. The fit script runs offline and is not part of the Render service.
3. The card says the hazard is transferred from eBay Best Offer 2012–13, Business & Industrial, and is not a measured Chinese-factory rate. The eBay auto-decline ratio is not used as the prior on `D`.
4. The user's risk cap applies to the incremental hazard `h(offer) − h(ask)`. An offer at the ask has incremental hazard 0.
5. A continue or a walk-away recorded for one supplier updates that supplier's effect only.

## Decision

1. Each supplier, each round, the push-lower offer is the maximum of a smooth one-dimensional objective on a closed interval. The maximiser is a closed form or a bounded scalar optimiser. A mesh step is not an input, and changing a step does not change the offer.
2. The default suggested counter matches the best other confirmed landed quote, and it never exceeds this supplier's ask or the user's ceiling. When this supplier is already the cheapest, that match is their own ask. The split between their ask and the user's target is not used.
3. The model's push-lower counter is shown beside the default and is less than or equal to the default. The card shows that counter at half and at double the prior strength.
4. The objective is the expected landed-cost saving if the supplier accepts, minus the incremental walk hazard priced at the outside option, subject to the incremental-risk cap.
5. A missing target or a missing ceiling is rejected with HTTP 422.
6. The payload states that this is a per-round heuristic: other suppliers' quotes are treated as fixed and recallable, the buyer is not committed to take-it-or-leave-it, and the offer is not an exact multi-round equilibrium.
7. A tighter rival quote can lower a counter and cannot raise one.

## Headline and UI

1. For a real closed deal, the saved figure is the best first confirmed landed unit price minus the agreed landed unit price. A simulated deal is labelled simulated and is not counted as a real closed deal.
2. The same screen shows `unconfirmed numbers used in decisions: 0`.
3. Demo and fixture quotes stay labelled simulated or fixture until a person confirms a live amount.

## What these bars do not claim

- The eBay fit is not a proof that an earlier coefficient was too low or too high. Both outcomes are reported because the sign depends on who ended the thread.
- Seller-grouped and by-time calibration are reported as measured on this fit. A random-row split is not quoted as out-of-seller calibration.
- Zero errors on a small corpus do not certify precision 0.98. That claim waits on 189 resolved shadow accepts with zero disagreements.
- The level of `D` for a factory that has not responded is the labelled prior, not a fitted margin.
