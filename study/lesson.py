from dataclasses import asdict

from simulator import Config, Event, simulate


def make_lesson():
    config = Config(
        initial_cash=1000.0,
        initial_inventory=2,
        fee_per_trade=0.0,
        allow_short=False,
    )

    events = [
        Event(1, 100.0, 100.0, "buy", 100.9),
        Event(2, 100.0, 100.0, "sell", 99.1),
        Event(3, 100.0, 100.0, "sell", 98.8),
        Event(4, 100.0, 97.0, None, None),
        Event(5, 97.0, 97.0, "buy", 97.9),
    ]
    result = simulate(events, config, "inventory", record=True)
    return {
        "title": "Five events: spread capture can be outweighed by inventory losses",
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
    print("Initial cash 1000.00; inventory 2; total value 1200.00; no trade fees.")
    print("step  side     bid     ask   fill      cash   inv   next mid      equity     P&L")
    for row in lesson["trace"]:
        side = row["customer_side"] or "none"
        print(
            f'{row["step"]:>4}  {side:>4}  {row["bid"]:>6.2f}  {row["ask"]:>6.2f}'
            f'  {str(row["filled"]):>5}  {row["cash"]:>8.2f}'
            f'  {row["inventory"]:>4}  {row["next_mid"]:>9.2f}'
            f'  {row["equity"]:>10.2f}  {row["pnl"]:>6.2f}'
        )
    metrics = lesson["metrics"]
    print(f'Spread capture: {metrics["spread_capture"]:.2f}')
    print(f'Inventory P&L: {metrics["inventory_pnl"]:.2f}')
    print(f'Mark-to-market P&L: {metrics["pnl_mtm"]:.2f}')
    print(f'P&L after terminal liquidation costs: {metrics["pnl_liquidated"]:.2f}')
