# Individual-trade diagnostic

This extension uses the official Binance spot **ETHUSDT `trades` archive for 2025-09-01 UTC**. It preserves the exchange's individual trade records, unlike `aggTrades`, which can combine several executions. One exchange trade record is not necessarily one independent customer order.

The day is the first day of the existing three-day aggregate-data sample. It was selected for a bounded format reconciliation before inspecting this new diagnostic. The earlier minute-level findings for that date were already known, so this is an exploratory extension, **not an unseen test set**.

## Question and measurement

After an aggressive buy or sell, does the next observed trade price move in the same direction? The fixed horizons are 1, 5 and 30 seconds.

For trade i, let `s_i` be +1 for an aggressive buy and -1 for an aggressive sell, and `p_i` its execution price. For each horizon h, find the **first subsequent record at or after `t_i + h`**. Accept it only if its timestamp is no more than **one second after the target** and the target and matched record remain within the same UTC day.

```text
signed_response_bps = s_i * 10,000 * (matched_trade_price / p_i - 1)
lookup_delay = matched_trade_timestamp - (t_i + h)
```

This measures the response at **h plus the reported lookup delay**, not necessarily exactly h seconds. A target outside the day, no subsequent match, or a delay over one second is excluded. Each horizon has its own retained denominator. The analysis reports all trades and each aggressor side separately, with actual lookup delays and exclusion counts.

For example, an aggressive buy at 100 followed by a matched trade at 100.02 gives +2 bp. An aggressive sell at 100 followed by 99.98 also gives +2 bp. Positive means price moved in the aggressor's direction. It is a useful way to investigate price movements that can be unfavorable to a passive counterparty, but it is **not that counterparty's measured profit or loss**.

Each retained trade receives equal weight. Large orders can generate multiple records, and outcome windows overlap. Consequently, millions of observations do not imply millions of independent bets. No independent-observation standard error or significance claim is made.

## Source and checks

- [Official archive specification](https://github.com/binance/binance-public-data#trades).
- [Individual-trade ZIP](https://data.binance.vision/data/spot/daily/trades/ETHUSDT/ETHUSDT-trades-2025-09-01.zip) and [official SHA-256 checksum](https://data.binance.vision/data/spot/daily/trades/ETHUSDT/ETHUSDT-trades-2025-09-01.zip.CHECKSUM).
- [Aggregate-trade ZIP for reconciliation](https://data.binance.vision/data/spot/daily/aggTrades/ETHUSDT/ETHUSDT-aggTrades-2025-09-01.zip).

The seven individual-trade fields are `id`, `price`, `qty`, `quoteQty`, `time`, `isBuyerMaker`, and `isBestMatch`. The 2025 spot archive uses microseconds. When `isBuyerMaker` is true, the buyer is passive and the aggressor is a seller; it is not an aggressive-buy flag.

Both archives are checksum-verified. Parsing checks timestamps, day membership, record ordering, duplicate IDs, finite positive values and notional consistency. ID gaps are reported. Reconciliation compares the actual individual-record count against the sum of aggregate first-to-last trade-ID spans, plus volumes, notionals, boundary prices and minute-level quantities. It is an agreement check between two official representations, not independent proof that the exchange captured every market event.

## Reproduction

From the repository root, using Python 3.10 or newer and the standard library:

```bash
python3 tick_data.py
# Once both official archives and their receipts are cached:
python3 tick_data.py --offline
python3 -m unittest discover -v
```

No trading account or API key is needed. Raw archives remain in the ignored `data/raw/` directory. Compact outputs are stored in `tick_data_results/`: [manifest, numeric results and daily reconciliation](../tick_data_results/summary.json), [response table](../tick_data_results/response_summary.csv), and [Chinese explanation and results](../tick_data_results/研究结果.md). The complete `tick_data_results/reconciliation.csv` is generated locally and excluded from Git.

## Recorded results

The run contains **4,011,776 individual trades** and **1,440 populated minutes**. Both archive SHA-256 checks passed. Individual IDs were continuous, with no duplicates or decreasing timestamps; **3,408,595** adjacent records shared a timestamp. The corresponding aggregate file has **1,378,465** rows whose underlying ID spans sum to the individual count. All **15,853** daily and minute-level comparisons passed at the documented tolerances.

| Target seconds | Aggressor scope | Retained events | Event-weighted response (bp) | ETH-volume-weighted response (bp) | Mean lookup delay (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | All | 3,821,339 | +1.0011 | +1.1272 | 221.065 |
| 1 | Buy | 1,826,724 | +0.9554 | +1.0608 | 223.477 |
| 1 | Sell | 1,994,615 | +1.0430 | +1.1884 | 218.856 |
| 5 | All | 3,784,136 | +1.3611 | +1.3315 | 234.093 |
| 5 | Buy | 1,811,221 | +1.2976 | +1.2005 | 238.959 |
| 5 | Sell | 1,972,915 | +1.4195 | +1.4521 | 229.626 |
| 30 | All | 3,767,921 | +1.1422 | +1.2950 | 242.354 |
| 30 | Buy | 1,801,382 | +1.4072 | +1.7827 | 243.842 |
| 30 | Sell | 1,966,539 | +0.8995 | +0.8472 | 240.991 |

| Target seconds | All events | Target outside day | No later record | Lookup delay over 1 second | Retained |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 4,011,776 | 1 | 16 | 190,420 | 3,821,339 |
| 5 | 4,011,776 | 151 | 2 | 227,487 | 3,784,136 |
| 30 | 4,011,776 | 561 | 0 | 243,294 | 3,767,921 |

All groups have a positive mean signed response in this sample. That is a concrete descriptive finding to investigate, not evidence that a particular person had private information or that our simulator would earn or lose these amounts. The delay cutoff alone excludes approximately **4.75%, 5.67% and 6.06%** of all events at the respective horizons. The means are therefore conditional on future trade availability within the cutoff, which can favor liquid states. This selection is not known at the event time and is not an executable entry rule. Different horizons also retain different event sets.

## Interpretation

These are real **executed trade prices**, not bid/ask midpoints. Bid-ask bounce, clustered executions, volatility, the observation delay and the chosen day can affect the response. Positive responses do not identify informed traders or causal market impact.

The data do not contain our quotes, our fills, queue priority, best bid/ask or full depth. They therefore do not establish a feasible maker strategy, its fill probability, its fees or its profitability. The synthetic simulator remains a separate experiment. A future history-only signal must wait for any response window to mature, use separately declared development and evaluation periods, and include a defensible execution model.
