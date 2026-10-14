# Contributing

Keep changes tied to a testable question about quoting, execution, or risk. A small reproducible experiment with explicit assumptions is more useful here than a larger set of unvalidated features.

## Before submitting a change

1. Describe the behavior being changed and the assumptions it requires.
2. Run `python3 -m unittest discover -v`.
3. Run a short simulation without replacing the recorded baseline:

   ```bash
   python3 experiment.py --runs 4 --steps 60 --sensitivity-runs 4 --output /tmp/market-making-smoke
   ```

4. Add a focused test if changing accounting, execution, timestamp handling, or data validation. Tests must not require network access.
5. Include commands, parameters, seed ranges, and any material limitations when reporting experimental results.

The core simulation requires only the Python standard library. Discuss a new dependency before adding one.

## Research and data changes

Preserve shared external paths when comparing strategies. Separate development results from a held-out evaluation, and state whether an apparent improvement changes trade count, cost, or risk exposure. Do not present synthetic profit as historical or live performance.

For public-data work, record the source, date range, schema, timestamp units, and checksum verification. Keep raw downloaded archives out of Git. Aggregate trade records do not establish hypothetical limit-order fills or queue position.

Changing a source file checked by `diagnose_pnl.py` requires regenerating the baseline with `python3 experiment.py` before replaying it. Do not bypass a source-hash mismatch to force agreement.

## Reporting a problem

Include the Python version, the smallest command or input that reproduces the issue, the expected behavior, and the observed output. Remove credentials, personal information, and machine-specific paths before sharing logs.
