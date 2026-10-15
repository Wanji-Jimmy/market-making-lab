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

`simulator.py` contains the quoting, customer fills and accounting. `experiment.py` compares both rules at inventory limits of 5, 10 and 20, and writes [results.csv](results.csv). Some code comments are in Chinese.

## Quotes and results

The fixed rule quotes `bid = m - 1` and `ask = m + 1`, where `m` is the current reference price. The inventory rule subtracts `0.1 * q` from both prices. A positive position therefore lowers the quotes, making further purchases less attractive and sales more likely. The baseline caps positions at 10 units in either direction. The quote shift is clipped to keep the bid below the reference and the ask above it, with prices rounded to ticks; this same rule applies at every inventory limit.

Each case uses 500 price paths of 300 steps, with seeds 10000 to 10499. Every rule and inventory limit faces exactly the same prices and customer orders. Mean P&L below uses the baseline limit of 10 and includes trade fees and the cost of closing the final position, in model units.

| Case | Fixed spread | Inventory adjustment |
| --- | ---: | ---: |
| Balanced customer flow | 107.17 | 109.82 |
| Selling pressure and falling prices | -95.93 | -26.88 |
| Informed customers | -30.71 | -29.00 |
| Fee increased from 0.05 to 1.10 per unit | -22.74 | -23.08 |

Inventory adjustment reduced the loss in the falling-price case. It did not solve adverse selection: with informed customers, the fraction of losing runs increased from 56.4% to 73.8%, despite a slightly smaller average loss and a lower average drawdown (131.84 to 62.91). These measures answer different questions about risk.

## What does a tighter limit cost?

The next comparison changes only the inventory limit. Results below use the inventory-adjusted rule; the CSV includes both rules in all four cases. Values are averages per path.

| Case | Limit | Net P&L | Mean absolute inventory | Drawdown | Fills | Blocked by limit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Balanced flow | 5 | 107.59 | 1.89 | 3.35 | 121.86 | 2.23 |
| Balanced flow | 10 | 109.82 | 2.05 | 3.80 | 124.46 | 0.01 |
| Balanced flow | 20 | 109.83 | 2.05 | 3.81 | 124.47 | 0.00 |
| Selling pressure | 5 | 3.41 | 3.87 | 44.05 | 83.45 | 28.14 |
| Selling pressure | 10 | -26.88 | 6.53 | 87.87 | 103.35 | 5.45 |
| Selling pressure | 20 | -40.91 | 7.64 | 107.92 | 108.59 | 0.14 |

Tightening the limit from 10 to 5 reduced both drawdown and fills. It gave up some profit with balanced flow, but improved average P&L under selling pressure. Against informed customers, the same change barely helped drawdown and slightly worsened average P&L (-29.00 to -29.11). A lower limit is not automatically a better strategy.

"Blocked" counts customers who accepted the current quote but could not trade because of the limit. It is not the number of profitable trades that a different limit would have produced: earlier fills also change inventory and later quotes. Drawdown uses portfolio values before terminal liquidation; net P&L includes liquidation costs. The CSV also splits net P&L into execution edge plus inventory P&L, less trade fees and liquidation costs.

The prices and customers are simulated. Fills depend on an assumed customer reservation price; there is no order book, queue or market impact. The results describe this model, rather than expected trading returns.

Earlier experiments with public trade data, the longer notes and their original results are preserved on the [extended-study branch](https://github.com/Wanji-Jimmy/market-making-lab/tree/extended-study).
