# Market-Making Simulator

**Why can a market maker earn the spread and still lose money?**

A small Python learning project about bid/ask quotes, cash and inventory. It compares two simple rules:

| Rule | How it quotes |
| --- | --- |
| Fixed spread | Keep the same spread around the changing reference price. |
| Inventory adjustment | Move both quotes down when holding too much inventory, encouraging sales; move them up when short. |

Both rules face the same simulated market and customers. The account tracks trades, inventory, costs and P&L.

## Start with the ledger

Python 3.10+; no extra packages. Run from the repository root:

```bash
python3 lesson.py
```

This follows five preset events through the account. In this zero-fee example:

| Contribution | Model units |
| --- | ---: |
| Execution edge relative to the reference price | +2.70 |
| Inventory price changes | −6.00 |
| Marked P&L before liquidation | **−3.30** |

The lesson: earning the spread does not remove the risk of holding inventory. In a separate falling-price scenario, inventory adjustment reduced the average loss, but both rules still lost money. [Scenario results](results/研究结果.md)

Start reading [lesson.py](lesson.py), then the quoting and accounting functions in [simulator.py](simulator.py). [中文简明介绍](README.zh-CN.md)

The core market and fills are simulated. Quotes straddle the reference by construction; there is no order book or queue model. Results are learning examples, not evidence of real trading profits.

<details>
<summary>Optional: real-data extension and full research notes</summary>

The extension uses public ETHUSDT trades to test whether recent buy/sell volume can help adjust quotes. Prediction results were mixed: squared error fell slightly, absolute error rose, and simulated policy comparisons depended on assumed customer willingness. Observed trades do not establish our own fills.

- [Detailed project overview](docs/PROJECT_DETAILS.md) · [完整中文说明](docs/PROJECT_DETAILS.zh-CN.md)
- [Real-data results](flow_research_results/report.md) · [Worked explanation](docs/FLOW_RESEARCH_GUIDE.zh-CN.md)
- [Methods](docs/METHODOLOGY.md) · [Plan and research history](research/RESEARCH_JOURNAL.md)
- [Minute-level diagnostic](docs/EMPIRICAL_RESULTS.md) · [Individual-trade diagnostic](docs/TICK_DATA_DIAGNOSTIC.md)

The detailed overview contains the full reproduction commands, controls and checks. All code and recorded results remain in the repository.

</details>

Developed with Codex assistance as a learning project.
