"""Round pipeline. Stages of one negotiation round that are pure decisions.

``src/app/main.py`` still holds the 877-line ``process_negotiation_round``
orchestrator; this package is where its stages are extracted as they are
identified. A stage belongs here when it is a decision with named inputs and a
single output, so it can be asserted without standing up a session.

Currently extracted: ``circuit_breaker`` — the safety gate that decides whether
a round is blocked and escalated.
"""
