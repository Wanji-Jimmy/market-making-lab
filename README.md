# Market Making Lab

**When does inventory control help, and what happens when quotes arrive late?**

A Python study of quoting and risk. It starts with a cash-and-inventory ledger, compares policies on the same simulated markets, and checks a separate price-risk assumption using historical stock quotes.

## Three findings

- **Inventory control has a trade-off.** In balanced simulated flow, the AS-inspired inventory rule reduced mean drawdown from 85.45 to 39.14 model units. Its profit difference against a rule with the same spread was small and uncertain: +0.89, with a paired 95% interval of −5.18 to +7.02.
- **Late replacement changes the outcome.** With 75% informed customers, the AS-inspired inventory rule's mean net P&L changed from +8.88 with immediate replacement to −114.11 with a three-event delay. A fixed wider buffer reduced the loss to −4.27, while fills fell from 96.32 to 31.80. Wider quotes gave up activity; they did not establish a profitable strategy.
- **An average can hide opening risk.** A bound fixed using earlier stock data covered about 95% of later three-minute moves over the whole session, but only 76–80% during the first 30 origin minutes.

| Stock: fixed empirical 95% bound | Whole-session coverage | Opening 30-minute coverage |
| --- | ---: | ---: |
| EBAY | 95.13% | 76.33% |
| LULU | 95.23% | 79.50% |

![Stock coverage by time of day](results/stock/equity_session_bands.png)

Synthetic P&L includes trading fees and terminal liquidation. The stock diagnostic measures price-risk coverage, not fills or trading profit. The two experiments use different units and are not combined into one backtest.

## Explore

- [Study and assumptions](docs/STUDY.md): comparisons, accounting and references.
- Saved tables: [inventory](results/inventory/summary.csv), [delay](results/delay/summary.csv), [stocks](results/stock/summary.csv).
- [Data preparation](data/README.md): exact file requirements and checksums.

The `study/` folder holds the code and tests. The saved CSVs and figures can be read without downloading market data. This public edition focuses on three questions; earlier experiments remain preserved separately.

## Run

Python 3.10+; calculations and tests use only the standard library. From the repository root:

```bash
python3 -m unittest discover -s study -q
python3 study/lesson.py
python3 study/research.py --output-dir /tmp/mm-inventory-check --runs 3 --steps 10 --paired-resamples 10 --markout-resamples 10
python3 study/latency_research.py --output-dir /tmp/mm-delay-check --runs 2 --steps 10 --paired-resamples 10 --markout-resamples 10
```

The small runs check the pipeline, not the published estimates. Omit size overrides for the frozen full experiments. Use a new output directory; existing results are protected.

After obtaining the matching stock CSVs as described in the data guide:

```bash
python3 study/equity_research.py --source-dir data/equities --acquisition-manifest data/acquisition_manifest.json --coverage-manifest data/equity_coverage.json --output-dir /tmp/mm-stock-check
```

This recalculation uses the published metadata and explicitly supplied data directory. Raw stock files are not bundled. New or revised downloads may fail the checksums.

## Limits

The simulated market has no queue position, partial fills or impact. Delays are event counts, not measured milliseconds. Informed flow is a stated stress assumption. The stock sample has two names and 20 evaluation sessions each; minute-end quotes cannot reveal our own fills or cancellations. Previously used historical data is not a blind test. Coverage intervals condition on earlier calibration.

This is a study of accounting, controlled comparisons and failed assumptions, not evidence of live profitability. [Saved-result provenance](results/provenance.json) distinguishes the original experiments from later maintenance changes.

The [earlier public version](https://github.com/Wanji-Jimmy/market-making-lab/tree/archive/before-2026-10-26-update) and [extended study](https://github.com/Wanji-Jimmy/market-making-lab/tree/extended-study) remain available.
