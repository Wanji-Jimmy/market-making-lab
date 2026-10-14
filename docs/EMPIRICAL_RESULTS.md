# Empirical diagnostic: trade flow and subsequent prices

This page covers the three-day **aggregate-trade / minute-level** analysis. The separate [individual-trade diagnostic](TICK_DATA_DIAGNOSTIC.md) uses one day of unaggregated executions, reconciles the two official formats, and examines 1/5/30-second price responses.

Fixed sample: Binance spot ETHUSDT, 2025-09-01, 2025-09-02, 2025-09-03 (UTC). Dates and symbol were chosen before examining outcomes.

Processed **3,838,071 aggregate-trade records** into **4,320 populated minutes** and **4,317 adjacent same-day next-minute outcomes**. An aggregate record may combine multiple individual fills.

## Results

Imbalance is aggressive buy ETH volume minus aggressive sell ETH volume, divided by total ETH volume. Each populated minute receives equal weight in the reported group means. One basis point (bp) equals 0.01%.

| Imbalance group | Minutes | Next-minute samples | Same-minute mean (bp) | Next-minute mean (bp) | Next-minute median (bp) |
| --- | ---: | ---: | ---: | ---: | ---: |
| [-1,-0.5) | 601 | 601 | -5.976 | -1.075 | -1.137 |
| [-0.5,-0.1) | 1307 | 1306 | -3.241 | +0.240 | +0.328 |
| [-0.1,0.1] | 718 | 717 | +0.325 | +0.551 | +0.572 |
| (0.1,0.5] | 1169 | 1168 | +3.812 | +0.154 | +0.157 |
| (0.5,1] | 525 | 525 | +6.243 | -0.144 | -0.068 |

Strong buying coincides with a positive same-minute price move in this sample, but the following-minute mean for the strongest-buying group is slightly negative. These descriptive means do not establish a predictive edge or statistical significance. The two return windows are different: same-minute uses first-to-last trade prices within the minute; next-minute uses last-to-last prices across adjacent minutes.

The feature for minute t is complete only at that minute's end. Missing adjacent minutes and each day's terminal minute are excluded rather than forward-filled; the analysis does not join across days. The last traded price is not a midpoint, and bid-ask bounce can affect these measurements.

## Daily groups

| UTC date | Imbalance group | Next-minute samples | Next-minute mean (bp) |
| --- | --- | ---: | ---: |
| 2025-09-01 | [-1,-0.5) | 201 | -1.610 |
| 2025-09-01 | [-0.5,-0.1) | 468 | -0.066 |
| 2025-09-01 | [-0.1,0.1] | 239 | +1.057 |
| 2025-09-01 | (0.1,0.5] | 403 | -0.059 |
| 2025-09-01 | (0.5,1] | 128 | -0.333 |
| 2025-09-02 | [-1,-0.5) | 159 | -0.879 |
| 2025-09-02 | [-0.5,-0.1) | 420 | +0.181 |
| 2025-09-02 | [-0.1,0.1] | 244 | -0.295 |
| 2025-09-02 | (0.1,0.5] | 422 | +0.538 |
| 2025-09-02 | (0.5,1] | 194 | -0.256 |
| 2025-09-03 | [-1,-0.5) | 241 | -0.759 |
| 2025-09-03 | [-0.5,-0.1) | 418 | +0.641 |
| 2025-09-03 | [-0.1,0.1] | 234 | +0.915 |
| 2025-09-03 | (0.1,0.5] | 343 | -0.067 |
| 2025-09-03 | (0.5,1] | 203 | +0.082 |

## Data checks

| UTC date | Aggregate records | Populated minutes | Missing IDs | Duplicate IDs |
| --- | ---: | ---: | ---: | ---: |
| 2025-09-01 | 1,378,465 | 1440 | 0 | 0 |
| 2025-09-02 | 1,414,656 | 1440 | 0 | 0 |
| 2025-09-03 | 1,044,950 | 1440 | 0 | 0 |

Each archive was verified against the official SHA-256 checksum. The parser rejects invalid dates, nonpositive/nonfinite values, duplicate or decreasing IDs, and decreasing timestamps. The files in this run passed these checks.

## Reproduction and interpretation

Run `python3 real_data.py` from the repository root. After downloading the source archives, use `python3 real_data.py --offline` for a cached replay. Detailed source URLs, acquisition times, archive hashes, source-code hash and quality metrics are recorded in the [manifest](../real_data_results/summary.json). Full numeric group summaries are in [group_summary.csv](../real_data_results/group_summary.csv).

The sample is three days of one crypto pair. There is no order book, queue position, hypothetical fill model, transaction-cost-adjusted trading return or held-out strategy evaluation. Minute observations are time-dependent; no interval based on treating every minute as independent is reported. These data do not validate the simulator's customer willingness or fill assumptions.

Source specification: [Binance public market data](https://github.com/binance/binance-public-data). See [methodology](METHODOLOGY.md) for the distinction between empirical observation and simulated execution.
