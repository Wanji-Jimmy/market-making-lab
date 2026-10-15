"""真实数据 → 冻结预测 → 报价实验；开发、首次测试、复算分开留痕。

这不是历史成交回测。报价规则只看当前输入，成交意愿仍是假设。
示例：python3 flow_research.py --stage develop，然后 --stage test。
已有结果的复算用 --stage reproduce --offline，保留首次测试文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
from datetime import datetime, timezone
from pathlib import Path

import flow_data
import flow_policy
from real_data import sha256_file, write_csv

ROOT = Path(__file__).resolve().parent
PLAN_PATH = ROOT / "research/FLOW_SIGNAL_PLAN.json"
SOURCE_FILES = ("flow_research.py", "flow_data.py", "flow_policy.py", "real_data.py", "simulator.py",
                "test_flow_research.py", "test_flow_data.py", "test_flow_policy.py")
POLICIES = ("fixed", "inventory", "constant_wide", "flow", "intercept_only")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def value_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def source_hashes(root: Path = ROOT) -> dict:
    return {name: sha256_file(root / name) for name in SOURCE_FILES}


def load_plan() -> dict:
    plan = read_json(PLAN_PATH)
    expected = {"train_dates": flow_data.TRAIN_DATES,
                "validation_dates": flow_data.VALIDATION_DATES, "test_dates": flow_data.TEST_DATES,
                "policies": POLICIES}
    for key, values in expected.items():
        if tuple(plan[key]) != tuple(values):
            raise ValueError(f"计划与实现不符：{key}")
    if plan["bucket_seconds"] * 1_000_000 != flow_data.BUCKET_US or plan["signal_clip_bps"] != 2:
        raise ValueError("计划的桶宽或信号上限与实现不符")
    policy_parameters = {"half_spread_bps": flow_policy.HALF_SPREAD_BPS,
                         "constant_wide_half_spread_bps": flow_policy.WIDE_HALF_SPREAD_BPS,
                         "inventory_skew_bps_per_unit": flow_policy.INVENTORY_SKEW_BPS,
                         "inventory_limit": flow_policy.INVENTORY_LIMIT,
                         "initial_cash": flow_policy.INITIAL_CASH, "initial_inventory": 0,
                         "normalized_tick": flow_policy.TICK_SIZE,
                         "liquidation_half_spread_bps": flow_policy.LIQUIDATION_HALF_SPREAD_BPS}
    if any(plan["replay"][key] != value for key, value in policy_parameters.items()):
        raise ValueError("计划的报价参数与实现不符")
    if tuple(flow_policy.STRATEGIES) != POLICIES or flow_policy.MAX_SIGNAL_BPS != plan["signal_clip_bps"]:
        raise ValueError("计划的策略或信号限制与实现不符")
    splits = [set(plan[key]) for key in ("train_dates", "validation_dates", "test_dates")]
    if any(splits[i] & splits[j] for i in range(3) for j in range(i + 1, 3)):
        raise ValueError("日期划分重叠")
    if not max(splits[0]) < min(splits[1]) <= max(splits[1]) < min(splits[2]):
        raise ValueError("划分必须按时间先后排列")
    return plan


def load_dates(dates: list[str], raw_dir: Path, offline: bool) -> dict:
    loaded = {}
    for day in dates:
        print(f"Reading {day} (checksum-verified public archive)...", flush=True)
        rows, quality, source = flow_data.load_day(day, raw_dir, offline)
        if not rows or any(row["date"] != day for row in rows):
            raise ValueError("日期为空或混入其他日期")
        if quality["missing_aggregate_ids"]:
            raise ValueError("成交 ID 缺口需要调查，停止本轮分析")
        loaded[day] = {"rows": rows, "quality": quality, "source": source}
    return loaded


def fit_training(loaded: dict, plan: dict) -> dict:
    # 即使调用者混入验证日也拒绝，而不是依赖调用顺序或命名习惯。
    if set(loaded) != set(plan["train_dates"]):
        raise ValueError("拟合只能使用预先声明的全部训练日期")
    rows = [row for day in plan["train_dates"] for row in loaded[day]["rows"]]
    if any(row["date"] not in plan["train_dates"] for row in rows):
        raise ValueError("训练行混入其他划分")
    model = flow_data.fit_ols(rows)
    model.update({"train_dates": list(plan["train_dates"]),
                  "train_mean_bps": math.fsum(row["target_return_bps"] for row in rows) / len(rows),
                  "signal_clip_bps": plan["signal_clip_bps"]})
    return model


def forecast_report(loaded: dict, model: dict) -> dict:
    daily = {day: flow_data.forecast_metrics(data["rows"], model, model["train_mean_bps"])
             for day, data in loaded.items()}
    return {"daily": daily, "pooled": flow_data.merge_metrics(daily.values()),
            "sources": {day: data["source"] for day, data in loaded.items()},
            "quality": {day: data["quality"] for day, data in loaded.items()}}


def make_freeze(output: Path, development: dict, root: Path = ROOT, plan_path: Path = PLAN_PATH) -> dict:
    freeze = {"frozen_at_utc": utc_now(), "python_version": platform.python_version(),
              "plan_sha256": sha256_file(plan_path), "model_sha256": sha256_file(output / "model.json"),
              "development_sha256": sha256_file(output / "development.json"),
              "source_sha256": source_hashes(root),
              "development_archives": {day: source["sha256"] for split in ("train", "validation")
                                       for day, source in development[split]["sources"].items()},
              "test_dates": read_json(plan_path)["test_dates"],
              "notice": "Frozen before first test access. Unchanged reruns reproduce this experiment; they do not create new holdout evidence."}
    save_json(output / "freeze.json", freeze)
    return freeze


def verify_freeze(output: Path, root: Path = ROOT, plan_path: Path = PLAN_PATH) -> dict:
    freeze = read_json(output / "freeze.json")
    checks = {"plan_sha256": sha256_file(plan_path), "model_sha256": sha256_file(output / "model.json"),
              "development_sha256": sha256_file(output / "development.json"),
              "source_sha256": source_hashes(root)}
    for key, actual in checks.items():
        if freeze[key] != actual:
            raise ValueError(f"冻结校验失败：{key}。不要覆盖冻结记录；修正必须另作研究记录。")
    if freeze["test_dates"] != read_json(plan_path)["test_dates"]:
        raise ValueError("测试日期与冻结记录不符")
    return freeze


def develop(output: Path, raw_dir: Path, offline: bool, plan: dict) -> None:
    if any((output / name).exists() for name in ("freeze.json", "test_access.json", "test.json")):
        raise ValueError("已有冻结或测试记录；复算请用 --stage reproduce，不重写研究历史")
    if any((raw_dir / f"ETHUSDT-aggTrades-{day}.zip").exists() for day in plan["test_dates"]):
        raise ValueError("测试档案已存在；不能宣称新未见测试。使用原冻结记录复算或另写新研究计划。")
    train = load_dates(plan["train_dates"], raw_dir, offline)
    model = fit_training(train, plan)
    validation = load_dates(plan["validation_dates"], raw_dir, offline)
    development = {"generated_at_utc": utc_now(), "train": forecast_report(train, model),
                   "validation": forecast_report(validation, model),
                   "selection": "No model/parameter/policy selection from validation results; the single declared model is retained."}
    save_json(output / "model.json", model)
    save_json(output / "development.json", development)
    make_freeze(output, development)
    print(json.dumps({"model": model, "validation": development["validation"]["pooled"]}, indent=2), flush=True)


def run_policies(loaded: dict, model: dict, plan: dict) -> list[dict]:
    settings = plan["replay"]
    cases = [("primary", seed, settings["fee_bps"], settings["urgency_bps"])
             for seed in settings["primary_seeds"]]
    cases.extend((case["name"], settings["sensitivity_seed"], case["fee_bps"], case["urgency_bps"])
                 for case in settings["sensitivity_cases"])
    runs = []
    for day, data in loaded.items():
        print(f"Replaying five declared policies on {day}...", flush=True)
        for case, seed, fee, urgency in cases:
            for policy in POLICIES:
                result = flow_policy.replay_day(data["rows"], model, policy, seed=seed,
                                               fee_bps=fee, urgency_bps=urgency)
                runs.append({"date": day, "case": case, **result["summary"]})
    return runs


def summarize_runs(runs: list[dict]) -> dict:
    """先在每一天内合并种子；种子不是新增的独立市场日期。"""
    groups = {}
    for run in runs:
        key = (run["case"], run["date"], run["strategy"])
        groups.setdefault(key, []).append(run)
    daily = []
    for (case, day, strategy), group in groups.items():
        numeric = {key: statistics.fmean(row[key] for row in group)
                   for key, value in group[0].items()
                   if isinstance(value, (int, float)) and not isinstance(value, bool) and key != "seed"}
        daily.append({"case": case, "date": day, "strategy": strategy, "seed_replicates": len(group), **numeric})
    return {"daily_seed_means": daily,
            "note": "Daily seed means describe the same historical path under willingness replicates. No seed-based inference or significance claim."}


def evaluate_test(output: Path, raw_dir: Path, offline: bool, plan: dict, reproduce: bool = False) -> None:
    # 必须先验证，才允许读取/下载测试日。失败也不得越过该门。
    freeze = verify_freeze(output)
    model = read_json(output / "model.json")
    access_path = output / "test_access.json"
    if not reproduce and access_path.exists():
        raise ValueError("测试日期已打开；请用 --stage reproduce，保留首次记录")
    if reproduce and not access_path.exists():
        raise ValueError("尚无首次测试记录；先运行 --stage test")
    if reproduce:
        access = read_json(access_path)
        current_freeze_hash = sha256_file(output / "freeze.json")
        if access["freeze_sha256"] != current_freeze_hash or access["dates"] != plan["test_dates"]:
            raise ValueError("首次测试访问记录与当前冻结版本/日期不符，停止读取数据")
        if (output / "test.json").exists():
            original_test = read_json(output / "test.json")
            if (original_test["freeze_sha256"] != current_freeze_hash or
                    original_test["model_sha256"] != sha256_file(output / "model.json")):
                raise ValueError("首次测试结果与当前冻结版本/模型不符，停止读取数据")
        train = load_dates(plan["train_dates"], raw_dir, offline)
        if fit_training(train, plan) != model:
            raise ValueError("复算的训练模型与冻结模型不一致")
        validation = load_dates(plan["validation_dates"], raw_dir, offline)
        if any(data["source"]["sha256"] != freeze["development_archives"][day]
               for loaded in (train, validation) for day, data in loaded.items()):
            raise ValueError("复算开发档案与冻结 SHA256 不一致")
        original = read_json(output / "development.json")
        for split, loaded in (("train", train), ("validation", validation)):
            report = forecast_report(loaded, model)
            for key in ("daily", "pooled", "quality"):
                if report[key] != original[split][key]:
                    raise ValueError(f"开发期复算不一致：{split}/{key}")
        del train, validation
    else:
        cached = [day for day in plan["test_dates"] if (raw_dir / f"ETHUSDT-aggTrades-{day}.zip").exists()]
        if cached:
            raise ValueError("首次测试前发现测试缓存，需人工调查并诚实记录此前访问，不能标记未见数据")
        save_json(access_path, {"first_access_started_at_utc": utc_now(),
                  "freeze_sha256": sha256_file(output / "freeze.json"),
                  "dates": plan["test_dates"], "state": "Test access opened before download; a failure also counts as access. Reproduction never erases this record."})
    loaded = load_dates(plan["test_dates"], raw_dir, offline)
    if reproduce and (output / "test.json").exists():
        original_sources = read_json(output / "test.json")["forecast"]["sources"]
        if any(data["source"]["sha256"] != original_sources[day]["sha256"] for day, data in loaded.items()):
            raise ValueError("测试源档案发生变化，停止复算")
    runs = run_policies(loaded, model, plan)
    report = {"generated_at_utc": utc_now(), "freeze_sha256": sha256_file(output / "freeze.json"),
              "model_sha256": sha256_file(output / "model.json"), "reproduction": reproduce,
              "forecast": forecast_report(loaded, model), "policy": summarize_runs(runs),
              "interpretation": "Empirical trade-price forecasts; hypothetical fills, normalized units, assumed costs. Not executable historical P&L."}
    stem = "reproduction" if reproduce else "test"
    save_json(output / f"{stem}.json", report)
    write_csv(output / f"{stem}_runs.csv", runs)
    if reproduce and (output / "test.json").exists():
        original = read_json(output / "test.json")
        forecast_matches = all(report["forecast"][key] == original["forecast"][key]
                               for key in ("daily", "pooled", "quality"))
        identical = forecast_matches and report["policy"] == original["policy"]
        save_json(output / "reproduction_check.json", {"checked_at_utc": utc_now(),
                  "model_and_development_reproduced": True, "test_metrics_identical": identical,
                  "original_test_sha256": sha256_file(output / "test.json"),
                  "reproduction_sha256": sha256_file(output / "reproduction.json")})
        if not identical:
            raise ValueError("复算结果与首次记录不同，请调查数值或环境差异")
    print(json.dumps(report["forecast"]["pooled"], indent=2), flush=True)
    print(f"Saved {stem}.json and {stem}_runs.csv; {len(runs)} policy runs.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("develop", "test", "reproduce"), required=True)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--output", type=Path, default=ROOT / "flow_research_results")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plan = load_plan()
    if args.stage == "develop":
        develop(args.output, args.raw_dir, args.offline, plan)
    else:
        evaluate_test(args.output, args.raw_dir, args.offline, plan, args.stage == "reproduce")


if __name__ == "__main__":
    main()
