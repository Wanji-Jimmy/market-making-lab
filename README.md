# Market Making Lab

**Inventory risk and adverse selection in a reproducible single-asset simulation.**

Can inventory-aware quoting reduce losses—and why can a dealer still lose money after reducing inventory? This project separates transaction cash flows from inventory revaluation, compares two quoting rules on identical market paths, and tests how the answer changes under directional flow, jumps, informed customers, and higher costs.

[中文学习说明](README.zh-CN.md) · [Methodology](docs/METHODOLOGY.md) · [Real-data findings](docs/EMPIRICAL_RESULTS.md) · [Simulation results](results/研究结果.md)

The simulator is an educational research baseline. Its prices and customer behavior are synthetic, and its parameters are not calibrated to a market. A separate public-data diagnostic investigates observed trade flow; it does not turn the simulator into a historical execution backtest.

## Current scope

| Component | Status |
| --- | --- |
| Cash, inventory, fees, position limits, and terminal liquidation | Implemented; accounting identities checked |
| Fixed-spread and linear inventory-skew quoting | Implemented |
| Six scenarios, eight sensitivity checks, paired comparisons | Implemented; full baseline run contains 9,200 strategy runs |
| One-step P&L attribution and replay of the original main experiment | Implemented; 6,000 main strategy runs reproduced |
| Offline interactive report and five-event teaching ledger | Implemented; generated locally |
| Public ETHUSDT trade-flow diagnostic | Implemented; 3,838,071 aggregate-trade records checked and analysed |
| Adaptive spread based on historical execution quality | Planned; not implemented |
| Participation controls, hidden-regime experiments, and held-out policy evaluation | Planned; not implemented |

## Reproduce the simulation

Python 3.10 or newer; standard library only. Run commands from the repository root.

```bash
# Follow five deterministic events through the ledger.
python3 lesson.py

# Run the tests without network access.
python3 -m unittest discover -v

# Run all baseline scenarios and sensitivity checks.
python3 experiment.py

# Replay the main experiment and decompose its P&L.
python3 diagnose_pnl.py
```

The full experiment uses six scenarios × 500 shared market paths × two strategies, plus eight sensitivity checks × 200 paths × two strategies. Each path contains 300 event steps. The default seed range starts at 10000. Seeds, parameters, and source hashes are saved in `results/results.json`; per-run metrics are saved in `results/all_runs.csv`.

Open the generated `results/report.html` in a browser to inspect the ledger, switch scenarios, and compare inventory and P&L paths. It works offline. Regenerate only the page with `python3 build_report.py`.

For a small execution check that does not overwrite the recorded baseline:

```bash
python3 experiment.py --runs 4 --steps 60 --sensitivity-runs 4 --output /tmp/market-making-smoke
```

Small runs check execution, not statistical robustness. Re-running an experiment overwrites files in its selected output directory. Large generated results, raw downloads, and local screenshots are excluded from version control; the small recorded summary is included.

## The model

At each event, the dealer observes reference price `S` and inventory `q`, quotes, processes at most one customer order, then marks the remaining inventory to the next reference price. A customer buys when the ask is no higher than their limit price, or sells when the bid is no lower than their limit price. Trades execute at the dealer's quote.

| Strategy | Bid | Ask |
| --- | --- | --- |
| A: fixed spread | `S - h` | `S + h` |
| B: inventory skew | `S - k*q - h` | `S - k*q + h` |

The default half-spread is `h = 1`, skew coefficient `k = 0.1`, and tick size `0.01`. The implementation clips the center shift to `±(h - tick)` and rounds quotes to ticks. Both strategies use the same cash, fee, inventory, and liquidation constraints. The default inventory limit is ±10 units; the fee is 0.05 per executed unit.

Market paths and customer opportunities are generated before either strategy runs. Both strategies receive the same external path, while different quotes can produce different fills. The strategy cannot observe customer limit prices or future reference prices. The informed-customer stress scenario deliberately gives the *customer generator* information correlated with the next price move.

## Follow the money

For cash `C`, inventory `q`, and reference price `S`, marked account value is `V = C + q*S`. With dealer trade quantity `dq` (+1 for buying, −1 for selling) and execution price `p`, every step satisfies:

```text
change in V = dq * (S - p) + q_after * (S_next - S) - fee
```

The diagnostic splits the inventory term further:

```text
change in V = execution edge
            + q_before * (S_next - S)
            + dq * (S_next - S)
            - fee
```

These are accounting components, not independently identified causes. In particular, the last two price-movement terms can offset one another. Multi-horizon markouts cannot be added together as if they were incremental account profit.

The five-event lesson makes the distinction concrete: +2.70 of execution edge and −6.00 of inventory revaluation produce −3.30 of marked P&L before liquidation, despite favorable execution prices.

## Selected baseline results

Amounts below are arbitrary simulated currency units **after terminal liquidation costs**, from 500 paths per scenario. They are not live or historical trading returns.

| Scenario | A mean P&L | B mean P&L | A loss frequency | B loss frequency |
| --- | ---: | ---: | ---: | ---: |
| Balanced flow, high volatility | 107.55 | 108.19 | 15.8% | 2.2% |
| Selling pressure, falling price | −95.93 | −26.88 | 88.2% | 64.8% |
| Customers with additional information | −30.71 | −29.00 | 56.4% | 73.8% |

Inventory skew reduces losses under directional selling pressure without making that scenario profitable. In the informed-customer case it reduces the average loss in the worst 5% of paths from −306.13 to −137.53, while increasing how often the strategy loses money. “Less risk” therefore needs a specified metric.

The high-volatility mean P&L improvement is only 0.63, with an approximate paired 95% Monte Carlo interval of [−6.79, 8.06]. That is not clear evidence of higher expected profit. These intervals measure sampling uncertainty within the model; they do not include model error.

See the [full baseline table](results/研究结果.md) for all six scenarios and the predefined fee, skew, demand, short-selling, and liquidation sensitivities.

## Public-data diagnostic

The empirical extension uses Binance's public spot **ETHUSDT aggregate-trade** archives for **2025-09-01, 2025-09-02, and 2025-09-03**, with archive checksum verification. It aggregates signed traded volume into one-minute intervals and compares volume imbalance with the next minute's trade-price return.

```bash
python3 real_data.py
```

This command requires network access to download public archives; no account or API key is needed. Raw inputs are cached under `data/raw/`. Derived outputs go to `real_data_results/`: `summary.json`, `minute_features.csv`, `group_summary.csv`, and `研究结果.md`.

This is a descriptive trade-flow study. Aggregate trades contain neither the full order book nor a dealer's queue position. A last traded price is not a midquote; an observed trade is not evidence that a hypothetical limit order would have filled. Three days are a limited sample, and an association with subsequent returns is not a validated trading strategy or a causal estimate.

The recorded run covers **3,838,071 aggregate trades**, **4,320 populated minutes** and **4,317 adjacent next-minute outcomes**. All three archive checksums passed. There were no missing minutes, duplicate aggregate IDs or ID gaps in this sample.

For the strongest aggressive-buying group (`imbalance > 0.5`, 525 minutes), the mean same-minute trade-price change was **+6.243 bp**, while the mean next-minute change was **−0.144 bp**. Contemporaneous association should not be mistaken for a predictive signal. See [all five groups and daily results](docs/EMPIRICAL_RESULTS.md), the [source manifest](real_data_results/summary.json), and the [full Chinese report](real_data_results/研究结果.md). No inference of statistical significance is made from these descriptive means.

After downloading once, `python3 real_data.py --offline` reproduces the analysis from the checksum-verified cache. The minute-level CSV is generated locally; compact group summaries and the source manifest are included in the repository.

## Limitations that matter

- Quotes are constrained to straddle the reference price. Execution edge relative to that price is therefore nonnegative by construction; ordinary customer willingness supplies a modeled opportunity to earn spread.
- There is no competition, queue priority, partial fill, latency, market impact, margin, or stock-borrow model. Terminal liquidation uses an assumed per-unit cost.
- Steps are abstract event time. The project does not annualize returns or report an annualized Sharpe ratio.
- A/B trades are not volume-matched. Changes in total P&L and inventory may also reflect changes in participation and which customers execute.
- New random seeds from the same model are not independent empirical validation. Policy selection and a genuinely held-out evaluation remain future work.

## Code map

| File | Responsibility |
| --- | --- |
| `simulator.py` | Parameters, external market generation, quotes, fills, ledger, and risk metrics |
| `lesson.py` | Five deterministic events for inspecting cash and inventory |
| `experiment.py` | Scenario definitions, shared-path comparisons, sensitivities, and result export |
| `diagnose_pnl.py` | Source-hash-checked replay and one-step P&L attribution |
| `build_report.py` | Offline HTML report from saved simulation results |
| `test_simulator.py` | Accounting, limits, liquidation, and reproducibility tests |
| `real_data.py` | Public trade archive ingestion and descriptive minute-level analysis |
| `.github/workflows/tests.yml` | Offline tests and a small simulation run on Python 3.10 and 3.12 |

Chinese comments and the teaching report are retained while the explanation is developed. The English overview records what has actually been implemented and keeps future research separate.

## References and implementation

- [Avellaneda and Stoikov, *High-frequency trading in a limit order book*](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf): motivation for inventory-sensitive quoting. This project does not implement the paper's full optimal policy.
- [Optiver, *Market making*](https://prod-www.optiver.com/insights/explainers/market-making/): background on liquidity provision. This is not an official Optiver or Optibook implementation.
- [Binance public data documentation](https://github.com/binance/binance-public-data): archive format, timestamps, and checksums for the empirical extension.

Implementation, tests, and documentation were developed with Codex assistance. The repository records the model, reproducible experiments, and their limitations; it does not claim unaided authorship or real-world profitability.
