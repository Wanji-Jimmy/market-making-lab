"""Run a frozen follow-up stress of delayed atomic quote replacement."""

import argparse
from collections import defaultdict
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shlex
from statistics import mean
import sys
import time

from diagnostics import fill_markouts, paired_difference, pooled_markout_interval
from latency_simulator import CORE_STRATEGIES, STRATEGIES, simulate_latency
from market_scenarios import generate_controlled_market
from research import validate_as_widths, validate_path
from simulator import Config, RiskParameters, Scenario, simulate


ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / "latency_protocol.json"
SOURCE_FILES = ("simulator.py", "market_scenarios.py", "diagnostics.py", "research.py",
                "research_protocol.json", "latency_simulator.py", "latency_research.py",
                "latency_protocol.json")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name: _sha256(ROOT / name) for name in SOURCE_FILES}


def load_protocol():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if protocol["schema_version"] != 1 or tuple(protocol["policies"]) != STRATEGIES:
        raise ValueError("unsupported latency protocol schema or policies")
    if protocol["parent_protocol_sha256"] != _sha256(ROOT / "research_protocol.json"):
        raise ValueError("the frozen parent v2 protocol changed")
    parent = json.loads((ROOT / "research_protocol.json").read_text(encoding="utf-8"))
    for key in ("config", "risk_parameters", "scenario_defaults"):
        if protocol[key] != parent[key]:
            raise ValueError(f"latency phase must retain parent {key}")
    names = [case["name"] for case in protocol["cases"]]
    if not names or len(names) != len(set(names)):
        raise ValueError("latency cases must be nonempty and unique")
    parent_cases = {case["name"]: case for case in parent["cases"]}
    if any(parent_cases.get(case["name"]) != case for case in protocol["cases"]):
        raise ValueError("latency cases must retain parent case parameters")
    latencies = protocol["latencies"]
    if not latencies or len(latencies) != len(set(latencies)) or 0 not in latencies:
        raise ValueError("latencies must be unique and include zero")
    for latency in latencies:
        _check_integer("latency", latency, 0)
    for contrast in protocol["within_latency_contrasts"]:
        if contrast["left"] not in STRATEGIES or contrast["right"] not in STRATEGIES:
            raise ValueError("unknown contrast policy")
    delay = protocol["delay_contrasts"]
    if delay["reference_latency"] != 0 or any(
            value not in latencies or value == 0 for value in delay["comparison_latencies"]):
        raise ValueError("invalid delay contrast settings")
    return protocol


def _check_integer(name, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def positive_int(value):
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _close(label, actual, expected, tolerance=1e-7):
    if not (math.isfinite(actual) and math.isfinite(expected)) or not math.isclose(
            actual, expected, rel_tol=1e-10, abs_tol=tolerance):
        raise ArithmeticError(f"{label}: {actual} != {expected}")
    return abs(actual - expected)


def assert_complete_paths(rows, cases, latencies, seeds, policies):
    expected = {(case, latency, seed, policy) for case in cases
                for latency in latencies for seed in seeds for policy in policies}
    observed = set()
    for row in rows:
        key = (row["case"], row["latency_steps"], row["seed"], row["policy"])
        if key in observed:
            raise ValueError(f"duplicate latency path key: {key}")
        observed.add(key)
    if expected != observed:
        raise ValueError(f"incomplete latency pairing: {len(expected - observed)} missing, "
                         f"{len(observed - expected)} unexpected keys")


def validate_order_diagnostics(result, latency_steps, steps):
    metrics, trace = result["metrics"], result["trace"]
    if len(trace) != steps:
        raise ArithmeticError("latency audit needs a complete trace")
    filled_ids = set()
    information_age_sum = resting_age_sum = negative_edges = stale = blocked = 0
    age_residual = 0.0
    for t, row in enumerate(trace):
        if row["model_step"] != t:
            raise ArithmeticError("latency model steps are inconsistent")
        information_age = t - row["quote_information_step"]
        resting_age = t - row["quote_activation_step"]
        if information_age < 0 or resting_age < 0:
            raise ArithmeticError("active quote uses future information or activation")
        if (row["quote_information_age"] != information_age
                or row["quote_resting_age"] != resting_age):
            raise ArithmeticError("quote age fields do not reconcile")
        blocked += int(row["reason"] == "no_live_order")
        if not row["filled"]:
            if row["filled_order_id"] is not None:
                raise ArithmeticError("unfilled row has a filled_order_id")
            if row["fill_information_age"] is not None or row["fill_resting_age"] is not None:
                raise ArithmeticError("unfilled row has fill ages")
            continue
        order_id = row["filled_order_id"]
        if not isinstance(order_id, str) or not order_id:
            raise ArithmeticError("a fill requires an order ID")
        if order_id in filled_ids:
            raise ArithmeticError(f"duplicate filled_order_id: {order_id}")
        filled_ids.add(order_id)
        side = "bid" if row["quantity"] == 1 else "ask"
        if order_id != f"{row['active_pair_id']}:{side}":
            raise ArithmeticError("filled order ID does not match its active side")
        if not row[side + "_live_before"] or row[side + "_live_after"]:
            raise ArithmeticError("fill did not consume exactly its live order side")
        if row["fill_information_age"] != information_age or row["fill_resting_age"] != resting_age:
            raise ArithmeticError("filled quote age fields do not reconcile")
        information_age_sum += information_age
        resting_age_sum += resting_age
        negative_edges += int(row["edge"] < 0)
        stale += int(information_age > 0)
    trades = len(filled_ids)
    expected = dict(trades=trades, negative_edge_fills=negative_edges,
                    active_stale_fills=stale, blocked_no_order=blocked,
                    fill_information_age_sum=information_age_sum,
                    fill_resting_age_sum=resting_age_sum, quote_requests=steps,
                    quote_activations=max(0, steps - latency_steps),
                    pending_quote_requests=min(steps, latency_steps),
                    warm_start_activations=1, latency_steps=latency_steps)
    for key, value in expected.items():
        if metrics[key] != value:
            raise ArithmeticError(f"latency metric {key}: {metrics[key]} != {value}")
    for key, total in (("fill_information_age_mean", information_age_sum),
                       ("fill_resting_age_mean", resting_age_sum)):
        if trades:
            age_residual = max(age_residual, _close(key, metrics[key], total / trades))
        elif metrics[key] is not None:
            raise ArithmeticError("no-fill age mean must be null")
    return dict(unique_filled_order_count=trades,
                max_fill_age_reconciliation_residual=age_residual)


def validate_zero_latency(reference, result):
    """Require exact recovery of every shared v2 metric and trace field."""
    if result["metrics"]["latency_steps"] != 0:
        raise ValueError("zero-latency recovery check needs L=0")
    max_gap = 0.0
    for key, value in reference["metrics"].items():
        actual = result["metrics"][key]
        if actual != value:
            raise ArithmeticError(f"zero-latency metric failed: {key}")
        if isinstance(value, (int, float)):
            max_gap = max(max_gap, abs(actual - value))
    if len(reference["trace"]) != len(result["trace"]):
        raise ArithmeticError("zero-latency trace length changed")
    for reference_row, row in zip(reference["trace"], result["trace"]):
        for key, value in reference_row.items():
            if row[key] != value:
                raise ArithmeticError(f"zero-latency trace field failed: {key}")
    return dict(zero_latency_recovery_checked=True,
                zero_latency_recovery_max_metric_gap=max_gap)


def validate_width_pairs(results):
    audit = {}
    for label, symmetric, inventory in (
            ("as_pair", "as_symmetric", "as_inventory"),
            ("buffered_pair", "buffered_as_symmetric", "buffered_as_inventory")):
        left, right = results[symmetric]["trace"], results[inventory]["trace"]
        audit[label + "_max_active_width_gap"] = validate_as_widths(left, right)
        request_gap = 0.0
        for lhs, rhs in zip(left, right):
            if lhs["active_pair_id"] != rhs["active_pair_id"]:
                raise ArithmeticError("matched-width policies have different activation schedules")
            request_gap = max(request_gap, _close(
                label + " requested width", lhs["request_ask"] - lhs["request_bid"],
                rhs["request_ask"] - rhs["request_bid"], 1e-9))
        audit[label + "_max_request_width_gap"] = request_gap
    return audit


def summarize_paths(rows, metric_names):
    groups = defaultdict(list)
    for row in rows:
        groups[row["case"], row["latency_steps"], row["policy"]].append(row)
    summaries = []
    for (case, latency, policy), samples in groups.items():
        total_trades = sum(row["trades"] for row in samples)
        info_sum = sum(row["fill_information_age_sum"] for row in samples)
        resting_sum = sum(row["fill_resting_age_sum"] for row in samples)
        summary = dict(case=case, latency_steps=latency, policy=policy, runs=len(samples),
                       loss_probability=mean(row["pnl_liquidated"] < 0 for row in samples),
                       total_trades=total_trades,
                       total_fill_information_age=info_sum, total_fill_resting_age=resting_sum,
                       pooled_fill_information_age_mean=info_sum / total_trades if total_trades else None,
                       pooled_fill_resting_age_mean=resting_sum / total_trades if total_trades else None,
                       negative_edge_fill_fraction=(sum(row["negative_edge_fills"] for row in samples)
                                                    / total_trades if total_trades else None),
                       stale_fill_fraction=(sum(row["active_stale_fills"] for row in samples)
                                            / total_trades if total_trades else None),
                       defined_fill_age_paths=sum(row["trades"] > 0 for row in samples))
        for metric in metric_names:
            if metric in ("fill_information_age_mean", "fill_resting_age_mean", "latency_steps"):
                continue
            column = metric if metric.startswith("mean_") else "mean_" + metric
            summary[column] = mean(row[metric] for row in samples)
        summaries.append(summary)
    return summaries


def _bootstrap_seed(base, *labels):
    return int.from_bytes(hashlib.sha256(_canonical([base, *labels])).digest()[:8], "big")


def paired_rows(rows, protocol, resamples, bootstrap_seed):
    groups = defaultdict(dict)
    for row in rows:
        group = groups[row["case"], row["latency_steps"], row["policy"]]
        if row["seed"] in group:
            raise ValueError("duplicate seed in latency paired aggregation")
        group[row["seed"]] = row
    contrasts = []
    for latency in protocol["latencies"]:
        for contrast in protocol["within_latency_contrasts"]:
            contrasts.append(("within_latency", contrast["name"], latency, latency,
                              contrast["left"], contrast["right"]))
    for latency in protocol["delay_contrasts"]["comparison_latencies"]:
        for policy in protocol["policies"]:
            contrasts.append(("delay_effect", f"latency_{latency}_minus_0", latency, 0, policy, policy))
    result = []
    for case in protocol["cases"]:
        for kind, contrast, left_latency, right_latency, left_policy, right_policy in contrasts:
            left = groups[case["name"], left_latency, left_policy]
            right = groups[case["name"], right_latency, right_policy]
            for metric in protocol["analysis"]["paired_metrics"]:
                estimate = paired_difference(
                    {seed: row[metric] for seed, row in left.items()},
                    {seed: row[metric] for seed, row in right.items()}, resamples=resamples,
                    seed=_bootstrap_seed(bootstrap_seed, case["name"], contrast, left_latency,
                                         right_latency, left_policy, right_policy, metric))
                win_fraction = estimate.pop("probability_positive")
                result.append(dict(case=case["name"], contrast_type=kind, contrast=contrast,
                                   left_latency_steps=left_latency, right_latency_steps=right_latency,
                                   left_policy=left_policy, right_policy=right_policy, metric=metric,
                                   primary=metric == protocol["analysis"]["primary_metric"],
                                   **estimate, paired_win_fraction=win_fraction, resamples=resamples,
                                   ci_method=protocol["analysis"]["ci_method"]))
    return result


def markout_rows(rows, protocol, resamples, bootstrap_seed):
    groups = defaultdict(list)
    for row in rows:
        groups[row["case"], row["latency_steps"], row["policy"]].append(row)
    result = []
    for (case, latency, policy), samples in groups.items():
        for horizon in protocol["analysis"]["markout_horizons"]:
            prefix = f"h{horizon}_"
            estimate = pooled_markout_interval(
                samples, horizon, resamples=resamples,
                seed=_bootstrap_seed(bootstrap_seed, case, latency, policy, horizon))
            count = estimate["total_fills"]
            pooled = dict(case=case, latency_steps=latency, policy=policy, horizon=horizon,
                          **{f"mid_move_{key}" if key in ("mean", "ci_low", "ci_high")
                             else key: value for key, value in estimate.items()},
                          censored_fills=sum(row[prefix + "censored"] for row in samples),
                          execution_markout_mean=(sum(row[prefix + "execution_markout_sum"]
                                                     for row in samples) / count if count else None),
                          edge_mean=sum(row[prefix + "edge_sum"] for row in samples) / count if count else None,
                          resamples=resamples, ci_method=protocol["analysis"]["ci_method"])
            for label in ("informed", "uninformed"):
                subgroup_count = sum(row[prefix + label + "_count"] for row in samples)
                subgroup_sum = sum(row[prefix + label + "_mid_move_sum"] for row in samples)
                pooled[label + "_count"] = subgroup_count
                pooled[label + "_mid_move_mean"] = subgroup_sum / subgroup_count if subgroup_count else None
            result.append(pooled)
    return result


def _write_csv(path, rows):
    if not rows:
        raise ValueError(f"refusing empty output table {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def run_latency_research(output_dir, *, runs=None, steps=None, seed_start=None,
                         paired_resamples=None, markout_resamples=None, bootstrap_seed=None,
                         command=None, progress=None):
    protocol = load_protocol()
    defaults = {key: protocol["simulation"][key] for key in ("runs", "steps", "seed_start")}
    defaults.update({key: protocol["analysis"][key]
                     for key in ("paired_resamples", "markout_resamples", "bootstrap_seed")})
    supplied = dict(runs=runs, steps=steps, seed_start=seed_start,
                    paired_resamples=paired_resamples, markout_resamples=markout_resamples,
                    bootstrap_seed=bootstrap_seed)
    settings = {key: defaults[key] if value is None else value for key, value in supplied.items()}
    for key, value in settings.items():
        _check_integer(key, value, None if key in ("seed_start", "bootstrap_seed") else 1)
    overrides = {key: {"planned": defaults[key], "actual": value}
                 for key, value in settings.items() if value != defaults[key]}
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be empty: {output_dir}")
    started = datetime.now(timezone.utc).isoformat()
    clock_start = time.perf_counter()
    hashes = source_hashes()
    risk = RiskParameters(**protocol["risk_parameters"])
    seeds = range(settings["seed_start"], settings["seed_start"] + settings["runs"])
    rows, representative_traces, resolved_cases = [], {}, []
    metric_names = None
    reference_simulations = recovery_checks = recovery_trace_rows = 0
    for case in protocol["cases"]:
        scenario = Scenario(name=case["name"], label=case["name"], steps=settings["steps"],
                            **{**protocol["scenario_defaults"], **case["scenario_overrides"]})
        config = Config(**{**protocol["config"], **case["config_overrides"]})
        resolved_cases.append(dict(case=case["name"], scenario=asdict(scenario), config=asdict(config)))
        for seed in seeds:
            events = tuple(generate_controlled_market(
                scenario, seed, initial_mid=protocol["simulation"]["initial_mid"], tick_size=config.tick_size))
            references = {policy: simulate(events, config, policy, record=True, risk=risk)
                          for policy in CORE_STRATEGIES}
            reference_simulations += len(references)
            for latency in protocol["latencies"]:
                path_results, path_rows = {}, []
                for policy in protocol["policies"]:
                    result = simulate_latency(events, config, policy, latency_steps=latency,
                                              risk=risk, buffer_multiplier=protocol["buffer_multiplier"],
                                              record=True)
                    if metric_names is None:
                        metric_names = tuple(result["metrics"])
                    elif set(result["metrics"]) != set(metric_names):
                        raise ValueError("latency metric schema changed during execution")
                    audit = validate_path(events, config, result)
                    audit.update(validate_order_diagnostics(result, latency, settings["steps"]))
                    if latency == 0:
                        reference = references[policy.removeprefix("buffered_")]
                        audit.update(validate_zero_latency(reference, result))
                        recovery_checks += 1
                        recovery_trace_rows += len(result["trace"])
                    else:
                        audit.update(zero_latency_recovery_checked=False,
                                     zero_latency_recovery_max_metric_gap=None)
                    diagnostics = fill_markouts(result["trace"], protocol["analysis"]["markout_horizons"])
                    path_rows.append(dict(case=case["name"], latency_steps=latency, seed=seed,
                                          policy=policy, **{key: value for key, value in result["metrics"].items()
                                                           if key != "latency_steps"}, **diagnostics, **audit))
                    path_results[policy] = result
                    if seed == settings["seed_start"]:
                        representative_traces[case["name"], latency, policy] = [
                            dict(case=case["name"], latency_steps=latency, seed=seed, policy=policy, **row)
                            for row in result["trace"]]
                width_audit = validate_width_pairs(path_results)
                for row in path_rows:
                    row.update(width_audit)
                rows.extend(path_rows)
        if progress:
            progress(f"Simulated and audited {case['name']}: {settings['runs']} tapes, "
                     f"{len(protocol['latencies'])} latencies, {len(protocol['policies'])} policies")
    assert_complete_paths(rows, [case["name"] for case in protocol["cases"]],
                          protocol["latencies"], seeds, protocol["policies"])
    summaries = summarize_paths(rows, metric_names)
    if progress:
        progress("Computing paired whole-path bootstrap intervals")
    paired = paired_rows(rows, protocol, settings["paired_resamples"], settings["bootstrap_seed"])
    if progress:
        progress("Computing pooled markout whole-path bootstrap intervals")
    markouts = markout_rows(rows, protocol, settings["markout_resamples"], settings["bootstrap_seed"])
    if hashes != source_hashes():
        raise RuntimeError("source dependencies changed during the run; outputs were not saved")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory became nonempty during the run: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, table in (("paths.csv", rows), ("summary.csv", summaries),
                            ("paired.csv", paired), ("markouts.csv", markouts)):
        _write_csv(output_dir / filename, table)
    _write_json(output_dir / "protocol.json", protocol)
    for (case, latency, policy), trace in representative_traces.items():
        _write_csv(output_dir / "traces" /
                   f"{case}__latency_{latency}__{policy}__seed_{settings['seed_start']}.csv", trace)
    outputs = {str(path.relative_to(output_dir)): dict(sha256=_sha256(path), bytes=path.stat().st_size)
               for path in sorted(output_dir.rglob("*")) if path.is_file()}
    fingerprint_payload = dict(source_sha256=hashes, settings=settings,
                               python_version=platform.python_version())
    residual_keys = ("terminal_accounting_residual", "inventory_split_residual",
                     "max_trace_ledger_residual", "max_fill_age_reconciliation_residual",
                     "as_pair_max_active_width_gap", "as_pair_max_request_width_gap",
                     "buffered_pair_max_active_width_gap", "buffered_pair_max_request_width_gap")
    manifest = dict(
        protocol_id=protocol["protocol_id"],
        run_classification="smoke" if overrides else "planned_followup_simulation",
        overrides=overrides, settings=settings, frozen_protocol=protocol,
        resolved_cases=resolved_cases, risk_parameters=asdict(risk),
        latencies=protocol["latencies"], buffer_multiplier=protocol["buffer_multiplier"],
        seed_range_inclusive=[seeds.start, seeds.stop - 1], source_sha256=hashes,
        source_directory=str(ROOT),
        fingerprint=hashlib.sha256(_canonical(fingerprint_payload)).hexdigest(),
        command=command or "Python API: latency_research.run_latency_research",
        runtime=dict(python=sys.version, implementation=platform.python_implementation(),
                     platform=platform.platform()),
        started_utc=started, completed_utc=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=time.perf_counter() - clock_start,
        validation=dict(
            path_count=len(rows), expected_path_count=len(protocol["cases"]) * settings["runs"]
            * len(protocol["latencies"]) * len(protocol["policies"]),
            case_seed_tapes=len(protocol["cases"]) * settings["runs"],
            unique_complete_pairing=True,
            v2_reference_simulations=reference_simulations,
            zero_latency_recovery_paths=recovery_checks,
            zero_latency_trace_rows_compared=recovery_trace_rows,
            max_zero_latency_metric_gap=max(row["zero_latency_recovery_max_metric_gap"]
                                            for row in rows if row["zero_latency_recovery_checked"]),
            unique_filled_orders_checked=sum(row["unique_filled_order_count"] for row in rows),
            duplicate_filled_orders=0,
            max_residuals={key: max(abs(row[key]) for row in rows) for key in residual_keys},
            representative_trace_count=len(representative_traces),
            representative_trace_selection="first planned seed, before outcomes",
            pooled_age_method="sum of fill-age numerators divided by total trades"),
        output_files=outputs, output_hash_note="manifest.json is excluded from its own output hashes")
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def make_parser():
    protocol = load_protocol()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "research_results" / "latency_v3")
    for key in ("runs", "steps"):
        parser.add_argument("--" + key, type=positive_int, default=protocol["simulation"][key])
    parser.add_argument("--seed-start", type=int, default=protocol["simulation"]["seed_start"])
    for key in ("paired_resamples", "markout_resamples"):
        parser.add_argument("--" + key.replace("_", "-"), type=positive_int,
                            default=protocol["analysis"][key])
    parser.add_argument("--bootstrap-seed", type=int, default=protocol["analysis"]["bootstrap_seed"])
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    command_args = sys.argv[1:] if argv is None else list(argv)
    try:
        manifest = run_latency_research(**vars(args),
                                        command=shlex.join([sys.executable, str(Path(__file__).resolve()),
                                                            *command_args]),
                                        progress=lambda value: print(value, flush=True))
    except (ValueError, ArithmeticError, RuntimeError, OSError) as error:
        parser.exit(1, f"latency research failed: {error}\n")
    print(f"Saved {manifest['validation']['path_count']} audited latency paths to {args.output_dir}")
    print(f"Run classification: {manifest['run_classification']}")
    print(f"Fingerprint: {manifest['fingerprint']}")


if __name__ == "__main__":
    main()
