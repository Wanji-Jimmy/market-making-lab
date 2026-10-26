"""Read existing equity minute-end quote snapshots; never fetch or copy data.

The fixed local AlgoSeek companion sample contains EBAY and LULU, forty US
sessions in early 2021. Prices here are minute-end NBBO bid/ask snapshots and
their midpoint, not trade closes, VWAP, event-level books or hypothetical fills.
"""

from collections import Counter
import csv
from datetime import date as Date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = ROOT.parent / "data" / "equities"
DEFAULT_COVERAGE = ROOT.parent / "data" / "equity_coverage.json"
ASSETS = ("EBAY", "LULU")
SESSION_TIMEZONE = "America/New_York"
REGULAR_START = 9 * 60 + 30
REGULAR_MINUTES = 390
_START, _END = Date(2021, 1, 4), Date(2021, 3, 2)
_HOLIDAYS = {Date(2021, 1, 18), Date(2021, 2, 15)}
SESSION_DATES = tuple(day.isoformat() for i in range((_END - _START).days + 1)
    if (day := _START + timedelta(days=i)).weekday() < 5 and day not in _HOLIDAYS)
HEADERS = (
    "Date", "SecId", "Ticker", "TimeBarStart", "OpenBarTime", "OpenBidPrice", "OpenBidSize", "OpenAskPrice", "OpenAskSize",
    "FirstTradeTime", "FirstTradePrice", "FirstTradeSize", "HighBidTime", "HighBidPrice", "HighBidSize", "HighAskTime", "HighAskPrice", "HighAskSize",
    "HighTradeTime", "HighTradePrice", "HighTradeSize", "LowBidTime", "LowBidPrice", "LowBidSize", "LowAskTime", "LowAskPrice", "LowAskSize",
    "LowTradeTime", "LowTradePrice", "LowTradeSize", "CloseBarTime", "CloseBidPrice", "CloseBidSize", "CloseAskPrice", "CloseAskSize",
    "LastTradeTime", "LastTradePrice", "LastTradeSize", "MinSpread", "MaxSpread", "CancelSize", "VolumeWeightPrice", "FinraVolumeWeightPrice",
    "TotalVolumeWeightPrice", "VolumeWeightPriceExcludePRP", "VolumeWeightSpread", "VolumeWeightSpreadExcludePRP", "TradeAtBid", "TradeAtBidCount",
    "TradeAtBidMid", "TradeAtBidMidCount", "TradeAtMid", "TradeAtMidCount", "TradeAtMidAsk", "TradeAtMidAskCount", "TradeAtAsk", "TradeAtAskCount",
    "TradeAtCrossOrLocked", "TradeAtCrossOrLockedCount", "Volume", "FinraVolume", "TotalVolume", "NBBOQuoteCount", "TotalQuoteCount",
    "ExchangesBidCount", "ExchangesAskCount", "ExchangeTradeCount", "FinraTradeCount", "TotalTrades", "OddLotTradeCount", "OddLotTotalShares",
    "PriorReferencePriceTradeCount", "PriorReferencePriceTradeShares", "UptickVolume", "DowntickVolume", "RepeatUptickVolume", "RepeatDowntickVolume",
    "UnknownTickVolume", "TradeToMidVolWeight", "TradeToMidVolWeightRelative", "TimeWeightSpread", "TimeWeightBid", "TimeWeightBidSize", "TimeWeightAsk",
    "TimeWeightAskSize", "SpreadValidTime", "RelativeSpreadAverage", "TradeCumulDistributionToBid", "RetailTRFBuySize", "RetailTRFSellSize")
QUOTE_INTEGER_FIELDS = ("CloseBidSize", "CloseAskSize", "NBBOQuoteCount", "TotalQuoteCount", "ExchangesBidCount", "ExchangesAskCount")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _uint(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+", value):
        raise ValueError("not an unsigned integer")
    return int(value)


def _positive(value):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("not numeric") from error
    if not math.isfinite(result) or result <= 0:
        raise ValueError("quote must be finite and positive")
    return result


def _clock(minute):
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _utc_end(day, minute):
    local = datetime.combine(day, datetime.min.time(), ZoneInfo(SESSION_TIMEZONE)) + timedelta(minutes=minute)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:") + "59.999999999Z"


def validate_equity_rows(rows, *, headers, asset, date):
    """Validate one session and preserve all 390 regular-session minute slots.

    ``rows`` contains dictionary rows from a header-bearing CSV. Structural
    errors mark the day unusable for planned analysis, without dropping it.
    Invalid quote content masks its minute; locked quotes remain valid and
    zero NBBO-update counts are allowed carried states, never guessed outages.
    Other trade/bar fields do not enter the midpoint or validity calculation.
    """
    if asset not in ASSETS:
        raise ValueError("unsupported equity asset")
    try:
        day = Date.fromisoformat(date)
    except (TypeError, ValueError) as error:
        raise ValueError("date must be an ISO date") from error
    if not isinstance(date, str) or date != day.isoformat():
        raise ValueError("date must be canonical YYYY-MM-DD")
    structural, invalid = Counter(), Counter()
    examples = []
    values = {}
    seen = set()
    security_ids = set()
    previous_slot = None
    rows_all = rows_regular = outside = crossed = locked = zero_updates = 0
    if tuple(headers or ()) != HEADERS:
        structural["unexpected_90_column_header"] = 1

    def issue(counter, reason, row_number=None, slot=None):
        counter[reason] += 1
        if len(examples) < 12:
            examples.append(dict(reason=reason, row_number=row_number, minute_slot=slot))

    for row_number, row in enumerate(rows, 1):
        rows_all += 1
        if structural.get("unexpected_90_column_header"):
            continue
        if not isinstance(row, dict) or set(row) != set(HEADERS) \
                or any(not isinstance(value, str) for value in row.values()):
            issue(structural, "wrong_column_count_or_missing_value", row_number)
            continue
        if row["Ticker"] != asset or row["Date"] != day.strftime("%Y%m%d"):
            issue(structural, "wrong_asset_or_date", row_number)
            continue
        try:
            secid = _uint(row["SecId"])
            if secid <= 0:
                raise ValueError("nonpositive SecId")
        except ValueError:
            issue(structural, "invalid_security_identifier", row_number)
            continue
        security_ids.add(secid)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", row["TimeBarStart"]):
            issue(structural, "invalid_minute_start_clock", row_number)
            continue
        hour, minute = map(int, row["TimeBarStart"].split(":"))
        clock = hour * 60 + minute
        if not REGULAR_START <= clock < REGULAR_START + REGULAR_MINUTES:
            outside += 1
            continue
        rows_regular += 1
        slot = clock - REGULAR_START
        if previous_slot is not None and slot <= previous_slot:
            issue(structural, "regular_minutes_not_strictly_increasing", row_number, slot)
        previous_slot = slot
        if slot in seen:
            issue(structural, "duplicate_regular_minute", row_number, slot)
            continue
        seen.add(slot)
        if row["OpenBarTime"] != _clock(clock) + ":00.000000000" \
                or row["CloseBarTime"] != _clock(clock) + ":59.999999999":
            issue(structural, "bar_boundary_timestamp_not_exact_nanoseconds", row_number, slot)
        quote_errors = []
        try:
            bid, ask = _positive(row["CloseBidPrice"]), _positive(row["CloseAskPrice"])
            if bid > ask:
                crossed += 1
                quote_errors.append("crossed_close_quote")
            midpoint = (bid + ask) / 2
            if not math.isfinite(midpoint):
                quote_errors.append("nonfinite_midpoint")
        except ValueError:
            quote_errors.append("nonpositive_or_nonfinite_close_quote")
            bid = ask = midpoint = None
        try:
            integers = {field: _uint(row[field]) for field in QUOTE_INTEGER_FIELDS}
        except ValueError:
            quote_errors.append("invalid_close_size_or_quote_count")
            integers = None
        if quote_errors:
            for reason in quote_errors:
                issue(invalid, reason, row_number, slot)
            values[slot] = (None, None, None, None, False, False)
        else:
            is_locked = bid == ask
            locked += int(is_locked)
            zero_updates += int(integers["NBBOQuoteCount"] == 0)
            values[slot] = (bid, ask, midpoint, ask - bid, True, is_locked)
    missing = sorted(set(range(REGULAR_MINUTES)) - seen)
    if missing:
        structural["missing_regular_minutes"] = len(missing)
    if len(security_ids) != 1:
        structural["nonunique_or_missing_security_identifier"] = len(security_ids) or 1
    ordered = [values.get(slot, (None, None, None, None, False, False)) for slot in range(REGULAR_MINUTES)]
    result = dict(asset=asset, date=date, bids=tuple(v[0] for v in ordered),
        asks=tuple(v[1] for v in ordered), mids=tuple(v[2] for v in ordered),
        spreads=tuple(v[3] for v in ordered), valid_mask=tuple(v[4] for v in ordered),
        locked_mask=tuple(v[5] for v in ordered), structurally_valid=not structural)
    audit = dict(asset=asset, date=date, rows_all=rows_all, rows_regular=rows_regular,
        outside_regular_rows=outside, observed_regular_minutes=len(seen),
        missing_regular_minute_slots=missing, structurally_valid=not structural,
        structural_errors=dict(structural), invalid_quote_reasons=dict(invalid),
        invalid_quote_minutes=sum(not value for value in result["valid_mask"]),
        valid_quote_minutes=sum(result["valid_mask"]), crossed_quote_minutes=crossed,
        locked_quote_minutes=locked, zero_nbbo_update_valid_minutes=zero_updates,
        security_id_count=len(security_ids), security_ids=sorted(security_ids),
        first_error_examples=examples, timezone=SESSION_TIMEZONE,
        selected_window_local="09:30 <= minute start < 16:00",
        first_close_time_utc=_utc_end(day, REGULAR_START),
        last_close_time_utc=_utc_end(day, REGULAR_START + REGULAR_MINUTES - 1),
        close_timestamp_precision="nine decimal places (nanoseconds), validated as text",
        price_definition="(CloseBidPrice + CloseAskPrice)/2; minute-end NBBO midpoint, not LastTradePrice/VWAP",
        repair_policy="No sorting, deduplication, interpolation, carry reconstruction or stock/date replacement")
    return dict(day=result, audit=audit)


def _json_object(path, key):
    value = json.loads(Path(path).read_bytes())
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        raise ValueError(f"provenance JSON must contain {key} list")
    return value


def load_existing_equity_minutes(source_dir=None, manifest_path=None, coverage_path=None, quality_path=None):
    """Load eighty existing CSVs; check acquisition and prior-result SHA hashes.

    Structural failures remain in returned days/audit so the planned producer
    can explicitly reject incomplete coverage. A changed existing file's hash
    is an integrity error and raises before it is used. Raw files and original
    provenance metadata remain unmodified, with no network requests. Metadata
    may live separately from the raw CSVs. An explicitly named quality report
    is required to exist; the default adjacent quality report remains optional.
    """
    source_dir = (DEFAULT_SOURCE_DIR if source_dir is None else Path(source_dir)).expanduser().resolve()
    manifest_path = (ROOT.parent / "data" / "acquisition_manifest.json" if manifest_path is None and source_dir == DEFAULT_SOURCE_DIR.resolve()
                     else source_dir / "acquisition_manifest.json" if manifest_path is None
                     else Path(manifest_path)).expanduser().resolve()
    coverage_path = (DEFAULT_COVERAGE if coverage_path is None and source_dir == DEFAULT_SOURCE_DIR.resolve()
                     else source_dir / "equity_results.json" if coverage_path is None else Path(coverage_path)).expanduser().resolve()
    explicit_quality = quality_path is not None
    quality_path = (source_dir / "quality_report.json" if quality_path is None
                    else Path(quality_path)).expanduser().resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"stock CSV directory not found: {source_dir}; set --source-dir")
    for path, flag in ((manifest_path, "--acquisition-manifest"), (coverage_path, "--coverage-manifest")):
        if not path.is_file():
            raise FileNotFoundError(f"required provenance file not found: {path}; set {flag}")
    if (explicit_quality or quality_path.exists()) and not quality_path.is_file():
        raise FileNotFoundError(f"quality report not found or not a file: {quality_path}; check --quality-report")
    manifest_sha, coverage_sha = _sha256(manifest_path), _sha256(coverage_path)
    quality_sha = _sha256(quality_path) if quality_path.is_file() else None
    manifest, coverage = _json_object(manifest_path, "files"), _json_object(coverage_path, "coverage")
    quality = json.loads(quality_path.read_bytes()) if quality_sha is not None else {}
    if not isinstance(quality, dict):
        raise ValueError("quality report must contain a JSON object")
    expected_keys = {(asset, day) for asset in ASSETS for day in SESSION_DATES}
    acquisition, previous = {}, {}
    for item in manifest["files"]:
        if not isinstance(item, dict):
            raise ValueError("invalid acquisition provenance entry")
        raw_date = item.get("date", "")
        try:
            canonical_date = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        except (ValueError, TypeError):
            raise ValueError("invalid acquisition provenance date")
        key = item.get("symbol"), canonical_date
        if key in expected_keys:
            if key in acquisition:
                raise ValueError("duplicate acquisition provenance key")
            acquisition[key] = item
    for item in coverage["coverage"]:
        if not isinstance(item, dict):
            raise ValueError("invalid prior coverage entry")
        key = item.get("asset"), item.get("date")
        if key in expected_keys:
            if key in previous:
                raise ValueError("duplicate prior coverage key")
            previous[key] = item
    if set(acquisition) != expected_keys or set(previous) != expected_keys:
        raise ValueError("both provenance sources must cover all eighty fixed stock-days")
    days, sources = [], []
    for asset in ASSETS:
        for day in SESSION_DATES:
            item, prior = acquisition[asset, day], previous[asset, day]
            filename = f"{asset}_{day.replace('-', '')}.csv"
            path = source_dir / filename
            expected_sha = item.get("sha256")
            if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha) \
                    or expected_sha != prior.get("sha256") or prior.get("file") != filename:
                raise ValueError(f"acquisition/prior coverage checksum disagreement: {filename}")
            if not path.exists():
                checked = validate_equity_rows([], headers=HEADERS, asset=asset, date=day)
                checked["audit"]["structural_errors"]["missing_file"] = 1
                digest = None
            else:
                digest = _sha256(path)
                if digest != expected_sha:
                    raise ValueError(f"existing stock CSV checksum mismatch: {filename}")
                if "bytes" in item and (type(item["bytes"]) is not int or item["bytes"] != path.stat().st_size):
                    raise ValueError(f"existing stock CSV byte count mismatch: {filename}")
                with path.open(newline="", encoding="utf-8") as stream:
                    reader = csv.DictReader(stream)
                    checked = validate_equity_rows(reader, headers=reader.fieldnames, asset=asset, date=day)
                if _sha256(path) != digest:
                    raise RuntimeError("existing stock CSV changed during validation")
            days.append(checked["day"])
            sources.append(dict(**checked["audit"], filename=filename, file_path=str(path),
                sha256=digest, expected_sha256=expected_sha, bytes=path.stat().st_size if path.exists() else None,
                acquisition_checksum_verified=digest == expected_sha,
                previous_coverage_checksum_verified=digest == prior.get("sha256"),
                source_url=manifest.get("source"), archive_url=item.get("archive_url"),
                original_archive_member=item.get("member"), original_member_crc32=item.get("member_crc32"),
                downloaded_this_run=False, raw_data_copied=False))
    if _sha256(manifest_path) != manifest_sha or _sha256(coverage_path) != coverage_sha \
            or (quality_sha is not None and _sha256(quality_path) != quality_sha):
        raise RuntimeError("equity provenance metadata changed during validation")
    per_asset = {}
    for asset in ASSETS:
        selected = [source for source in sources if source["asset"] == asset]
        per_asset[asset] = dict(sessions=len(selected),
            structurally_valid_sessions=sum(s["structurally_valid"] for s in selected),
            regular_rows=sum(s["rows_regular"] for s in selected),
            valid_quote_minutes=sum(s["valid_quote_minutes"] for s in selected),
            invalid_quote_minutes=sum(s["invalid_quote_minutes"] for s in selected),
            crossed_quote_minutes=sum(s["crossed_quote_minutes"] for s in selected),
            locked_quote_minutes=sum(s["locked_quote_minutes"] for s in selected),
            zero_nbbo_update_valid_minutes=sum(s["zero_nbbo_update_valid_minutes"] for s in selected))
    audit = dict(assets=list(ASSETS), expected_asset_sessions=len(expected_keys), observed_asset_sessions=len(days),
        structurally_valid_sessions=sum(s["structurally_valid"] for s in sources),
        structurally_invalid_sessions=[dict(asset=s["asset"], date=s["date"], reasons=s["structural_errors"])
                                       for s in sources if not s["structurally_valid"]],
        regular_rows=sum(s["rows_regular"] for s in sources),
        invalid_quote_minutes=sum(s["invalid_quote_minutes"] for s in sources), per_asset=per_asset,
        session_dates=list(SESSION_DATES), first_date=SESSION_DATES[0], last_date=SESSION_DATES[-1],
        timezone=SESSION_TIMEZONE, regular_minutes=REGULAR_MINUTES,
        observation="Minute-end NBBO midpoint snapshots, not event quotes or observed maker fills",
        invalid_quote_rule="Retain all390 slots; invalid minute masked, locked quote valid, zero updates allowed",
        source_url=quality.get("source", manifest.get("source")),
        field_documentation=quality.get("field_documentation"), license_urls=quality.get("license_urls", []),
        original_licensing_status=quality.get("licensing_status", "not recorded"),
        corporate_action_note="Existing execution study reviewed no split/share-unit reset in this period; provider adjustment policy not independently certified here.",
        current_server_availability_verified=False, network_requests=0, new_legal_acceptances=0,
        raw_data_copied=False, original_metadata_modified=False)
    return dict(days=days, audit=audit, sources=sources,
        source_manifest=str(manifest_path), manifest_sha256=manifest_sha,
        source_coverage=str(coverage_path), coverage_sha256=coverage_sha,
        source_quality=str(quality_path) if quality_sha is not None else None,
        quality_sha256=quality_sha)
