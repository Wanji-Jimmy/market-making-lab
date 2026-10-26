import copy
import csv
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import latency_research
from latency_research import (assert_complete_paths, load_protocol, make_parser,
                              paired_rows, run_latency_research, summarize_paths,
                              validate_order_diagnostics, validate_width_pairs,
                              validate_zero_latency)
from latency_simulator import simulate_latency
from simulator import Config, Event, RiskParameters, simulate


class LatencyRunnerAuditTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.risk = RiskParameters()
        self.events = [Event(i + 1, 100, 100, "buy", 1000) for i in range(6)]

    def test_all_zero_latency_core_and_buffered_policies_recover_v2(self):
        for policy in latency_research.STRATEGIES:
            with self.subTest(policy=policy):
                reference = simulate(self.events, self.config, policy.removeprefix("buffered_"),
                                     risk=self.risk)
                result = simulate_latency(self.events, self.config, policy, latency_steps=0,
                                          risk=self.risk)
                self.assertEqual(validate_zero_latency(reference, result),
                                 dict(zero_latency_recovery_checked=True,
                                      zero_latency_recovery_max_metric_gap=0))
        corrupted = copy.deepcopy(result)
        corrupted["trace"][0]["bid"] += 0.01
        with self.assertRaisesRegex(ArithmeticError, "zero-latency trace"):
            validate_zero_latency(reference, corrupted)

    def test_duplicate_order_ids_and_incorrect_age_metrics_are_rejected(self):
        result = simulate_latency(self.events, self.config, "fixed", latency_steps=3)
        validate_order_diagnostics(result, 3, 6)
        filled = [row for row in result["trace"] if row["filled"]]
        self.assertGreaterEqual(len(filled), 2)
        corrupted = copy.deepcopy(result)
        copied_fills = [row for row in corrupted["trace"] if row["filled"]]
        copied_fills[1]["filled_order_id"] = copied_fills[0]["filled_order_id"]
        with self.assertRaisesRegex(ArithmeticError, "duplicate filled_order_id"):
            validate_order_diagnostics(corrupted, 3, 6)
        corrupted = copy.deepcopy(result)
        corrupted["metrics"]["fill_information_age_sum"] += 1
        with self.assertRaisesRegex(ArithmeticError, "fill_information_age_sum"):
            validate_order_diagnostics(corrupted, 3, 6)

    def test_width_control_audits_request_width_as_well_as_active_width(self):
        results = {policy: simulate_latency(self.events, self.config, policy,
                                             latency_steps=3, risk=self.risk)
                   for policy in latency_research.STRATEGIES}
        self.assertTrue(all(gap < 1e-9 for gap in validate_width_pairs(results).values()))
        results["buffered_as_inventory"]["trace"][1]["request_ask"] += 0.01
        with self.assertRaisesRegex(ArithmeticError, "requested width"):
            validate_width_pairs(results)

    def test_complete_pairing_requires_all_latency_dimensions(self):
        rows = [dict(case="a", latency_steps=l, seed=s, policy=p)
                for l in (0, 3) for s in (1, 2) for p in ("fixed", "inventory")]
        assert_complete_paths(rows, ["a"], [0, 3], [1, 2], ["fixed", "inventory"])
        with self.assertRaisesRegex(ValueError, "missing"):
            assert_complete_paths(rows[:-1], ["a"], [0, 3], [1, 2], ["fixed", "inventory"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assert_complete_paths(rows + rows[:1], ["a"], [0, 3], [1, 2], ["fixed", "inventory"])

    def test_summary_pools_age_by_fills_including_zero_fill_paths(self):
        samples = [dict(case="a", latency_steps=3, policy="fixed", trades=1,
                        pnl_liquidated=-1, fill_information_age_sum=3, fill_resting_age_sum=0,
                        fill_information_age_mean=3, fill_resting_age_mean=0,
                        negative_edge_fills=1, active_stale_fills=1),
                   dict(case="a", latency_steps=3, policy="fixed", trades=9,
                        pnl_liquidated=1, fill_information_age_sum=9, fill_resting_age_sum=0,
                        fill_information_age_mean=1, fill_resting_age_mean=0,
                        negative_edge_fills=0, active_stale_fills=9),
                   dict(case="a", latency_steps=3, policy="fixed", trades=0,
                        pnl_liquidated=0, fill_information_age_sum=0, fill_resting_age_sum=0,
                        fill_information_age_mean=None, fill_resting_age_mean=None,
                        negative_edge_fills=0, active_stale_fills=0)]
        summary = summarize_paths(samples, ["trades", "fill_information_age_mean"])[0]
        self.assertEqual(summary["pooled_fill_information_age_mean"], 1.2)
        self.assertEqual(summary["negative_edge_fill_fraction"], 0.1)
        self.assertEqual(summary["runs"], 3)
        self.assertEqual(summary["defined_fill_age_paths"], 2)
        self.assertNotIn("mean_fill_information_age_mean", summary)

    def test_delay_contrast_uses_matched_seeds_and_declared_direction(self):
        protocol = load_protocol()
        protocol["cases"] = [dict(name="a")]
        protocol["analysis"]["paired_metrics"] = ["pnl_liquidated"]
        rows = [dict(case="a", latency_steps=l, seed=s, policy=p,
                     pnl_liquidated=s * 10 + l * (s - 1))
                for l in protocol["latencies"] for s in (1, 2)
                for p in protocol["policies"]]
        pairs = paired_rows(rows, protocol, 5, 1)
        delay = next(row for row in pairs if row["contrast_type"] == "delay_effect"
                     and row["left_latency_steps"] == 3 and row["left_policy"] == "fixed")
        self.assertEqual(delay["right_latency_steps"], 0)
        self.assertEqual(delay["mean"], 1.5)
        self.assertEqual(delay["paired_win_fraction"], 0.5)


class LatencyRunnerSmokeTests(unittest.TestCase):
    def test_tiny_run_shares_tapes_saves_54_traces_and_labels_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "smoke"
            with patch("latency_research.generate_controlled_market",
                       wraps=latency_research.generate_controlled_market) as market, \
                 patch("latency_research.simulate_latency", wraps=simulate_latency) as engine, \
                 patch("latency_research.simulate", wraps=simulate) as baseline:
                manifest = run_latency_research(target, runs=2, steps=3,
                                                paired_resamples=5, markout_resamples=5)
            self.assertEqual(market.call_count, 6)
            self.assertEqual(engine.call_count, 108)
            self.assertEqual(baseline.call_count, 24)
            calls = engine.call_args_list
            for offset in range(0, len(calls), 18):
                group = calls[offset:offset + 18]
                self.assertTrue(all(call.args[0] is group[0].args[0] for call in group))
                self.assertIsInstance(group[0].args[0], tuple)
                self.assertEqual({(call.kwargs["latency_steps"], call.args[2]) for call in group},
                                 {(l, p) for l in (0, 1, 3) for p in latency_research.STRATEGIES})
            self.assertEqual(manifest["run_classification"], "smoke")
            self.assertEqual(manifest["validation"]["path_count"], 108)
            self.assertEqual(manifest["validation"]["zero_latency_recovery_paths"], 36)
            self.assertEqual(manifest["validation"]["max_zero_latency_metric_gap"], 0)
            self.assertEqual(len(list((target / "traces").glob("*.csv"))), 54)
            for name, metadata in manifest["output_files"].items():
                self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(),
                                 metadata["sha256"])
            with (target / "paths.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 108)
            with (target / "paired.csv").open(newline="", encoding="utf-8") as stream:
                pairs = list(csv.DictReader(stream))
            self.assertEqual(len(pairs), 288)
            self.assertEqual(json.loads((target / "manifest.json").read_text()), manifest)
            with self.assertRaises(FileExistsError):
                run_latency_research(target, runs=1, steps=1)

    def test_cli_defaults_are_frozen_and_invalid_run_count_is_rejected(self):
        args = make_parser().parse_args([])
        self.assertEqual((args.runs, args.steps, args.seed_start), (500, 300, 40000))
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            make_parser().parse_args(["--runs", "0"])


if __name__ == "__main__":
    unittest.main()
