# Methodology and evidence boundaries

This project has two separate evidence layers: a controlled synthetic market and an empirical diagnostic of public trades. Empirical observations are not substituted for simulated fills or presented as realized trading profit.

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

The existing runs have already been inspected and are exploratory evidence. Future signal selection requires newly declared development, validation and frozen test sets. It would be incorrect to relabel the existing seeds as unseen test data.

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

## Next research milestone

The next study is to test whether a history-only quote adjustment can improve the trade-off between execution quality, profit and participation, beyond inventory control. The intended controls include fixed wider quotes and random reductions in quoting, with parameters frozen before evaluation. Hidden-regime signals, four-policy ablations and frozen holdout performance are not implemented in this release.

## Sources

- [Avellaneda and Stoikov: High-frequency trading in a limit order book](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf): inventory-sensitive quoting and its assumptions.
- [Nasdaq: What Markouts Are and Why They Don't Always Matter](https://www.nasdaq.com/articles/what-markouts-are-and-why-they-dont-always-matter-2020-07-23): execution quality must be considered together with participation.
- [Binance public-data specification](https://github.com/binance/binance-public-data): archive fields, timestamps and checksums.

Implementation and documentation were developed with AI assistance. This repository records the implemented scope and reproducible evidence; future research claims must be supported by new experiments.
