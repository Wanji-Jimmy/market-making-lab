import copy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import equity_data as data


def fixture_row(minute, *, asset="EBAY", date="2021-01-04"):
    row = dict.fromkeys(data.HEADERS, "0")
    clock = f"{minute // 60:02d}:{minute % 60:02d}"
    row.update(Date=date.replace("-", ""), Ticker=asset, SecId="1", TimeBarStart=clock,
        OpenBarTime=clock + ":00.000000000", CloseBarTime=clock + ":59.999999999",
        CloseBidPrice="99", CloseAskPrice="101", CloseBidSize="10", CloseAskSize="20",
        NBBOQuoteCount="1", TotalQuoteCount="1", ExchangesBidCount="1", ExchangesAskCount="1",
        LastTradePrice="1000", TotalVolumeWeightPrice="500")
    return row


def fixture_session(**kwargs):
    return [fixture_row(minute, **kwargs) for minute in range(570, 960)]


def validate(rows, *, asset="EBAY", date="2021-01-04", headers=data.HEADERS):
    return data.validate_equity_rows(rows, headers=headers, asset=asset, date=date)


def fixture_sources(root):
    records, coverage = [], []
    for asset in data.ASSETS:
        for day in data.SESSION_DATES:
            filename = f"{asset}_{day.replace('-', '')}.csv"
            path = root / filename
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=data.HEADERS, lineterminator="\n")
                writer.writeheader()
                writer.writerows(fixture_session(asset=asset, date=day))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            records.append(dict(symbol=asset, date=day.replace("-", ""), sha256=digest,
                bytes=path.stat().st_size, member=f"{day.replace('-', '')}/{asset[0]}/{asset}.csv"))
            coverage.append(dict(asset=asset, date=day, file=filename, sha256=digest))
    manifest = root / "acquisition_manifest.json"
    manifest.write_text(json.dumps(dict(source="https://algoseek.com/ml-for-trading/", files=records)))
    (root / "equity_results.json").write_text(json.dumps(dict(coverage=coverage)))
    return manifest


class EquityQuoteValidationTests(unittest.TestCase):
    def test_midpoint_uses_quote_fields_not_trade_or_vwap_and_is_available_at_minute_end(self):
        checked = validate(fixture_session())
        day, audit = checked["day"], checked["audit"]
        self.assertTrue(day["structurally_valid"])
        self.assertEqual(len(day["mids"]), 390)
        self.assertIsInstance(day["mids"], tuple)
        self.assertEqual(set(day["mids"]), {100.0})
        self.assertEqual(set(day["spreads"]), {2.0})
        self.assertTrue(all(day["valid_mask"]))
        self.assertEqual(audit["first_close_time_utc"], "2021-01-04T14:30:59.999999999Z")
        self.assertEqual(audit["last_close_time_utc"], "2021-01-04T20:59:59.999999999Z")

    def test_exact_0930_inclusion_and_1600_exclusion(self):
        rows = [fixture_row(569)] + fixture_session() + [fixture_row(960)]
        rows[0]["CloseBidPrice"] = rows[-1]["CloseBidPrice"] = "-99"
        checked = validate(rows)
        self.assertTrue(checked["day"]["structurally_valid"])
        self.assertEqual(checked["audit"]["rows_regular"], 390)
        self.assertEqual(checked["audit"]["outside_regular_rows"], 2)
        self.assertEqual(checked["audit"]["invalid_quote_minutes"], 0)

    def test_timezone_is_new_york_including_dst_and_fixed_sample_is_pre_dst(self):
        self.assertEqual(len(data.SESSION_DATES), 40)
        self.assertEqual((data.SESSION_DATES[0], data.SESSION_DATES[-1]), ("2021-01-04", "2021-03-02"))
        self.assertNotIn("2021-01-18", data.SESSION_DATES)
        self.assertNotIn("2021-02-15", data.SESSION_DATES)
        checked = validate(fixture_session(date="2021-03-15"), date="2021-03-15")
        self.assertEqual(checked["audit"]["first_close_time_utc"], "2021-03-15T13:30:59.999999999Z")

    def test_bad_nanosecond_boundaries_or_identity_make_session_structurally_invalid(self):
        for field, value, reason in (("CloseBarTime", "09:30:59.99999999", "bar_boundary_timestamp_not_exact_nanoseconds"),
                ("OpenBarTime", "09:30:00", "bar_boundary_timestamp_not_exact_nanoseconds"),
                ("Ticker", "LULU", "wrong_asset_or_date"),
                ("Date", "20210105", "wrong_asset_or_date"),
                ("TimeBarStart", "9:30", "invalid_minute_start_clock"),
                ("SecId", "1.5", "invalid_security_identifier")):
            rows = fixture_session()
            rows[0][field] = value
            with self.subTest(field=field):
                checked = validate(rows)
                self.assertFalse(checked["day"]["structurally_valid"])
                self.assertIn(reason, checked["audit"]["structural_errors"])

    def test_duplicate_reordered_and_missing_minutes_are_never_repaired_for_planned_use(self):
        duplicate = fixture_session()
        duplicate.insert(21, duplicate[20].copy())
        reordered = fixture_session()
        reordered[20], reordered[21] = reordered[21], reordered[20]
        missing = fixture_session()
        missing.pop(20)
        for rows, reason in ((duplicate, "duplicate_regular_minute"),
                (reordered, "regular_minutes_not_strictly_increasing"), (missing, "missing_regular_minutes")):
            with self.subTest(reason=reason):
                checked = validate(rows)
                self.assertFalse(checked["day"]["structurally_valid"])
                self.assertIn(reason, checked["audit"]["structural_errors"])
        self.assertFalse(validate(missing)["day"]["valid_mask"][20])

    def test_crossed_quote_masks_only_its_minute_and_locked_quote_remains_valid(self):
        rows = fixture_session()
        rows[20]["CloseAskPrice"] = "98"
        rows[21]["CloseAskPrice"] = "99"
        checked = validate(rows)
        day, audit = checked["day"], checked["audit"]
        self.assertTrue(day["structurally_valid"])
        self.assertFalse(day["valid_mask"][20])
        self.assertIsNone(day["mids"][20])
        self.assertTrue(day["valid_mask"][21])
        self.assertTrue(day["locked_mask"][21])
        self.assertEqual(day["spreads"][21], 0)
        self.assertEqual((audit["crossed_quote_minutes"], audit["locked_quote_minutes"]), (1, 1))

    def test_zero_quote_updates_are_valid_carried_state_and_bad_size_counts_are_masked(self):
        rows = fixture_session()
        rows[0]["NBBOQuoteCount"] = "0"
        checked = validate(rows)
        self.assertTrue(checked["day"]["valid_mask"][0])
        self.assertEqual(checked["audit"]["zero_nbbo_update_valid_minutes"], 1)
        for field, value in (("CloseBidSize", "-1"), ("CloseAskSize", "NaN"),
                ("NBBOQuoteCount", "1.5"), ("CloseBidPrice", "inf"), ("CloseAskPrice", "0")):
            bad = copy.deepcopy(rows)
            bad[5][field] = value
            with self.subTest(field=field):
                checked = validate(bad)
                self.assertTrue(checked["day"]["structurally_valid"])
                self.assertFalse(checked["day"]["valid_mask"][5])
                self.assertEqual(checked["audit"]["invalid_quote_minutes"], 1)

    def test_exact_header_and_row_width_are_required(self):
        headers = list(data.HEADERS)
        headers[0], headers[1] = headers[1], headers[0]
        self.assertFalse(validate(fixture_session(), headers=headers)["day"]["structurally_valid"])
        rows = fixture_session()
        rows[0][None] = ["extra"]
        checked = validate(rows)
        self.assertFalse(checked["day"]["structurally_valid"])
        self.assertIn("wrong_column_count_or_missing_value", checked["audit"]["structural_errors"])


class ExistingEquityProvenanceTests(unittest.TestCase):
    def test_explicit_quality_file_must_exist_and_contain_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "acquisition_manifest.json").write_text('{"files": []}')
            (root / "equity_results.json").write_text('{"coverage": []}')
            quality = root / "custom-quality.json"
            with self.assertRaisesRegex(FileNotFoundError, "--quality-report"):
                data.load_existing_equity_minutes(root, quality_path=quality)
            quality.write_text("[]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                data.load_existing_equity_minutes(root, quality_path=quality)

    def test_all_eighty_provenance_entries_are_checked_and_inputs_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_sources(root)
            before = {p.name: data._sha256(p) for p in root.iterdir()}
            result = data.load_existing_equity_minutes(root)
            self.assertEqual(len(result["days"]), 80)
            self.assertEqual(result["audit"]["structurally_valid_sessions"], 80)
            self.assertEqual(result["audit"]["regular_rows"], 31200)
            self.assertEqual(result["audit"]["network_requests"], 0)
            self.assertEqual(result["audit"]["new_legal_acceptances"], 0)
            self.assertTrue(all(s["acquisition_checksum_verified"] and s["previous_coverage_checksum_verified"] for s in result["sources"]))
            self.assertEqual(before, {p.name: data._sha256(p) for p in root.iterdir()})

    def test_changed_csv_and_disagreeing_provenance_hashes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_sources(root)
            path = root / "EBAY_20210104.csv"
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ValueError, "CSV checksum mismatch"):
                data.load_existing_equity_minutes(root)
            path.write_bytes(original)
            coverage = json.loads((root / "equity_results.json").read_text())
            coverage["coverage"][0]["sha256"] = "0" * 64
            (root / "equity_results.json").write_text(json.dumps(coverage))
            with self.assertRaisesRegex(ValueError, "provenance|checksum disagreement"):
                data.load_existing_equity_minutes(root)

    def test_missing_file_is_retained_as_a_structurally_invalid_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_sources(root)
            (root / "EBAY_20210104.csv").unlink()
            result = data.load_existing_equity_minutes(root)
            self.assertEqual(len(result["days"]), 80)
            day = result["days"][0]
            self.assertFalse(day["structurally_valid"])
            self.assertFalse(any(day["valid_mask"]))
            self.assertEqual(result["audit"]["structurally_valid_sessions"], 79)
            self.assertIn("missing_file", result["sources"][0]["structural_errors"])


if __name__ == "__main__":
    unittest.main()
