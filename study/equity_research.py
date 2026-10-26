"""Historical stock minute-END NBBO-mid risk pilot; no own orders or PnL.

All h+1 quote minutes must be valid, including intermediate minutes. Locked
quotes remain valid. Nothing is interpolated, and windows never cross a
regular trading session. Fixed train-only bounds reuse frozen v5 statistics.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shlex
import sys
import time

from empirical_research import (BPS, HORIZONS, RULES, bound_statistics,
    calibrate_training, canonical, linear_percentile, moving_block_indices,
    price_diagnostics, sha256, write_csv, write_json)


ROOT=Path(__file__).resolve().parent
PROTOCOL_PATH=ROOT/"equity_protocol.json"
ASSETS=("EBAY","LULU")
SOURCE_FILES=("equity_research.py","equity_data.py","equity_protocol.json",
              "test_equity_research.py","test_equity_data.py",
              "empirical_research.py","empirical_protocol.json")
ENDPOINT_NAMES=("old_bid_above_future_mid","old_ask_below_future_mid",
                "old_bid_touch_or_cross_future_ask","old_ask_touch_or_cross_future_bid")


def _integer(name,value,minimum=1):
    if isinstance(value,bool) or not isinstance(value,int) or value<minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def source_hashes():
    return {filename:sha256(ROOT/filename) for filename in SOURCE_FILES}


def load_protocol():
    protocol=json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if protocol["schema_version"]!=1 or tuple(protocol["assets"])!=ASSETS \
            or tuple(protocol["horizons_minutes"])!=HORIZONS \
            or tuple(protocol["calibration"]["rules"])!=RULES:
        raise ValueError("unsupported frozen stock protocol")
    sessions=protocol["session_dates"]
    if len(sessions)!=40 or len(set(sessions))!=40 or sessions!=sorted(sessions) \
            or (sessions[0],sessions[19],sessions[20],sessions[-1])!=("2021-01-04","2021-02-01","2021-02-02","2021-03-02"):
        raise ValueError("frozen forty-session calendar changed")
    if protocol["data"]["minutes_per_session"]!=390 or protocol["returns"]["cross_session"] is not False:
        raise ValueError("regular 390-minute within-session observations required")
    for key,value in dict(block_sessions=5,resamples=2000,seed=100629,confidence_level=0.95).items():
        if protocol["bootstrap"][key]!=value:
            raise ValueError(f"frozen stock bootstrap {key} changed")
    if protocol["parent_empirical_source_sha256"]!=sha256(ROOT/"empirical_research.py") \
            or protocol["parent_empirical_protocol_sha256"]!=sha256(ROOT/"empirical_protocol.json"):
        raise ValueError("frozen parent empirical dependency changed")
    return protocol


def admissible_windows(mids,valid_mask,horizon):
    """Return immutable (origin, log-mid-return) pairs, no gap bridging."""
    _integer("horizon",horizon)
    mids,valid_mask=tuple(mids),tuple(valid_mask)
    if len(mids)!=len(valid_mask) or len(mids)<=horizon:
        raise ValueError("quote/mask lengths must agree and exceed horizon")
    invalid=[0]
    for mid,valid in zip(mids,valid_mask):
        if not isinstance(valid,bool):
            raise ValueError("valid mask must contain booleans")
        if valid and (isinstance(mid,bool) or not isinstance(mid,(int,float)) or not math.isfinite(mid) or mid<=0):
            raise ValueError("a valid quote needs a positive finite midpoint")
        invalid.append(invalid[-1]+int(not valid))
    result=[]
    for origin in range(len(mids)-horizon):
        if invalid[origin+horizon+1]!=invalid[origin]:
            continue
        value=math.log(mids[origin+horizon]/mids[origin])
        if not math.isfinite(value):
            raise ValueError("midpoint ratio must give a finite log return")
        result.append((origin,value))
    return tuple(result)


def safe_statistics(values,bound,candidate_windows):
    values=tuple(values)
    _integer("candidate_windows",candidate_windows,0)
    if candidate_windows<len(values):
        raise ValueError("eligible windows exceed candidates")
    if values:
        stats=dict(**bound_statistics(values,bound),**price_diagnostics(values))
    else:
        stats=dict(bound_bps=bound*BPS,n=0,covered=0,coverage=None,exceedances=0,
            exceedance_rate=None,upward_exceedances=0,downward_exceedances=0,
            upward_exceedance_rate=None,downward_exceedance_rate=None,
            excess_sum_bps=0.0,mean_excess_bps=None,mean_excess_given_exceedance_bps=None,
            mean_abs_return_bps=None,q95_abs_return_bps=None,q99_abs_return_bps=None,max_abs_return_bps=None)
    return dict(candidate_windows=candidate_windows,skipped_invalid_windows=candidate_windows-len(values),**stats)


def stock_coverage_interval(daily_rows,draws):
    """Retain zero-observation calendar sessions; discard undefined ratios."""
    daily_rows=tuple(daily_rows)
    if not daily_rows or not draws:
        raise ValueError("bootstrap needs calendar rows and draws")
    for row in daily_rows:
        _integer("n",row["n"],0)
        _integer("covered",row["covered"],0)
        if row["covered"]>row["n"]:
            raise ValueError("covered exceeds eligible n")
    ratios=[]
    for draw in draws:
        if len(draw)!=len(daily_rows) or any(isinstance(i,bool) or not isinstance(i,int)
                or not 0<=i<len(daily_rows) for i in draw):
            raise ValueError("invalid session bootstrap indices")
        n=sum(daily_rows[i]["n"] for i in draw)
        if n:
            ratios.append(sum(daily_rows[i]["covered"] for i in draw)/n)
    return (linear_percentile(ratios,0.025),linear_percentile(ratios,0.975),len(ratios)) if ratios else (None,None,0)


def endpoint_statistics(day,horizon,windows=None):
    """Recorded BBO endpoints only: comparisons do not establish executions."""
    windows=admissible_windows(day["mids"],day["valid_mask"],horizon) if windows is None else tuple(windows)
    counts={name+"_count":0 for name in ENDPOINT_NAMES}
    half_spreads=[]
    for origin,_ in windows:
        future=origin+horizon
        bid,ask,mid=day["bids"][origin],day["asks"][origin],day["mids"][origin]
        future_bid,future_ask,future_mid=day["bids"][future],day["asks"][future],day["mids"][future]
        half_spreads.append((ask-bid)/(2*mid)*BPS)
        for name,condition in zip(ENDPOINT_NAMES,(bid>future_mid,ask<future_mid,bid>=future_ask,ask<=future_bid)):
            counts[name+"_count"]+=int(condition)
    return dict(n=len(windows),origin_halfspread_sum_bps=math.fsum(half_spreads),**counts)


def validate_sessions(days,protocol,sessions_per_phase):
    all_dates=protocol["session_dates"]
    train=all_dates[:20][:sessions_per_phase]
    evaluation=all_dates[20:][:sessions_per_phase]
    expected={(asset,day) for asset in ASSETS for day in train+evaluation}
    observed,selected=set(),{}
    required={"asset","date","bids","asks","mids","valid_mask","spreads","structurally_valid"}
    for row in days:
        if not required<=set(row) or set(row)-required-{"locked_mask"}:
            raise ValueError("unexpected stock session schema")
        key=(row["asset"],row["date"])
        if key in observed or row["asset"] not in ASSETS or row["date"] not in all_dates:
            raise ValueError(f"duplicate/unexpected stock asset/session {key}")
        observed.add(key)
        if row["structurally_valid"] is not True:
            raise ValueError(f"structurally invalid session {key}")
        if any(len(row[field])!=390 for field in ("bids","asks","mids","valid_mask","spreads")):
            raise ValueError(f"session needs all 390 regular-minute positions {key}")
        for i,valid in enumerate(row["valid_mask"]):
            if not isinstance(valid,bool):
                raise ValueError("stock valid mask must be boolean")
            if not valid:
                continue
            bid,ask,mid,spread=(row[field][i] for field in ("bids","asks","mids","spreads"))
            if any(isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)
                   for value in (bid,ask,mid,spread)) or bid<=0 or ask<bid:
                raise ValueError("valid end-NBBO quote is invalid or crossed")
            if not math.isclose(mid,(bid+ask)/2,rel_tol=1e-12,abs_tol=1e-10) \
                    or not math.isclose(spread,ask-bid,rel_tol=1e-12,abs_tol=1e-10):
                raise ValueError("end midpoint/spread does not reconcile")
        if key in expected:
            selected[key]=row
    if set(selected)!=expected:
        raise ValueError(f"missing selected stock session keys: {sorted(expected-set(selected))[:8]}")
    return [selected[key] for key in sorted(selected)],dict(expected_full_asset_sessions=80,
        expected_selected_asset_sessions=len(expected),selected_asset_sessions=len(selected),
        selected_training_dates=train,selected_evaluation_dates=evaluation,
        calendar_keys_unique_complete=True,missing_selected_keys=[],observed_input_asset_sessions=len(observed))


def provenance_hashes(data,fixture=False):
    if fixture:
        return {"fixture_payload":hashlib.sha256(canonical(data)).hexdigest()}
    result={}
    for path_key,hash_key in (("source_manifest","manifest_sha256"),("source_coverage","coverage_sha256"),
                              ("source_quality","quality_sha256")):
        if data.get(path_key):
            path=Path(data[path_key]);digest=sha256(path)
            if digest!=data[hash_key]:
                raise RuntimeError("stock source manifest/coverage checksum mismatch")
            result[str(path)]=digest
    for source in data["sources"]:
        path=Path(source["file_path"])
        if not path.is_file():
            raise FileNotFoundError(f"required stock CSV not found: {path}; --source-dir must contain all eighty frozen files")
        digest=sha256(path)
        if digest!=source["sha256"]:
            raise RuntimeError("original stock CSV checksum mismatch")
        result[str(path)]=digest
    return result


def run_equity_research(output_dir,*,data=None,source_dir=None,manifest_path=None,
        coverage_path=None,quality_path=None,sessions_per_phase=None,
        bootstrap_resamples=None,bootstrap_block_sessions=None,bootstrap_seed=None,
        command=None,progress=None):
    protocol=load_protocol()
    defaults=dict(sessions_per_phase=20,bootstrap_resamples=2000,bootstrap_block_sessions=5,bootstrap_seed=100629)
    supplied=dict(sessions_per_phase=sessions_per_phase,bootstrap_resamples=bootstrap_resamples,
                  bootstrap_block_sessions=bootstrap_block_sessions,bootstrap_seed=bootstrap_seed)
    settings={key:defaults[key] if value is None else value for key,value in supplied.items()}
    for key,value in settings.items():
        _integer(key,value,0 if key=="bootstrap_seed" else 1)
    if settings["sessions_per_phase"]>20 or settings["bootstrap_block_sessions"]>settings["sessions_per_phase"]:
        raise ValueError("session count/block exceeds the frozen phase")
    overrides={key:dict(planned=defaults[key],actual=value) for key,value in settings.items() if value!=defaults[key]}
    fixture=data is not None
    if fixture:
        if any(value is not None for value in (source_dir,manifest_path,coverage_path,quality_path)):
            raise ValueError("data fixtures cannot be combined with source or provenance paths")
        overrides["data_override"]=dict(planned="existing stock loader",actual="fixture payload")
    output_dir=Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("equity output directory must be empty")
    started,clock_start=datetime.now(timezone.utc).isoformat(),time.perf_counter()
    hashes=source_hashes()
    if data is None:
        from equity_data import load_existing_equity_minutes
        data=load_existing_equity_minutes(source_dir=source_dir,manifest_path=manifest_path,
            coverage_path=coverage_path,quality_path=quality_path)
    required={"days","audit","sources","source_manifest","manifest_sha256"}
    if not required<=set(data) or set(data)-required-{"source_coverage","coverage_sha256","source_quality","quality_sha256"}:
        raise ValueError("unexpected stock loader payload")
    raw_hashes=provenance_hashes(data,fixture)
    selected,selection=validate_sessions(data["days"],protocol,settings["sessions_per_phase"])
    train_dates=set(selection["selected_training_dates"])
    eval_dates=selection["selected_evaluation_dates"]
    observations,session_by_key={},{}
    for day in selected:
        phase="train" if day["date"] in train_dates else "evaluation"
        session_by_key[(day["asset"],day["date"])]=day
        for horizon in HORIZONS:
            observations[(day["asset"],phase,horizon,day["date"])]=admissible_windows(day["mids"],day["valid_mask"],horizon)
    calibration,bounds_by_asset=[],{}
    for asset in ASSETS:
        training={h:tuple(value for day in selection["selected_training_dates"]
            for _,value in observations[(asset,"train",h,day)]) for h in HORIZONS}
        bounds,stats=calibrate_training(training)
        bounds_by_asset[asset]=bounds
        for horizon in HORIZONS:
            candidates=len(train_dates)*(390-horizon)
            calibration.append(dict(asset=asset,horizon_minutes=horizon,phase="train",
                training_start=selection["selected_training_dates"][0],training_end=selection["selected_training_dates"][-1],
                train_days=len(train_dates),candidate_windows=candidates,
                skipped_invalid_windows=candidates-len(training[horizon]),**stats[horizon]))
    calibration_sha=hashlib.sha256(canonical(calibration)).hexdigest()
    if progress:
        progress("Stock training bounds frozen; evaluating recorded end-NBBO-mid risk")
    daily=[]
    grouped=defaultdict(list)
    for (asset,phase,horizon,day),windows in sorted(observations.items()):
        values=tuple(value for _,value in windows)
        for rule in RULES:
            row=dict(asset=asset,date=day,phase=phase,horizon_minutes=horizon,rule=rule,
                **safe_statistics(values,bounds_by_asset[asset][horizon][rule],390-horizon))
            daily.append(row)
            if phase=="evaluation":
                grouped[(asset,horizon,rule)].append(row)
    draws=moving_block_indices(len(eval_dates),block_days=settings["bootstrap_block_sessions"],
        resamples=settings["bootstrap_resamples"],seed=settings["bootstrap_seed"])
    summary,segments,endpoints=[],[],[]
    for asset in ASSETS:
        for horizon in HORIZONS:
            windows_by_day={day:observations[(asset,"evaluation",horizon,day)] for day in eval_dates}
            values=tuple(value for day in eval_dates for _,value in windows_by_day[day])
            candidates=len(eval_dates)*(390-horizon)
            for rule in RULES:
                daily_rows=sorted(grouped[(asset,horizon,rule)],key=lambda row:row["date"])
                if [row["date"] for row in daily_rows]!=eval_dates:
                    raise ArithmeticError("stock evaluation calendar pairing failed")
                stats=safe_statistics(values,bounds_by_asset[asset][horizon][rule],candidates)
                if stats["n"]!=sum(row["n"] for row in daily_rows) or stats["covered"]!=sum(row["covered"] for row in daily_rows):
                    raise ArithmeticError("stock daily sufficient statistics do not reconcile")
                low,high,valid_draws=stock_coverage_interval(daily_rows,draws)
                summary.append(dict(asset=asset,horizon_minutes=horizon,rule=rule,phase="evaluation",
                    date_start=eval_dates[0],date_end=eval_dates[-1],days=len(eval_dates),**stats,
                    coverage_ci_low=low,coverage_ci_high=high,
                    coverage_ci_method=f"noncircular {settings['bootstrap_block_sessions']}-trading-session percentile bootstrap; frozen training bound; pointwise 95%",
                    bootstrap_resamples=settings["bootstrap_resamples"],bootstrap_valid_resamples=valid_draws,
                    bootstrap_block_sessions=settings["bootstrap_block_sessions"],bootstrap_block_days=settings["bootstrap_block_sessions"],
                    bootstrap_seed=settings["bootstrap_seed"],confidence_level=0.95))
                for segment in protocol["segments"]:
                    start,stop=segment["origin_start"],min(segment["origin_stop"],390-horizon)
                    segment_values=tuple(value for day in eval_dates for origin,value in windows_by_day[day] if start<=origin<stop)
                    segments.append(dict(asset=asset,horizon_minutes=horizon,rule=rule,phase="evaluation",segment=segment["name"],
                        origin_start_index=start,origin_stop_exclusive=stop,date_start=eval_dates[0],date_end=eval_dates[-1],days=len(eval_dates),
                        **safe_statistics(segment_values,bounds_by_asset[asset][horizon][rule],len(eval_dates)*(stop-start))))
            per_day=[endpoint_statistics(session_by_key[(asset,day)],horizon,windows_by_day[day]) for day in eval_dates]
            n=sum(row["n"] for row in per_day)
            endpoint=dict(asset=asset,horizon_minutes=horizon,phase="evaluation",date_start=eval_dates[0],date_end=eval_dates[-1],days=len(eval_dates),
                n=n,admissible_n=n,candidate_windows=candidates,skipped_invalid_windows=candidates-n,
                origin_halfspread_sum_bps=math.fsum(row["origin_halfspread_sum_bps"] for row in per_day))
            endpoint["origin_mean_halfspread_bps"]=endpoint["origin_halfspread_sum_bps"]/n if n else None
            for name in ENDPOINT_NAMES:
                count=sum(row[name+"_count"] for row in per_day)
                endpoint[name+"_count"]=count
                endpoint[name+"_fraction"]=count/n if n else None
            endpoints.append(endpoint)
    if hashes!=source_hashes() or raw_hashes!=provenance_hashes(data,fixture):
        raise RuntimeError("stock source/raw dependencies changed; results not written")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("equity output became nonempty during computation")
    output_dir.mkdir(parents=True,exist_ok=True)
    for filename,rows in (("daily.csv",daily),("summary.csv",summary),("calibration.csv",calibration),
                          ("session_segments.csv",segments),("endpoint_quotes.csv",endpoints)):
        write_csv(output_dir/filename,rows)
    write_json(output_dir/"protocol.json",protocol)
    write_json(output_dir/"data_audit.json",dict(loader_audit=data["audit"],sources=data["sources"],
        source_manifest=data["source_manifest"],manifest_sha256=data["manifest_sha256"],
        source_coverage=data.get("source_coverage"),coverage_sha256=data.get("coverage_sha256"),
        source_quality=data.get("source_quality"),quality_sha256=data.get("quality_sha256"),
        raw_dependency_sha256=raw_hashes,selection=selection,fixture_override=fixture))
    outputs={path.name:dict(sha256=sha256(path),bytes=path.stat().st_size) for path in sorted(output_dir.iterdir()) if path.is_file()}
    fingerprint_data=dict(source_sha256=hashes,raw_dependency_sha256=raw_hashes,settings=settings,
                          calibration_sha256=calibration_sha,python_version=platform.python_version())
    manifest=dict(protocol_id=protocol["protocol_id"],run_classification="smoke" if overrides else "planned_equity_diagnostic",
        overrides=overrides,settings=settings,frozen_protocol=protocol,source_sha256=hashes,raw_dependency_sha256=raw_hashes,
        calibration_sha256=calibration_sha,fingerprint=hashlib.sha256(canonical(fingerprint_data)).hexdigest(),
        fingerprint_definition=fingerprint_data,source_directory=str(ROOT),command=command or "Python API: equity_research.run_equity_research",
        runtime=dict(python=sys.version,implementation=platform.python_implementation(),platform=platform.platform()),
        started_utc=started,completed_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.perf_counter()-clock_start,
        validation=dict(selected_asset_sessions=len(selected),expected_selected_asset_sessions=len(ASSETS)*2*settings["sessions_per_phase"],
            full_expected_asset_sessions=80,calendar_keys_unique_complete=True,daily_rows=len(daily),summary_rows=len(summary),
            calibration_rows=len(calibration),session_segment_rows=len(segments),endpoint_quote_rows=len(endpoints),
            thresholds_use_training_only=True,shared_bootstrap_calendar_draws=True,bootstrap_draws=len(draws),bootstrap_draw_sessions=len(eval_dates),
            invalid_windows_skipped_without_interpolation=True,cross_session_windows=0,endpoint_diagnostics_not_fills=True),
        limitations=protocol["excluded_claims"],output_files=outputs,output_hash_note="manifest.json excluded from its own output hashes")
    write_json(output_dir/"manifest.json",manifest)
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,default=ROOT/"research_results"/"equity_v6")
    parser.add_argument("--source-dir",type=Path,
        help="directory containing the eighty EBAY/LULU CSVs; defaults to the existing local acquisition")
    parser.add_argument("--acquisition-manifest",dest="manifest_path",type=Path,
        help="acquisition JSON with a files list and original SHA-256 values; defaults to SOURCE_DIR/acquisition_manifest.json")
    parser.add_argument("--coverage-manifest",dest="coverage_path",type=Path,
        help="prior coverage JSON with all eighty checksums; defaults to SOURCE_DIR/equity_results.json for a custom source directory")
    parser.add_argument("--quality-report",dest="quality_path",type=Path,
        help="optional provenance JSON; required if supplied, otherwise uses SOURCE_DIR/quality_report.json when present")
    parser.add_argument("--sessions-per-phase",type=int)
    parser.add_argument("--bootstrap-resamples",type=int)
    parser.add_argument("--bootstrap-block-sessions",type=int)
    parser.add_argument("--bootstrap-seed",type=int)
    args=parser.parse_args(argv)
    try:
        manifest=run_equity_research(**vars(args),command=shlex.join([sys.executable,str(Path(__file__).resolve()),*(sys.argv[1:] if argv is None else argv)]),
            progress=lambda message:print(message,flush=True))
    except (ValueError,ArithmeticError,RuntimeError,OSError) as error:
        parser.exit(1,f"equity diagnostic failed: {error}\n")
    print(f"Saved {manifest['run_classification']}: {manifest['fingerprint']}")


if __name__=="__main__":
    main()
