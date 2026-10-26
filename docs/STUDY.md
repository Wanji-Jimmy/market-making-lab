# Study notes

## 1. Start with the ledger

`study/simulator.py` tracks cash, inventory and marked wealth after each event. Buying one unit reduces cash and increases inventory. Selling reverses both changes. Wealth is cash plus inventory times the current reference price.

The accounting identity is execution edge plus inventory price-change P&L, less transaction fees. Closing the final inventory has a separate cost. A positive spread contribution can coexist with an overall loss if inventory moves against the dealer. Run `study/lesson.py` for a five-event example: +2.70 execution edge, −6.00 inventory P&L, −3.30 marked P&L and −4.30 after liquidation.

The comparison uses common market paths. Five random-number streams prevent a customer draw in one branch from changing another branch's reference-price path. Matched policies face the same environments and seeds. Equal-width comparisons separate the effect of inventory shifting from the effect of wider quotes.

The original research uses eight stated environments, four policies and 500 independent paths per environment. Each path has 300 events. Multiple policies on the same path are paired observations, not extra independent market histories. Fees and terminal liquidation are included. Risk measures use marked wealth before terminal liquidation. The protocols retain seeds, parameters and declared comparisons.

The AS-inspired policies use an inventory-dependent reservation price and a width rule. They are simplified controls, not a fitted or optimal implementation of the original model. See [Avellaneda and Stoikov's paper](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf).

## 2. Add delayed quote replacement

The latency engine stores a one-unit bid and ask with immutable prices and order-generation identifiers. A replacement arrives after a fixed number of events. The old quote can fill while replacement is pending; a consumed side cannot fill again until a new quote activates.

Three environments, six policies, delays of 0/1/3 events and 500 common paths per environment produce paired comparisons. The fixed additional half-width is `sigma * sqrt(delay)`. Its parameters are not fitted to improve the displayed outcomes.

With 75% informed flow, the AS-inspired inventory rule’s mean net P&L changes from +8.88 at delay zero to −114.11 at delay three. Adding the buffer improves the paired result by +109.84 model units, with a pointwise paired 95% interval of [103.93, 115.78]. The resulting mean is still −4.27 and fills decrease from 96.32 to 31.80. The trade-off is protection versus activity.

![Delay-buffer trade-off](../results/delay/figure_2_buffer_tradeoff.png)

This is a fixed-delay, atomic-replacement model with a live initial quote pair. After startup the pipeline can activate a replacement every event; delay is not a separate replenishment wait after every fill. Cash/inventory limits use an idealised immediate veto. Queues, competing orders, partial fills, feed latency and actual exchange risk reservation are absent. These limitations are not hidden by the paired comparisons.

[hftbacktest's latency documentation](https://hftbacktest.readthedocs.io/en/latest/latency_models.html) and [ABIDES](https://arxiv.org/abs/1904.12066) informed the order-state questions. Their engines are not used here.

## 3. Check a separate price-risk assumption with stocks

The stock pilot uses minute-end NBBO midpoints from EBAY and LULU, 40 sessions each from 4 January to 2 March 2021. Each session contains 390 regular-session minute starts, 09:30 inclusive to 16:00 exclusive in New York time. The 16:00 bar and overnight windows are excluded.

The first 20 sessions calibrate; the next 20 evaluate. For horizons 1, 3 and 10 minutes, compare three fixed bounds: one-sigma square-root-of-time, Gaussian 95% reference, and the training absolute-return 95th percentile. The first rule has no 95% coverage target. No threshold is refitted to the evaluation or opening-period outcomes.

Every minute in a return window must have valid quotes. Locked bid/ask quotes are retained; crossed or invalid quotes are masked. Zero quote updates need not mean a missing observation. Missing minutes are not interpolated. Windows stay within one session and phase.

At three minutes, the empirical bound covers 95.13% of EBAY and 95.23% of LULU evaluation windows. The opening 30 origin-minute coverages are 76.33% and 79.50%, compared with 96.68% and 96.53% in the middle 330 minutes. The pooled threshold can mask opening-period undercoverage. This is a descriptive split, not a fitted opening strategy or a segment-significance test.

All 18 aggregate cells, 54 time-band cells and six endpoint comparisons are retained. Whole-session uncertainty uses 2,000 shared non-circular five-trading-session block draws, conditional on the earlier calibration. Windows overlap, and only 20 evaluation sessions per stock are available. Intervals are pointwise and unadjusted; calibration uncertainty is not included.

Endpoint comparisons ask whether an earlier quote is beyond a later midpoint or opposite quote. They do not show that our order was resting or filled. An unchanged locked quote can satisfy both inclusive touch comparisons without a price move. Minute observations cannot calibrate the simulator's event delays into actual time or establish real maker P&L.

## Evidence and reproduction

The [data guide](../data/README.md) explains source files and checksum validation. The stock statistics now live in `study/stock_statistics.py`. Its eleven functions were extracted unchanged from the earlier crypto runner. The crypto study and superseded inventory experiment remain on the [archive branch](https://github.com/Wanji-Jimmy/market-making-lab/tree/archive/before-2026-10-26-simplification); they are no longer entry points on this branch.

The publication keeps selected original result tables and two figures byte-for-byte. [Provenance](../results/provenance.json) records their original source hashes. Later maintenance changes paths, messages and file organisation without changing the retained calculations. The stock protocol still records the original parent source and protocol hashes as historical identifiers. Current stock runs hash the extracted helpers, current producer, protocol and relocated tests before and after calculation. Fresh source hashes and run fingerprints therefore differ from the historical ones. Original full manifests, private machine paths, older branches of the research and large trace archives remain in the local archive. A fresh run generates its own complete outputs and hashes. No new performance numbers were selected or optimised during packaging.


## Run the studies

Use Python 3.10+ from the repository root. No external packages are required:

```bash
python3 study/lesson.py
PYTHONPATH=study python3 -m unittest discover -s tests -q
python3 study/research.py --output-dir /tmp/mm-inventory-check --runs 3 --steps 10 --paired-resamples 10 --markout-resamples 10
python3 study/latency_research.py --output-dir /tmp/mm-delay-check --runs 2 --steps 10 --paired-resamples 10 --markout-resamples 10
```

The small runs check the pipeline, not the published estimates. Omit size overrides for the frozen full experiments. Choose an unused output directory; existing results are protected. The tests check the ledger, inventory limits, paired paths, quote replacement, training-only bounds and stock input validation.

After obtaining the matching stock CSVs described in the data guide:

```bash
python3 study/equity_research.py --source-dir data/equities --acquisition-manifest data/acquisition_manifest.json --coverage-manifest data/equity_coverage.json --output-dir /tmp/mm-stock-check
```

Stock defaults resolve relative to this repository. Explicit paths let the raw files stay elsewhere. New or revised vendor downloads may fail the checksums; do not replace expected hashes to force a match.
