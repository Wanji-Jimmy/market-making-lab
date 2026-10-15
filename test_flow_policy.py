"""离线小例子验证历史环境回放的账本、时间边界与信息隔离。"""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict
import math
import unittest

from flow_policy import (
    HALF_SPREAD_BPS, HORIZON_US, INVENTORY_LIMIT, STRATEGIES, TICK_SIZE,
    make_policy_quote, prepare_environment, replay_day,
)


MODEL = {"intercept_bps": 0.25, "slope_bps_per_imbalance": 1.0}
START_US = 1_756_684_800_000_000


def row(index=0, reference=2000.0, next_reference=2000.0, side="buy", imbalance=0.0,
        date="2025-09-01", timestamp=None):
    return {
        "date": date,
        "decision_timestamp_us": START_US + index * HORIZON_US if timestamp is None else timestamp,
        "reference_price": reference, "next_reference_price": next_reference,
        "imbalance": imbalance, "next_customer_side": side,
        "target_return_bps": (next_reference / reference - 1) * 10000,
        "reference_age_us": 10, "next_reference_age_us": 20,
    }


class QuoteIsolationTests(unittest.TestCase):
    def test_model_changes_flow_quote(self):
        low = make_policy_quote(100, 0, 0.8, {"intercept_bps": 0, "slope_bps_per_imbalance": -1}, "flow")
        high = make_policy_quote(100, 0, 0.8, {"intercept_bps": 0, "slope_bps_per_imbalance": 1}, "flow")
        self.assertGreater(high.quote.bid, low.quote.bid)
        self.assertGreater(high.quote.ask, low.quote.ask)
        self.assertAlmostEqual(high.model_signal_bps, 0.8)

    def test_baselines_do_not_read_model(self):
        for strategy in ("fixed", "inventory", "constant_wide"):
            first = make_policy_quote(100, 2, 0.8, MODEL, strategy)
            second = make_policy_quote(100, 2, 0.8, None, strategy)
            self.assertEqual(first, second)
            self.assertEqual(first.model_signal_bps, 0)

    def test_fixed_has_no_inventory_skew_and_wide_changes_only_width(self):
        fixed = make_policy_quote(100, 5, 0, None, "fixed")
        inv = make_policy_quote(100, 5, 0, None, "inventory")
        wide = make_policy_quote(100, 5, 0, None, "constant_wide")
        self.assertEqual(fixed.inventory_shift_bps, 0)
        self.assertEqual(inv.inventory_shift_bps, -1)
        self.assertEqual(wide.inventory_shift_bps, -1)
        self.assertAlmostEqual(fixed.quote.ask - fixed.quote.bid, 0.04)
        self.assertAlmostEqual(wide.quote.ask - wide.quote.bid, 0.06)

    def test_intercept_ablation_has_same_inventory_and_no_slope(self):
        flow_zero = make_policy_quote(100, 3, 0.9, {"intercept_bps": 0.6, "slope_bps_per_imbalance": 0}, "flow")
        intercept = make_policy_quote(100, 3, -0.9, {"intercept_bps": 0.6}, "intercept_only")
        self.assertEqual(flow_zero.quote, intercept.quote)
        self.assertEqual(flow_zero.inventory_shift_bps, intercept.inventory_shift_bps)
        self.assertEqual(intercept.model_signal_bps, 0.6)
        self.assertEqual(intercept.slope_bps_per_imbalance, 0)

    def test_signal_and_combined_center_are_clipped_separately(self):
        snapshot = make_policy_quote(100, -10, 1, {"intercept_bps": 0, "slope_bps_per_imbalance": 100}, "flow")
        self.assertEqual(snapshot.raw_signal_bps, 100)
        self.assertEqual(snapshot.model_signal_bps, 2)
        self.assertEqual(snapshot.desired_center_shift_bps, 4)
        self.assertAlmostEqual(snapshot.applied_center_shift_bps, 1.99)
        self.assertTrue(snapshot.quote.clipped)

    def test_rounding_and_current_reference_bps(self):
        for reference in (100, 100.123456, 200):
            for strategy in STRATEGIES:
                for inventory in (-10, 0, 10):
                    snapshot = make_policy_quote(reference, inventory, 0.7, MODEL, strategy)
                    self.assertLess(snapshot.quote.bid, reference)
                    self.assertGreater(snapshot.quote.ask, reference)
                    for price in (snapshot.quote.bid, snapshot.quote.ask):
                        self.assertAlmostEqual(price / TICK_SIZE, round(price / TICK_SIZE), places=6)
        snapshot = make_policy_quote(200, 0, 0, None, "fixed")
        self.assertAlmostEqual(snapshot.quote.ask - snapshot.quote.bid, 0.08)
        self.assertAlmostEqual(snapshot.quoted_half_spread_bps, HALF_SPREAD_BPS)

    def test_snapshot_is_frozen_and_excludes_future_fields(self):
        snapshot = make_policy_quote(100, 0, 0.3, MODEL, "flow")
        with self.assertRaises(FrozenInstanceError):
            snapshot.inventory = 9
        fields = asdict(snapshot)
        for forbidden in ("next_reference_price", "target_return_bps", "customer_side", "next_customer_side"):
            self.assertNotIn(forbidden, fields)

    def test_future_side_price_and_label_cannot_change_current_quote(self):
        first = row(next_reference=1990, side="sell", imbalance=0.4)
        second = row(next_reference=2050, side="buy", imbalance=0.4)
        second["target_return_bps"] = -999
        a = replay_day([first], MODEL, "flow", record=True)["trace"][0]
        b = replay_day([second], MODEL, "flow", record=True)["trace"][0]
        self.assertEqual((a["bid"], a["ask"], a["quote_inputs"]), (b["bid"], b["ask"], b["quote_inputs"]))


class EnvironmentTests(unittest.TestCase):
    def test_draws_are_pre_generated_and_identical_across_policies(self):
        rows = [row(i, side="buy" if i % 2 else "sell", imbalance=0.7) for i in range(30)]
        original_rows, original_model = deepcopy(rows), deepcopy(MODEL)
        first = replay_day(rows, MODEL, "fixed", seed=7001, record=True)
        second = replay_day(rows, MODEL, "flow", seed=7001, record=True)
        fields = ("synthetic_urgency_bps", "synthetic_limit_price", "customer_side")
        self.assertEqual([[r[k] for k in fields] for r in first["trace"]],
                         [[r[k] for k in fields] for r in second["trace"]])
        self.assertEqual(rows, original_rows)
        self.assertEqual(MODEL, original_model)
        self.assertEqual(prepare_environment(rows, 7001), prepare_environment(rows, 7001))
        self.assertNotEqual(prepare_environment(rows, 7001), prepare_environment(rows, 7002))

    def test_first_available_reference_is_normalized_to_100(self):
        event = prepare_environment([row(reference=5000, next_reference=5050)])[0]
        self.assertEqual(event.reference_price, 100)
        self.assertEqual(event.next_reference_price, 101)
        self.assertEqual(event.raw_reference_price, 5000)

    def test_missing_customer_has_no_synthetic_limit(self):
        event = prepare_environment([row(side=None)])[0]
        self.assertIsNone(event.customer_side)
        self.assertIsNone(event.synthetic_limit_price)
        self.assertIsNone(event.synthetic_urgency_bps)

    def test_dates_order_and_overlapping_windows_rejected(self):
        for rows in ([row(), row(1, date="2025-09-02")],
                     [row(1), row(0)], [row(), row()],
                     [row(), row(timestamp=START_US + 1)]):
            with self.assertRaises(ValueError):
                prepare_environment(rows)

    def test_contiguous_reference_discontinuity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "contiguous decision reference"):
            prepare_environment([row(next_reference=2020), row(1, reference=2000)])


class ReplayAccountingTests(unittest.TestCase):
    def test_customer_buy_is_dealer_sale_with_current_notional_fee(self):
        run = replay_day([row(side="buy")], MODEL, "fixed", seed=0, urgency_bps=100, record=True)
        trade = run["trace"][0]
        self.assertTrue(trade["filled"])
        self.assertEqual(trade["quantity"], -1)
        self.assertEqual(trade["inventory"], -1)
        self.assertAlmostEqual(trade["execution_price"], 100.02)
        self.assertAlmostEqual(trade["fee"], 0.0025)
        self.assertAlmostEqual(trade["cash"], 10000 + 100.02 - 0.0025)
        self.assertEqual(run["summary"]["dealer_sell_count"], 1)

    def test_customer_sell_is_dealer_purchase(self):
        run = replay_day([row(side="sell")], MODEL, "fixed", seed=0, urgency_bps=100, record=True)
        trade = run["trace"][0]
        self.assertTrue(trade["filled"])
        self.assertEqual(trade["quantity"], 1)
        self.assertEqual(trade["inventory"], 1)
        self.assertAlmostEqual(trade["execution_price"], 99.98)
        self.assertAlmostEqual(trade["cash"], 10000 - 99.98 - 0.0025)
        self.assertEqual(run["summary"]["dealer_buy_count"], 1)

    def test_gap_valuation_and_liquidation_reconcile_independently(self):
        rows = [row(reference=2000, next_reference=2020, side="sell"),
                row(3, reference=2060, next_reference=2080, side=None)]
        run = replay_day(rows, MODEL, "fixed", seed=0, urgency_bps=100, record=True)
        summary = run["summary"]
        self.assertAlmostEqual(summary["execution_edge"], 0.02)
        self.assertAlmostEqual(summary["gap_inventory_revaluation"], 2.0)
        self.assertAlmostEqual(summary["interval_inventory_revaluation"], 2.0)
        self.assertAlmostEqual(summary["inventory_revaluation"], 4.0)
        self.assertAlmostEqual(summary["fees"], 0.0025)
        self.assertAlmostEqual(summary["terminal_cost"], 104 * 1.25 / 10000)
        expected = 0.02 + 4.0 - 0.0025 - 0.013
        self.assertAlmostEqual(summary["pnl_liquidated"], expected)
        self.assertEqual(summary["gap_count"], 1)
        self.assertEqual(summary["gap_seconds"], 10)
        self.assertEqual(summary["elapsed_seconds"], 20)
        self.assertEqual(run["trace"][1]["gap_duration_us"], 10_000_000)
        self.assertEqual(summary["fill_count"], 1)
        self.assertEqual(summary["inventory_after_liquidation"], 0)
        self.assertLess(summary["accounting_residual"], 1e-8)

    def test_short_inventory_benefits_from_gap_price_decline(self):
        rows = [row(reference=2000, next_reference=2000, side="buy"),
                row(2, reference=1980, next_reference=1980, side=None)]
        summary = replay_day(rows, MODEL, "fixed", seed=0, urgency_bps=100)["summary"]
        self.assertEqual(summary["inventory_before_liquidation"], -1)
        self.assertAlmostEqual(summary["gap_inventory_revaluation"], 1)
        self.assertAlmostEqual(summary["terminal_cost"], 99 * 1.25 / 10000)

    def test_fees_scale_with_each_current_reference(self):
        rows = [row(reference=2000, next_reference=2200, side="sell"),
                row(1, reference=2200, next_reference=2200, side="buy")]
        run = replay_day(rows, MODEL, "fixed", seed=0, urgency_bps=100, record=True)
        self.assertEqual(run["summary"]["fill_count"], 2)
        self.assertAlmostEqual(run["trace"][1]["fee"], 110 * 0.25 / 10000)
        self.assertAlmostEqual(run["summary"]["fees"], (100 + 110) * 0.25 / 10000)
        self.assertEqual(run["summary"]["terminal_cost"], 0)

    def test_caps_block_more_risk_but_allow_inventory_reduction(self):
        for side, expected in (("buy", -INVENTORY_LIMIT), ("sell", INVENTORY_LIMIT)):
            rows = [row(i, side=side) for i in range(30)]
            rows.append(row(30, side="sell" if side == "buy" else "buy"))
            run = replay_day(rows, MODEL, "fixed", seed=0, urgency_bps=1000, record=True)
            self.assertEqual(run["trace"][29]["inventory"], expected)
            self.assertTrue(run["trace"][30]["filled"])
            self.assertEqual(abs(run["trace"][30]["inventory"]), INVENTORY_LIMIT - 1)
            self.assertEqual(run["summary"]["max_abs_inventory"], INVENTORY_LIMIT)
            self.assertGreater(run["summary"]["blocked_inventory"], 0)

    def test_drawdown_includes_initial_equity_and_terminal_cost(self):
        run = replay_day([row(next_reference=1980, side="sell")], MODEL, "fixed",
                         seed=0, urgency_bps=100, record=True)
        summary = run["summary"]
        trade = run["trace"][0]
        self.assertAlmostEqual(summary["max_drawdown_mtm"], 1.0)
        self.assertAlmostEqual(summary["max_drawdown"], 1.0 + summary["terminal_cost"])
        self.assertGreater(trade["equity_after_execution"], summary["initial_equity"])

    def test_no_customers_no_position_no_profit(self):
        rows = [row(side=None, next_reference=2100), row(2, reference=2300, next_reference=1900, side=None)]
        summary = replay_day(rows, MODEL, "flow")["summary"]
        for field in ("pnl_mtm", "pnl_liquidated", "fill_count", "inventory_revaluation", "fees", "terminal_cost"):
            self.assertEqual(summary[field], 0)
        self.assertEqual(summary["eligible_opportunities"], 0)
        self.assertEqual(summary["fills_per_eligible_opportunity"], 0)

    def test_trace_option_cannot_change_metrics(self):
        rows = [row(i, side="buy" if i % 2 else "sell", imbalance=0.4) for i in range(20)]
        first = replay_day(rows, MODEL, "flow", record=True)
        second = replay_day(rows, MODEL, "flow", record=False)
        self.assertEqual(first["summary"], second["summary"])
        self.assertEqual(second["trace"], [])
        initial = first["summary"]["initial_equity"]
        cumulative = 0.0
        for trade in first["trace"]:
            cumulative += trade["net_change"]
            self.assertAlmostEqual(trade["equity"], initial + cumulative)

    def test_inventory_time_average_includes_gap_holding(self):
        rows = [row(side="sell"), row(3, side="buy")]
        summary = replay_day(rows, MODEL, "fixed", seed=0, urgency_bps=100)["summary"]
        self.assertEqual(summary["mean_abs_inventory"], 0.5)
        self.assertEqual(summary["time_weighted_mean_abs_inventory"], 0.75)

    def test_invalid_inputs_fail_without_silent_defaults(self):
        for kwargs in ({"strategy": "unknown"}, {"fee_bps": -1}, {"urgency_bps": -1},
                       {"seed": True}, {"record": 1}):
            params = {"rows": [row()], "model": MODEL, "strategy": "flow", **kwargs}
            with self.assertRaises(ValueError):
                replay_day(**params)
        with self.assertRaises(ValueError):
            replay_day([], MODEL, "flow")
        invalid = row()
        invalid["imbalance"] = math.nan
        with self.assertRaises(ValueError):
            replay_day([invalid], MODEL, "flow")


if __name__ == "__main__":
    unittest.main()
