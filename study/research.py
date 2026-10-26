"""Run the frozen v2 synthetic-market research protocol using only the stdlib."""

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
from market_scenarios import generate_controlled_market
from simulator import Config, RiskParameters, Scenario, simulate


ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / "research_protocol.json"
SOURCE_FILES = ("simulator.py", "market_scenarios.py", "diagnostics.py",
                "research.py", "research_protocol.json")
TOLERANCE = 1e-7


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name: _sha256(ROOT / name) for name in SOURCE_FILES}


def load_protocol():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if protocol["schema_version"] != 1:
        raise ValueError("unsupported protocol schema")
    names = [case["name"] for case in protocol["cases"]]
    if not names or len(names) != len(set(names)):
        raise ValueError("protocol cases must have unique names")
    policies = protocol["policies"]
    if not policies or len(policies) != len(set(policies)):
        raise ValueError("protocol policies must be unique")
    for contrast in protocol["contrasts"]:
        if contrast["left"] not in policies or contrast["right"] not in policies:
            raise ValueError("unknown policy in protocol contrast")
    return protocol


def positive_int(value):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _check_integer(name, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def _require_close(label, actual, expected):
    if not math.isfinite(actual) or not math.isfinite(expected):
        raise ArithmeticError(f"non-finite accounting value: {label}")
    if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=TOLERANCE):
        raise ArithmeticError(f"{label}: {actual} != {expected}")
    return actual - expected


def validate_path(events, config, result):
    """Audit the trace against an independently reconstructed cash ledger."""
    metrics, trace = result["metrics"], result["trace"]
    if len(trace) != len(events) or not trace:
        raise ArithmeticError("the research audit requires a complete trace")
    cash, inventory = config.initial_cash, config.initial_inventory
    initial_equity = cash + inventory * events[0].mid_price
    carry = revaluation = edge = fees = 0.0
    max_ledger_residual = 0.0
    for event, row in zip(events, trace):
        if row["step"] != event.step:
            raise ArithmeticError("trace/event steps do not match")
        quantity = row["quantity"] if row["filled"] else 0
        if (row["filled"] and quantity not in (-1, 1)) or row["quantity"] != quantity:
            raise ArithmeticError("invalid trace fill quantity")
        if not row["filled"] and row["fee"] != 0:
            raise ArithmeticError("unfilled order paid an execution fee")
        price_move = event.next_mid - event.mid_price
        carry += inventory * price_move
        revaluation += quantity * price_move
        if row["filled"]:
            cash -= quantity * row["execution_price"] + row["fee"]
            edge += quantity * (event.mid_price - row["execution_price"])
        inventory += quantity
        fees += row["fee"]
        if row["inventory"] != inventory:
            raise ArithmeticError("trace inventory does not match filled quantities")
        residual = _require_close("trace cash ledger", row["cash"], cash)
        max_ledger_residual = max(max_ledger_residual, abs(residual))
        residual = _require_close("trace wealth ledger", row["equity"],
                                  cash + inventory * event.next_mid)
        max_ledger_residual = max(max_ledger_residual, abs(residual))
    _require_close("initial equity", metrics["initial_equity"], initial_equity)
    _require_close("total fees", metrics["total_fees"], fees)
    _require_close("spread capture", metrics["spread_capture"], edge)
    _require_close("inventory carry", metrics["inventory_carry_pnl"], carry)
    _require_close("fill revaluation", metrics["fill_revaluation_pnl"], revaluation)
    _require_close("terminal cash", metrics["final_cash"], cash)
    if metrics["final_inventory"] != inventory or metrics["inventory_after_liquidation"] != 0:
        raise ArithmeticError("terminal inventory failed to reconcile")
    liquidation = abs(inventory) * (config.liquidation_half_spread + config.fee_per_trade)
    final_equity = cash + inventory * events[-1].next_mid
    _require_close("final marked equity", metrics["final_equity"], final_equity)
    _require_close("liquidation cost", metrics["liquidation_cost"], liquidation)
    _require_close("liquidated final wealth", metrics["final_cash_after_liquidation"],
                   final_equity - liquidation)
    _require_close("marked pnl", metrics["pnl_mtm"], final_equity - initial_equity)
    inventory_residual = _require_close(
        "inventory split", metrics["inventory_pnl"], carry + revaluation)
    terminal_residual = _require_close(
        "terminal pnl decomposition", metrics["pnl_liquidated"],
        edge + carry + revaluation - fees - liquidation)
    _require_close("terminal pnl wealth", metrics["pnl_liquidated"],
                   final_equity - liquidation - initial_equity)
    return dict(terminal_accounting_residual=terminal_residual,
                inventory_split_residual=inventory_residual,
                max_trace_ledger_residual=max_ledger_residual)


def validate_as_widths(symmetric_trace, inventory_trace):
    if not symmetric_trace or len(symmetric_trace) != len(inventory_trace):
        raise ArithmeticError("AS pair requires matching complete traces")
    max_gap = 0.0
    for left, right in zip(symmetric_trace, inventory_trace):
        if left["step"] != right["step"]:
            raise ArithmeticError("AS pair trace steps differ")
        left_width, right_width = left["ask"] - left["bid"], right["ask"] - right["bid"]
        gap = abs(left_width - right_width)
        if gap > 1e-9:
            raise ArithmeticError("AS pair total quote widths differ")
        max_gap = max(max_gap, gap)
    return max_gap


def assert_complete_paths(rows, case_names, seeds, policies):
    expected = {(case, seed, policy) for case in case_names
                for seed in seeds for policy in policies}
    observed = set()
    for row in rows:
        key = (row["case"], row["seed"], row["policy"])
        if key in observed:
            raise ValueError(f"duplicate path key: {key}")
        observed.add(key)
    if observed != expected:
        missing, unexpected = expected - observed, observed - expected
        raise ValueError(f"incomplete pairing: {len(missing)} missing, "
                         f"{len(unexpected)} unexpected path keys")


def _bootstrap_seed(base, *labels):
    digest = hashlib.sha256(_canonical([base, *labels])).digest()
    return int.from_bytes(digest[:8], "big")


def summarize_paths(rows, metric_names):
    groups = defaultdict(list)
    for row in rows:
        groups[row["case"], row["policy"]].append(row)
    result = []
    for (case, policy), samples in groups.items():
        summary = dict(case=case, policy=policy, runs=len(samples),
                       loss_probability=mean(row["pnl_liquidated"] < 0 for row in samples))
        for name in metric_names:
            column = name if name.startswith("mean_") else f"mean_{name}"
            summary[column] = mean(row[name] for row in samples)
        result.append(summary)
    return result


def paired_rows(rows, protocol, resamples, bootstrap_seed):
    groups = defaultdict(dict)
    for row in rows:
        group = groups[row["case"], row["policy"]]
        if row["seed"] in group:
            raise ValueError("duplicate seed in paired aggregation")
        group[row["seed"]] = row
    result = []
    for case in protocol["cases"]:
        for contrast in protocol["contrasts"]:
            left = groups[case["name"], contrast["left"]]
            right = groups[case["name"], contrast["right"]]
            for metric in protocol["analysis"]["paired_metrics"]:
                estimate = paired_difference(
                    {seed: row[metric] for seed, row in left.items()},
                    {seed: row[metric] for seed, row in right.items()},
                    resamples=resamples,
                    seed=_bootstrap_seed(bootstrap_seed, case["name"], contrast["name"], metric))
                win_fraction = estimate.pop("probability_positive")
                result.append(dict(case=case["name"], contrast=contrast["name"],
                                   left_policy=contrast["left"], right_policy=contrast["right"],
                                   metric=metric,
                                   primary=metric == protocol["analysis"]["primary_metric"],
                                   **estimate, paired_win_fraction=win_fraction,
                                   resamples=resamples,
                                   ci_method=protocol["analysis"]["ci_method"]))
    return result


def markout_rows(rows, protocol, resamples, bootstrap_seed):
    groups = defaultdict(list)
    for row in rows:
        groups[row["case"], row["policy"]].append(row)
    result = []
    for (case, policy), samples in groups.items():
        for horizon in protocol["analysis"]["markout_horizons"]:
            prefix = f"h{horizon}_"
            estimate = pooled_markout_interval(
                samples, horizon, resamples=resamples,
                seed=_bootstrap_seed(bootstrap_seed, case, policy, horizon))
            count = estimate["total_fills"]
            pooled = dict(case=case, policy=policy, horizon=horizon,
                          **{f"mid_move_{key}" if key in ("mean", "ci_low", "ci_high")
                             else key: value for key, value in estimate.items()},
                          censored_fills=sum(row[prefix + "censored"] for row in samples),
                          execution_markout_mean=(sum(row[prefix + "execution_markout_sum"]
                                                     for row in samples) / count if count else None),
                          edge_mean=(sum(row[prefix + "edge_sum"] for row in samples)
                                     / count if count else None),
                          resamples=resamples,
                          ci_method=protocol["analysis"]["ci_method"])
            for label in ("informed", "uninformed"):
                subgroup_count = sum(row[prefix + label + "_count"] for row in samples)
                subgroup_sum = sum(row[prefix + label + "_mid_move_sum"] for row in samples)
                pooled[label + "_count"] = subgroup_count
                pooled[label + "_mid_move_mean"] = (subgroup_sum / subgroup_count
                                                       if subgroup_count else None)
            result.append(pooled)
    return result


def _write_csv(path, rows):
    if not rows:
        raise ValueError(f"refusing an empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
                    + "\n", encoding="utf-8")


def run_research(output_dir, *, runs=None, steps=None, seed_start=None,
                 paired_resamples=None, markout_resamples=None, bootstrap_seed=None,
                 command=None, progress=None):
    """Execute one versioned run; optional small overrides are labelled exploratory."""
    protocol = load_protocol()
    defaults = {key: protocol["simulation"][key] for key in ("runs", "steps", "seed_start")}
    defaults.update({key: protocol["analysis"][key]
                     for key in ("paired_resamples", "markout_resamples", "bootstrap_seed")})
    supplied = dict(runs=runs, steps=steps, seed_start=seed_start,
                    paired_resamples=paired_resamples, markout_resamples=markout_resamples,
                    bootstrap_seed=bootstrap_seed)
    settings = {key: defaults[key] if value is None else value
                for key, value in supplied.items()}
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
    for case in protocol["cases"]:
        scenario = Scenario(name=case["name"], label=case["name"], steps=settings["steps"],
                            **{**protocol["scenario_defaults"], **case["scenario_overrides"]})
        config = Config(**{**protocol["config"], **case["config_overrides"]})
        resolved_cases.append(dict(case=case["name"], scenario=asdict(scenario), config=asdict(config)))
        for seed in seeds:
            events = generate_controlled_market(
                scenario, seed, initial_mid=protocol["simulation"]["initial_mid"],
                tick_size=config.tick_size)
            path_results, path_rows = {}, []
            for policy in protocol["policies"]:
                result = simulate(events, config, policy, record=True, risk=risk)
                if metric_names is None:
                    metric_names = tuple(result["metrics"])
                elif set(result["metrics"]) != set(metric_names):
                    raise ValueError("simulation metric schema changed during the run")
                audit = validate_path(events, config, result)
                diagnostics = fill_markouts(result["trace"], protocol["analysis"]["markout_horizons"])
                path_rows.append(dict(case=case["name"], seed=seed, policy=policy,
                                      **result["metrics"], **diagnostics, **audit))
                path_results[policy] = result
                if seed == settings["seed_start"]:
                    representative_traces[case["name"], policy] = [
                        dict(case=case["name"], seed=seed, policy=policy, **row)
                        for row in result["trace"]]
            width_gap = validate_as_widths(path_results["as_symmetric"]["trace"],
                                            path_results["as_inventory"]["trace"])
            for row in path_rows:
                row["as_pair_max_quote_width_gap"] = width_gap
            rows.extend(path_rows)
        if progress:
            progress(f"Simulated and audited {case['name']}: {settings['runs']} shared paths")
    assert_complete_paths(rows, [case["name"] for case in protocol["cases"]],
                          seeds, protocol["policies"])
    summaries = summarize_paths(rows, metric_names)
    if progress:
        progress("Computing paired path bootstrap intervals")
    paired = paired_rows(rows, protocol, settings["paired_resamples"], settings["bootstrap_seed"])
    if progress:
        progress("Computing pooled markout path bootstrap intervals")
    markouts = markout_rows(rows, protocol, settings["markout_resamples"], settings["bootstrap_seed"])
    if hashes != source_hashes():
        raise RuntimeError("source files changed during the run; results were not saved")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory became nonempty during the run: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, table in (("paths.csv", rows), ("summary.csv", summaries),
                            ("paired.csv", paired), ("markouts.csv", markouts)):
        _write_csv(output_dir / filename, table)
    _write_json(output_dir / "protocol.json", protocol)
    for (case, policy), trace in representative_traces.items():
        _write_csv(output_dir / "traces" /
                   f"{case}__{policy}__seed_{settings['seed_start']}.csv", trace)
    outputs = {str(path.relative_to(output_dir)): dict(sha256=_sha256(path), bytes=path.stat().st_size)
               for path in sorted(output_dir.rglob("*")) if path.is_file()}
    fingerprint_payload = dict(protocol_sha256=hashes["research_protocol.json"],
                               source_sha256=hashes, settings=settings,
                               python_version=platform.python_version())
    manifest = dict(
        protocol_id=protocol["protocol_id"],
        run_classification="exploratory" if overrides else "planned_confirmatory_simulation",
        overrides=overrides, settings=settings, frozen_protocol=protocol,
        resolved_cases=resolved_cases, risk_parameters=asdict(risk),
        seed_range_inclusive=[seeds.start, seeds.stop - 1],
        source_sha256=hashes, source_directory=str(ROOT),
        fingerprint=hashlib.sha256(_canonical(fingerprint_payload)).hexdigest(),
        command=command or "Python API: research.run_research",
        runtime=dict(python=sys.version, implementation=platform.python_implementation(),
                     platform=platform.platform()),
        started_utc=started, completed_utc=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=time.perf_counter() - clock_start,
        validation=dict(path_count=len(rows), expected_path_count=len(protocol["cases"])
                        * settings["runs"] * len(protocol["policies"]),
                        unique_complete_pairing=True,
                        max_terminal_accounting_residual=max(abs(row["terminal_accounting_residual"])
                                                             for row in rows),
                        max_inventory_split_residual=max(abs(row["inventory_split_residual"])
                                                         for row in rows),
                        max_trace_ledger_residual=max(row["max_trace_ledger_residual"] for row in rows),
                        max_as_pair_quote_width_gap=max(row["as_pair_max_quote_width_gap"] for row in rows),
                        accounting_absolute_tolerance=TOLERANCE,
                        representative_trace_selection="first seed, fixed before observing outcomes"),
        output_files=outputs,
        output_hash_note="manifest.json is excluded from its own output hashes")
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def make_parser():
    protocol = load_protocol()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "research_results" / "v2")
    for name in ("runs", "steps"):
        parser.add_argument("--" + name, type=positive_int, default=protocol["simulation"][name])
    parser.add_argument("--seed-start", type=int, default=protocol["simulation"]["seed_start"])
    for name in ("paired_resamples", "markout_resamples"):
        parser.add_argument("--" + name.replace("_", "-"), type=positive_int,
                            default=protocol["analysis"][name])
    parser.add_argument("--bootstrap-seed", type=int, default=protocol["analysis"]["bootstrap_seed"])
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    command_args = sys.argv[1:] if argv is None else list(argv)
    try:
        manifest = run_research(**vars(args),
                                command=shlex.join([sys.executable, str(Path(__file__).resolve()),
                                                    *command_args]),
                                progress=lambda value: print(value, flush=True))
    except (ValueError, ArithmeticError, RuntimeError, OSError) as exc:
        parser.exit(1, f"research failed: {exc}\n")
    print(f"Saved {manifest['validation']['path_count']} audited policy paths to {args.output_dir}")
    print(f"Run classification: {manifest['run_classification']}")
    print(f"Fingerprint: {manifest['fingerprint']}")


if __name__ == "__main__":
    main()
