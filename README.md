# Market Making Lab

**Inventory risk, observed trade flow, and a frozen forecast-to-quote experiment.**

Can inventory-aware quoting reduce losses—and can past trade flow add useful information to the quote? The project starts with a controlled simulation and accounting ledger, then studies public trade records. Its latest pipeline fits a simple flow forecast on training dates, freezes the model and code, and evaluates prediction and five quoting policies on separate test dates.

[中文学习说明](README.zh-CN.md) · [Flow research guide 中文](docs/FLOW_RESEARCH_GUIDE.zh-CN.md) · [Methodology](docs/METHODOLOGY.md) · [Research plan](research/FLOW_SIGNAL_PLAN.json) · [Research journal](research/RESEARCH_JOURNAL.md) · [Flow research results](flow_research_results/report.md)

The original simulator remains an uncalibrated educational baseline with synthetic prices and customers. The new forecast uses observed trade data, but its execution experiment still assumes customer willingness, fills and costs. Neither layer is an executable historical trading backtest.

## Current scope

| Component | Status |
| --- | --- |
| Cash, inventory, fees, position limits, and terminal liquidation | Implemented; accounting identities checked |
| Fixed-spread and linear inventory-skew quoting | Implemented |
| Six scenarios, eight sensitivity checks, paired comparisons | Implemented; full baseline run contains 9,200 strategy runs |
| One-step P&L attribution and replay of the original main experiment | Implemented; 6,000 main strategy runs reproduced |
| Offline interactive report and five-event teaching ledger | Implemented; generated locally |
| Public ETHUSDT trade-flow diagnostic | Implemented; 3,838,071 aggregate-trade records checked and analysed |
| Unaggregated ETHUSDT trade-response diagnostic | Implemented; 4,011,776 individual trades, 1/5/30-second responses and format reconciliation |
| Past-five-second imbalance → next-five-second trade-price forecast | Implemented; training-only OLS, zero and training-mean baselines, raw and clipped forecasts |
| Five-policy comparison with wider-spread and intercept-only controls | Implemented; synthetic customer willingness on shared historical price paths |
| Declared training/validation/test dates, source/model freeze and reproduction checks | Implemented; execution history and results are recorded in the linked journal and report |
| Adaptive spread based on historical execution quality | Planned; not implemented |
| Hidden-regime experiments, random participation controls and queue-aware real execution | Not implemented |

## Reproduce the flow research

Python 3.10 or newer; standard library only. From the repository root, reproduce the shipped model and freeze:

```bash
# First reproduction downloads the declared public archives and verifies checksums.
python3 flow_research.py --stage reproduce

# Subsequent reproduction uses the complete local cache without network access.
python3 flow_research.py --stage reproduce --offline

# Tests do not download data.
python3 -m unittest discover -v
```

No account or API key is required. Raw files are cached under `data/raw/`. Reproduction checks the plan, model, computational sources, development metrics and source manifests before comparing the regenerated test results with the first recorded run. It writes `reproduction.json`, `reproduction_runs.csv` and `reproduction_check.json` under `flow_research_results/`, preserving the original test record. A hash mismatch stops the run; changing code and silently replacing the freeze is not a valid reproduction.

For the original first evaluation, before a freeze or test-access record exists, the stage sequence is `python3 flow_research.py --stage develop` followed by `python3 flow_research.py --stage test`. Development fits only the training dates, evaluates the predetermined model on validation dates without tuning, and saves the freeze. The first test stage verifies that freeze and records access before downloading test dates. These commands deliberately refuse to overwrite an existing research history. To start a genuinely new study, declare new unseen dates and a new plan in a separate version/output directory; deleting caches or choosing a new directory does not make previously inspected dates unseen.

### What this iteration tests

At decision time `t`, signed ETH volume imbalance uses only `[t-5s,t)`. The reference is the last trade strictly before `t`. The label is the change to the last trade in `[t,t+5s)`, in basis points. Only adjacent populated buckets are labeled; gaps are not forward-filled and dates are not joined. Reference ages and missingness are reported. Label availability conditions on future activity, which is not known when quoting.

OLS fits `next_return_bps = intercept + slope * imbalance` on **September 1–3, 2025**. These development dates were previously inspected. **September 4–5** are validation dates, without model or hyperparameter selection; **September 6–8** are the declared final test dates. This is a project-level frozen evaluation, not a formally registered study. Once evaluated, these test dates are no longer unseen for future work.

| Policy | Centre adjustment | Half-spread |
| --- | --- | ---: |
| `fixed` | None | 2 bp |
| `inventory` | `−0.2 bp × inventory` | 2 bp |
| `constant_wide` | Inventory adjustment | 3 bp |
| `flow` | Inventory adjustment + clipped `intercept + slope × imbalance` | 2 bp |
| `intercept_only` | Inventory adjustment + clipped intercept | 2 bp |

Signals are clipped to ±2 bp, and the total centre shift is constrained so quotes straddle the reference. Widths are fixed within each policy; this is not adaptive spread control. `flow − inventory` is the primary paired comparison; `flow − intercept_only` isolates the imbalance term. The intercept is not generally the training-mean return, which is a separate forecast baseline.

The environment takes the first observed aggressor side in the next bucket and assigns that hypothetical customer an independent exponential willingness distance. Quotes cannot see that future side, customer limit or next price. Assumed fills occur at the bucket start: the real aggressor's intrabucket arrival time is not replayed. Each day is normalized to an initial reference of 100, with one normalized unit per fill, so P&L is model currency rather than USDT. Missing intervals carry inventory and include gap revaluation; terminal liquidation uses the last eligible next reference, not necessarily the UTC close.

The [research plan](research/FLOW_SIGNAL_PLAN.json) declares costs, willingness sensitivities and seeds. The [journal](research/RESEARCH_JOURNAL.md) records chronology, and the [results report](flow_research_results/report.md) records outcomes without preselecting a winner. Five willingness seeds on one price path are not five independent markets. Only three test days are used; no seed-based significance claim is made.

### Recorded held-out findings

The frozen model was evaluated on **51,402** eligible five-second observations. All **135** declared policy runs were retained and reproduced offline without changing the first evaluation.

| Measure | Constant / inventory baseline | Flow model / policy | Interpretation |
| --- | ---: | ---: | --- |
| Forecast MSE (bp²), versus zero prediction | 1.610946 | 1.602750 | About 0.51% lower |
| Forecast MAE (bp), versus zero prediction | 0.637356 | 0.704599 | About 10.55% higher |
| Mean daily modeled P&L, versus inventory policy | 127.0193 | 127.9396 | +0.9203 normalized units; one of three daily differences is negative |
| Mean path maximum drawdown | 1.9359 | 1.9552 | Slightly worse despite lower mean inventory |

The wider-spread control produces higher modeled P&L under the main willingness assumption, but that ranking reverses when willingness is lower. In that lower-willingness case, the flow policy also underperforms the inventory policy. The evidence is mixed and execution-model dependent: a small forecast improvement under squared loss does not establish real trading profitability. See the [paired decomposition and sensitivity results](flow_research_results/report.md) and [reproduction receipt](flow_research_results/verification.json).

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

## The original synthetic model

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

### Individual executions and short-horizon responses

A second module uses the official unaggregated `trades` archive for **2025-09-01 UTC**. It reads **4,011,776 individual executions** and reconciles them against that day's **1,378,465 aggregate records**. All **15,853** daily and minute-level comparisons passed within the declared tolerances. Both source archive checksums passed; raw trade IDs were continuous, without duplicates or timestamp reversals.

```bash
python3 tick_data.py
# Cached reproduction after the first download:
python3 tick_data.py --offline
```

The diagnostic measures aggressive-side signed trade-price responses at fixed 1, 5 and 30 second targets. It uses the first execution at or after each target and rejects matches more than one second late. Positive means the price moved in the aggressor's direction.

| Target horizon | Retained events | Mean signed response (bp) | Mean lookup delay (ms) |
| --- | ---: | ---: | ---: |
| 1 second | 3,821,339 | +1.0011 | 221.1 |
| 5 seconds | 3,784,136 | +1.3611 | 234.1 |
| 30 seconds | 3,767,921 | +1.1422 | 242.4 |

These are event-weighted descriptive responses at the target plus lookup delay. They are not strategy returns, exact-horizon midquote markouts or evidence of a causal effect. Individual executions can be split parts of one order, and response windows overlap. The earlier minute results for this date were already inspected; the extension is exploratory, not a held-out validation.

See the [method and complete interpretation](docs/TICK_DATA_DIAGNOSTIC.md), [buy/sell results and exclusions](tick_data_results/response_summary.csv), [manifest and reconciliation summary](tick_data_results/summary.json), and [Chinese report](tick_data_results/研究结果.md). The full 15,853-row reconciliation CSV is generated locally; compact summaries are tracked.

## Limitations that matter

- Quotes are constrained to straddle the reference price. Execution edge relative to that price is therefore nonnegative by construction; ordinary customer willingness supplies a modeled opportunity to earn spread.
- There is no competition, queue priority, partial fill, latency, market impact, margin, or stock-borrow model. Terminal liquidation uses an assumed per-unit cost.
- The original simulator uses abstract event time; the new flow study uses five-second buckets with a simplified bucket-start fill convention. Neither reports annualized returns or an annualized Sharpe ratio.
- Policies are not volume-matched. Changes in total P&L and inventory may also reflect changes in participation and which customers execute.
- The new pipeline separates fitting from a frozen date-based evaluation, but only three test days are available. Changing hypothetical willingness seeds does not add independent market histories. Hidden-regime experiments and validated real execution remain future work.

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
| `tick_data.py` | Individual-trade validation, signed forward responses and aggregate-format reconciliation |
| `test_real_data.py` / `test_tick_data.py` | Offline schema, timestamp, direction, matching and reconciliation tests |
| `flow_data.py` | Five-second past-only features, OLS and forecast metrics |
| `flow_policy.py` | Five fixed-width quote policies, hypothetical fills and gap-aware accounting |
| `flow_research.py` | Development, freeze verification, first test access and reproduction |
| `build_flow_report.py` | Offline integrity checks and deterministic result presentation |
| `test_flow_data.py` / `test_flow_policy.py` / `test_flow_research.py` | Time-boundary, policy, accounting, split and freeze checks |
| `research/FLOW_SIGNAL_PLAN.json` / `research/RESEARCH_JOURNAL.md` | Declared design and research chronology |
| `.github/workflows/tests.yml` | Offline tests and a small simulation run on Python 3.10 and 3.12 |

Chinese comments and the teaching report are retained while the explanation is developed. The English overview records what has actually been implemented and keeps future research separate.

## References and implementation

- [Avellaneda and Stoikov, *High-frequency trading in a limit order book*](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf): motivation for inventory-sensitive quoting. This project does not implement the paper's full optimal policy.
- [Optiver, *Market making*](https://prod-www.optiver.com/insights/explainers/market-making/): background on liquidity provision. This is not an official Optiver or Optibook implementation.
- [Binance public data documentation](https://github.com/binance/binance-public-data): archive format, timestamps, and checksums for the empirical extension.

Implementation, tests, and documentation were developed with Codex assistance. The repository records the model, reproducible experiments, and their limitations; it does not claim unaided authorship or real-world profitability.
