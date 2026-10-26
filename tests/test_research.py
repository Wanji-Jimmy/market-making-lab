import copy
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research import (assert_complete_paths, load_protocol, make_parser,
                      run_research, summarize_paths, validate_as_widths,
                      validate_path)
from simulator import Config, Event, simulate


class ResearchAuditTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(initial_cash=1000, initial_inventory=1)
        self.events = [Event(1, 100, 98, "sell", 98),
                       Event(2, 98, 103, "buy", 100),
                       Event(3, 103, 101, None, None)]
        self.result = simulate(self.events, self.config, "fixed")

    def test_independent_ledger_accepts_accounting_and_rejects_component_tampering(self):
        audit = validate_path(self.events, self.config, self.result)
        self.assertLess(abs(audit["terminal_accounting_residual"]), 1e-7)
        corrupted = copy.deepcopy(self.result)
        corrupted["metrics"]["inventory_carry_pnl"] += 1
        with self.assertRaisesRegex(ArithmeticError, "inventory carry"):
            validate_path(self.events, self.config, corrupted)

    def test_independent_ledger_rejects_cash_trace_tampering(self):
        corrupted = copy.deepcopy(self.result)
        corrupted["trace"][0]["cash"] += 0.1
        with self.assertRaisesRegex(ArithmeticError, "trace cash ledger"):
            validate_path(self.events, self.config, corrupted)

    def test_as_width_control_checks_each_step_not_just_the_mean(self):
        left = [dict(step=1, bid=99, ask=101), dict(step=2, bid=99, ask=101)]
        translated = [dict(step=1, bid=98, ask=100), dict(step=2, bid=100, ask=102)]
        self.assertEqual(validate_as_widths(left, translated), 0)
        same_mean_wrong_steps = [dict(step=1, bid=99, ask=100), dict(step=2, bid=99, ask=102)]
        with self.assertRaisesRegex(ArithmeticError, "widths differ"):
            validate_as_widths(left, same_mean_wrong_steps)

    def test_missing_duplicate_and_unexpected_path_keys_are_rejected(self):
        rows = [dict(case="a", seed=1, policy="fixed"),
                dict(case="a", seed=1, policy="inventory")]
        assert_complete_paths(rows, ["a"], [1], ["fixed", "inventory"])
        with self.assertRaisesRegex(ValueError, "missing"):
            assert_complete_paths(rows[:1], ["a"], [1], ["fixed", "inventory"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assert_complete_paths(rows + rows[:1], ["a"], [1], ["fixed", "inventory"])
        with self.assertRaisesRegex(ValueError, "unexpected"):
            assert_complete_paths(rows, ["a"], [1], ["fixed"])

    def test_summary_uses_liquidated_losses_and_unrounded_arithmetic_means(self):
        rows = [dict(case="a", policy="fixed", pnl_liquidated=-2, pnl_mtm=1, trades=1),
                dict(case="a", policy="fixed", pnl_liquidated=4, pnl_mtm=5, trades=4)]
        summary = summarize_paths(rows, ["pnl_liquidated", "pnl_mtm", "trades"])[0]
        self.assertEqual(summary["loss_probability"], 0.5)
        self.assertEqual(summary["mean_pnl_liquidated"], 1)
        self.assertEqual(summary["mean_trades"], 2.5)


class FrozenProtocolTests(unittest.TestCase):
    def test_protocol_is_factorial_where_intended_and_parameters_are_explicit(self):
        protocol = load_protocol()
        cases = {case["name"]: case for case in protocol["cases"]}
        self.assertEqual(set(cases), {"balanced", "flow_only", "drift_only", "flow_and_drift",
                                     "informed_25", "informed_75", "fee_stress", "uniform_demand"})
        self.assertEqual(cases["flow_and_drift"]["scenario_overrides"],
                         {**cases["flow_only"]["scenario_overrides"],
                          **cases["drift_only"]["scenario_overrides"]})
        self.assertEqual(protocol["scenario_defaults"]["volatility"], 1.2)
        self.assertEqual(protocol["simulation"]["seed_start"], 20000)
        self.assertEqual(protocol["simulation"]["runs"], 500)
        self.assertEqual(protocol["risk_parameters"]["risk_aversion"], 0.0005)
        self.assertEqual(cases["fee_stress"]["config_overrides"], {"fee_per_trade": 1.1})

    def test_parser_rejects_nonpositive_run_counts_before_simulation(self):
        with self.assertRaises(SystemExit):
            make_parser().parse_args(["--runs", "0"])


class ResearchSmokeTests(unittest.TestCase):
    def test_two_paths_three_steps_share_events_and_reconcile_saved_estimands(self):
        # This is explicitly exploratory: it must never create the frozen 500-path output.
        import research
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "smoke"
            with patch("research.generate_controlled_market",
                       wraps=research.generate_controlled_market) as market, \
                 patch("research.simulate", wraps=research.simulate) as simulation:
                manifest = run_research(target, runs=2, steps=3,
                                        paired_resamples=7, markout_resamples=7,
                                        command="unittest smoke, 2 paths x 3 steps")
            self.assertEqual(market.call_count, 16)
            calls = simulation.call_args_list
            self.assertEqual(len(calls), 64)
            for offset in range(0, len(calls), 4):
                group = calls[offset:offset + 4]
                self.assertTrue(all(call.args[0] is group[0].args[0] for call in group))
                self.assertEqual({call.args[2] for call in group},
                                 {"fixed", "inventory", "as_symmetric", "as_inventory"})
            self.assertEqual(manifest["run_classification"], "exploratory")
            self.assertEqual(manifest["validation"]["path_count"], 64)
            self.assertIn("runs", manifest["overrides"])
            with (target / "paths.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            with (target / "paired.csv").open(newline="", encoding="utf-8") as stream:
                paired = list(csv.DictReader(stream))
            observed = next(row for row in paired if row["case"] == "balanced"
                            and row["contrast"] == "inventory_minus_fixed"
                            and row["metric"] == "pnl_liquidated")
            samples = {(int(row["seed"]), row["policy"]): float(row["pnl_liquidated"])
                       for row in rows if row["case"] == "balanced"}
            manual = sum(samples[seed, "inventory"] - samples[seed, "fixed"]
                         for seed in (20000, 20001)) / 2
            self.assertAlmostEqual(float(observed["mean"]), manual)
            self.assertEqual(int(observed["count"]), 2)
            self.assertEqual(len(list((target / "traces").glob("*.csv"))), 32)
            with (target / "markouts.csv").open(newline="", encoding="utf-8") as stream:
                markouts = list(csv.DictReader(stream))
            long_horizon = [row for row in markouts if row["horizon"] == "20"]
            self.assertTrue(all(row["total_fills"] == "0" and row["mid_move_mean"] == ""
                                for row in long_horizon))
            self.assertNotIn("manifest.json", manifest["output_files"])
            for name, metadata in manifest["output_files"].items():
                self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(),
                                 metadata["sha256"])
            self.assertEqual(json.loads((target / "manifest.json").read_text()), manifest)
            with self.assertRaises(FileExistsError):
                run_research(target, runs=2, steps=3, paired_resamples=7, markout_resamples=7)


if __name__ == "__main__":
    unittest.main()
