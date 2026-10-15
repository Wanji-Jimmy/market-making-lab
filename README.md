# Market Making Lab

**Can recent trade flow improve inventory-aware quotes?**

A focused undergraduate Python study of inventory risk and quoting. It connects a simple accounting simulator to public ETHUSDT trade data, then tests whether a linear forecast adds useful information to an inventory-based quoting rule.

[Results](flow_research_results/report.md) · [Methodology](docs/METHODOLOGY.md) · [Research journal](research/RESEARCH_JOURNAL.md) · [中文讲解](docs/FLOW_RESEARCH_GUIDE.zh-CN.md)

## The experiment

```text
Past five-second buy/sell volume → imbalance → frozen linear forecast
                                                    ↓
Current reference price + inventory adjustment + forecast → bid / ask
                                                    ↓
Hypothetical fills → cash, inventory, costs and P&L
```

The ledger tracks cash, inventory, fees, position limits and terminal liquidation. The empirical feature is `(aggressive buy volume − aggressive sell volume) / total volume`. A single OLS regression predicts the next five-second trade-price change. Its frozen prediction shifts the quote centre alongside the inventory adjustment.

| Quoting rule | Purpose |
| --- | --- |
| Fixed spread | Basic reference-price quoting |
| Inventory adjustment | Main baseline: discourage accumulating inventory |
| Inventory + flow forecast | Test whether the forecast adds value |
| Inventory + wider spread | Control for simply changing spread and participation |
| Inventory + intercept only | Isolate the contribution of the imbalance term |

All policies face the same price path and hypothetical customer opportunities for each date and seed. The comparisons include P&L attribution, inventory exposure, drawdown, fees and customer-willingness sensitivities.

## Data and research design

Official Binance spot ETHUSDT aggregate-trade archives are checksum-verified. Features use only the preceding five-second interval; the reference is the last traded price, not a midquote.

| Split | UTC dates in 2025 | Use |
| --- | --- | --- |
| Training | September 1–3 | Fit the one-feature model; these dates had already been explored |
| Validation | September 4–5 | Diagnose the declared model without tuning |
| Test | September 6–8 | Evaluate the frozen model and policies |

The [plan](research/FLOW_SIGNAL_PLAN.json) was published before opening new dates; the [model and code were frozen](flow_research_results/freeze.json) before test access. The study includes **51,402 test observations** and **135 policy runs**. Seeds change assumed customer willingness on the same three price paths; they do not create independent market histories.

## What the results showed

| Held-out measure | Baseline | Flow model / policy | Finding |
| --- | ---: | ---: | --- |
| Forecast MSE (bp²), versus zero prediction | 1.610946 | 1.602750 | 0.51% lower |
| Forecast MAE (bp), versus zero prediction | 0.637356 | 0.704599 | 10.55% higher |
| Mean daily simulated P&L, versus inventory policy | 127.0193 | 127.9396 | Small improvement; one of three daily differences is negative |
| Mean maximum drawdown | 1.9359 | 1.9552 | Slightly worse |

**The evidence is mixed.** A small improvement under squared prediction error does not imply a better trading strategy. Wider quotes perform better under the main assumed customer willingness, but that ranking reverses at lower willingness; the flow rule then also trails the inventory baseline. Execution assumptions materially affect the conclusion.

P&L is in normalized model-currency units, not USDT or realized returns. Historical prices and aggressor directions are observed; customer limits, routing, size and our fills are assumed. Quotes straddle the reference by construction. There is no order-book or queue model, and three test days do not establish broad profitability. See the [complete daily results, controls and sensitivities](flow_research_results/report.md).

## Reproduce

Python 3.10+; standard library only. From the repository root:

```bash
# Read saved evidence and regenerate the report without downloading data.
python3 build_flow_report.py

# Check accounting, information boundaries and the frozen research workflow.
python3 -m unittest discover -v

# Reproduce the empirical experiment; downloads official archives on first use.
python3 flow_research.py --stage reproduce
# Add --offline once the raw-data cache is complete.
```

The original records are preserved; reproduction writes separate outputs and checks agreement. All **110 tests** passed, and the full offline reproduction matched the original metrics. [Verification receipt](flow_research_results/verification.json) · [GitHub checks](https://github.com/Wanji-Jimmy/market-making-lab/actions)

## Read the code

| File | Responsibility |
| --- | --- |
| [simulator.py](simulator.py), [lesson.py](lesson.py) | Accounting and a five-event worked ledger |
| [flow_data.py](flow_data.py) | Five-second features, OLS and forecast evaluation |
| [flow_policy.py](flow_policy.py) | The five quoting rules and hypothetical execution |
| [flow_research.py](flow_research.py) | Date splits, freeze, first evaluation and reproduction |

The current research scope is complete: one asset, one feature, one linear model and a small set of quoting controls. Earlier [synthetic scenarios](results/研究结果.md), [minute-level diagnostics](docs/EMPIRICAL_RESULTS.md) and [individual-trade checks](docs/TICK_DATA_DIAGNOSTIC.md) remain supporting studies. The [Chinese learning guide](docs/FLOW_RESEARCH_GUIDE.zh-CN.md) supplies a worked quote example and checks for understanding; [detailed methods](docs/METHODOLOGY.md) cover execution conventions and reproduction.

Implementation, tests and documentation were developed with Codex assistance. This repository records an educational research experiment and its limitations; it does not claim unaided authorship or real-world profitability.
