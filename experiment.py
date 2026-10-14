"""固定参数、共享外生市场、配对比较。只使用 Python 标准库。"""

import argparse
import csv
import hashlib
import json
import math
import statistics
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from lesson import make_lesson
from simulator import Config, Scenario, generate_market, simulate


SCENARIO_SPECS = [
    ("neutral_low", "平衡客户／低波动", "普通客户买卖机会均衡，价格变化与客户意愿独立。", {}),
    ("neutral_high", "平衡客户／高波动", "仅增加价格波动，观察持有库存的风险。", {"volatility": 1.2}),
    ("selling_down", "客户卖出偏多／下跌", "75%普通客户想卖，价格每步平均下降0.06；这两个因素共同设为压力情景。", {"buy_probability": 0.25, "drift": -0.06, "volatility": 0.5}),
    ("buying_up", "客户买入偏多／上涨", "75%普通客户想买，价格每步平均上升0.06；这是镜像压力情景。", {"buy_probability": 0.75, "drift": 0.06, "volatility": 0.5}),
    ("jumps", "偶发价格跳跃", "每步2%概率产生正负8元的额外跳跃；并非来自真实市场估计。", {"volatility": 0.5, "jump_probability": 0.02, "jump_size": 8.0}),
    ("informed", "客户拥有额外信息", "75%到达客户的价格底线与下一步价格变化有关；策略无法读取该信息。", {"volatility": 1.2, "informed_fraction": 0.75}),
]

# 在观察实验结果前确定这些敏感性检查，不按结果挑选参数或种子。
SENSITIVITY_SPECS = [
    ("higher_fee", "费用提高至每股0.50", "neutral_low", {"fee_per_trade": 0.5}, {}),
    ("fee_exceeds_edge", "费用提高至每股1.10", "neutral_low", {"fee_per_trade": 1.1}, {}),
    ("weak_skew", "较弱库存调整 k=0.025", "neutral_low", {"skew_strength": 0.025}, {}),
    ("strong_skew", "较强库存调整 k=0.20", "neutral_low", {"skew_strength": 0.2}, {}),
    ("uniform_demand", "客户意愿改用均匀分布", "neutral_low", {}, {"demand_model": "uniform"}),
    ("uniform_stress", "下跌压力＋均匀客户意愿", "selling_down", {}, {"demand_model": "uniform"}),
    ("long_only", "仅持有股票：不允许卖空", "neutral_low", {"allow_short": False, "initial_inventory": 2}, {}),
    ("costly_exit", "期末清仓每股价差成本2.00", "neutral_low", {"liquidation_half_spread": 2.0}, {}),
]


def summarize(rows):
    values = [r["pnl_liquidated"] for r in rows]
    if not values:
        raise ValueError("至少需要一条实验路径")
    tail_count = max(1, math.ceil(len(values) * 0.05))
    avg = lambda key: statistics.mean(r[key] for r in rows)
    return {
        "n": len(rows),
        "mean_pnl": statistics.mean(values),
        "median_pnl": statistics.median(values),
        "loss_probability": sum(x < 0 for x in values) / len(values),
        "tail_mean_pnl": statistics.mean(sorted(values)[:tail_count]),
        "tail_count": tail_count,
        "mean_trades": avg("trades"),
        "mean_abs_inventory": avg("mean_abs_inventory"),
        "mean_max_inventory": avg("max_abs_inventory"),
        "mean_liquidation_cost": avg("liquidation_cost"),
        "mean_spread_capture": avg("spread_capture"),
        "mean_inventory_pnl": avg("inventory_pnl"),
        "mean_fees": avg("total_fees"),
        "mean_quote_clips": avg("quote_clips"),
        "mean_blocked_inventory": avg("blocked_inventory"),
        "mean_max_drawdown": avg("max_drawdown"),
    }


def paired_difference(fixed, inventory):
    # 相同种子的B-A，不把两个独立样本的波动当成配对比较的误差。
    differences = [b["pnl_liquidated"] - a["pnl_liquidated"] for a, b in zip(fixed, inventory)]
    estimate = statistics.mean(differences)
    standard_error = statistics.stdev(differences) / math.sqrt(len(differences)) if len(differences) > 1 else 0.0
    return {
        "mean_difference": estimate,
        "standard_error": standard_error,
        "ci_low": estimate - 1.96 * standard_error,
        "ci_high": estimate + 1.96 * standard_error,
        "interval_method": "Monte Carlo paired mean normal approximation, 95%; model-conditional, not a real-market confidence claim",
    }


def run_batch(scenario, config, seeds):
    rows = {"fixed": [], "inventory": []}
    paths = []
    for seed in seeds:
        # 一次生成，分别交给A和B；成交不同不会使后续随机市场错位。
        events = generate_market(scenario, seed, tick_size=config.tick_size)
        for strategy in rows:
            result = simulate(events, config, strategy, record=False)
            metrics = result["metrics"]
            rows[strategy].append(metrics)
            paths.append({"seed": seed, "strategy": strategy, **metrics})
    return {
        "strategies": {strategy: summarize(values) for strategy, values in rows.items()},
        "paired": paired_difference(rows["fixed"], rows["inventory"]),
        "paths": paths,
    }


def write_csv(path, rows):
    if not rows:
        return
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def save_text_report(data, path):
    lines = [
        "# 实际模拟实验结果", "",
        "所有金额为模型中的假设货币单位。下表为计入期末清仓成本后的盈亏；没有真实交易数据或盈利证明。", "",
        f'每个主情景 {data["metadata"]["seed_count"]} 条路径，每条 {data["metadata"]["steps"]} 步；A/B共享外生输入。', "",
        "| 情景 | 策略 | 平均盈亏 | 亏损比例 | 最差5%平均盈亏 | 平均成交数 | 平均绝对库存 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    names = {"fixed": "A 固定价差", "inventory": "B 库存调整"}
    for case in data["scenarios"]:
        for strategy, stats in case["strategies"].items():
            lines.append(f'| {case["label"]} | {names[strategy]} | {stats["mean_pnl"]:.2f} | {stats["loss_probability"]:.1%} | {stats["tail_mean_pnl"]:.2f} | {stats["mean_trades"]:.1f} | {stats["mean_abs_inventory"]:.2f} |')
    lines += ["", "## 配对比较", "", "区间只描述该模拟模型内的蒙特卡洛均值误差，不反映模型错误，也不代表现实交易收益区间。", ""]
    for case in data["scenarios"]:
        pair = case["paired"]
        lines.append(f'- {case["label"]}：B−A平均差 {pair["mean_difference"]:.2f}，约95%区间 [{pair["ci_low"]:.2f}, {pair["ci_high"]:.2f}]。')
    lines += ["", "## 敏感性检查", "", "这些情景在首次完整运行前列定，未选择表现最好的参数作为默认。", "", "| 检查 | A 平均盈亏 | B 平均盈亏 | B−A |", "| --- | ---: | ---: | ---: |"]
    for case in data["sensitivity"]:
        lines.append(f'| {case["label"]} | {case["strategies"]["fixed"]["mean_pnl"]:.2f} | {case["strategies"]["inventory"]["mean_pnl"]:.2f} | {case["paired"]["mean_difference"]:.2f} |')
    lines += ["", "## 教学账本", "", "初始1000现金＋2股×100＝1200总价值，无费用、禁止卖空。", "", "| 步 | 客户方向 | 价格底线 | 报买价 | 报卖价 | 成交 | 现金 | 库存 | 新参考价 | 盈亏 |", "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |"]
    for row in data["lesson"]["trace"]:
        limit = "—" if row["limit_price"] is None else f'{row["limit_price"]:.2f}'
        lines.append(f'| {row["step"]} | {row["customer_side"] or "无客户"} | {limit} | {row["bid"]:.2f} | {row["ask"]:.2f} | {"是" if row["filled"] else "否"} | {row["cash"]:.2f} | {row["inventory"]} | {row["next_mid"]:.2f} | {row["pnl"]:.2f} |')
    lines += ["", "## 方法边界", "", "普通客户的交易意愿独立于下一步价格，本身给模型提供赚取价差的机会；知情客户情景用于检验这一假设失效时的结果。全体参数是人为场景，不是实证估计。", "", "研究模式允许有限负库存，模拟借券卖空，但未建模借券费、保证金、竞争者、排队、延迟及市场冲击。教学模式不允许卖空。参考价用于估值，不是可免费成交的价格。", "", "本项目使用Codex辅助实现。方法、模型假设与复现步骤见项目文档。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_experiment(output, runs=500, steps=300, seed_start=10000, sensitivity_runs=200):
    if runs < 2 or sensitivity_runs < 2 or steps < 1:
        raise ValueError("runs和sensitivity-runs至少2，steps至少1")
    output.mkdir(parents=True, exist_ok=True)
    config = Config()
    cases = []
    scenarios = {}
    all_rows = []
    for name, label, description, changes in SCENARIO_SPECS:
        scenario = Scenario(name=name, label=label, steps=steps, **changes)
        scenarios[name] = scenario
        case = {"name": name, "label": label, "description": description, "parameters": asdict(scenario), **run_batch(scenario, config, range(seed_start, seed_start + runs))}
        cases.append(case)
        all_rows += [{"batch": "main", "case": name, **row} for row in case["paths"]]
        print(f'完成 {label}: A={case["strategies"]["fixed"]["mean_pnl"]:.2f}, B={case["strategies"]["inventory"]["mean_pnl"]:.2f}', flush=True)

    sensitivities = []
    for name, label, scenario_name, config_changes, scenario_changes in SENSITIVITY_SPECS:
        scenario = replace(scenarios[scenario_name], **scenario_changes)
        altered_config = replace(config, **config_changes)
        batch = run_batch(scenario, altered_config, range(seed_start, seed_start + sensitivity_runs))
        all_rows += [{"batch": "sensitivity", "case": name, **row} for row in batch["paths"]]
        sensitivities.append({"name": name, "label": label, "scenario_name": scenario_name, "config_changes": config_changes, "scenario_changes": scenario_changes, "parameters": asdict(scenario), "config": asdict(altered_config), "strategies": batch["strategies"], "paired": batch["paired"]})
        print(f'完成敏感性检查: {label}', flush=True)

    example_scenario = scenarios["selling_down"]
    example_events = generate_market(example_scenario, seed_start, tick_size=config.tick_size)
    example = {"scenario": example_scenario.name, "label": example_scenario.label, "seed": seed_start, "initial_mid": 100.0, "initial_cash": config.initial_cash, "initial_inventory": config.initial_inventory, "traces": {}}
    for strategy in ("fixed", "inventory"):
        example["traces"][strategy] = simulate(example_events, config, strategy, record=True)["trace"]
        write_csv(output / f"example_{strategy}.csv", example["traces"][strategy])
    lesson = make_lesson()
    write_csv(output / "lesson.csv", lesson["trace"])
    write_csv(output / "example_market.csv", [asdict(event) for event in example_events])
    write_csv(output / "all_runs.csv", all_rows)

    root = Path(__file__).resolve().parent
    source_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("simulator.py", "experiment.py", "lesson.py")}
    data = {
        "metadata": {"created_at": datetime.now(timezone.utc).isoformat(), "seed_start": seed_start, "seed_count": runs, "sensitivity_seed_count": sensitivity_runs, "steps": steps, "config": asdict(config), "source_sha256": source_hashes, "note": "Synthetic single-asset market, uncalibrated scenario parameters; reference implementation written by Codex. Fixed seed range and predefined scenarios; no parameters selected by best results."},
        "scenarios": cases, "sensitivity": sensitivities, "example": example, "lesson": lesson,
    }
    (output / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    save_text_report(data, output / "研究结果.md")
    from build_report import build_report
    build_report(data, output / "report.html")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=500)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--seed-start", type=int, default=10000)
    parser.add_argument("--sensitivity-runs", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args()
    run_experiment(args.output, args.runs, args.steps, args.seed_start, args.sensitivity_runs)
