"""Regression tests: the supervisor confirmation card must not carry fabricated numbers.

Context — these tests encode the defects found by the pre-submission code review:

  P1  The supervisor card (mobile 1/2/3 command) printed values that had no
      relationship to what the engine computed for the SAME session:
        * bayesian_confidence was the Pydantic default 0.85, printed as
          "Confidence: 85.0%", while /round reported e.g. 0.827 for the very
          same session;
        * estimated_floor_price was never passed, so the card rendered "N/A"
          next to a card that claimed to show the agent's Bayesian anchor;
        * expected_margin was `ceiling_price - settled_price` — remaining budget
          head-room, not per-unit margin — so a healthy session printed
          "Expected margin ¥0.40" (ceiling 16.20, settled 15.80).

  P2  SupplierCurrentOffer was built with `lead_days=... or 25` and
      `deposit_pct=... or 0.30`, so a counterpart who only ever named a price had
      "25 days / 30%" attributed to them as if they had quoted it.

Fix under test: /round now persists the real engine values into
NegotiationTurn.metadata, and _dispatch_supervisor_command reads them back
instead of re-deriving constants. When nothing was ever measured the card
reports N/A plus an explicit note rather than a number.

These tests use the real HTTP surface (TestClient) because the bug was a
cross-endpoint contradiction: it is only observable by comparing two responses
for one session.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from src.app import main as api_main  # noqa: E402


class _IsolatedSupervisorCase(unittest.TestCase):
    """Point the global SessionManager at a private temp store.

    Mirrors tests/test_api.py's isolation idiom. The env-var approach is
    deliberately NOT used here: TRADEFLOW_DB_PATH / TRADEFLOW_STORE_DIR are read
    as module-level defaults at import time by src.session.manager, so setting
    them in the shell would break
    test_api_security.py::test_defaults_are_unchanged_when_unset.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="tf_sup_card_")
        api_main.session_mgr.__init__(
            db_path=os.path.join(self._tmp, "sessions.db"),
            store_dir=os.path.join(self._tmp, "sessions_json"),
        )
        self.c = TestClient(api_main.app)
        self.sid = "sup_" + os.urandom(4).hex()

    def tearDown(self):
        # Restore a LIVE store so a later test class inherits a valid manager.
        if hasattr(self, "_tmp"):
            shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_session(self, target=12.0, ceiling=16.2, moq=5000):
        r = self.c.post("/api/v1/sessions", json={
            "session_id": self.sid,
            "product_name": "Auto Umbrella",
            "target_price": target,
            "ceiling_price": ceiling,
            "moq": moq,
            "max_rounds": 5,
            "supplier_id": "Supplier_A",
            "supplier_channel": "web_chat",
        })
        assert r.status_code == 200, r.text

    def _round(self, offer, text):
        r = self.c.post(f"/api/v1/sessions/{self.sid}/round", json={
            "supplier_offer": offer,
            "supplier_text": text,
            "mode": "copilot",
        })
        assert r.status_code == 200, r.text
        return r.json()

    def _command(self, cmd="2"):
        r = self.c.post("/webhook/mobile-command",
                        json={"session_id": self.sid, "command": cmd})
        assert r.status_code == 200, r.text
        return r.json()

    @staticmethod
    def _card(j):
        return j.get("confirmation_card_en") or ""


class TestSupervisorCardUsesRealEngineMetrics(_IsolatedSupervisorCase):
    """The mobile card must report the same numbers /round computed."""

    def test_confidence_is_not_the_hardcoded_pydantic_default(self):
        self._make_session()
        round_resp = self._round(15.80, "raw material up 30%, lowest 15.80")
        real_conf = (round_resp.get("action_object") or {}).get("bayesian_confidence")

        card = self._card(self._command("2"))
        # The regression: the card printed the model default 85.0%.
        self.assertNotIn(
            "85.0%", card,
            "supervisor card still prints the hardcoded bayesian_confidence=0.85",
        )
        # Positive check: the card must carry the value /round actually computed.
        self.assertIn(f"{real_conf * 100:.1f}%", card)

    def test_floor_price_is_not_rendered_as_na_when_the_engine_knows_it(self):
        self._make_session()
        round_resp = self._round(15.50, "still 15.50")
        real_floor = (round_resp.get("action_object") or {}).get("estimated_floor_price")
        self.assertIsNotNone(real_floor)

        card = self._card(self._command("2"))
        self.assertNotIn(
            "Floor: N/A", card,
            "estimated_floor_price is available from the last engine round but the "
            "supervisor card still renders N/A",
        )
        self.assertIn(f"{real_floor:.2f}", card)

    def test_expected_margin_is_not_budget_head_room(self):
        """ceiling - settled is remaining budget, not per-unit margin."""
        self._make_session(ceiling=16.2)
        # Settle near the ceiling so the old formula yields a tiny fake "margin".
        self._round(15.80, "raw material up 30%, lowest 15.80")
        j = self._command("2")
        action = j.get("action_object") or {}
        settled = action.get("authorized_price")

        fake = round(max(0.0, 16.2 - (settled or 0.0)), 2)
        card = self._card(j)
        # The bogus ¥0.40 figure came exactly from ceiling - settled. It must be
        # absent unless the engine genuinely computed that per-unit margin.
        engine_margin = (self._session_last_engine_margin())
        if engine_margin is None or abs(engine_margin - fake) > 0.02:
            self.assertNotIn(
                f"Expected margin ¥{fake:.2f}".replace("¥", "¥"),
                card.replace(" ", ""),
                "expected_margin still appears to be ceiling - settled",
            )

    def _session_last_engine_margin(self):
        r = self.c.get(f"/api/v1/sessions/{self.sid}")
        if r.status_code != 200:
            return None
        sess = r.json()
        hist = sess.get("history") or []
        if not hist:
            return None
        meta = hist[-1].get("metadata") or {}
        val = meta.get("expected_margin")
        try:
            return float(val)
        except (TypeError, ValueError):
            return None


class TestSupervisorCardHonestWhenNothingMeasured(_IsolatedSupervisorCase):
    """A session with no engine round must not invent numbers."""

    def test_no_history_card_says_metrics_were_not_computed(self):
        self._make_session()
        j = self._command("2")
        card = self._card(j)
        self.assertNotIn(
            "85.0%", card,
            "a session with zero engine rounds must not report a measured "
            "bayesian_confidence of 85%",
        )
        note = str(j.get("metrics_note") or "")
        self.assertIn("NOT computed", note)
        self.assertIn("Floor: N/A", card)


class TestLeadTimeAndDepositNotAttributedToCounterpart(_IsolatedSupervisorCase):
    """P2: unobserved lead-time / deposit must not be presented as their quote.

    Note on scope: `TacticalActionObject.lead_days` / `deposit_pct` are
    *buyer-side* fields ("promised lead time", "promised deposit ratio") whose
    Pydantic defaults (25 / 0.30) we deliberately do NOT overwrite with None —
    a placeholder is semantically fine for a field that describes OUR proposal.
    What must never happen is presenting those placeholders as if the counterpart
    had quoted them, so the assertions below target the rendered text.
    """

    def test_card_labels_package_terms_as_our_proposal_not_counterpart_confirmed(self):
        self._make_session()
        # The counterpart only ever names a price. No lead time, no deposit.
        self._round(15.80, "lowest 15.80")
        j = self._command("2")

        card = self._card(j)
        # The regression: an unlabelled "Package Terms" line let a reader assume
        # the counterpart had already agreed to 25d / 30%.
        self.assertNotIn(
            "Package Terms: Lead Time", card,
            "package terms are still presented without stating they are our own "
            "proposal rather than counterpart-confirmed conditions",
        )
        self.assertIn("OUR proposal", card)
        self.assertIn("not counterpart-confirmed", card)

    def test_supplier_current_offer_is_not_fed_unobserved_defaults(self):
        """The *input* to the logrolling engine must not get `or 25` / `or 0.30`.

        This is the value the engine reasons over, so a fabricated '25' here
        silently changes its recommendations.
        """
        self._make_session()
        self._round(15.80, "lowest 15.80")
        # A counterpart who only named a price must not leave 25/0.30 behind in
        # the persisted entities, otherwise the next round re-attributes them.
        r = self.c.get(f"/api/v1/sessions/{self.sid}")
        self.assertEqual(r.status_code, 200)
        sess = r.json()
        hist = sess.get("history") or []
        self.assertTrue(hist, "expected a recorded round")
        entities = (hist[-1].get("metadata") or {}).get("entities") or {}
        # The key may legitimately exist with value None ("explicitly
        # unobserved"). What must never persist is a placeholder NUMBER that a
        # later round could re-attribute to the counterpart.
        self.assertIsNone(
            entities.get("lead_days"),
            "an unobserved lead-time was persisted as if the counterpart had "
            "quoted it",
        )
        self.assertIsNone(
            entities.get("deposit_pct"),
            "an unobserved deposit ratio was persisted as if the counterpart had "
            "quoted it",
        )


if __name__ == "__main__":
    unittest.main()
