"""Frozen minute-close price-risk diagnostic; not a fill or PnL backtest.

Train-only thresholds are frozen before evaluation statistics. Complete UTC
days are the bootstrap unit and overlapping returns never cross midnight.
Historical data were already used elsewhere: no unseen/blind claim applies.
"""

import argparse
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import shlex
from statistics import NormalDist, fmean, stdev
import sys
import time


ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / "empirical_protocol.json"
SOURCE_FILES = ("empirical_research.py", "empirical_data.py", "empirical_protocol.json",
                "test_empirical_research.py", "test_empirical_data.py")
ASSETS = ("BTCUSDT", "ETHUSDT")
HORIZONS = (1, 3, 10)
RULES = ("unit_sigma", "gaussian95", "empirical95")
BPS = 10000.0
CI_METHOD = "noncircular 7-day moving-block percentile bootstrap; frozen training bound; pointwise 95%"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {filename: sha256(ROOT / filename) for filename in SOURCE_FILES}


def _integer(name, value, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def date_range(start, end):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    if start > end:
        raise ValueError("date range is reversed")
    return tuple((start + timedelta(days=i)).isoformat()
                 for i in range((end - start).days + 1))


def load_protocol():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if protocol["schema_version"] != 1 or tuple(protocol["assets"]) != ASSETS \
            or tuple(protocol["horizons_minutes"]) != HORIZONS:
        raise ValueError("unsupported empirical assets/horizons/schema")
    if tuple(protocol["calibration"]["rules"]) != RULES:
        raise ValueError("unsupported empirical rules")
    split = protocol["split"]
    expected = dict(training_start="2026-06-01", training_end="2026-07-31",
                    evaluation_start="2026-08-01", evaluation_end="2026-09-30",
                    training_days=61, evaluation_days=61)
    if split != expected:
        raise ValueError("the chronological empirical split changed")
    if protocol["data"]["required_minutes_per_day"] != 1440 \
            or protocol["returns"]["cross_day"] is not False:
        raise ValueError("complete within-day minute-close observations required")
    bootstrap = protocol["bootstrap"]
    for key, expected_value in dict(block_days=7, resamples=2000, seed=100628,
                                    confidence_level=0.95).items():
        if bootstrap[key] != expected_value:
            raise ValueError(f"frozen bootstrap {key} changed")
    return protocol


def within_day_returns(closes, horizon):
    """Every eligible log(C[i+h]/C[i]); never concatenate different days."""
    _integer("horizon", horizon)
    closes = tuple(closes)
    if len(closes) <= horizon:
        raise ValueError("a day must have more closes than its horizon")
    if any(isinstance(c, bool) or not isinstance(c, (float, int))
           or not math.isfinite(c) or c <= 0 for c in closes):
        raise ValueError("closes must be positive finite numbers")
    returns=tuple(math.log(closes[i+horizon] / closes[i])
                  for i in range(len(closes)-horizon))
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("close ratios must yield finite log returns")
    return returns


def nearest_rank(values, probability):
    values = tuple(values)
    if not values or not 0 < probability <= 1:
        raise ValueError("nearest rank needs data and probability in (0,1]")
    if any(not math.isfinite(x) for x in values):
        raise ValueError("quantile input must be finite")
    return sorted(values)[math.ceil(probability * len(values))-1]


def price_diagnostics(returns):
    absolute = tuple(abs(value) for value in returns)
    if not absolute:
        raise ValueError("price diagnostics need returns")
    return dict(mean_abs_return_bps=fmean(absolute)*BPS,
                q95_abs_return_bps=nearest_rank(absolute,0.95)*BPS,
                q99_abs_return_bps=nearest_rank(absolute,0.99)*BPS,
                max_abs_return_bps=max(absolute)*BPS)


def calibrate_training(training_by_horizon):
    """Accept training returns only; evaluation observations are not an input."""
    if set(training_by_horizon) != set(HORIZONS):
        raise ValueError("training calibration requires exactly 1/3/10 minute keys")
    inputs = {h:tuple(training_by_horizon[h]) for h in HORIZONS}
    if any(not values or any(not math.isfinite(r) for r in values)
           for values in inputs.values()) or len(inputs[1]) < 2:
        raise ValueError("training returns must be finite and have a sample variance")
    sigma = stdev(inputs[1])
    q95_1 = nearest_rank(tuple(abs(r) for r in inputs[1]),0.95)
    bounds, diagnostics = {}, {}
    for horizon, values in inputs.items():
        q95 = nearest_rank(tuple(abs(r) for r in values),0.95)
        unit = sigma*math.sqrt(horizon)
        bounds[horizon] = dict(unit_sigma=unit,
            gaussian95=NormalDist().inv_cdf(0.975)*unit, empirical95=q95)
        denominator = q95_1*math.sqrt(horizon)
        diagnostics[horizon] = dict(sigma_1min=sigma,sigma_1min_bps=sigma*BPS,
            n_sigma_1min=len(inputs[1]), n_train=len(values), **price_diagnostics(values),
            sqrt_h_q95_scaling_ratio=q95/denominator if denominator else None,
            **{rule+"_bound_bps":bound*BPS for rule,bound in bounds[horizon].items()})
    return bounds, diagnostics


def bound_statistics(returns, bound):
    """The excess mean is unconditional; a separate field is conditional."""
    returns = tuple(returns)
    if not returns or isinstance(bound,bool) or not isinstance(bound,(int,float)) \
            or not math.isfinite(bound) or bound < 0:
        raise ValueError("bound statistics need data and a finite nonnegative bound")
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("returns must be finite")
    n = len(returns)
    upward = sum(value > bound for value in returns)
    downward = sum(value < -bound for value in returns)
    exceedances = upward+downward
    excess = math.fsum(max(abs(value)-bound,0.0) for value in returns)*BPS
    return dict(bound_bps=bound*BPS,n=n,covered=n-exceedances,
        coverage=(n-exceedances)/n,exceedances=exceedances,
        exceedance_rate=exceedances/n,upward_exceedances=upward,
        downward_exceedances=downward,upward_exceedance_rate=upward/n,
        downward_exceedance_rate=downward/n,excess_sum_bps=excess,
        mean_excess_bps=excess/n,
        mean_excess_given_exceedance_bps=excess/exceedances if exceedances else None)


def moving_block_indices(days, *, block_days=7, resamples=2000, seed=100628):
    """Shared noncircular calendar-day draws, concatenated then truncated."""
    for name,value in (("days",days),("block_days",block_days),("resamples",resamples)):
        _integer(name,value)
    _integer("seed",seed,0)
    if block_days > days:
        raise ValueError("bootstrap block exceeds the number of evaluation days")
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        indices = []
        for _ in range(math.ceil(days/block_days)):
            start = rng.randrange(days-block_days+1)
            indices.extend(range(start,start+block_days))
        draws.append(tuple(indices[:days]))
    return tuple(draws)


def linear_percentile(values, probability):
    values = sorted(values)
    if not values or not 0 <= probability <= 1:
        raise ValueError("percentile needs data and p in [0,1]")
    index=(len(values)-1)*probability
    low,high=math.floor(index),math.ceil(index)
    return values[low]+(values[high]-values[low])*(index-low)


def coverage_interval(daily_rows, draws):
    daily_rows = tuple(daily_rows)
    if not daily_rows or not draws:
        raise ValueError("coverage bootstrap needs day rows and draws")
    for row in daily_rows:
        _integer("daily n",row["n"])
        _integer("daily covered",row["covered"],0)
        if row["covered"] > row["n"]:
            raise ValueError("covered count exceeds n")
    ratios=[]
    for draw in draws:
        if len(draw) != len(daily_rows) or any(isinstance(i,bool) or not isinstance(i,int)
                or not 0 <= i < len(daily_rows) for i in draw):
            raise ValueError("invalid daily bootstrap indices")
        ratios.append(sum(daily_rows[i]["covered"] for i in draw)
                      / sum(daily_rows[i]["n"] for i in draw))
    return linear_percentile(ratios,0.025),linear_percentile(ratios,0.975)


def validate_days(days, protocol, days_per_phase):
    split = protocol["split"]
    train_dates=date_range(split["training_start"],split["training_end"])
    eval_dates=date_range(split["evaluation_start"],split["evaluation_end"])
    allowed_dates=set(train_dates+eval_dates)
    selected_dates=set(train_dates[:days_per_phase]+eval_dates[:days_per_phase])
    expected={(asset,day) for asset in ASSETS for day in selected_dates}
    observed, selected = set(),{}
    for row in days:
        if set(row) != {"asset","date","closes"}:
            raise ValueError("day row requires exactly asset/date/closes")
        key=(row["asset"],row["date"])
        if key in observed:
            raise ValueError(f"duplicate asset/date key: {key}")
        observed.add(key)
        if row["asset"] not in ASSETS or row["date"] not in allowed_dates:
            raise ValueError(f"unexpected asset/date key: {key}")
        closes=tuple(row["closes"])
        if len(closes) != protocol["data"]["required_minutes_per_day"]:
            raise ValueError(f"incomplete day: {key}")
        if any(isinstance(c,bool) or not isinstance(c,(float,int))
               or not math.isfinite(c) or c<=0 for c in closes):
            raise ValueError(f"invalid day close: {key}")
        if key in expected:
            selected[key]=dict(asset=row["asset"],date=row["date"],closes=closes)
    if set(selected) != expected:
        raise ValueError(f"missing selected asset/date keys: {sorted(expected-set(selected))[:8]}")
    return [selected[key] for key in sorted(selected)],dict(
        expected_full_asset_days=len(ASSETS)*(len(train_dates)+len(eval_dates)),
        expected_selected_asset_days=len(expected),selected_asset_days=len(selected),
        selected_training_dates=list(train_dates[:days_per_phase]),
        selected_evaluation_dates=list(eval_dates[:days_per_phase]),
        full_calendar_grid_required=days_per_phase==61,missing_selected_keys=[],
        observed_input_asset_days=len(observed))


def provenance_hashes(data, *, fixture=False):
    """Actual files are rehashed before and after; fixtures are always smoke."""
    if fixture:
        return {"fixture_payload":hashlib.sha256(canonical(data)).hexdigest()}
    snapshots={}
    manifest_path=Path(data["source_manifest"])
    actual=sha256(manifest_path)
    if actual != data["manifest_sha256"]:
        raise RuntimeError("raw source manifest hash mismatch")
    snapshots[str(manifest_path)]=actual
    for source in data["sources"]:
        path=Path(source["archive_path"])
        actual=sha256(path)
        if actual != source["sha256"]:
            raise RuntimeError("raw archive hash mismatch")
        snapshots[str(path)]=actual
        if source.get("sidecar_checksum_verified"):
            sidecar=Path(str(path)+".CHECKSUM")
            sidecar_hash=sha256(sidecar)
            if sidecar_hash!=source["sidecar_sha256"]:
                raise RuntimeError("raw checksum sidecar hash mismatch")
            snapshots[str(sidecar)]=sidecar_hash
    return snapshots


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing empty output table")
    keys=list(rows[0])
    if any(set(row)!=set(keys) for row in rows):
        raise ValueError("output table schema is inconsistent")
    with Path(path).open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=keys,lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")


def run_empirical_research(output_dir, *, data=None, days_per_phase=None,
        bootstrap_resamples=None, bootstrap_block_days=None, bootstrap_seed=None,
        command=None, progress=None):
    protocol=load_protocol()
    defaults=dict(days_per_phase=61,bootstrap_resamples=protocol["bootstrap"]["resamples"],
        bootstrap_block_days=protocol["bootstrap"]["block_days"],bootstrap_seed=protocol["bootstrap"]["seed"])
    supplied=dict(days_per_phase=days_per_phase,bootstrap_resamples=bootstrap_resamples,
        bootstrap_block_days=bootstrap_block_days,bootstrap_seed=bootstrap_seed)
    settings={key:defaults[key] if value is None else value for key,value in supplied.items()}
    for key,value in settings.items():
        _integer(key,value,0 if key=="bootstrap_seed" else 1)
    if settings["days_per_phase"]>61:
        raise ValueError("days_per_phase cannot exceed the frozen 61-day phases")
    if settings["bootstrap_block_days"]>settings["days_per_phase"]:
        raise ValueError("bootstrap block exceeds the selected evaluation days")
    overrides={key:dict(planned=defaults[key],actual=value) for key,value in settings.items() if value!=defaults[key]}
    fixture=data is not None
    if fixture:
        overrides["data_override"]=dict(planned="existing audited archive loader",actual="provided fixture payload")
    output_dir=Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be empty: {output_dir}")
    started,clock_start=datetime.now(timezone.utc).isoformat(),time.perf_counter()
    hashes=source_hashes()
    if data is None:
        from empirical_data import load_existing_minutes
        data=load_existing_minutes()
    if set(data) != {"days","audit","sources","manifest_sha256","source_manifest"}:
        raise ValueError("unexpected data loader payload keys")
    raw_hashes=provenance_hashes(data,fixture=fixture)
    selected,selection=validate_days(data["days"],protocol,settings["days_per_phase"])
    train_dates=set(selection["selected_training_dates"])
    evaluation_dates=selection["selected_evaluation_dates"]
    returns=defaultdict(dict)
    for row in selected:
        phase="train" if row["date"] in train_dates else "evaluation"
        for horizon in HORIZONS:
            returns[(row["asset"],phase,horizon)][row["date"]]=within_day_returns(row["closes"],horizon)
    bounds_by_asset,calibration={},[]
    for asset in ASSETS:
        training={h:tuple(value for day in sorted(returns[(asset,"train",h)])
                           for value in returns[(asset,"train",h)][day]) for h in HORIZONS}
        bounds,stats=calibrate_training(training)
        bounds_by_asset[asset]=bounds
        for horizon in HORIZONS:
            calibration.append(dict(asset=asset,horizon_minutes=horizon,phase="train",
                training_start=selection["selected_training_dates"][0],training_end=selection["selected_training_dates"][-1],
                train_days=len(train_dates),**stats[horizon]))
    # Freeze training thresholds before computing any evaluation coverage.
    calibration_sha=hashlib.sha256(canonical(calibration)).hexdigest()
    if progress:
        progress("Training thresholds frozen; evaluating all three declared bounds")
    daily,summary=[],[]
    for asset,phase,horizon in sorted(returns):
        for day,values in sorted(returns[(asset,phase,horizon)].items()):
            diagnostics=price_diagnostics(values)
            for rule in RULES:
                daily.append(dict(asset=asset,date=day,phase=phase,horizon_minutes=horizon,rule=rule,
                    **bound_statistics(values,bounds_by_asset[asset][horizon][rule]),**diagnostics))
    draws=moving_block_indices(len(evaluation_dates),block_days=settings["bootstrap_block_days"],
        resamples=settings["bootstrap_resamples"],seed=settings["bootstrap_seed"])
    grouped=defaultdict(list)
    for row in daily:
        if row["phase"]=="evaluation":
            grouped[(row["asset"],row["horizon_minutes"],row["rule"])].append(row)
    for asset in ASSETS:
        for horizon in HORIZONS:
            values=tuple(value for day in evaluation_dates
                         for value in returns[(asset,"evaluation",horizon)][day])
            diagnostics=price_diagnostics(values)
            for rule in RULES:
                days=sorted(grouped[(asset,horizon,rule)],key=lambda row:row["date"])
                if [row["date"] for row in days] != evaluation_dates:
                    raise ArithmeticError("evaluation calendar-day pairing failed")
                stats=bound_statistics(values,bounds_by_asset[asset][horizon][rule])
                if stats["n"]!=sum(row["n"] for row in days) or stats["covered"]!=sum(row["covered"] for row in days):
                    raise ArithmeticError("daily coverage sufficient statistics do not reconcile")
                if not math.isclose(stats["excess_sum_bps"],math.fsum(row["excess_sum_bps"] for row in days),
                                    rel_tol=1e-12,abs_tol=1e-8):
                    raise ArithmeticError("daily excess sufficient statistics do not reconcile")
                low,high=coverage_interval(days,draws)
                summary.append(dict(asset=asset,horizon_minutes=horizon,rule=rule,phase="evaluation",
                    date_start=evaluation_dates[0],date_end=evaluation_dates[-1],days=len(days),
                    **stats,**diagnostics,coverage_ci_low=low,coverage_ci_high=high,
                    coverage_ci_method=CI_METHOD if settings["bootstrap_block_days"]==7 else
                        f"noncircular {settings['bootstrap_block_days']}-day moving-block percentile bootstrap; frozen training bound; pointwise 95%",
                    bootstrap_resamples=settings["bootstrap_resamples"],bootstrap_block_days=settings["bootstrap_block_days"],
                    bootstrap_seed=settings["bootstrap_seed"],confidence_level=0.95))
    if hashes!=source_hashes() or raw_hashes!=provenance_hashes(data,fixture=fixture):
        raise RuntimeError("source/raw dependencies changed; results not saved")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("output directory became nonempty during computation")
    output_dir.mkdir(parents=True,exist_ok=True)
    for filename,rows in (("daily.csv",daily),("summary.csv",summary),("calibration.csv",calibration)):
        write_csv(output_dir/filename,rows)
    write_json(output_dir/"protocol.json",protocol)
    write_json(output_dir/"data_audit.json",dict(loader_audit=data["audit"],sources=data["sources"],
        source_manifest=data["source_manifest"],manifest_sha256=data["manifest_sha256"],
        raw_dependency_sha256=raw_hashes,selection=selection,fixture_override=fixture))
    outputs={str(path.relative_to(output_dir)):dict(sha256=sha256(path),bytes=path.stat().st_size)
             for path in sorted(output_dir.iterdir()) if path.is_file()}
    fingerprint_data=dict(source_sha256=hashes,raw_dependency_sha256=raw_hashes,settings=settings,
                          calibration_sha256=calibration_sha,python_version=platform.python_version())
    manifest=dict(protocol_id=protocol["protocol_id"],run_classification="smoke" if overrides else "planned_empirical_diagnostic",
        overrides=overrides,settings=settings,frozen_protocol=protocol,source_sha256=hashes,
        raw_dependency_sha256=raw_hashes,calibration_sha256=calibration_sha,
        fingerprint=hashlib.sha256(canonical(fingerprint_data)).hexdigest(),
        fingerprint_definition=fingerprint_data,source_directory=str(ROOT),
        command=command or "Python API: empirical_research.run_empirical_research",
        runtime=dict(python=sys.version,implementation=platform.python_implementation(),platform=platform.platform()),
        started_utc=started,completed_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-clock_start,
        validation=dict(selected_asset_days=selection["selected_asset_days"],expected_selected_asset_days=selection["expected_selected_asset_days"],
            full_expected_asset_days=244,calendar_keys_unique_complete=True,daily_rows=len(daily),expected_daily_rows=len(ASSETS)*2*settings["days_per_phase"]*len(HORIZONS)*len(RULES),
            summary_rows=len(summary),calibration_rows=len(calibration),thresholds_use_training_only=True,
            shared_bootstrap_calendar_draws=True,bootstrap_draws=len(draws),bootstrap_draw_days=len(evaluation_dates),
            retained_return_rows=False,raw_archive_changes=False),
        limitations=protocol["excluded_claims"],output_files=outputs,
        output_hash_note="manifest.json excluded from its own output hashes")
    write_json(output_dir/"manifest.json",manifest)
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,default=ROOT/"research_results"/"empirical_v5")
    parser.add_argument("--days-per-phase",type=int)
    parser.add_argument("--bootstrap-resamples",type=int)
    parser.add_argument("--bootstrap-block-days",type=int)
    parser.add_argument("--bootstrap-seed",type=int)
    args=parser.parse_args(argv)
    command_args=sys.argv[1:] if argv is None else list(argv)
    try:
        manifest=run_empirical_research(**vars(args),command=shlex.join([sys.executable,str(Path(__file__).resolve()),*command_args]),
            progress=lambda value:print(value,flush=True))
    except (ValueError,ArithmeticError,RuntimeError,OSError) as error:
        parser.exit(1,f"empirical diagnostic failed: {error}\n")
    print(f"Saved {manifest['run_classification']}: {manifest['fingerprint']}")


if __name__=="__main__":
    main()
