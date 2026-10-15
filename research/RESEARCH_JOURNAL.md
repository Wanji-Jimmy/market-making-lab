# Research journal: trade flow into quotes

This journal records work actually performed in this repository. It does not imply that the learner wrote the implementation independently. Code, tests and initial documentation in this iteration are AI-assisted; understanding, interpretation and subsequent independent work must be described separately.

## 2026-10-15 — Question and protocol, before new dates

The earlier simulator and real-data diagnostics were separate. September 1-3 had already been inspected; the one-minute relationship was not evidence of reliable prediction. The individual-trade response also could not measure our own fills.

We therefore narrowed the question to a measurable link: previous five-second volume imbalance → next-bucket trade-price forecast → quote-center adjustment. The old diagnostics motivated the hypothesis, but did not supply a calibrated coefficient or a proof of profitability.

The accompanying [frozen research plan](FLOW_SIGNAL_PLAN.json) declares September 1-3 training, September 4-5 diagnostic validation and September 6-8 testing. At this entry, September 4-8 archives have not been downloaded or inspected by this project. Dates and parameters will not be selected again after seeing their outcomes. The plan is published separately before those downloads.

One-feature OLS is deliberately interpretable: the test asks whether this particular signal adds anything, before considering more complex models. Validation is diagnostic, with no hyperparameter search. The imbalance slope, train-only intercept, zero forecast and training-mean forecast can be compared directly.

The execution experiment retains artificial customer willingness. Real prices and subsequent aggressor sides provide an external environment; they do not identify our fills. Inventory marking must include gaps, and strategy logic cannot access the future environment. These distinctions are part of the research question, not small-print after the result.

## Pending recorded stages

- Implement and test temporal boundaries, information isolation, exact accounting and freeze protection.
- Run development, save source/archive hashes and frozen coefficients.
- Verify the freeze, then open the declared test dates once and report every policy and sensitivity.
- Record negative findings, limitations and an interview explanation based on the actual outputs.
