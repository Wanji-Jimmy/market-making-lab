"""五个固定事件的账本示例。运行：python3 lesson.py。"""

from dataclasses import asdict

from simulator import Config, Event, simulate


def make_lesson():
    # 这是已有两股的账户，初始总价值是 1000 + 2 * 100 = 1200。
    # 教学例子不计手续费、不允许卖空；完整实验使用另一组明确列出的参数。
    config = Config(
        initial_cash=1000.0,
        initial_inventory=2,
        fee_per_trade=0.0,
        allow_short=False,
    )

    # Event: 步数、当前参考价、下一步参考价、客户方向、客户价格底线。
    # buy/sell 始终是客户方向；客户 buy 意味着做市商卖出。
    events = [
        Event(1, 100.0, 100.0, "buy", 100.9),
        Event(2, 100.0, 100.0, "sell", 99.1),
        Event(3, 100.0, 100.0, "sell", 98.8),
        Event(4, 100.0, 97.0, None, None),
        Event(5, 97.0, 97.0, "buy", 97.9),
    ]
    result = simulate(events, config, "inventory", record=True)
    return {
        "title": "五笔事件：成交赚到差价，库存下跌仍可能令账户亏损",
        "initial_cash": config.initial_cash,
        "initial_inventory": config.initial_inventory,
        "initial_mid": events[0].mid_price,
        "config": asdict(config),
        "strategy": "inventory",
        "trace": result["trace"],
        "metrics": result["metrics"],
    }


if __name__ == "__main__":
    lesson = make_lesson()
    print(lesson["title"])
    print("初始现金 1000.00；初始库存 2；初始总价值 1200.00；无手续费。")
    print("step  客户    买价    卖价  成交      现金  库存   新参考价      总价值    盈亏")
    for row in lesson["trace"]:
        side = row["customer_side"] or "none"
        print(
            f'{row["step"]:>4}  {side:>4}  {row["bid"]:>6.2f}  {row["ask"]:>6.2f}'
            f'  {str(row["filled"]):>5}  {row["cash"]:>8.2f}'
            f'  {row["inventory"]:>4}  {row["next_mid"]:>9.2f}'
            f'  {row["equity"]:>10.2f}  {row["pnl"]:>6.2f}'
        )
    metrics = lesson["metrics"]
    print(f'成交优势合计：{metrics["spread_capture"]:.2f}')
    print(f'库存价格变化损益：{metrics["inventory_pnl"]:.2f}')
    print(f'按参考价估值盈亏：{metrics["pnl_mtm"]:.2f}')
    print(f'计入期末清仓成本后的盈亏：{metrics["pnl_liquidated"]:.2f}')
