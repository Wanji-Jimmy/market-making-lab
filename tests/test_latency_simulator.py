"""Focused invariants for delayed one-unit passive-order replacement."""

import copy
from dataclasses import FrozenInstanceError
import math
import unittest

from diagnostics import fill_markouts
from latency_simulator import CORE_STRATEGIES, STRATEGIES, simulate_latency
from market_scenarios import generate_controlled_market
from research import validate_path
from simulator import Config, Event, RiskParameters, Scenario, make_quotes, simulate


class ZeroLatencyTests(unittest.TestCase):
    def setUp(self):
        self.risk = RiskParameters()
        self.config = Config(initial_inventory=2, inventory_limit=5)
        self.events = generate_controlled_market(Scenario(
            name="latency-tests", label="latency tests", steps=75,
            volatility=1.2, urgency_scale=2.0, informed_fraction=0.5,
            jump_probability=0.1, jump_size=3), seed=631)

    def test_every_core_policy_exactly_recovers_v2(self):
        for strategy in CORE_STRATEGIES:
            with self.subTest(strategy=strategy):
                expected = simulate(self.events, self.config, strategy, risk=self.risk)
                result = simulate_latency(self.events, self.config, strategy,
                                          latency_steps=0, risk=self.risk)
                for key, value in expected["metrics"].items():
                    self.assertEqual(result["metrics"][key], value, key)
                for expected_row, row in zip(expected["trace"], result["trace"]):
                    for key, value in expected_row.items():
                        self.assertEqual(row[key], value, key)
                    self.assertEqual(row["quote_information_age"], 0)
                    self.assertEqual(row["quote_resting_age"], 0)
                self.assertEqual(result["metrics"]["quote_activations"], len(self.events))
                self.assertEqual(result["metrics"]["pending_quote_requests"], 0)

    def test_buffered_zero_latency_exactly_recovers_corresponding_as(self):
        for core in ("as_symmetric", "as_inventory"):
            with self.subTest(core=core):
                expected = simulate(self.events, self.config, core, risk=self.risk)
                result = simulate_latency(self.events, self.config, "buffered_" + core,
                                          latency_steps=0, risk=self.risk)
                for key, value in expected["metrics"].items():
                    self.assertEqual(result["metrics"][key], value, key)
                for expected_row, row in zip(expected["trace"], result["trace"]):
                    for key, value in expected_row.items():
                        self.assertEqual(row[key], value, key)


class PassiveOrderTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(initial_cash=10000, inventory_limit=20)

    def run_path(self, events, latency, **kwargs):
        return simulate_latency(events, self.config, "fixed", latency_steps=latency,
                                **kwargs)

    def test_warm_pair_and_due_requests_activate_only_at_their_due_rows(self):
        events = [Event(10 + 10 * i, 100 + i, 101 + i, None, None) for i in range(5)]
        result = self.run_path(events, 2)
        trace = result["trace"]
        self.assertEqual([r["active_pair_id"] for r in trace], [0, 0, 1, 2, 3])
        self.assertEqual([r["quote_information_step"] for r in trace], [0, 0, 0, 1, 2])
        self.assertEqual([r["quote_activation_step"] for r in trace], [0, 0, 2, 3, 4])
        self.assertEqual([r["request_due_step"] for r in trace], [2, 3, 4, 5, 6])
        self.assertEqual([r["ask"] for r in trace], [101, 101, 101, 102, 103])
        self.assertEqual([r["step"] for r in trace], [10, 20, 30, 40, 50])
        self.assertEqual(result["metrics"]["quote_requests"], 5)
        self.assertEqual(result["metrics"]["quote_activations"], 3)
        self.assertEqual(result["metrics"]["pending_quote_requests"], 2)
        self.assertEqual(result["metrics"]["warm_start_activations"], 1)

    def test_old_quote_can_fill_before_cancel_is_effective(self):
        events = [Event(1, 100, 110, None, None),
                  Event(2, 110, 110, "buy", 101),
                  Event(3, 110, 110, None, None)]
        result = self.run_path(events, 2)
        row = result["trace"][1]
        self.assertEqual(row["active_pair_id"], 0)
        self.assertEqual(row["request_ask"], 111)
        self.assertEqual(row["execution_price"], 101)
        self.assertEqual(row["filled_order_id"], "0:ask")
        self.assertEqual(row["edge"], -9)
        self.assertEqual(result["metrics"]["active_stale_fills"], 1)
        self.assertEqual(result["metrics"]["negative_edge_fills"], 1)

    def test_a_side_is_not_replenished_until_an_actual_activation(self):
        events = [Event(i + 1, 100, 100, "buy", 1000) for i in range(5)]
        result = self.run_path(events, 3)
        trace = result["trace"]
        self.assertEqual([r["filled"] for r in trace], [True, False, False, True, True])
        self.assertEqual([r["reason"] for r in trace],
                         ["filled", "no_live_order", "no_live_order", "filled", "filled"])
        self.assertEqual([r["ask_live_before"] for r in trace],
                         [True, False, False, True, True])
        self.assertTrue(all(r["bid_live_after"] for r in trace))
        self.assertTrue(all(not r["ask_live_after"] for r in trace))
        self.assertEqual(result["metrics"]["blocked_no_order"], 2)
        self.assertEqual(result["metrics"]["two_sided_live_steps"], 3)

    def test_neither_side_of_a_pair_can_fill_twice(self):
        events = [Event(i + 1, 100, 100, "buy" if i % 2 == 0 else "sell",
                        1000 if i % 2 == 0 else 1) for i in range(25)]
        result = self.run_path(events, 10)
        ids = [r["filled_order_id"] for r in result["trace"] if r["filled"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[:2], ["0:ask", "0:bid"])
        self.assertTrue(all(r["reason"] == "no_live_order"
                            for r in result["trace"][2:10]))

    def test_atomic_replacement_replenishes_both_sides(self):
        events = [Event(1, 100, 100, "buy", 1000),
                  Event(2, 100, 100, "sell", 1),
                  Event(3, 100, 100, None, None)]
        result = self.run_path(events, 2)
        last = result["trace"][2]
        self.assertEqual(last["active_pair_id"], 1)
        self.assertTrue(last["bid_live_before"])
        self.assertTrue(last["ask_live_before"])
        self.assertEqual(last["quote_activations_this_step"], 1)

    def test_information_age_is_distinct_from_resting_age(self):
        events = [Event(i + 1, 100, 100, "buy" if i == 3 else None,
                        1000 if i == 3 else None) for i in range(4)]
        result = self.run_path(events, 3)
        row = result["trace"][-1]
        self.assertEqual(row["quote_information_age"], 3)
        self.assertEqual(row["quote_resting_age"], 0)
        self.assertEqual(result["metrics"]["fill_information_age_sum"], 3)
        self.assertEqual(result["metrics"]["fill_information_age_mean"], 3)
        self.assertEqual(result["metrics"]["fill_resting_age_sum"], 0)
        self.assertEqual(result["metrics"]["fill_resting_age_mean"], 0)

    def test_current_replacement_uses_inventory_before_current_fill(self):
        events = [Event(i + 1, 100, 100, "sell", 1) for i in range(3)]
        result = simulate_latency(events, self.config, "inventory", latency_steps=1)
        trace = result["trace"]
        self.assertEqual(trace[0]["request_bid"], 99)
        self.assertAlmostEqual(trace[1]["request_bid"], 98.9)
        self.assertEqual(trace[1]["bid"], 99)
        self.assertAlmostEqual(trace[2]["bid"], 98.9)

    def test_width_retains_last_active_pair_after_both_sides_fill(self):
        events = [Event(1, 100, 100, "buy", 1000),
                  Event(2, 100, 100, "sell", 1),
                  Event(3, 100, 100, None, None)]
        result = self.run_path(events, 10)
        self.assertEqual(result["metrics"]["mean_quote_width"], 2)
        last = result["trace"][-1]
        self.assertFalse(last["bid_live_before"])
        self.assertFalse(last["ask_live_before"])
        self.assertEqual(last["actual_width"], 2)


class PolicyAndAccountingTests(unittest.TestCase):
    def setUp(self):
        self.risk = RiskParameters(risk_aversion=0.02, volatility=1.2,
                                   arrival_decay=2 / 3)
        self.config = Config(initial_inventory=2, inventory_limit=4)
        self.events = generate_controlled_market(Scenario(
            name="accounting", label="accounting", steps=90, volatility=1.2,
            urgency_scale=5, informed_fraction=0.7), seed=77)

    def test_all_strategies_keep_caps_and_reconcile_to_independent_ledger(self):
        for strategy in STRATEGIES:
            for latency in (0, 1, 5):
                with self.subTest(strategy=strategy, latency=latency):
                    result = simulate_latency(self.events, self.config, strategy,
                                              latency_steps=latency, risk=self.risk)
                    self.assertLessEqual(result["metrics"]["max_abs_inventory"], 4)
                    self.assertTrue(all(-4 <= r["inventory"] <= 4
                                        for r in result["trace"]))
                    residuals = validate_path(self.events, self.config, result)
                    self.assertTrue(all(abs(x) < 1e-7 for x in residuals.values()))
                    markouts = fill_markouts(result["trace"], (1, 5))
                    self.assertEqual(markouts["h1_count"], result["metrics"]["trades"])

    def test_inventory_and_cash_rejections_do_not_consume_orders(self):
        events = [Event(1, 100, 100, "sell", 1),
                  Event(2, 100, 100, "sell", 1)]
        at_cap = simulate_latency(events, Config(initial_inventory=1, inventory_limit=1),
                                  "fixed", latency_steps=10)
        cash_limited = simulate_latency(events, Config(initial_cash=10),
                                        "fixed", latency_steps=10)
        for result, reason, metric in ((at_cap, "inventory_limit", "blocked_inventory"),
                                      (cash_limited, "insufficient_cash",
                                       "blocked_insufficient_cash")):
            self.assertEqual([r["reason"] for r in result["trace"]], [reason, reason])
            self.assertTrue(all(r["bid_live_after"] for r in result["trace"]))
            self.assertEqual(result["metrics"][metric], 2)
            self.assertEqual(result["metrics"]["trades"], 0)
            self.assertIsNone(result["metrics"]["fill_information_age_mean"])
            self.assertIsNone(result["metrics"]["fill_resting_age_mean"])

    def test_buffered_pair_has_identical_width_at_every_request_and_activation(self):
        symmetric = simulate_latency(self.events, self.config, "buffered_as_symmetric",
                                      latency_steps=5, risk=self.risk)
        inventory = simulate_latency(self.events, self.config, "buffered_as_inventory",
                                     latency_steps=5, risk=self.risk)
        expected_buffer = self.risk.volatility * math.sqrt(5)
        self.assertEqual(inventory["metrics"]["latency_buffer_half_width"], expected_buffer)
        for left, right in zip(symmetric["trace"], inventory["trace"]):
            self.assertEqual(left["active_pair_id"], right["active_pair_id"])
            self.assertEqual(left["quote_activation_step"], right["quote_activation_step"])
            self.assertAlmostEqual(left["actual_width"], right["actual_width"], places=10)
            self.assertAlmostEqual(left["request_ask"] - left["request_bid"],
                                   right["request_ask"] - right["request_bid"], places=10)
            base = make_quotes(left["mid_price"], left["inventory_before"], self.config,
                               "as_symmetric", remaining_steps=len(self.events)
                               - left["model_step"], risk=self.risk)
            self.assertEqual(left["request_requested_width"],
                             base.requested_width + 2 * expected_buffer)

    def test_no_future_price_or_information_enters_current_quotes(self):
        rising = [Event(1, 100, 110, "buy", 1, True)]
        falling = [Event(1, 100, 90, "buy", 1, False)]
        for strategy in STRATEGIES:
            for latency in (0, 3):
                with self.subTest(strategy=strategy, latency=latency):
                    left = simulate_latency(rising, self.config, strategy,
                                            latency_steps=latency, risk=self.risk)["trace"][0]
                    right = simulate_latency(falling, self.config, strategy,
                                             latency_steps=latency, risk=self.risk)["trace"][0]
                    for key in ("bid", "ask", "request_bid", "request_ask",
                                "filled", "inventory", "active_pair_id"):
                        self.assertEqual(left[key], right[key], key)

    def test_record_false_changes_only_trace_retention(self):
        for strategy in STRATEGIES:
            with self.subTest(strategy=strategy):
                recorded = simulate_latency(self.events, self.config, strategy,
                                            latency_steps=5, risk=self.risk)
                compact = simulate_latency(self.events, self.config, strategy,
                                           latency_steps=5, risk=self.risk, record=False)
                self.assertEqual(recorded["metrics"], compact["metrics"])
                self.assertEqual(compact["trace"], [])

    def test_events_are_frozen_and_shared_input_is_not_changed(self):
        before = copy.deepcopy(self.events)
        first = simulate_latency(self.events, self.config, "as_inventory",
                                 latency_steps=3, risk=self.risk)
        simulate_latency(self.events, self.config, "buffered_as_inventory",
                         latency_steps=3, risk=self.risk)
        again = simulate_latency(self.events, self.config, "as_inventory",
                                 latency_steps=3, risk=self.risk)
        self.assertEqual(before, self.events)
        self.assertEqual(first, again)
        with self.assertRaises(FrozenInstanceError):
            self.events[0].mid_price = 5

    def test_invalid_inputs_are_rejected(self):
        event = Event(1, 100, 100, None, None)
        for latency in (-1, True, 1.5):
            with self.subTest(latency=latency), self.assertRaises(ValueError):
                simulate_latency([event], self.config, "fixed", latency_steps=latency)
        for multiplier in (-1, True, float("inf"), float("nan")):
            with self.subTest(multiplier=multiplier), self.assertRaises(ValueError):
                simulate_latency([event], self.config, "buffered_as_inventory",
                                 latency_steps=2, buffer_multiplier=multiplier,
                                 risk=self.risk)
        for events in ([], [object()], [event, event],
                       [event, Event(2, 101, 101, None, None)]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                simulate_latency(events, self.config, "fixed", latency_steps=0)
        with self.assertRaises(ValueError):
            simulate_latency([event], self.config, "as_inventory", latency_steps=1)
        with self.assertRaises(ValueError):
            simulate_latency([event], self.config, "unknown", latency_steps=1)
        with self.assertRaises(ValueError):
            simulate_latency([event], self.config, "fixed", latency_steps=1, record=1)


if __name__ == "__main__":
    unittest.main()
