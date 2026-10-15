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

## 2026-10-15 — First held-out evaluation

The model, code, tests and development records were published in commit [`9ed3ed0`](https://github.com/Wanji-Jimmy/market-making-lab/commit/9ed3ed0e207ce8294a881bacea8a9a4d02c2c5e1) before the test access began at `2026-10-15T09:01:18.474804+00:00`. The original access receipt is retained in [test_access.json](../flow_research_results/test_access.json). There were no post-test changes to computational sources, coefficients, dates or policy parameters.

September 6-8 supplied 51,402 eligible labeled observations. Against the zero forecast, held-out MSE fell from 1.610946 to 1.602750 bp², while MAE rose from 0.637356 to 0.704599 bp. The small MSE improvement persisted, but the absolute-error deterioration became more pronounced. A prediction hit rate alone would have concealed this mixed result.

All 135 declared policy runs were retained: 3 dates × 5 policies × (5 primary willingness seeds + 4 single-seed sensitivities). The primary daily mean P&L differences for `flow − inventory` were +0.772193, −0.073633 and +2.062366 normalized model-currency units. The wider-spread control had higher modeled P&L than the flow policy. These comparisons reflect assumed willingness and fills; they do not identify an executable trading edge.

The fitted signal does now enter quote construction. The result supports a modest, mixed empirical relationship and a small conditional policy effect in this model, not a broad profitability claim. No new specification was selected from the test results. Next-stage hypotheses belong in a new plan with new unseen dates; realistic execution would additionally require quote/order-book and order-lifecycle information.

The [generated report](../flow_research_results/report.md) retains every policy, daily result, sensitivity, error baseline and source manifest. The [learning guide](../docs/FLOW_RESEARCH_GUIDE.zh-CN.md) separates the implemented method from the learner's own understanding and contribution.

## 2026-10-15 — Reproduction and independent result review

The full offline reproduction finished at `2026-10-15T09:03:26.042171+00:00`. It refitted the same coefficients, reproduced development forecasts and regenerated all 135 test policy runs. Forecast metrics, daily policy summaries and quality diagnostics matched the original records exactly. Original test and first-access records were preserved.

An independent result review checked all 135 rows, their 75 day/case/policy summaries, accounting reconciliation, source hashes and access chronology. It confirmed a mean primary `flow − inventory` P&L change of +0.920309 model units but a slightly worse mean maximum drawdown (1.955199 versus 1.935918). Participation was essentially unchanged. These mixed results remain in the report.

GitHub Actions passed the frozen implementation on Python 3.10 and 3.12, including all 110 unit tests, the teaching ledger and the baseline smoke simulation: [run 38709866432](https://github.com/Wanji-Jimmy/market-making-lab/actions/runs/38709866432). No frozen computational source changed after test access. Later edits are result presentation and documentation only.

This completes the declared iteration. It does not prove real execution quality or generalization beyond three test days. Wider quotes outperform the flow rule under the main willingness assumption, while the ranking changes under lower willingness; the apparent profitability depends materially on the artificial execution model. The next research decision is to examine execution assumptions using suitable quote/order-book evidence, not to tune this already-inspected test into a more attractive result.

## 2026-10-15 — Scope closed for the undergraduate project

The project is now scoped as a focused undergraduate study suitable for an approximately 40-hour guided learning and research effort. That is a planning budget, not a record of hours already completed. The feature set is complete for the stated question; further execution research is optional future work, not required to finish this project.

The public overview was shortened to one research question, the method, the mixed findings and reproduction. Earlier diagnostics remain supporting evidence. No computational sources, parameters, dates or recorded results changed. Personal CV wording depends on the learner actually understanding and verifying the work described; AI assistance and the distinction between observed prices and hypothetical fills remain explicit.
