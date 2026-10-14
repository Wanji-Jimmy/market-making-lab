"""复查已有实验：把原有库存重估与本次成交后的价格变化分开。

不改变策略或市场，不选新参数。输入原实验的参数和种子，并逐条核对原盈亏。
这里只做一步分解；较长窗口的 markout 不能直接相加当作账户利润。
"""

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

from simulator import Config, Scenario, generate_market, simulate


def decompose(trace, initial_inventory):
    """做市商买入的 delta_q 为正；向客户卖出的 delta_q 为负。"""
    old_inventory_pnl = trade_price_move = 0.0
    previous_inventory = initial_inventory
    max_residual = 0.0
    for row in trace:
        delta_q = row["quantity"]
        assert row["inventory"] == previous_inventory + delta_q
        price_move = row["next_mid"] - row["mid_price"]
        old_part = previous_inventory * price_move
        trade_part = delta_q * price_move
        reconstructed = row["edge"] + old_part + trade_part - row["fee"]
        residual = abs(reconstructed - row["net_change"])
        if residual > 1e-8:
            raise ArithmeticError("逐步盈亏分解不平")
        max_residual = max(max_residual, residual)
        old_inventory_pnl += old_part
        trade_price_move += trade_part
        previous_inventory = row["inventory"]
    return old_inventory_pnl, trade_price_move, max_residual


def main():
    root = Path(__file__).resolve().parent
    original = json.loads((root / "results/results.json").read_text())
    meta = original["metadata"]
    for name in ("simulator.py", "experiment.py", "lesson.py"):
        actual_hash = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual_hash != meta["source_sha256"][name]:
            raise RuntimeError(f"{name} 与原实验版本不一致，请先核对版本")
    config = Config(**meta["config"])
    rows, summaries = [], []
    for case in original["scenarios"]:
        scenario = Scenario(**case["parameters"])
        original_paths = {(r["seed"], r["strategy"]): r for r in case["paths"]}
        grouped = {"fixed": [], "inventory": []}
        for seed in range(meta["seed_start"], meta["seed_start"] + meta["seed_count"]):
            events = generate_market(scenario, seed, tick_size=config.tick_size)
            for strategy in grouped:
                run = simulate(events, config, strategy)
                metrics = run["metrics"]
                old, new, residual = decompose(run["trace"], config.initial_inventory)
                expected = original_paths[seed, strategy]["pnl_liquidated"]
                if not math.isclose(metrics["pnl_liquidated"], expected, abs_tol=1e-8, rel_tol=0):
                    raise ArithmeticError("重放盈亏与原结果不一致")
                reconstructed = metrics["spread_capture"] + old + new - metrics["total_fees"] - metrics["liquidation_cost"]
                if not math.isclose(reconstructed, expected, abs_tol=1e-7, rel_tol=0):
                    raise ArithmeticError("最终清仓盈亏分解不平")
                row = {
                    "case": case["name"], "strategy": strategy, "seed": seed,
                    "spread_capture": metrics["spread_capture"],
                    "old_inventory_pnl": old, "trade_price_move": new,
                    "fees": metrics["total_fees"], "liquidation_cost": metrics["liquidation_cost"],
                    "pnl_liquidated": metrics["pnl_liquidated"],
                    "trades": metrics["trades"], "mean_abs_inventory": metrics["mean_abs_inventory"],
                    "max_step_residual": residual,
                }
                rows.append(row)
                grouped[strategy].append(row)
        for strategy, values in grouped.items():
            summary = {"case": case["name"], "label": case["label"], "strategy": strategy, "n": len(values)}
            for key in ("spread_capture", "old_inventory_pnl", "trade_price_move", "fees", "liquidation_cost", "pnl_liquidated", "trades", "mean_abs_inventory"):
                summary[key] = statistics.mean(r[key] for r in values)
            total_trades = sum(r["trades"] for r in values)
            summary["price_move_per_fill"] = sum(r["trade_price_move"] for r in values) / total_trades if total_trades else None
            summaries.append(summary)
        print(f"已核对 {case['label']}：{len(grouped['fixed'])} 条相同市场路径", flush=True)

    output = root / "research_upgrade"
    output.mkdir(exist_ok=True)
    with (output / "pnl_diagnostics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "source_metadata": meta, "summaries": summaries, "replayed_strategy_runs": len(rows),
        "max_step_residual": max(r["max_step_residual"] for r in rows),
        "interpretation": "Descriptive one-step accounting attribution, not causal identification. Signed post-fill price movement excludes execution edge. Multi-horizon markouts must not be summed as PnL.",
    }
    (output / "pnl_diagnostics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 已有实验的盈亏诊断", "",
        "仅重放原主实验：6种情景，每种500条路径，每条300步，两种策略。未重新选择参数或改变任何报价。", "",
        "每步恒等式：ΔV = Δq(S−p) + q_before(S_next−S) + Δq(S_next−S) − fee。", "",
        "前两项分别是成交价格优势和旧库存重估；第三项是本次成交对应的下一步价格变化。它衡量成交后的不利/有利价格移动，但并不独立证明客户拥有信息或交易导致了价格变化。", "",
        "所有项先按一条路径求和，再跨路径取均值；最后扣期末清仓成本。", "",
        "| 情景 | 策略 | 成交优势 | 旧库存重估 | 本次成交后价格变化 | 手续费 | 清仓成本 | 净盈亏 | 每笔成交后的价格变化 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in summaries:
        strategy_name = "A 固定价差" if s["strategy"] == "fixed" else "B 库存调整"
        lines.append(f"| {s['label']} | {strategy_name} | {s['spread_capture']:.2f} | {s['old_inventory_pnl']:.2f} | {s['trade_price_move']:.2f} | {s['fees']:.2f} | {s['liquidation_cost']:.2f} | {s['pnl_liquidated']:.2f} | {s['price_move_per_fill']:.4f} |")
    lines += ["", "注意：单个分项为负不等于策略有错误。旧库存重估也可能包含之前成交后继续承担的价格变化。这里严格按一步分解，不把不同时间窗口的markout重叠相加。所有数据仍是人工模拟。", "",
              "这两个价格变化分项是代数归因，不是互相独立的风险：原持有1股、本步卖光后价格跌10，旧库存项为−10、新成交项为+10，恰好抵消，账户已没有持仓损失。", "",
              "成交数量不同会影响总额；新成交项总额更负不等于每笔成交更差。每笔的成交量加权均值见表，未计算其差值区间，不据此判断每笔改善明确。", ""]
    (output / "盈亏诊断.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
