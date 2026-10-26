"""Validate and reuse previously downloaded minute archives without networking.

The default inputs are eight existing spot OHLCV archives from a separate local
project. No data are downloaded, copied, filled, reordered or redistributed by
this module. Only complete UTC-day close-price tuples are returned. These are
minute candles, not BBO quotes, order-book events or executable maker prices.
"""

from collections import Counter, defaultdict
import calendar
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parent
EXISTING_PROJECT = ROOT.parent / "execution_timing_lab"
ASSETS = ("BTCUSDT", "ETHUSDT")
MONTHS = ("2026-06", "2026-07", "2026-08", "2026-09")
MINUTE_US = 60_000_000
DAY_US = 1440 * MINUTE_US
EXPECTED_COLUMNS = 12
MAX_MEMBER_BYTES = 50_000_000


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_us(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    value = value.astimezone(timezone.utc)
    return calendar.timegm(value.utctimetuple()) * 1_000_000 + value.microsecond


def _format_us(value):
    if value is None:
        return None
    seconds, micros = divmod(value, 1_000_000)
    return (datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=micros)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def _month_dates(month):
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must be YYYY-MM")
    try:
        first = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ValueError("invalid calendar month") from error
    return [first + timedelta(days=i) for i in range(calendar.monthrange(first.year, first.month)[1])]


def _integer_text(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+", value):
        raise ValueError("not an unsigned integer")
    return int(value)


def _finite(value, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("not a finite numeric value") from error
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError("numeric bounds violated")
    return result


def validate_minute_rows(rows, *, asset, month, as_of=None):
    """Validate headerless 12-column candles, returning complete daily closes.

    This public helper also supports tiny fixtures. ``rows`` is an iterable of
    twelve-string CSV rows. Open/close timestamps must be integer microseconds;
    close time is open+59,999,999 microseconds. Every calendar date in ``month``
    is audited against all 1,440 UTC minute opens, including days wholly absent.

    A row error with an identifiable in-month date excludes that whole day.
    Wrong column counts, unparseable/mis-unit timestamps or out-of-month rows
    cannot safely be assigned to an intended day, so exclude the entire month.
    No row is silently repaired or sorted. Future timestamps are excluded.
    """
    if asset not in ASSETS:
        raise ValueError("unsupported empirical asset")
    dates = _month_dates(month)
    as_of = datetime.now(timezone.utc) if as_of is None else as_of
    as_of_us = _utc_us(as_of)
    start_us = _utc_us(dates[0])
    end_us = _utc_us(dates[-1] + timedelta(days=1))
    daily_rows = defaultdict(list)
    rows_seen = Counter()
    day_errors = defaultdict(Counter)
    seen_times = defaultdict(set)
    unassigned_errors = Counter()
    first_examples = []
    previous_open = previous_date = None
    total_rows = duplicate_rows = 0

    def reject(reason, row_number, date=None):
        target = day_errors[date] if date is not None else unassigned_errors
        target[reason] += 1
        if len(first_examples) < 12:
            first_examples.append(dict(row_number=row_number, date=date, reason=reason))

    for row_number, row in enumerate(rows, 1):
        total_rows += 1
        if len(row) != EXPECTED_COLUMNS:
            reject("column_count_not_12", row_number)
            continue
        try:
            open_us = _integer_text(row[0])
        except ValueError:
            reject("open_time_not_integer_microseconds", row_number)
            continue
        # For this fixed 2026 study the magnitude check diagnoses accidental
        # milliseconds rather than interpreting them as plausible 1970 data.
        if not 100_000_000_000_000 <= open_us < 10_000_000_000_000_000:
            reject("open_time_wrong_unit_or_range", row_number)
            continue
        if not start_us <= open_us < end_us:
            reject("open_time_outside_declared_month", row_number)
            continue
        date = dates[(open_us - start_us) // DAY_US].date().isoformat()
        rows_seen[date] += 1
        valid = True
        if previous_open is not None and open_us <= previous_open:
            reject("timestamps_not_strictly_increasing", row_number, date)
            if previous_date != date:
                reject("timestamps_not_strictly_increasing", row_number, previous_date)
            valid = False
        previous_open, previous_date = open_us, date
        if open_us in seen_times[date]:
            duplicate_rows += 1
            reject("duplicate_minute_open", row_number, date)
            valid = False
        seen_times[date].add(open_us)
        if open_us % MINUTE_US:
            reject("open_time_not_utc_minute_boundary", row_number, date)
            valid = False
        try:
            close_us = _integer_text(row[6])
            if close_us != open_us + MINUTE_US - 1:
                raise ValueError("close boundary")
        except ValueError:
            reject("close_time_inconsistent", row_number, date)
            valid = False
            close_us = None
        if open_us > as_of_us or close_us is not None and close_us > as_of_us:
            reject("future_timestamp", row_number, date)
            valid = False
        try:
            opened, high, low, closed = (_finite(row[i], positive=True) for i in (1, 2, 3, 4))
            if not low <= min(opened, closed) <= max(opened, closed) <= high:
                raise ValueError("OHLC bounds")
        except ValueError:
            reject("invalid_ohlc", row_number, date)
            valid = False
            closed = None
        try:
            base, quote, taker_base, taker_quote = (_finite(row[i]) for i in (5, 7, 9, 10))
            _integer_text(row[8])
            _finite(row[11])
            if taker_base > base + 1e-9 * max(1.0, base) \
                    or taker_quote > quote + 1e-9 * max(1.0, quote):
                raise ValueError("taker volume exceeds total")
        except ValueError:
            reject("invalid_volume_trade_count_or_ignore", row_number, date)
            valid = False
        if valid:
            daily_rows[date].append((open_us, closed))

    valid_days, excluded_days = [], []
    total_missing = 0
    for day in dates:
        date = day.date().isoformat()
        expected = tuple(_utc_us(day) + i * MINUTE_US for i in range(1440))
        actual = tuple(value[0] for value in daily_rows[date])
        missing = len(set(expected) - set(actual))
        total_missing += missing
        reasons = dict(day_errors[date])
        if actual != expected:
            reasons["incomplete_or_noncanonical_1440_minute_grid"] = 1
        if unassigned_errors:
            reasons["unassigned_invalid_row_in_source_month"] = sum(unassigned_errors.values())
        if reasons:
            excluded_days.append(dict(asset=asset, date=date, rows_seen=rows_seen[date],
                valid_rows=len(actual), missing_minutes=missing, reasons=reasons))
        else:
            valid_days.append(dict(asset=asset, date=date,
                closes=tuple(value[1] for value in daily_rows[date])))
    first_valid = _utc_us(datetime.fromisoformat(valid_days[0]["date"]).replace(tzinfo=timezone.utc)) if valid_days else None
    last_valid = (_utc_us(datetime.fromisoformat(valid_days[-1]["date"]).replace(tzinfo=timezone.utc))
                  + DAY_US - 1) if valid_days else None
    audit = dict(asset=asset, month=month, rows_read=total_rows, expected_days=len(dates),
        valid_days=len(valid_days), excluded_days=excluded_days, valid_minutes=len(valid_days) * 1440,
        excluded_day_count=len(excluded_days), duplicate_rows=duplicate_rows,
        missing_validated_minutes=total_missing, unassigned_errors=dict(unassigned_errors),
        first_error_examples=first_examples, first_open_time_utc=_format_us(first_valid),
        last_close_time_utc=_format_us(last_valid), as_of_utc=as_of.astimezone(timezone.utc).isoformat(),
        timestamp_unit="microseconds", required_minutes_per_day=1440,
        repair_policy="No sorting, gap filling, deduplication or candle-value repair; reject whole invalid UTC days.")
    return dict(days=valid_days, audit=audit)


def _verify_sidecar(sidecar, archive_name, actual_sha):
    text = sidecar.read_text(encoding="ascii")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(f"checksum sidecar must contain one entry: {sidecar.name}")
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\r\n]+)", lines[0])
    if match is None or match.group(2) != archive_name:
        raise ValueError(f"checksum sidecar filename/format mismatch: {sidecar.name}")
    if match.group(1).lower() != actual_sha:
        raise ValueError(f"checksum sidecar digest mismatch: {sidecar.name}")


def load_existing_minutes(source_dir=None, manifest_path=None):
    """Return validated existing daily close tuples and complete local provenance.

    Defaults select exactly BTCUSDT/ETHUSDT monthly 1m ZIPs for Jun-Sep 2026.
    Extra cached files (including October) are never discovered or used. The
    original manifest is read only and its old 08:00-16:00 validation is not
    mistaken for this module's fresh full-day validation.
    """
    source_dir = (EXISTING_PROJECT / ".cache" if source_dir is None else Path(source_dir)).resolve()
    manifest_path = (source_dir.parent / "data" / "manifest.json" if manifest_path is None
                     else Path(manifest_path)).resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list):
        raise ValueError("existing source manifest must contain a sources list")
    selected = {}
    for source in manifest["sources"]:
        if not isinstance(source, dict):
            raise ValueError("source manifest contains a non-object entry")
        key = (source.get("asset"), source.get("month"))
        if key[0] in ASSETS and key[1] in MONTHS:
            if key in selected:
                raise ValueError(f"duplicate source manifest entry: {key}")
            selected[key] = source
    expected = {(asset, month) for asset in ASSETS for month in MONTHS}
    if set(selected) != expected:
        raise ValueError("source manifest does not contain all eight frozen archives")
    all_days, source_results = [], []
    as_of = datetime.now(timezone.utc)
    for asset in ASSETS:
        for month in MONTHS:
            source = selected[asset, month]
            archive_name = f"{asset}-1m-{month}.zip"
            member_name = f"{asset}-1m-{month}.csv"
            path = source_dir / archive_name
            digest = _sha256(path)
            expected_sha = source.get("sha256")
            if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha) \
                    or digest != expected_sha.lower():
                raise ValueError(f"source manifest checksum mismatch: {archive_name}")
            size = path.stat().st_size
            if "bytes" in source and (type(source["bytes"]) is not int or source["bytes"] != size):
                raise ValueError(f"source manifest byte count mismatch: {archive_name}")
            sidecar = source_dir / (archive_name + ".CHECKSUM")
            sidecar_sha = None
            if sidecar.exists():
                _verify_sidecar(sidecar, archive_name, digest)
                sidecar_sha = _sha256(sidecar)
            try:
                with ZipFile(path) as archive:
                    infos = archive.infolist()
                    if len(infos) != 1 or infos[0].filename != member_name or infos[0].is_dir() \
                            or infos[0].flag_bits & 1 or infos[0].file_size > MAX_MEMBER_BYTES:
                        raise ValueError(f"unexpected archive members, encryption or size: {archive_name}")
                    with archive.open(member_name) as binary, io.TextIOWrapper(binary, encoding="utf-8", newline="") as text:
                        validated = validate_minute_rows(csv.reader(text), asset=asset, month=month, as_of=as_of)
            except (BadZipFile, UnicodeError, csv.Error) as error:
                raise ValueError(f"unreadable archive: {archive_name}") from error
            if _sha256(path) != digest or _sha256(manifest_path) != manifest_sha:
                raise RuntimeError("existing source archive or manifest changed during validation")
            all_days.extend(validated["days"])
            source_results.append(dict(**validated["audit"], archive_name=archive_name,
                archive_path=str(path), member_name=member_name, member_bytes=infos[0].file_size,
                sha256=digest, bytes=size, manifest_checksum_verified=True,
                sidecar_checksum_verified=sidecar.exists(), sidecar_sha256=sidecar_sha,
                url=source.get("url"), checksum_url=source.get("checksum_url"),
                archive_retrieved_at=source.get("archive_retrieved_at"),
                checksum_retrieved_at=source.get("checksum_retrieved_at"),
                prior_validation_scope=source.get("validation_scope"), downloaded_this_run=False))
    all_days.sort(key=lambda day: (day["asset"], day["date"]))
    excluded = [day for source in source_results for day in source["excluded_days"]]
    dates = sorted({day["date"] for day in all_days})
    audit = dict(expected_asset_days=sum(source["expected_days"] for source in source_results),
        valid_asset_days=len(all_days), excluded_asset_days=len(excluded), excluded_days=excluded,
        source_archive_count=len(source_results), total_rows_read=sum(source["rows_read"] for source in source_results),
        valid_minutes=len(all_days) * 1440, assets=list(ASSETS), months=list(MONTHS),
        first_date_utc=dates[0] if dates else None, last_date_utc=dates[-1] if dates else None,
        required_minutes_per_day=1440, timestamp_unit="microseconds",
        observation="UTC-day tuples of 1-minute candle close prices; neither quotes nor fills.",
        full_day_validation_performed_now=True, network_requests=0, new_legal_acceptances=0,
        raw_data_copied=False, original_manifest_modified=False)
    return dict(days=all_days, audit=audit, sources=source_results,
                source_manifest=str(manifest_path), manifest_sha256=manifest_sha)
