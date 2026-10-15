# Methodology and evidence boundaries

This project contains an uncalibrated controlled simulation, descriptive diagnostics of public trades, and a forecast-to-quote study with a frozen date split. In the last layer, forecasts are evaluated against observed trade prices, while executions still come from an explicitly hypothetical customer model. Observed trades are not evidence of the dealer's fills or realized trading profit.

## Synthetic market

Each event follows this order:

1. Observe the current reference price and the dealer's inventory.
2. Submit a bid and ask using the selected policy.
3. Evaluate whether a customer accepts a quote, subject to cash and inventory limits.
4. Update cash and inventory, including the transaction fee.
5. Move to the next reference price and revalue inventory.

The entire external event path is generated once and reused for every policy in a comparison. Policies cannot read the customer limit, informed-customer label or future reference price. Different quotes can produce different fills on the same opportunity.

The fixed policy uses `bid = S - h` and `ask = S + h`. The inventory policy shifts the centre to `S - k*q` before applying the same half-spread. Quotes are rounded to a tick grid, and the shift is capped so the quotes straddle the reference price. At an inventory limit, the exposure-increasing side cannot fill.

The straddling constraint makes contemporaneous execution edge non-negative. Ordinary customer willingness to trade is independent of future price changes. These assumptions supply spread-capture opportunities: positive baseline profit is not evidence of a real-market edge.

The informed-customer scenario deliberately links customer direction and limit price to the next price change plus noise. It is an adverse-selection stress construction. It is not an empirical customer model or information available to the dealer.

## Accounting

Let `C` be cash, `q` inventory, `S` the reference price, `p` the fill price and `delta_q` the signed dealer quantity (+1 for buying, -1 for selling).

```
equity = C + q * S
cash_after = cash_before - delta_q * p - fee
q_after = q_before + delta_q
change_in_equity = delta_q * (S - p) + q_after * (S_next - S) - fee
```

The diagnostic further decomposes the revaluation:

```
change_in_equity = execution_edge
                 + q_before * (S_next - S)
                 + delta_q * (S_next - S)
                 - fee
```

This is an exact accounting identity, not a causal identification of independent risks. For example, selling an existing position can make the old-position and new-transaction terms cancel. Multi-step markouts are overlapping diagnostic windows and must not be summed as an alternative P&L ledger.

Final headline P&L includes a fixed cost to liquidate residual long or short inventory. This cost approximates friction; it does not model execution against a real order book.

## Experiment design

The main run uses six predefined scenarios, 500 seeds per scenario and 300 events per path. Eight sensitivity cases use 200 seeds each. Both policies share each external path. This is 9,200 strategy runs, not 9,200 independent real-market observations.

The comparison reports mean P&L, loss frequency, mean P&L in the worst 5% of paths, inventory, fees and trade count. A paired comparison computes policy B minus A on each shared path. Its interval describes simulation sampling uncertainty conditional on the model, not uncertainty about the adequacy of the model itself.

These original simulation runs have already been inspected and remain exploratory evidence. Their random seeds are not unseen empirical test data. The later five-second study below has its own declared chronological split, freeze and first-access record; it does not change the evidence status of the original experiment.

## Public trade-data diagnostic

The fixed sample is ETHUSDT spot aggregate trades from Binance's daily archives for 2025-09-01, 2025-09-02 and 2025-09-03 (UTC). The symbol and three consecutive dates were specified before reading the results. Three days are a bounded engineering and exploratory sample, not a representative assessment across assets or regimes.

The downloader verifies each ZIP against the archive's SHA-256 checksum. The parser checks identifiers, timestamps, positive prices and quantities, day boundaries and ordering. Spot timestamps from 2025 onwards use microseconds. Raw files stay outside version control; the source manifest and derived summaries are retained.

An aggregate trade is not necessarily one individual fill. `isBuyerMaker=true` identifies a passive buyer and therefore an aggressive seller. Minute-level signed volume imbalance is:

```
imbalance = (aggressive_buy_volume - aggressive_sell_volume) / total_volume
```

At the end of minute t, this feature is observable. The diagnostic relates it to the return between the last trade price in minute t and the last trade price in minute t+1. It requires adjacent populated minutes, does not forward-fill gaps and excludes missing future windows. The group boundaries are fixed before inspecting outcomes.

Trade prices are not bid/ask midpoints. A relationship between imbalance and future trade-price returns is not a strategy return, a midpoint markout, a proof of informed trading or a guarantee that a hypothetical quote would fill. Bid-ask bounce, volatility, aggregation and sample choice can influence the result. No real-data strategy is fitted or executed by this diagnostic.

## Individual-trade extension

A separate diagnostic reads the unaggregated ETHUSDT `trades` archive for 2025-09-01 and reconciles it against that day's `aggTrades` archive. The day was selected as the first existing sample date, whose minute-level findings were already inspected; it is not a held-out test.

For each aggressive trade, the diagnostic measures its signed trade-price response after fixed 1, 5 and 30 second horizons. The matched price is the first trade at or after the target, with a maximum one-second lookup delay and no cross-day matching. Retained sample counts, excluded observations and actual delays accompany the results. A positive response means the subsequent trade-price move favored the aggressor's direction; it is not a dealer P&L or a midpoint markout.

The complete [individual-trade method](TICK_DATA_DIAGNOSTIC.md) states the weighting, source schema, checks and interpretation. Split orders and overlapping response windows make observations dependent. No confidence interval based on treating all executions as independent is used.

## Five-second forecast-to-quote study

The implemented design is declared in [FLOW_SIGNAL_PLAN.json](../research/FLOW_SIGNAL_PLAN.json). The [journal](../research/RESEARCH_JOURNAL.md) records chronology, corrections and access; the [result report](../flow_research_results/report.md) records the actual evaluation. The [Chinese guide](FLOW_RESEARCH_GUIDE.zh-CN.md) gives a worked quote example. The method does not assume a positive result.

### Clock, information and target

At decision boundary `t`, signed ETH trade volume from `[t-5s,t)` gives imbalance `(buy-sell)/(buy+sell)`. The reference is the last executed trade price strictly before `t`, not an observed midquote. A trade timestamped exactly `t` belongs to the following bucket. The target is `10000 * (last_price_in_[t,t+5s) / reference - 1)`.

Only adjacent nonempty buckets within one UTC day create a labeled row. No gap is forward-filled. Current and target reference-price ages, empty buckets and excluded rows are reported; this module applies no extra one-second future-price cutoff. Labeled-row inclusion itself conditions on a populated next bucket, an outcome not known at decision time. Forecast results therefore describe the retained sample, not every clock interval.

The next bucket's first observed aggressor side is stored separately as future environment information for the hypothetical replay. The forecasting model sees only the preceding bucket's imbalance, and the quote function receives only current reference, inventory, past imbalance and frozen coefficients. Neither future side, next price nor customer willingness enters the quote.

### Chronological fitting and freeze

Training uses September 1–3, 2025; those dates were already explored in the earlier diagnostics. Validation uses September 4–5, without choosing a model or hyperparameter from its outcomes. The declared final test is September 6–8. A single OLS model with intercept is fitted only on the training dates. Training-mean and zero-return forecasts provide separate constant baselines. The raw model and its ±2 bp clipped forecast are both evaluated.

Before accessing the test data, the pipeline stores the model, plan hash, computational-source hashes, development metrics and data archive hashes. The first test stage verifies the freeze before downloading and records the start of access, including failed attempts. A correction after opening test data must be documented; the same dates cannot be relabeled untouched. This is a prospective evaluation within the project, not a formally registered study or evidence of general validity across markets.

MSE is measured in bp squared, MAE in bp. Directional accuracy excludes exactly zero realized changes; a zero prediction on a nonzero target counts incorrect and reduces directional coverage. Pooled metrics sum errors and denominators across days rather than averaging daily ratios. Outcomes are temporally dependent; no IID significance test is applied. Once the test is inspected, any new hypothesis requires a new dated design and new unseen dates.

### Five quote policies and controls

| Policy | Centre signal | Half-spread |
| --- | --- | ---: |
| `fixed` | Zero | 2 bp |
| `inventory` | Inventory adjustment only | 2 bp |
| `constant_wide` | Inventory adjustment only | 3 bp |
| `flow` | Inventory adjustment plus clipped `intercept + slope × imbalance` | 2 bp |
| `intercept_only` | Inventory adjustment plus clipped intercept | 2 bp |

Inventory adjustment is `−0.2 bp × inventory`; inventory is limited to ±10 normalized units. Total centre shift is capped to keep quotes straddling the reference, then quotes round outward to a tick of 0.0001. Execution edge against the reference is nonnegative by construction. These policies change the centre or use a predetermined width; they do not adapt the width to current estimated execution quality.

`flow − inventory` is the primary paired comparison on the same date and willingness seed. `flow − intercept_only` distinguishes the imbalance slope from a constant intercept shift. The OLS intercept need not equal the training-mean label. The wider policy controls for a simple change in participation incentives; it does not volume-match the competing strategies or fully identify the cause of a P&L difference.

### Hypothetical execution and accounting

Each day's first eligible reference is normalized to 100, starting cash is 10,000 and inventory is zero. Each eligible next bucket provides at most one hypothetical customer. Its direction is the first real aggressor side in that bucket, but its willingness distance is an independent exponential draw with predefined mean 3 bp. The actual intrabucket arrival timestamp is not replayed: any assumed fill occurs at the bucket decision/start, with post-fill inventory held for the five-second interval. This is a discrete clock approximation, not a claim that the future observed aggressor arrived at the boundary.

Five primary willingness seeds are shared across policies. The primary fee is 0.25 bp per normalized unit; terminal friction adds 1 bp plus the fee. Predeclared sensitivities change fees to 0 or 1 bp, or mean willingness to 1.5 or 6 bp. Model P&L is in normalized currency, not USDT and not a realized return. There is no order book, queue position, competition, partial-fill, latency, impact, margin or borrowing model.

Across missing buckets, no hypothetical customer opportunity is inserted. Existing inventory is carried and revalued from the previous observed next reference to the next eligible current reference. Total P&L reconciles execution edge plus within-bucket and gap inventory revaluation, less fees and terminal cost. Time-weighted inventory follows this bucket-time convention. Drawdown uses observed ledger checkpoints and cannot recover an unobserved intrabucket path. Liquidation occurs at the last eligible next reference, not necessarily UTC midnight.

The five seeds vary assumed willingness on the same historical path, not independently sampled markets. Results are paired by day and seed, then shown per day and across the three test days. No seed-based confidence interval or significance claim is appropriate. Better prediction and better hypothetical execution are separate findings; either can improve while the other worsens.

### Reproduction versus a new study

Use `python3 flow_research.py --stage reproduce` with the shipped freeze; the first run needs network access for public archives. Once cached, add `--offline`. Reproduction verifies the frozen model, plan and sources, recomputes development data, checks source checksums and compares regenerated test metrics to the first recorded test. Outputs named `reproduction*` preserve the original `test*` files.

The original first-run sequence was `--stage develop` then `--stage test`, before any freeze/test-access records or test archive cache existed. The CLI rejects attempts to overwrite this history. A fresh research question needs new unseen dates, a new declared plan and a separately versioned study; an empty cache is not evidence that the researcher has never seen the dates.

## Remaining research

Adaptive spreads based on historical execution quality, hidden-regime experiments and randomized participation controls remain unimplemented. A queue-aware execution study would require additional order-book and order-lifecycle information. The implemented fixed-width controls and frozen date-based evaluation do not supply that missing execution evidence.

## Sources

- [Avellaneda and Stoikov: High-frequency trading in a limit order book](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf): inventory-sensitive quoting and its assumptions.
- [Nasdaq: What Markouts Are and Why They Don't Always Matter](https://www.nasdaq.com/articles/what-markouts-are-and-why-they-dont-always-matter-2020-07-23): execution quality must be considered together with participation.
- [Binance public-data specification](https://github.com/binance/binance-public-data): archive fields, timestamps and checksums.

Implementation and documentation were developed with AI assistance. This repository records the implemented scope and reproducible evidence; future research claims must be supported by new experiments.
