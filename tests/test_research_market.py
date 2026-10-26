from dataclasses import replace
import unittest

from market_scenarios import generate_controlled_market
from simulator import Config, Event, RiskParameters, Scenario, make_quotes, simulate


class ControlledMarketTests(unittest.TestCase):
    def setUp(self):
        self.scenario = Scenario(name="control", label="Control", steps=100,
                                 volatility=1.2, informed_fraction=0)

    def test_information_mix_does_not_change_price_path(self):
        ordinary = generate_controlled_market(self.scenario, 19)
        toxic = generate_controlled_market(
            replace(self.scenario, informed_fraction=0.75), 19)
        self.assertEqual([(e.mid_price, e.next_mid) for e in ordinary],
                         [(e.mid_price, e.next_mid) for e in toxic])
        self.assertEqual([e.customer_side is None for e in ordinary],
                         [e.customer_side is None for e in toxic])
        self.assertTrue(any(e.informed for e in toxic))
        self.assertFalse(any(e.informed for e in ordinary))

    def test_demand_distribution_does_not_change_prices_or_arrival_sides(self):
        exponential = generate_controlled_market(self.scenario, 29)
        uniform = generate_controlled_market(
            replace(self.scenario, demand_model="uniform"), 29)
        self.assertEqual([(e.mid_price, e.next_mid, e.customer_side) for e in exponential],
                         [(e.mid_price, e.next_mid, e.customer_side) for e in uniform])
        self.assertTrue(any(a.limit_price != b.limit_price
                            for a, b in zip(exponential, uniform)))

    def test_prefix_and_seed_are_reproducible(self):
        first = generate_controlled_market(self.scenario, 31)
        longer = generate_controlled_market(replace(self.scenario, steps=101), 31)
        self.assertEqual(first, longer[:100])
        self.assertEqual(first, generate_controlled_market(self.scenario, 31))
        self.assertNotEqual(first, generate_controlled_market(self.scenario, 32))


class RiskQuoteTests(unittest.TestCase):
    def setUp(self):
        self.risk = RiskParameters()
        self.config = Config(inventory_limit=20)

    def quote(self, q, strategy="as_inventory", remaining=300, **kwargs):
        return make_quotes(100, q, self.config, strategy,
                           remaining_steps=remaining, risk=kwargs.get("risk", self.risk))

    def test_matched_width_even_when_inventory_quote_is_clipped(self):
        for remaining in (0, 1, 100, 300):
            for inventory in (-20, -10, -1, 0, 1, 10, 20):
                with self.subTest(remaining=remaining, inventory=inventory):
                    symmetric = self.quote(inventory, "as_symmetric", remaining)
                    adjusted = self.quote(inventory, remaining=remaining)
                    self.assertAlmostEqual(symmetric.ask - symmetric.bid,
                                           adjusted.ask - adjusted.bid, places=10)
                    self.assertLess(adjusted.bid, 100)
                    self.assertGreater(adjusted.ask, 100)
        self.assertTrue(self.quote(20).clipped)

    def test_remaining_time_and_inventory_have_expected_direction(self):
        early = self.quote(3)
        late = self.quote(3, remaining=1)
        self.assertLess((early.bid + early.ask) / 2, (late.bid + late.ask) / 2)
        self.assertGreater(early.ask - early.bid, late.ask - late.bid)
        self.assertGreater(self.quote(-3).bid, self.quote(3).bid)

    def test_symmetric_control_has_no_half_tick_centre_bias(self):
        for remaining in range(301):
            quote = self.quote(0, "as_symmetric", remaining)
            self.assertAlmostEqual((quote.bid + quote.ask) / 2, 100, places=10)
            self.assertEqual(round((quote.ask - quote.bid) / self.config.tick_size) % 2, 0)

    def test_zero_variance_removes_inventory_and_time_effect(self):
        risk = replace(self.risk, volatility=0)
        self.assertEqual(self.quote(8, risk=risk), self.quote(-8, remaining=1, risk=risk))

    def test_explicit_inputs_are_required(self):
        with self.assertRaises(ValueError):
            make_quotes(100, 0, self.config, "as_inventory")
        for changes in ({"risk_aversion": 0}, {"volatility": -1},
                        {"arrival_decay": 0}, {"volatility": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.risk, **changes)

    def test_future_price_cannot_change_current_risk_quote(self):
        rising = simulate([Event(1, 100, 110, None, None)], self.config,
                          "as_inventory", risk=self.risk)
        falling = simulate([Event(1, 100, 90, None, None)], self.config,
                           "as_inventory", risk=self.risk)
        for field in ("bid", "ask", "requested_shift", "actual_width"):
            self.assertEqual(rising["trace"][0][field], falling["trace"][0][field])

    def test_inventory_revaluation_split_and_full_ledger(self):
        events = generate_controlled_market(
            Scenario(name="test", label="Test", steps=70,
                     volatility=1.2, informed_fraction=0.5), 41)
        for policy in ("fixed", "inventory", "as_symmetric", "as_inventory"):
            with self.subTest(policy=policy):
                result = simulate(events, self.config, policy, risk=self.risk)
                metrics = result["metrics"]
                self.assertAlmostEqual(metrics["inventory_pnl"],
                                       metrics["inventory_carry_pnl"]
                                       + metrics["fill_revaluation_pnl"])
                self.assertAlmostEqual(metrics["pnl_liquidated"],
                                       metrics["spread_capture"] + metrics["inventory_pnl"]
                                       - metrics["total_fees"] - metrics["liquidation_cost"])
                for row in result["trace"]:
                    self.assertEqual(row["inventory"],
                                     row["inventory_before"] + row["quantity"])
                    self.assertAlmostEqual(row["inventory_pnl"],
                                           row["inventory_carry_pnl"]
                                           + row["fill_revaluation_pnl"])


if __name__ == "__main__":
    unittest.main()
