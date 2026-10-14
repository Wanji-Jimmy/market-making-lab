"""公开数据模块的离线检查，不访问网络。"""

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from real_data import (GROUPS, MINUTE_US, Trade, aggregate_day, day_bounds,
                       group_summary, imbalance_group, parse_trade,
                       read_archive, verify_checksum)


DAY = "2025-09-01"
START = day_bounds(DAY)[0]


def row(trade_id=1, timestamp=START, price="100", quantity="2", maker="true"):
    return [str(trade_id), price, quantity, "100", "101", str(timestamp), maker, "true"]


class ParseTests(unittest.TestCase):
    def test_buyer_maker_means_aggressor_sell(self):
        self.assertEqual(parse_trade(row(maker="true"), DAY).aggressor_sign, -1)
        self.assertEqual(parse_trade(row(maker="false"), DAY).aggressor_sign, 1)

    def test_microseconds_preserved(self):
        value = START + 123456
        self.assertEqual(parse_trade(row(timestamp=value), DAY).timestamp_us, value)

    def test_milliseconds_rejected(self):
        with self.assertRaisesRegex(ValueError, "毫秒"):
            parse_trade(row(timestamp=START // 1000), DAY)

    def test_next_day_rejected(self):
        with self.assertRaisesRegex(ValueError, "UTC"):
            parse_trade(row(timestamp=day_bounds(DAY)[1]), DAY)

    def test_invalid_positive_values(self):
        for price, quantity in [("0", "1"), ("100", "-1"), ("nan", "1"), ("1", "inf")]:
            with self.subTest(price=price, quantity=quantity), self.assertRaises(ValueError):
                parse_trade(row(price=price, quantity=quantity), DAY)

    def test_invalid_flag_and_columns(self):
        with self.assertRaises(ValueError):
            parse_trade(row(maker="yes"), DAY)
        with self.assertRaises(ValueError):
            parse_trade(row()[:-1], DAY)

    def test_all_bin_boundaries(self):
        for value, group in [(-1, 0), (-.5, 1), (-.1, 2), (0, 2), (.1, 2), (.5, 3), (1, 4)]:
            self.assertEqual(imbalance_group(value), GROUPS[group])
        for value in [-1.1, 1.1, float("nan")]:
            with self.assertRaises(ValueError):
                imbalance_group(value)


class AggregationTests(unittest.TestCase):
    def test_volume_direction_boundary_and_forward_return(self):
        trades = [parse_trade(row(1, START, "100", "3", "false"), DAY),
                  parse_trade(row(2, START + MINUTE_US - 1, "102", "1", "true"), DAY),
                  parse_trade(row(3, START + MINUTE_US, "99", "1", "true"), DAY)]
        rows, quality = aggregate_day(trades, DAY)
        self.assertEqual(rows[0]["aggregate_rows"], 2)
        self.assertEqual(rows[0]["imbalance"], .5)
        self.assertAlmostEqual(rows[0]["next_minute_return_bps"], (99 / 102 - 1) * 10000)
        self.assertAlmostEqual(rows[0]["same_minute_return_bps"], 200)
        self.assertIsNone(rows[1]["next_minute_return_bps"])
        self.assertEqual(quality["minutes_with_next_return"], 1)

    def test_missing_minutes_not_bridged(self):
        trades = [parse_trade(row(1, START), DAY), parse_trade(row(2, START + 2 * MINUTE_US), DAY)]
        rows, quality = aggregate_day(trades, DAY)
        self.assertEqual(quality["minutes_with_next_return"], 0)
        self.assertTrue(all(x["next_minute_return_bps"] is None for x in rows))

    def test_duplicate_and_out_of_order_ids_rejected(self):
        for second_id in [1, 0]:
            with self.subTest(second_id=second_id), self.assertRaises(ValueError):
                aggregate_day([parse_trade(row(), DAY), parse_trade(row(second_id), DAY)], DAY)

    def test_out_of_order_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "时间戳倒序"):
            aggregate_day([parse_trade(row(1, START + 1), DAY), parse_trade(row(2, START), DAY)], DAY)

    def test_id_gaps_are_reported(self):
        _, quality = aggregate_day([parse_trade(row(1), DAY), parse_trade(row(5), DAY)], DAY)
        self.assertEqual(quality["id_gap_events"], 1)
        self.assertEqual(quality["missing_aggregate_ids"], 3)

    def test_terminal_is_excluded_from_next_groups(self):
        rows, _ = aggregate_day([parse_trade(row(), DAY)], DAY)
        groups = group_summary(rows, DAY)
        self.assertEqual(groups[0]["minutes"], 1)
        self.assertEqual(groups[0]["next_return_count"], 0)
        self.assertIsNone(groups[0]["next_minute_mean_bps"])


class ArchiveTests(unittest.TestCase):
    def test_checksum_valid_and_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.zip"
            path.write_bytes(b"data")
            checksum = hashlib.sha256(b"data").hexdigest() + "  data.zip"
            self.assertEqual(verify_checksum(path, checksum), hashlib.sha256(b"data").hexdigest())
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "不匹配"):
                verify_checksum(path, checksum)

    def test_checksum_wrong_filename_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.zip"
            path.write_bytes(b"data")
            with self.assertRaises(ValueError):
                verify_checksum(path, hashlib.sha256(b"data").hexdigest() + "  other.zip")

    def test_zip_exact_member_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.zip"
            with zipfile.ZipFile(path, "w") as bundle:
                bundle.writestr(f"ETHUSDT-aggTrades-{DAY}.csv", ",".join(row()) + "\n")
            self.assertEqual(len(list(read_archive(path, DAY))), 1)
            with zipfile.ZipFile(path, "w") as bundle:
                bundle.writestr("../outside.csv", ",".join(row()) + "\n")
            with self.assertRaisesRegex(ValueError, "预期"):
                list(read_archive(path, DAY))


if __name__ == "__main__":
    unittest.main()
