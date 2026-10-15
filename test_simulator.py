import copy
import unittest

from simulator import (
    Config, Event, Quote, Scenario, execute_trade, generate_market,
    make_quotes, simulate,
)


class QuoteTests(unittest.TestCase):
    def test_fixed_quotes_do_not_depend_on_inventory(self):
        config = Config()
        for inventory in (-5, 0, 5):
            with self.subTest(inventory=inventory):
                quote = make_quotes(100, inventory, config, "fixed")
                self.assertAlmostEqual(quote.bid, 99)
                self.assertAlmostEqual(quote.ask, 101)

    def test_inventory_moves_both_quotes_in_the_right_direction(self):
        config = Config(skew_strength=0.1)
        long = make_quotes(100, 5, config, "inventory")
        short = make_quotes(100, -5, config, "inventory")
        self.assertAlmostEqual(long.bid, 98.5)
        self.assertAlmostEqual(long.ask, 100.5)
        self.assertAlmostEqual(short.bid, 99.5)
        self.assertAlmostEqual(short.ask, 101.5)

    def test_quotes_respect_tick_size_and_remain_positive(self):
        config = Config(tick_size=0.05, skew_strength=1)
        ordinary = make_quotes(100.03, 0, config, "fixed")
        clipped = make_quotes(0.10, 10, config, "inventory")
        self.assertAlmostEqual(ordinary.bid, 99.00)
        self.assertAlmostEqual(ordinary.ask, 101.05)
        for quote in (ordinary, clipped):
            for price in (quote.bid, quote.ask):
                self.assertGreater(price, 0)
                self.assertAlmostEqual(price / 0.05, round(price / 0.05))
            self.assertGreater(quote.ask, quote.bid)
        self.assertTrue(clipped.clipped)

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(ValueError):
            make_quotes(100, 0, Config(), "future-aware")


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(fee_per_trade=0.05)
        self.quote = Quote(bid=99, ask=101)

    def trade(self, side, limit, cash=1000, inventory=2, config=None):
        return execute_trade(
            cash, inventory, self.quote, side, limit, config or self.config
        )

    def test_customer_buy_is_maker_sell_at_ask(self):
        result = self.trade("buy", 102)
        self.assertTrue(result["filled"])
        self.assertAlmostEqual(result["cash"], 1100.95)
        self.assertEqual(result["inventory"], 1)
        self.assertAlmostEqual(result["execution_price"], 101)
        self.assertAlmostEqual(result["fee"], 0.05)

    def test_customer_sell_is_maker_buy_at_bid(self):
        result = self.trade("sell", 98)
        self.assertTrue(result["filled"])
        self.assertAlmostEqual(result["cash"], 900.95)
        self.assertEqual(result["inventory"], 3)
        self.assertAlmostEqual(result["execution_price"], 99)

    def test_equal_limit_is_accepted_on_both_sides(self):
        for side, limit in (("buy", 101), ("sell", 99)):
            with self.subTest(side=side):
                self.assertTrue(self.trade(side, limit)["filled"])

    def test_rejected_price_leaves_ledger_unchanged(self):
        for side, limit in (("buy", 100.99), ("sell", 99.01)):
            with self.subTest(side=side):
                result = self.trade(side, limit)
                self.assertFalse(result["filled"])
                self.assertEqual(result["cash"], 1000)
                self.assertEqual(result["inventory"], 2)
                self.assertEqual(result["fee"], 0)
                self.assertIsNone(result["execution_price"])

    def test_long_only_cannot_sell_missing_inventory(self):
        result = self.trade("buy", 102, inventory=0,
                            config=Config(allow_short=False))
        self.assertFalse(result["filled"])
        self.assertEqual(result["inventory"], 0)
        self.assertEqual(result["cash"], 1000)

    def test_short_sale_creates_negative_inventory(self):
        result = self.trade("buy", 102, inventory=0)
        self.assertTrue(result["filled"])
        self.assertEqual(result["inventory"], -1)
        self.assertAlmostEqual(result["cash"], 1100.95)

    def test_buying_power_includes_fee_and_exact_boundary(self):
        rejected = self.trade("sell", 99, cash=99.04, inventory=0)
        accepted = self.trade("sell", 99, cash=99.05, inventory=0)
        self.assertFalse(rejected["filled"])
        self.assertAlmostEqual(rejected["cash"], 99.04)
        self.assertTrue(accepted["filled"])
        self.assertAlmostEqual(accepted["cash"], 0)
        self.assertEqual(accepted["inventory"], 1)

    def test_upper_inventory_limit_blocks_only_increasing_position(self):
        config = Config(inventory_limit=2)
        blocked = self.trade("sell", 99, inventory=2, config=config)
        reduced = self.trade("buy", 101, inventory=2, config=config)
        self.assertFalse(blocked["filled"])
        self.assertEqual(blocked["inventory"], 2)
        self.assertTrue(reduced["filled"])
        self.assertEqual(reduced["inventory"], 1)

    def test_lower_inventory_limit_blocks_only_increasing_short(self):
        config = Config(inventory_limit=2)
        blocked = self.trade("buy", 101, inventory=-2, config=config)
        reduced = self.trade("sell", 99, inventory=-2, config=config)
        self.assertFalse(blocked["filled"])
        self.assertEqual(blocked["inventory"], -2)
        self.assertTrue(reduced["filled"])
        self.assertEqual(reduced["inventory"], -1)

    def test_unknown_customer_side_is_rejected(self):
        with self.assertRaises(ValueError):
            self.trade("BUY", 101)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(initial_cash=1000, fee_per_trade=0.05)

    def run_events(self, events, config=None, record=True):
        return simulate(events, config or self.config, "fixed", record=record)

    def test_no_trade_and_no_inventory_cannot_create_profit(self):
        for side, limit in (("buy", 90), (None, None)):
            with self.subTest(side=side):
                result = self.run_events([Event(1, 100, 105, side, limit)])
                self.assertEqual(result["metrics"]["trades"], 0)
                self.assertAlmostEqual(result["metrics"]["pnl_mtm"], 0)
                self.assertAlmostEqual(result["metrics"]["pnl_liquidated"], 0)

    def test_buy_99_sell_101_round_trip_earns_2_less_two_fees(self):
        events = [Event(1, 100, 100, "sell", 99),
                  Event(2, 100, 100, "buy", 101)]
        result = self.run_events(events)
        metrics = result["metrics"]
        self.assertEqual(metrics["trades"], 2)
        self.assertEqual(metrics["final_inventory"], 0)
        self.assertAlmostEqual(metrics["total_fees"], 0.10)
        self.assertAlmostEqual(metrics["pnl_mtm"], 1.90)
        self.assertAlmostEqual(metrics["pnl_liquidated"], 1.90)
        self.assertAlmostEqual(result["trace"][-1]["cash"], 1001.90)

    def test_initial_inventory_is_included_in_initial_wealth(self):
        config = Config(initial_cash=1000, initial_inventory=2)
        result = self.run_events([Event(1, 100, 100, "buy", 90)], config)
        self.assertAlmostEqual(result["metrics"]["pnl_mtm"], 0)
        self.assertAlmostEqual(result["trace"][0]["equity"], 1200)

    def test_new_long_inventory_bears_the_next_price_change(self):
        result = self.run_events([Event(1, 100, 90, "sell", 99)])
        metrics = result["metrics"]
        self.assertAlmostEqual(metrics["spread_capture"], 1)
        self.assertAlmostEqual(metrics["inventory_pnl"], -10)
        self.assertAlmostEqual(metrics["pnl_mtm"], -9.05)
        self.assertAlmostEqual(result["trace"][0]["cash"], 900.95)

    def test_new_short_inventory_benefits_from_a_price_fall(self):
        result = self.run_events([Event(1, 100, 90, "buy", 101)])
        metrics = result["metrics"]
        self.assertEqual(metrics["final_inventory"], -1)
        self.assertAlmostEqual(metrics["spread_capture"], 1)
        self.assertAlmostEqual(metrics["inventory_pnl"], 10)
        self.assertAlmostEqual(metrics["pnl_mtm"], 10.95)

    def test_pnl_decomposition_matches_independent_ledger(self):
        # Buy and sell at 99: only the two fees remain after closing.
        events = [Event(1, 100, 98, "sell", 99),
                  Event(2, 98, 103, "buy", 99)]
        result = self.run_events(events)
        metrics = result["metrics"]
        self.assertAlmostEqual(metrics["pnl_mtm"], -0.10)
        self.assertAlmostEqual(metrics["spread_capture"], 2)
        self.assertAlmostEqual(metrics["inventory_pnl"], -2)
        previous_pnl = 0
        for row in result["trace"]:
            expected_change = row["edge"] + row["inventory_pnl"] - row["fee"]
            self.assertAlmostEqual(row["net_change"], expected_change)
            self.assertAlmostEqual(row["pnl"] - previous_pnl, expected_change)
            previous_pnl = row["pnl"]

    def test_liquidation_cost_is_charged_for_both_long_and_short(self):
        for inventory in (-2, 2):
            with self.subTest(inventory=inventory):
                config = Config(initial_cash=1000, initial_inventory=inventory,
                                fee_per_trade=0.05, liquidation_half_spread=1)
                result = self.run_events([Event(1, 100, 100, "buy", 90)], config)
                self.assertAlmostEqual(result["metrics"]["pnl_mtm"], 0)
                self.assertAlmostEqual(result["metrics"]["liquidation_cost"], 2.10)
                self.assertAlmostEqual(result["metrics"]["pnl_liquidated"], -2.10)

    def test_drawdown_starts_from_initial_wealth(self):
        config = Config(initial_cash=1000, initial_inventory=1)
        events = [Event(1, 100, 90, "buy", 80),
                  Event(2, 90, 95, "buy", 80)]
        metrics = self.run_events(events, config)["metrics"]
        self.assertAlmostEqual(metrics["max_drawdown"], 10)
        self.assertAlmostEqual(metrics["pnl_mtm"], -5)

    def test_record_flag_changes_only_trace_retention(self):
        events = [Event(1, 100, 98, "sell", 99),
                  Event(2, 98, 103, "buy", 99)]
        recorded = self.run_events(events)
        compact = self.run_events(events, record=False)
        self.assertEqual(recorded["metrics"], compact["metrics"])
        self.assertEqual(compact["trace"], [])


class MarketAndValidationTests(unittest.TestCase):
    def test_same_seed_recreates_the_same_environment(self):
        scenario = Scenario(name="test", label="测试", steps=50,
                            informed_fraction=0.2, jump_probability=0.1,
                            jump_size=1)
        first = generate_market(scenario, 17)
        self.assertEqual(first, generate_market(scenario, 17))
        self.assertNotEqual(first, generate_market(scenario, 18))

    def test_strategies_do_not_modify_shared_external_events(self):
        events = generate_market(Scenario(name="test", label="测试", steps=40), 9)
        original = copy.deepcopy(events)
        config = Config()
        first = simulate(events, config, "fixed")
        simulate(events, config, "inventory")
        repeated = simulate(events, config, "fixed")
        self.assertEqual(events, original)
        self.assertEqual(first, repeated)

    def test_future_price_cannot_change_current_quote(self):
        config = Config(initial_inventory=3)
        rising = simulate([Event(1, 100, 110, "buy", 90)], config, "inventory")
        falling = simulate([Event(1, 100, 90, "buy", 90)], config, "inventory")
        for key in ("bid", "ask", "filled", "inventory"):
            self.assertEqual(rising["trace"][0][key], falling["trace"][0][key])

    def test_one_sided_flow_never_breaks_inventory_limits(self):
        config = Config(inventory_limit=3, initial_cash=10000)
        events = [Event(i + 1, 100, 100, "sell", 1) for i in range(20)]
        events += [Event(i + 21, 100, 100, "buy", 1000) for i in range(40)]
        for strategy in ("fixed", "inventory"):
            with self.subTest(strategy=strategy):
                result = simulate(events, config, strategy)
                self.assertTrue(all(-3 <= row["inventory"] <= 3
                                    for row in result["trace"]))
                self.assertEqual(result["metrics"]["max_abs_inventory"], 3)
                self.assertEqual(result["metrics"]["final_inventory"], -3)
                self.assertGreater(result["metrics"]["blocked_inventory"], 0)

    def test_invalid_configuration_is_rejected(self):
        cases = [dict(fee_per_trade=-1), dict(tick_size=0),
                 dict(inventory_limit=0), dict(half_spread=0),
                 dict(initial_cash=-1), dict(skew_strength=-1),
                 dict(initial_inventory=11, inventory_limit=10),
                 dict(initial_inventory=-1, allow_short=False)]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Config(**arguments)

    def test_invalid_scenario_is_rejected(self):
        cases = [dict(steps=0), dict(volatility=-1),
                 dict(arrival_probability=1.1), dict(buy_probability=-0.1),
                 dict(urgency_scale=-1), dict(informed_fraction=1.1),
                 dict(demand_model="magic")]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                Scenario(name="bad", label="无效参数", **arguments)

    def test_broken_price_continuity_is_rejected(self):
        events = [Event(1, 100, 101, "buy", 90),
                  Event(2, 102, 103, "buy", 90)]
        with self.assertRaises(ValueError):
            simulate(events, Config(), "fixed")


if __name__ == "__main__":
    unittest.main()
