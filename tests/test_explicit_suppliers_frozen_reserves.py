"""Regression tests: explicit supplier lists must not inherit the bundle's
frozen reserves.

Before the fix, `simulate_reverse_auction(demo=True, self_play=bundle,
suppliers=[...])` still built `frozen_reserves` from the bundle's 4 specs
while the session had the caller's k suppliers, so the constructor raised
"frozen reserves must name one reserve per supplier". The verify_all jury
suite (pillar 2, input-sensitivity check) hit exactly this path and failed
on master while the pytest suite stayed green, because no test exercised
the combination.

The invariant under test: frozen reserves describe the bundle's own
simulated suppliers only. A caller-named supplier list runs without the
bundle's reserves, and its supplier count is respected.
"""
import unittest

from src.app.demo_data import self_play_bundle
from src.legacy.multi_solver import simulate_reverse_auction


class TestExplicitSuppliersDoNotInheritBundleReserves(unittest.TestCase):
    def test_explicit_suppliers_with_bundle_does_not_raise(self):
        # The exact verify_all pillar-2 second call: fewer explicit suppliers
        # than the bundle has specs must not raise a reserve-count mismatch.
        res = simulate_reverse_auction(
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=2000,
            demo=True,
            self_play=self_play_bundle(),
            suppliers=[
                {"name": "A", "profile": "Agile Conceder"},
                {"name": "B", "profile": "Rational Tit-for-Tat"},
            ],
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["k_suppliers"], 2, "supplier list was ignored")

    def test_bundle_default_path_still_uses_frozen_reserves(self):
        # The first pillar-2 call: no explicit suppliers, demo bundle drives
        # the session and its reserves stay frozen (reserve_source=simulated).
        res = simulate_reverse_auction(
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=2000,
            demo=True,
            self_play=self_play_bundle(),
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["k_suppliers"], 4)
        self.assertEqual(res.get("reserve_source"), "simulated")

    def test_reserve_source_is_not_simulated_for_explicit_suppliers(self):
        # With the caller's own suppliers, the session must not claim the
        # bundle's simulated reserves as its prior source.
        res = simulate_reverse_auction(
            target_price=12.0,
            walk_away_ceiling=16.2,
            n_samples=2000,
            demo=True,
            self_play=self_play_bundle(),
            suppliers=[
                {"name": "A", "profile": "Agile Conceder"},
                {"name": "B", "profile": "Rational Tit-for-Tat"},
            ],
        )
        self.assertNotEqual(res.get("reserve_source"), "simulated")


if __name__ == "__main__":
    unittest.main()
