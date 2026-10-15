# Market-making simulation

A Python experiment comparing fixed-spread quotes with quotes that adjust for inventory. The account tracks cash, positions and P&L after each trade.

## Run

Python 3.10 or later, with no extra packages:

```bash
python3 lesson.py
python3 experiment.py
python3 -m unittest test_simulator -v
```

`lesson.py` walks through five events. The account earns 2.70 from execution prices but loses 6.00 as its inventory falls in value, leaving a loss of 3.30 before closing the remaining position.

`simulator.py` contains the quoting, customer fills and accounting. `experiment.py` compares the two rules and writes [results.csv](results.csv). The code comments are currently in Chinese.

## Quotes and results

The fixed rule quotes `bid = m - 1` and `ask = m + 1`, where `m` is the current reference price. The inventory rule subtracts `0.1 * q` from both prices. A positive position therefore lowers the quotes, making further purchases less attractive and sales more likely. Positions are capped at 10 units in either direction; the quote shift is clipped to keep the bid below the reference and the ask above it, with prices rounded to ticks.

Each case uses 500 price paths of 300 steps, with seeds 10000 to 10499. Both rules face exactly the same prices and customer orders. Mean P&L below includes trade fees and the cost of closing the final position, in model units.

| Case | Fixed spread | Inventory adjustment |
| --- | ---: | ---: |
| Balanced customer flow | 107.17 | 109.82 |
| Selling pressure and falling prices | -95.93 | -26.88 |
| Informed customers | -30.71 | -29.00 |
| Fee increased from 0.05 to 1.10 per unit | -22.74 | -23.08 |

Inventory adjustment reduced the loss in the falling-price case. It did not solve adverse selection: with informed customers, the fraction of losing runs increased from 56.4% to 73.8%, despite a slightly smaller average loss.

The prices and customers are simulated. Fills depend on an assumed customer reservation price; there is no order book, queue or market impact. The results describe this model, rather than expected trading returns.

Earlier experiments with public trade data, the longer notes and their original results are preserved on the [extended-study branch](https://github.com/Wanji-Jimmy/market-making-lab/tree/extended-study).

Built with Codex assistance.
