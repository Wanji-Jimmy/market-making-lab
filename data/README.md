# Stock input files

Saved tables and synthetic tests need no raw market data. Recalculating the stock diagnostic requires the exact 80 EBAY/LULU CSV files listed in [acquisition_manifest.json](acquisition_manifest.json).

1. Obtain the 2021 US-equity minute sample from [AlgoSeek's Machine Learning for Trading companion page](https://algoseek.com/ml-for-trading/) under its applicable terms. The acquisition manifest records the original daily archive URLs and member paths. Source availability can change; no current availability guarantee is made here.
2. Extract only the listed EBAY and LULU members for 4 January–2 March 2021. Rename each file using `SYMBOL_YYYYMMDD.csv`, for example `EBAY_20210104.csv`, and put them in `data/equities/`. Do not modify rows or headers: matching raw bytes are required.
3. Run the following from the repository root, choosing an unused output directory:

```bash
python3 study/equity_research.py --source-dir data/equities --acquisition-manifest data/acquisition_manifest.json --coverage-manifest data/equity_coverage.json --output-dir /tmp/mm-stock-reproduction
```

The loader checks all fixed stock-days, both sets of SHA-256 records, the 90-column sample schema, minute coverage and NBBO values. Missing or altered inputs stop the run before result writing. A revised vendor download is a different dataset; do not replace expected hashes merely to make validation pass. Reproducing the original statistics requires the original bytes.

`acquisition_manifest.json` is an export of the original acquisition metadata with machine-local paths removed. `equity_coverage.json` contains only file/checksum records extracted from the earlier validation. Hashes of the raw CSVs are unchanged. These metadata exports have new hashes of their own; fresh run manifests correctly record those new metadata hashes.

Attribution: AlgoSeek historical stock trade-and-quote educational samples. Raw prices are not bundled or redistributed. Relevant field definitions are in the [AlgoSeek TAQ Minute Bar Guide](https://us-equity-market-data-docs.s3.amazonaws.com/algoseek.US.Equity.TAQ.Minute.Bars.pdf); see the provider's [licensing information](https://algoseek.com/licensing-faq/). Provider adjustment flags are not independently certified. Within-session ratios avoid overnight jumps and cancel day-constant scaling, but do not certify total-return history. AlgoSeek does not endorse this analysis.
