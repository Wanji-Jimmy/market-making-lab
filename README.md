# Market Making Lab

A Python study of inventory, quote delay and stock price risk. It compares quoting rules on the same simulated markets, then uses historical stock quotes to check a separate assumption about price moves.

Three findings:

- In balanced simulated flow, adjusting quotes for inventory reduced maximum drawdown. The comparison with equally wide quotes did not establish a profit improvement.
- When replacements were delayed and many simulated customers had advance price information, wider quotes reduced losses and fills. They still lost money.
- Bounds calibrated on earlier data covered about 95% of later three-minute moves in EBAY and LULU, but only 76-80% for moves starting in the opening half-hour.

![Stock price-bound coverage by time of day](results/stock/equity_session_bands.png)

Start with the [five-event example](study/lesson.py). Then read the [ledger and quotes](study/simulator.py), [inventory comparison](study/research.py), [delayed quotes](study/latency_simulator.py) and [stock diagnostic](study/equity_research.py).

Python 3.10+ is enough; calculations and tests use only the standard library. From the repository root:

```bash
python3 study/lesson.py
PYTHONPATH=study python3 -m unittest discover -s tests -q
```

These commands need no market data. [Study notes](docs/STUDY.md) contain the full findings, saved tables, assumptions and commands for small checks or full experiments. The [data guide](data/README.md) covers stock inputs and checksums; raw prices are not bundled.

Synthetic profit includes fees and terminal liquidation. The model has no queues, partial fills or market impact. The stock exercise measures price coverage, not fills or trading profit, and its data were used before this study.

[Result provenance](results/provenance.json) identifies the original runs. The [archive before simplification](https://github.com/Wanji-Jimmy/market-making-lab/tree/archive/before-2026-10-26-simplification) preserves the older code and experiments.
