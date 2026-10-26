import calendar
import copy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

import empirical_data as data


AS_OF = datetime(2026, 10, 26, tzinfo=timezone.utc)


def fixture_day(date="2026-06-01"):
    start = calendar.timegm(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).utctimetuple()) * 1_000_000
    return [[str(start + i * data.MINUTE_US), "100", "101", "99", "100.5", "10",
             str(start + (i + 1) * data.MINUTE_US - 1), "1000", "2", "4", "400", "0"]
            for i in range(1440)]


def validate(rows, **kwargs):
    return data.validate_minute_rows(rows, asset="BTCUSDT", month="2026-06", as_of=AS_OF, **kwargs)


def fixture_sources(root):
    cache = root / ".cache"
    cache.mkdir()
    sources = []
    for asset in data.ASSETS:
        for month in data.MONTHS:
            archive_name, member_name = f"{asset}-1m-{month}.zip", f"{asset}-1m-{month}.csv"
            content = io.StringIO(newline="")
            csv.writer(content, lineterminator="\n").writerows(fixture_day(month + "-01"))
            archive = cache / archive_name
            with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
                zipped.writestr(member_name, content.getvalue())
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            (cache / (archive_name + ".CHECKSUM")).write_text(f"{digest}  {archive_name}\n")
            url = f"https://data.binance.vision/data/spot/monthly/klines/{asset}/1m/{archive_name}"
            sources.append(dict(asset=asset, month=month, sha256=digest, bytes=archive.stat().st_size,
                url=url, checksum_url=url + ".CHECKSUM", validation_scope="08:00-16:00 UTC"))
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(dict(sources=sources)))
    return cache, manifest


class MinuteValidationTests(unittest.TestCase):
    def test_complete_day_returns_frozen_ordered_closes_and_full_month_qa(self):
        result = validate(fixture_day())
        self.assertEqual(len(result["days"]), 1)
        day = result["days"][0]
        self.assertEqual((day["asset"], day["date"]), ("BTCUSDT", "2026-06-01"))
        self.assertIsInstance(day["closes"], tuple)
        self.assertEqual(len(day["closes"]), 1440)
        self.assertTrue(all(value == 100.5 for value in day["closes"]))
        self.assertEqual(result["audit"]["expected_days"], 30)
        self.assertEqual(result["audit"]["excluded_day_count"], 29)
        self.assertEqual(result["audit"]["first_open_time_utc"], "2026-06-01T00:00:00.000000Z")
        self.assertEqual(result["audit"]["last_close_time_utc"], "2026-06-01T23:59:59.999999Z")

    def test_missing_and_duplicate_minutes_exclude_day_without_fill_or_dedup(self):
        missing = fixture_day()
        missing.pop(500)
        result = validate(missing)
        self.assertEqual(result["days"], [])
        self.assertEqual(result["audit"]["excluded_days"][0]["missing_minutes"], 1)
        duplicated = fixture_day()
        duplicated.insert(501, duplicated[500].copy())
        result = validate(duplicated)
        self.assertEqual(result["days"], [])
        self.assertEqual(result["audit"]["duplicate_rows"], 1)
        self.assertIn("duplicate_minute_open", result["audit"]["excluded_days"][0]["reasons"])

    def test_reordered_rows_are_rejected_rather_than_sorted(self):
        rows = fixture_day()
        rows[500], rows[501] = rows[501], rows[500]
        result = validate(rows)
        self.assertEqual(result["days"], [])
        self.assertIn("timestamps_not_strictly_increasing", result["audit"]["excluded_days"][0]["reasons"])

    def test_millisecond_units_out_of_month_and_headers_fail_closed_for_source_month(self):
        for mutate, reason in (
            (lambda row: row.__setitem__(0, str(int(row[0]) // 1000)), "open_time_wrong_unit_or_range"),
            (lambda row: row.__setitem__(0, fixture_day("2026-07-01")[0][0]), "open_time_outside_declared_month"),
            (lambda row: row.__setitem__(0, "open_time"), "open_time_not_integer_microseconds"),
            (lambda row: row.pop(), "column_count_not_12"),
        ):
            rows = fixture_day()
            mutate(rows[100])
            with self.subTest(reason=reason):
                result = validate(rows)
                self.assertEqual(result["days"], [])
                self.assertIn(reason, result["audit"]["unassigned_errors"])
                self.assertTrue(all("unassigned_invalid_row_in_source_month" in day["reasons"]
                                    for day in result["audit"]["excluded_days"]))

    def test_ohlc_finite_bounds_volume_and_close_time_errors_exclude_only_affected_day(self):
        for column, value, reason in (
            (1, "NaN", "invalid_ohlc"), (4, "0", "invalid_ohlc"),
            (2, "99.5", "invalid_ohlc"), (5, "-1", "invalid_volume_trade_count_or_ignore"),
            (9, "11", "invalid_volume_trade_count_or_ignore"),
            (10, "1001", "invalid_volume_trade_count_or_ignore"),
            (8, "1.5", "invalid_volume_trade_count_or_ignore"),
            (6, "1", "close_time_inconsistent"),
        ):
            rows = fixture_day() + fixture_day("2026-06-02")
            rows[50][column] = value
            with self.subTest(column=column, value=value):
                result = validate(rows)
                self.assertEqual([day["date"] for day in result["days"]], ["2026-06-02"])
                self.assertIn(reason, result["audit"]["excluded_days"][0]["reasons"])

    def test_future_minutes_are_never_returned(self):
        result = data.validate_minute_rows(fixture_day(), asset="BTCUSDT", month="2026-06",
            as_of=datetime(2026, 6, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(result["days"], [])
        self.assertIn("future_timestamp", result["audit"]["excluded_days"][0]["reasons"])
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            data.validate_minute_rows([], asset="BTCUSDT", month="2026-06", as_of=datetime(2026, 10, 26))


class ExistingArchiveTests(unittest.TestCase):
    def test_existing_archives_and_manifest_are_read_only_and_do_not_discover_other_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, manifest = fixture_sources(root)
            # An irrelevant file is deliberately invalid and must not be read.
            (cache / "BTCUSDT-1m-2026-10.zip").write_bytes(b"not a zip")
            before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
            result = data.load_existing_minutes(cache, manifest)
            self.assertEqual(len(result["days"]), 8)
            self.assertEqual(result["audit"]["source_archive_count"], 8)
            self.assertEqual(result["audit"]["expected_asset_days"], 244)
            self.assertEqual(result["audit"]["network_requests"], 0)
            self.assertEqual(result["audit"]["new_legal_acceptances"], 0)
            self.assertTrue(all(s["sidecar_checksum_verified"] for s in result["sources"]))
            self.assertTrue(all(s["prior_validation_scope"] == "08:00-16:00 UTC" for s in result["sources"]))
            self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()})

    def test_corrupt_manifest_checksum_is_rejected_before_parsing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            cache, manifest = fixture_sources(Path(directory))
            value = json.loads(manifest.read_text())
            value["sources"][0]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "manifest checksum mismatch"):
                data.load_existing_minutes(cache, manifest)

    def test_sidecar_hash_and_filename_must_both_match_when_present(self):
        for replacement, pattern in (("0" * 64 + "  BTCUSDT-1m-2026-06.zip\n", "digest mismatch"),
                ("0" * 64 + "  other.zip\n", "filename/format mismatch")):
            with tempfile.TemporaryDirectory() as directory:
                cache, manifest = fixture_sources(Path(directory))
                (cache / "BTCUSDT-1m-2026-06.zip.CHECKSUM").write_text(replacement)
                with self.assertRaisesRegex(ValueError, pattern):
                    data.load_existing_minutes(cache, manifest)

    def test_missing_sidecar_is_explicit_but_manifest_hash_remains_required(self):
        with tempfile.TemporaryDirectory() as directory:
            cache, manifest = fixture_sources(Path(directory))
            (cache / "BTCUSDT-1m-2026-06.zip.CHECKSUM").unlink()
            result = data.load_existing_minutes(cache, manifest)
            self.assertFalse(result["sources"][0]["sidecar_checksum_verified"])
            self.assertTrue(result["sources"][0]["manifest_checksum_verified"])

    def test_unexpected_archive_member_is_rejected_even_when_hashes_match(self):
        with tempfile.TemporaryDirectory() as directory:
            cache, manifest = fixture_sources(Path(directory))
            archive = cache / "BTCUSDT-1m-2026-06.zip"
            with ZipFile(archive, "w") as zipped:
                zipped.writestr("../unexpected.csv", "header")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            value = json.loads(manifest.read_text())
            value["sources"][0].update(sha256=digest, bytes=archive.stat().st_size)
            manifest.write_text(json.dumps(value))
            archive.with_name(archive.name + ".CHECKSUM").write_text(f"{digest}  {archive.name}\n")
            with self.assertRaisesRegex(ValueError, "unexpected archive members"):
                data.load_existing_minutes(cache, manifest)


if __name__ == "__main__":
    unittest.main()
