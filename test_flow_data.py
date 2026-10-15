"""五秒特征时间边界、训练拟合和预测评估的离线检查。"""

import copy
import unittest

import real_data
from flow_data import (BUCKET_US, build_day_rows, fit_ols, forecast_metrics,
                       merge_metrics, predict)


DAY = "2025-09-01"
START, END = real_data.day_bounds(DAY)


def trade(identifier, timestamp, price=100.0, quantity=1.0, sell=False):
    return real_data.Trade(identifier, price, quantity, identifier, identifier, timestamp, sell, True)


def row(x, y):
    return {"imbalance": x, "target_return_bps": y}


class BucketTests(unittest.TestCase):
    def test_exact_boundary_is_future_not_feature(self):
        trades = [trade(1, START, quantity=3), trade(2, START + BUCKET_US - 1, quantity=1, sell=True),
                  trade(3, START + BUCKET_US, price=102, sell=True)]
        rows, quality = build_day_rows(trades, DAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision_timestamp_us"], START + BUCKET_US)
        self.assertEqual(rows[0]["reference_price"], 100)
        self.assertEqual(rows[0]["imbalance"], .5)
        self.assertEqual(rows[0]["current_aggregate_rows"], 2)
        self.assertEqual(rows[0]["reference_age_us"], 1)
        self.assertEqual(rows[0]["next_reference_age_us"], BUCKET_US)
        self.assertEqual(rows[0]["next_customer_side"], "sell")
        self.assertAlmostEqual(rows[0]["target_return_bps"], 200)
        self.assertIsNone(quality["future_age_filter"])

    def test_future_mutation_cannot_change_current_features(self):
        past = [trade(1, START, price=99, quantity=2, sell=True)]
        before, _ = build_day_rows(past + [trade(2, START + BUCKET_US, 100, 1, False)], DAY)
        after, _ = build_day_rows(past + [trade(2, START + BUCKET_US, 500, 100, True)], DAY)
        for key in ("decision_timestamp_us", "reference_price", "imbalance", "reference_age_us",
                    "current_volume_eth", "current_aggregate_rows", "current_first_timestamp_us", "current_last_timestamp_us"):
            self.assertEqual(before[0][key], after[0][key])
        self.assertNotEqual(before[0]["target_return_bps"], after[0]["target_return_bps"])
        self.assertNotEqual(before[0]["next_customer_side"], after[0]["next_customer_side"])

    def test_gap_not_bridged(self):
        rows, quality = build_day_rows([trade(1, START), trade(2, START + 2 * BUCKET_US)], DAY)
        self.assertEqual(rows, [])
        self.assertEqual(quality["excluded_missing_adjacent_next_bucket"], 2)

    def test_last_bucket_no_cross_day(self):
        rows, quality = build_day_rows([trade(1, END - 2 * BUCKET_US), trade(2, END - 1)], DAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(quality["excluded_day_edge"], 1)
        with self.assertRaises(ValueError):
            build_day_rows([trade(1, END)], DAY)

    def test_equal_timestamps_keep_first_side_last_price(self):
        rows, quality = build_day_rows([trade(1, START), trade(2, START + BUCKET_US, 101, sell=False),
                                       trade(3, START + BUCKET_US, 102, sell=True)], DAY)
        self.assertEqual(rows[0]["next_customer_side"], "buy")
        self.assertEqual(rows[0]["next_reference_price"], 102)
        self.assertEqual(quality["adjacent_equal_timestamps"], 1)

    def test_id_time_order_checks_and_gap_reporting(self):
        for trades in ([trade(1, START), trade(1, START)], [trade(2, START), trade(1, START)],
                       [trade(1, START + 1), trade(2, START)]):
            with self.assertRaises(ValueError):
                build_day_rows(trades, DAY)
        _, quality = build_day_rows([trade(1, START), trade(4, START + BUCKET_US)], DAY)
        self.assertEqual(quality["missing_aggregate_ids"], 2)


class ForecastTests(unittest.TestCase):
    def test_ols_hand_calculation(self):
        model = fit_ols([row(-1, -1), row(0, 1), row(1, 3)])
        self.assertEqual(model, {"intercept_bps": 1.0, "slope_bps_per_imbalance": 2.0, "n": 3})

    def test_constant_feature_and_single_training_row(self):
        model = fit_ols([row(.5, 1), row(.5, 3)])
        self.assertEqual(model["intercept_bps"], 2)
        self.assertEqual(model["slope_bps_per_imbalance"], 0)
        self.assertEqual(fit_ols([row(0, 3)])["intercept_bps"], 3)
        with self.assertRaises(ValueError):
            fit_ols([])

    def test_clipping_and_raw_prediction(self):
        model = {"intercept_bps": 1, "slope_bps_per_imbalance": 5}
        self.assertEqual(predict(model, 1), 2)
        self.assertEqual(predict(model, -1), -2)
        self.assertEqual(predict(model, 1, None), 6)
        self.assertEqual(predict(model, .1), 1.5)
        with self.assertRaises(ValueError):
            predict(model, 2)

    def test_metrics_baselines_zero_handling(self):
        data = [row(-1, -1), row(0, 0), row(1, 2)]
        model = {"intercept_bps": 0, "slope_bps_per_imbalance": 2}
        metrics = forecast_metrics(data, model, 1)
        zero = metrics["predictors"]["zero"]
        self.assertAlmostEqual(zero["mse"], 5 / 3)
        self.assertEqual(zero["mae"], 1)
        self.assertEqual(zero["direction_target_nonzero_n"], 2)
        self.assertEqual(zero["directional_accuracy"], 0)
        self.assertEqual(zero["directional_coverage"], 0)
        constant = metrics["predictors"]["train_mean"]
        self.assertEqual(constant["mse"], 2)
        self.assertEqual(constant["directional_accuracy"], .5)
        self.assertEqual(metrics["predictors"]["model_raw"]["directional_accuracy"], 1)

    def test_merge_is_weighted_by_rows_not_days(self):
        data = [row(-1, -1), row(0, 0), row(1, 2)]
        model = fit_ols(data)
        combined = merge_metrics([forecast_metrics(data[:1], model, 1 / 3), forecast_metrics(data[1:], model, 1 / 3)])
        direct = forecast_metrics(data, model, 1 / 3)
        self.assertEqual(combined["n"], direct["n"])
        for name in direct["predictors"]:
            for key in direct["predictors"][name]:
                self.assertAlmostEqual(combined["predictors"][name][key], direct["predictors"][name][key])

    def test_forecasting_does_not_refit_model_or_mutate_inputs(self):
        training = [row(-1, -1), row(1, 1)]
        model = fit_ols(training)
        original = copy.deepcopy(model)
        future_rows = [row(-1, 100), row(1, -100)]
        forecast_metrics(future_rows, model, 0)
        self.assertEqual(model, original)
        self.assertEqual(predict(model, 1), 1)

    def test_empty_metric_and_all_zero_labels(self):
        model = {"intercept_bps": 0, "slope_bps_per_imbalance": 0}
        self.assertIsNone(forecast_metrics([], model, 0)["predictors"]["zero"]["mse"])
        self.assertIsNone(forecast_metrics([row(0, 0)], model, 0)["predictors"]["zero"]["directional_accuracy"])
        with self.assertRaises(ValueError):
            fit_ols([row(0, float("nan"))])


if __name__ == "__main__":
    unittest.main()
