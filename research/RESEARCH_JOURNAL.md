# Research journal: trade flow into quotes

This journal records work actually performed in this repository. It does not imply that the learner wrote the implementation independently. Code, tests and initial documentation in this iteration are AI-assisted; understanding, interpretation and subsequent independent work must be described separately.

## 2026-10-15 — Question and protocol, before new dates

The earlier simulator and real-data diagnostics were separate. September 1-3 had already been inspected; the one-minute relationship was not evidence of reliable prediction. The individual-trade response also could not measure our own fills.

We therefore narrowed the question to a measurable link: previous five-second volume imbalance → next-bucket trade-price forecast → quote-center adjustment. The old diagnostics motivated the hypothesis, but did not supply a calibrated coefficient or a proof of profitability.

The accompanying [frozen research plan](FLOW_SIGNAL_PLAN.json) declares September 1-3 training, September 4-5 diagnostic validation and September 6-8 testing. At this entry, September 4-8 archives have not been downloaded or inspected by this project. Dates and parameters will not be selected again after seeing their outcomes. The plan is published separately before those downloads.

One-feature OLS is deliberately interpretable: the test asks whether this particular signal adds anything, before considering more complex models. Validation is diagnostic, with no hyperparameter search. The imbalance slope, train-only intercept, zero forecast and training-mean forecast can be compared directly.

The execution experiment retains artificial customer willingness. Real prices and subsequent aggressor sides provide an external environment; they do not identify our fills. Inventory marking must include gaps, and strategy logic cannot access the future environment. These distinctions are part of the research question, not small-print after the result.

## 2026-10-15 — Implementation and checks before development run

The plan was published in commit [`37eaf0a`](https://github.com/Wanji-Jimmy/market-making-lab/commit/37eaf0af8a5a7786546e524dc2031debbac3a512) before fetching September 4-8. Three modules now separate feature construction, quote-policy accounting and staged execution. The existing baseline modules remain unchanged.

Before the development run, 110 offline tests passed. New tests cover exact interval boundaries, mutations of future inputs, train-date contamination, current-only quotes, shared random environments, missing-interval inventory marking, transaction costs, terminal liquidation and preservation of the original freeze/access records. A separate review also reconstructed 12 known-date feature/label pairs directly using Decimal arithmetic.

Review identified two issues before any new-date results: adjacent reference continuity needed explicit checking, and reproduction needed to bind the first test-access record back to the exact original freeze. Both were corrected and tested before the development run. This is recorded as pre-result code review, not a strategy improvement inferred from market performance.

Forecast metrics will cover training, validation and test periods. Policy replay is deliberately limited to the declared test period. The validation period does not select a model, parameter or policy. Random seeds generate hypothetical willingness on the same observed path and do not increase the number of observed test days.

## 2026-10-15 — Development result and model freeze

The declared development run completed at 08:59 UTC. Training used 51,817 eligible five-second observations from September 1-3. It estimated `alpha = 0.0080421135 bp` and `beta = 0.2216479781 bp per unit imbalance`. Neither coefficient uses validation or test labels. The +/-2bp clipping limit is inactive over the possible imbalance range for this model.

The September 4-5 validation sample has 34,490 eligible observations. Against a zero-change forecast, MSE fell from 5.033152 to 5.008791 bp², but MAE rose from 1.281456 to 1.311213 bp. This is mixed evidence, not a reason to claim reliable profitability. As declared, no model, parameter, policy or date was changed after viewing these results.

The model, computational source files, tests, plan and development outputs are bound in [freeze.json](../flow_research_results/freeze.json), timestamped `2026-10-15T08:59:36.155589+00:00`. These files will be published before opening the test dates. The reporting script and explanatory prose may be written later; they do not alter frozen forecasting or policy computation.

## Pending recorded stages

- Verify the freeze, then open the declared test dates once and report every policy and sensitivity.
- Record negative findings, limitations and an interview explanation based on the actual outputs.
