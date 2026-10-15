"""Compare two quoting rules across four small simulated markets."""

import argparse
import csv
from dataclasses import replace
from pathlib import Path
from statistics import mean

from simulator import Config, Scenario, generate_market, simulate


CASES = [
    ("neutral_low", {}, {}),
    ("selling_down", {"buy_probability": 0.25, "drift": -0.06,
                      "volatility": 0.5}, {}),
    ("informed", {"volatility": 1.2, "informed_fraction": 0.75}, {}),
    ("fee_exceeds_edge", {}, {"fee_per_trade": 1.10}),
]
STRATEGIES = ("fixed", "inventory")


def positive_int(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def run_experiment(runs, steps, seed_start):
    rows = []
    for name, market_changes, cost_changes in CASES:
        scenario = Scenario(name=name, label=name, steps=steps, **market_changes)
        config = replace(Config(), **cost_changes)
        samples = {strategy: [] for strategy in STRATEGIES}

        for seed in range(seed_start, seed_start + runs):
            # 同一条价格和客户路径交给两种策略，保证比较公平。
            events = generate_market(scenario, seed, tick_size=config.tick_size)
            for strategy in STRATEGIES:
                result = simulate(events, config, strategy, record=False)
                samples[strategy].append(result["metrics"])

        for strategy in STRATEGIES:
            metrics = samples[strategy]
            # 最终盈亏包含平仓成本；回撤按持仓期间的估值计算。
            pnls = [sample["pnl_liquidated"] for sample in metrics]
            rows.append({
                "case": name,
                "strategy": strategy,
                "runs": runs,
                "steps": steps,
                "seed_start": seed_start,
                "fee_per_trade": config.fee_per_trade,
                "mean_pnl": round(mean(pnls), 6),
                "loss_probability": sum(pnl < 0 for pnl in pnls) / runs,
                "mean_abs_inventory": round(mean(
                    sample["mean_abs_inventory"] for sample in metrics), 6),
                "mean_max_drawdown": round(mean(
                    sample["max_drawdown"] for sample in metrics), 6),
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=positive_int, default=500)
    parser.add_argument("--steps", type=positive_int, default=300)
    parser.add_argument("--seed-start", type=int, default=10000)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).with_name("results.csv"))
    args = parser.parse_args()
    rows = run_experiment(args.runs, args.steps, args.seed_start)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"{args.runs} paths per case, {args.steps} steps per path")
    print("PnL includes liquidation costs; drawdown uses marked portfolio value.")
    print(f"{'Case':<20} {'Strategy':<10} {'Mean PnL':>10} "
          f"{'Loss %':>8} {'Mean |q|':>10} {'Mean DD':>10}")
    for row in rows:
        print(f"{row['case']:<20} {row['strategy']:<10} "
              f"{row['mean_pnl']:>10.2f} {row['loss_probability']:>8.1%} "
              f"{row['mean_abs_inventory']:>10.2f} "
              f"{row['mean_max_drawdown']:>10.2f}")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
