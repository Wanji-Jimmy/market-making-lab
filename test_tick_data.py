"""未聚合成交诊断的离线边界与对账测试。"""

import unittest

import real_data
from tick_data import (DAY, MAX_LOOKUP_DELAY_US, compare_totals, forward_lookup, load_aggregate_reference,
                       load_ticks, parse_tick, reconcile, render_report, response_summary, signed_response)


START, END = real_data.day_bounds(DAY)


def row(trade_id=1, timestamp=START, price="100", quantity="2", quote="200", maker="false"):
    return [str(trade_id), price, quantity, quote, str(timestamp), maker, "true"]


class TickParserTests(unittest.TestCase):
    def test_sign_and_microseconds(self):
        self.assertEqual(parse_tick(row()).sign, 1)
        self.assertEqual(parse_tick(row(maker="true")).sign, -1)
        self.assertEqual(parse_tick(row(timestamp=START + 123)).timestamp_us, START + 123)

    def test_invalid_values_and_schema(self):
        for change in ({"price": "nan"}, {"quantity": "inf"}, {"quote": "0"}, {"quantity": "-1"}, {"maker": "yes"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_tick(row(**change))
        with self.assertRaises(ValueError):
            parse_tick(row()[:-1])

    def test_quote_product_consistency(self):
        self.assertEqual(parse_tick(row(quote="200.000000001")).quantity, 2)
        with self.assertRaisesRegex(ValueError, "quoteQty"):
            parse_tick(row(quote="201"))

    def test_milliseconds_and_outside_date_rejected(self):
        with self.assertRaisesRegex(ValueError, "毫秒"):
            parse_tick(row(timestamp=START // 1000))
        with self.assertRaises(ValueError):
            parse_tick(row(timestamp=END))

    def test_duplicate_order_and_time_rejected(self):
        for ticks in ([parse_tick(row()), parse_tick(row())],
                      [parse_tick(row(2)), parse_tick(row(1))],
                      [parse_tick(row(1, START + 2)), parse_tick(row(2, START + 1))]):
            with self.subTest(ticks=ticks), self.assertRaises(ValueError):
                load_ticks(ticks)

    def test_equal_timestamps_and_id_gaps_reported(self):
        _, quality, _ = load_ticks([parse_tick(row(1)), parse_tick(row(3))])
        self.assertEqual(quality["adjacent_equal_timestamps"], 1)
        self.assertEqual(quality["missing_trade_ids"], 1)

    def test_utc_minute_boundary(self):
        _, _, minutes = load_ticks([parse_tick(row(1, START + 59_999_999)), parse_tick(row(2, START + 60_000_000))])
        self.assertEqual(len(minutes), 2)
        self.assertTrue(all(x["trade_count"] == 1 for x in minutes.values()))


class ResponseTests(unittest.TestCase):
    def test_exact_target_first_of_timestamp_ties(self):
        times = [START, START + 999_999, START + 1_000_000, START + 1_000_000]
        self.assertEqual(forward_lookup(times, START, 1, END), (2, "accepted", 0))

    def test_max_delay_inclusive_and_exceeded(self):
        self.assertEqual(forward_lookup([START + 2_000_000], START, 1, END), (0, "accepted", MAX_LOOKUP_DELAY_US))
        index, reason, delay = forward_lookup([START + 2_000_001], START, 1, END)
        self.assertIsNone(index)
        self.assertEqual(reason, "lookup_delay_exceeded")
        self.assertEqual(delay, 1_000_001)

    def test_day_boundary_and_no_future(self):
        self.assertEqual(forward_lookup([END - 1], END - 1_000_000, 1, END)[1], "outside_utc_day")
        self.assertEqual(forward_lookup([START], START, 1, END)[1], "no_future_trade")

    def test_signed_response(self):
        self.assertAlmostEqual(signed_response(100, 101, 1), 100)
        self.assertAlmostEqual(signed_response(100, 99, -1), 100)
        self.assertAlmostEqual(signed_response(100, 101, -1), -100)
        self.assertEqual(signed_response(100, 100, 1), 0)

    def test_all_events_accounted_zero_responses_retained(self):
        data, _, _ = load_ticks([parse_tick(row(1, START)), parse_tick(row(2, START + 1_000_000)),
                                  parse_tick(row(3, END - 1, maker="true"))])
        results = response_summary(data)
        for item in results:
            self.assertEqual(item["events"], item["retained"] + item["excluded_outside_utc_day"] +
                             item["excluded_no_future_trade"] + item["excluded_lookup_delay_exceeded"])
        one = next(x for x in results if x["horizon_seconds"] == 1 and x["scope"] == "all")
        self.assertEqual(one["retained"], 1)
        self.assertEqual(one["zero_count"], 1)

    def test_nonzero_event_and_eth_weighted_means(self):
        ticks = [parse_tick(row(1, START, "100", "1", "100")),
                 parse_tick(row(2, START + 500_000, "200", "3", "600", "true")),
                 parse_tick(row(3, START + 1_500_000, "110", "9", "990"))]
        data, _, _ = load_ticks(ticks)
        one = next(item for item in response_summary(data) if item["scope"] == "all" and item["horizon_seconds"] == 1)
        # 前两笔分别 +1000、+4500 bp；第三笔无后续，被排除。
        self.assertEqual(one["retained"], 2)
        self.assertAlmostEqual(one["event_weighted_mean_response_bps"], 2750)
        self.assertAlmostEqual(one["eth_volume_weighted_mean_response_bps"], 3625)

    def test_report_displays_empty_horizon(self):
        data, quality, _ = load_ticks([parse_tick(row())])
        result = {"tick_quality": quality, "responses": response_summary(data),
                  "reconciliation": {"check_count": 0, "failed_count": 0, "daily_checks": []}, "sources": []}
        report = render_report(result)
        self.assertIn("| 1 | all | 0 | — | — | — | — |", report)


class ReconciliationTests(unittest.TestCase):
    def fixture(self):
        ticks = [parse_tick(row(100, quantity="1", quote="100")), parse_tick(row(101, quantity="2", quote="200")),
                 parse_tick(row(102, START + 60_000_000, price="101", quantity="1", quote="101", maker="true"))]
        agg = [real_data.parse_trade(["1", "100", "3", "100", "101", str(START), "false", "true"], DAY),
               real_data.parse_trade(["2", "101", "1", "102", "102", str(START + 60_000_000), "true", "true"], DAY)]
        _, quality, minutes = load_ticks(ticks)
        reference, reference_minutes = load_aggregate_reference(agg)
        return quality, minutes, reference, reference_minutes

    def test_two_ticks_one_aggregate_conserve_values(self):
        quality, minutes, reference, reference_minutes = self.fixture()
        self.assertEqual(quality["rows"], 3)
        self.assertEqual(reference["aggregate_rows"], 2)
        self.assertEqual(reference["totals"]["trade_count"], 3)
        self.assertTrue(reconcile(quality, minutes, reference, reference_minutes)["passed"])

    def test_mismatch_is_reported_not_adjusted(self):
        quality, minutes, reference, reference_minutes = self.fixture()
        reference["totals"]["buy_quantity_eth"] += .1
        check = reconcile(quality, minutes, reference, reference_minutes)
        self.assertFalse(check["passed"])
        self.assertEqual(check["failed_count"], 1)
        self.assertEqual(check["failures"][0]["field"], "buy_quantity_eth")

    def test_missing_minute_is_failure(self):
        quality, minutes, reference, reference_minutes = self.fixture()
        reference_minutes.pop(next(iter(reference_minutes)))
        check = reconcile(quality, minutes, reference, reference_minutes)
        self.assertFalse(check["minute_keys_match"])
        self.assertFalse(check["passed"])


if __name__ == "__main__":
    unittest.main()
