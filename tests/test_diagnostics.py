import copy
import math
import random
import unittest
from unittest.mock import patch

from diagnostics import fill_markouts, paired_difference, pooled_markout_interval
from simulator import Config, Event, simulate


def fill(mid, next_mid, quantity=1, execution=None, informed=False):
    if execution is None:
        execution = mid - quantity
    return dict(mid_price=mid, next_mid=next_mid, filled=True, quantity=quantity,
                execution_price=execution, edge=quantity * (mid - execution),
                informed=informed)


def no_fill(mid, next_mid):
    return dict(mid_price=mid, next_mid=next_mid, filled=False, quantity=0,
                execution_price=None, edge=0, informed=False)


class FillMarkoutTests(unittest.TestCase):
    def test_maker_buy_and_sell_have_opposite_mid_move_signs(self):
        buy = fill_markouts([fill(100, 103, quantity=1)], horizons=(1,))
        sell = fill_markouts([fill(100, 103, quantity=-1)], horizons=(1,))
        self.assertEqual(buy["h1_mid_move_sum"], 3)
        self.assertEqual(sell["h1_mid_move_sum"], -3)
        self.assertEqual(buy["h1_execution_markout_sum"], 4)
        self.assertEqual(sell["h1_execution_markout_sum"], -2)
        self.assertEqual(buy["h1_edge_sum"], sell["h1_edge_sum"])

    def test_constant_mid_separates_execution_spread_from_adverse_selection(self):
        trace = simulate([Event(1, 100, 100, "sell", 99),
                          Event(2, 100, 100, "buy", 101)], Config(), "fixed")["trace"]
        actual = fill_markouts(trace, horizons=(1,))
        self.assertEqual(actual["h1_count"], 2)
        self.assertEqual(actual["h1_mid_move_sum"], 0)
        self.assertEqual(actual["h1_execution_markout_sum"], 2)
        self.assertEqual(actual["h1_edge_sum"], 2)
        self.assertEqual(actual["h1_mid_move_mean"], 0)

    def test_horizon_uses_next_mid_and_censors_only_missing_future(self):
        trace = [fill(100, 101, informed=True), no_fill(101, 103),
                 fill(103, 102, quantity=-1), fill(102, 107)]
        actual = fill_markouts(trace, horizons=(1, 2, 4, 5))
        self.assertEqual((actual["h1_count"], actual["h1_censored"]), (3, 0))
        self.assertEqual(actual["h1_mid_move_sum"], 7)
        self.assertEqual((actual["h2_count"], actual["h2_censored"]), (2, 1))
        self.assertEqual(actual["h2_mid_move_sum"], -1)  # +3 and -4
        self.assertEqual(actual["h2_informed_count"], 1)
        self.assertEqual(actual["h2_informed_mid_move_sum"], 3)
        self.assertEqual(actual["h2_uninformed_count"], 1)
        self.assertEqual(actual["h2_uninformed_mid_move_sum"], -4)
        self.assertEqual((actual["h4_count"], actual["h4_censored"]), (1, 2))
        self.assertEqual(actual["h4_mid_move_sum"], 7)
        self.assertEqual((actual["h5_count"], actual["h5_censored"]), (0, 3))
        self.assertIsNone(actual["h5_mid_move_mean"])
        for horizon in (1, 2, 4, 5):
            prefix = f"h{horizon}_"
            self.assertAlmostEqual(actual[prefix + "mid_move_sum"] + actual[prefix + "edge_sum"],
                                   actual[prefix + "execution_markout_sum"])

    def test_reflecting_prices_and_swapping_maker_side_preserves_markouts(self):
        trace = [fill(100, 98, 1, informed=True), no_fill(98, 103),
                 fill(103, 105, -1)]
        reflected = copy.deepcopy(trace)
        for row in reflected:
            row["mid_price"] = 200 - row["mid_price"]
            row["next_mid"] = 200 - row["next_mid"]
            row["quantity"] *= -1
            if row["filled"]:
                row["execution_price"] = 200 - row["execution_price"]
        self.assertEqual(fill_markouts(trace, (1, 2, 3)),
                         fill_markouts(reflected, (1, 2, 3)))

    def test_no_fills_and_empty_trace_have_undefined_means(self):
        for trace in ([], [no_fill(100, 101)]):
            actual = fill_markouts(trace)
            for horizon in (1, 5, 20):
                prefix = f"h{horizon}_"
                self.assertEqual(actual[prefix + "count"], 0)
                self.assertEqual(actual[prefix + "censored"], 0)
                self.assertEqual(actual[prefix + "mid_move_sum"], 0)
                self.assertIsNone(actual[prefix + "mid_move_mean"])
                self.assertIsNone(actual[prefix + "execution_markout_mean"])
                self.assertIsNone(actual[prefix + "informed_mid_move_mean"])

    def test_horizons_and_relevant_fill_values_are_validated(self):
        for horizons in ((), (0,), (-1,), (True,), (1.0,), (1, 1), None):
            with self.subTest(horizons=horizons), self.assertRaises(ValueError):
                fill_markouts([], horizons)
        bad_fields = [("quantity", 0), ("quantity", True), ("quantity", float("nan")),
                      ("mid_price", float("inf")), ("next_mid", float("nan")),
                      ("execution_price", float("inf")), ("edge", 12),
                      ("informed", 1), ("filled", 1)]
        for key, value in bad_fields:
            row = fill(100, 101)
            row[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                fill_markouts([row], (1,))
        with self.assertRaises(ValueError):
            fill_markouts([no_fill(100, float("nan"))], (1,))

    def test_diagnostics_do_not_mutate_recorded_trace(self):
        trace = [fill(100, 101), no_fill(101, 103)]
        original = copy.deepcopy(trace)
        fill_markouts(trace, (1, 2))
        self.assertEqual(trace, original)


class PairedDifferenceTests(unittest.TestCase):
    def test_seed_alignment_is_independent_of_mapping_order(self):
        left = {30: 3, 10: 2, 20: 8}
        right = {20: 10, 30: 0, 10: 1}
        result = paired_difference(left, right, resamples=250, seed=91)
        shuffled = paired_difference(dict(reversed(list(left.items()))),
                                     dict(reversed(list(right.items()))),
                                     resamples=250, seed=91)
        self.assertEqual(result, shuffled)
        self.assertAlmostEqual(result["mean"], 2 / 3)
        self.assertAlmostEqual(result["standard_error"], math.sqrt(19 / 3) / math.sqrt(3))
        self.assertEqual(result["probability_positive"], 2 / 3)

    def test_identical_strategy_has_exact_zero_difference_and_interval(self):
        samples = {3: 90.5, 1: -12, 2: 8}
        result = paired_difference(samples, samples)
        self.assertEqual(result, dict(count=3, mean=0, standard_error=0,
                                     ci_low=0, ci_high=0, probability_positive=0))

    def test_one_path_has_descriptive_values_but_no_inferential_interval(self):
        result = paired_difference({7: 3}, {7: 1})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["mean"], 2)
        self.assertEqual(result["probability_positive"], 1)
        for key in ("standard_error", "ci_low", "ci_high"):
            self.assertIsNone(result[key])

    def test_probability_positive_counts_observed_paths_strictly(self):
        result = paired_difference({1: 1, 2: 0, 3: -10}, {1: 0, 2: 0, 3: 0})
        self.assertEqual(result["probability_positive"], 1 / 3)

    def test_bootstrap_resamples_paired_differences_and_uses_percentiles(self):
        # Paired differences are [1, 3]; prescribed paired draws yield [1, 2, 3].
        class Draws:
            def __init__(self, seed):
                self.indices = iter([0, 0, 0, 1, 1, 1])

            def randrange(self, count):
                assert count == 2
                return next(self.indices)

        with patch("diagnostics.random.Random", Draws):
            result = paired_difference({1: 101, 2: 203}, {1: 100, 2: 200}, resamples=3)
        self.assertAlmostEqual(result["ci_low"], 1.05)
        self.assertAlmostEqual(result["ci_high"], 2.95)

    def test_bad_seed_sets_nonfinite_values_and_controls_are_rejected(self):
        for left, right in (({}, {}), ({1: 1}, {2: 1}), ({1: 1, 2: 2}, {1: 1}),
                            ({"1": 1}, {"1": 1}), ({True: 1}, {True: 1}),
                            ({1: float("nan")}, {1: 1}), ({1: 1}, {1: float("inf")})):
            with self.subTest(left=left, right=right), self.assertRaises(ValueError):
                paired_difference(left, right)
        for kwargs in ({"resamples": 0}, {"resamples": True}, {"seed": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                paired_difference({1: 2}, {1: 1}, **kwargs)

    def test_local_bootstrap_rng_preserves_global_random_state(self):
        state = random.getstate()
        paired_difference({1: 2, 2: 1}, {1: 0, 2: 2}, resamples=20)
        self.assertEqual(random.getstate(), state)


class PooledMarkoutIntervalTests(unittest.TestCase):
    def test_ratio_bootstrap_keeps_path_clusters_and_zero_fill_paths(self):
        rows = [{"h5_count": 100, "h5_mid_move_sum": 100},
                {"h5_count": 1, "h5_mid_move_sum": 9},
                {"h5_count": 0, "h5_mid_move_sum": 0}]

        class Draws:
            def __init__(self, seed):
                self.indices = iter([0, 0, 0, 1, 1, 1, 2, 2, 2, 0, 1, 2])

            def randrange(self, count):
                assert count == 3  # No-fill path is part of the population.
                return next(self.indices)

        with patch("diagnostics.random.Random", Draws):
            result = pooled_markout_interval(rows, 5, resamples=4)
        self.assertEqual(result["count_paths"], 3)
        self.assertEqual(result["total_fills"], 101)
        self.assertAlmostEqual(result["mean"], 109 / 101)
        self.assertEqual(result["valid_resamples"], 3)
        # Defined cluster ratios [1, 109/101, 9], not means of path means.
        self.assertAlmostEqual(result["ci_low"], 1 + 0.05 * (109 / 101 - 1))
        self.assertAlmostEqual(result["ci_high"], 109 / 101 + 0.95 * (9 - 109 / 101))

    def test_zero_fills_have_undefined_ratio_and_no_valid_resamples(self):
        for rows in ([], [{"h1_count": 0, "h1_mid_move_sum": 0}]):
            result = pooled_markout_interval(rows, 1)
            self.assertEqual(result["count_paths"], len(rows))
            self.assertEqual(result["total_fills"], 0)
            self.assertEqual(result["valid_resamples"], 0)
            for key in ("mean", "ci_low", "ci_high"):
                self.assertIsNone(result[key])

    def test_one_path_interval_is_degenerate_and_reproducible(self):
        rows = [{"h1_count": 2, "h1_mid_move_sum": -3}]
        result = pooled_markout_interval(rows, 1, resamples=20)
        self.assertEqual(result["mean"], -1.5)
        self.assertEqual(result["ci_low"], -1.5)
        self.assertEqual(result["ci_high"], -1.5)
        self.assertEqual(result["valid_resamples"], 20)

    def test_local_rng_and_rows_are_unchanged(self):
        rows = [{"h1_count": 3, "h1_mid_move_sum": 2},
                {"h1_count": 0, "h1_mid_move_sum": 0}]
        original = copy.deepcopy(rows)
        state = random.getstate()
        result = pooled_markout_interval(rows, 1, resamples=50, seed=73)
        self.assertEqual(result, pooled_markout_interval(rows, 1, resamples=50, seed=73))
        self.assertEqual(random.getstate(), state)
        self.assertEqual(rows, original)

    def test_invalid_aggregates_are_rejected(self):
        for row in ({"h1_count": -1, "h1_mid_move_sum": 0},
                    {"h1_count": 1.0, "h1_mid_move_sum": 0},
                    {"h1_count": True, "h1_mid_move_sum": 0},
                    {"h1_count": 0, "h1_mid_move_sum": 1},
                    {"h1_count": 1, "h1_mid_move_sum": float("nan")},
                    {"h1_count": 1}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                pooled_markout_interval([row], 1)
        for kwargs in ({"horizon": 0}, {"horizon": True}, {"resamples": 0},
                       {"resamples": 1.2}, {"seed": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                pooled_markout_interval([], **dict({"horizon": 1}, **kwargs))


if __name__ == "__main__":
    unittest.main()
