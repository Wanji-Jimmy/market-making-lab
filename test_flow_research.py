"""研究流程保护：禁止训练混入测试、篡改冻结后看测试、覆盖首次访问记录。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import flow_research as research


class ResearchTests(unittest.TestCase):
    def test_training_rejects_wrong_date_set(self):
        plan = {"train_dates": ["2025-09-01"]}
        with self.assertRaisesRegex(ValueError, "训练日期"):
            research.fit_training({"2025-09-06": {"rows": []}}, plan)

    def test_training_rejects_contaminated_rows(self):
        plan = {"train_dates": ["2025-09-01"]}
        with self.assertRaisesRegex(ValueError, "训练行"):
            research.fit_training({"2025-09-01": {"rows": [{"date": "2025-09-06"}]}}, plan)

    def test_freeze_detects_every_mutable_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "plan.json"
            research.save_json(plan, {"test_dates": ["2025-09-06"]})
            for name in research.SOURCE_FILES:
                (root / name).write_text("original\n")
            research.save_json(root / "model.json", {"slope": 1})
            development = {"train": {"sources": {"day": {"sha256": "abc"}}},
                           "validation": {"sources": {}}}
            research.save_json(root / "development.json", development)
            research.make_freeze(root, development, root, plan)
            research.verify_freeze(root, root, plan)
            for path in [plan, root / "model.json", root / "development.json"] + [root / name for name in research.SOURCE_FILES]:
                original = path.read_text()
                path.write_text(original + " ")
                with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, "冻结校验失败"):
                    research.verify_freeze(root, root, plan)
                path.write_text(original)

    def test_failed_freeze_blocks_any_test_data_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(research, "verify_freeze", side_effect=ValueError("frozen")), \
                 patch.object(research, "load_dates") as load:
                with self.assertRaisesRegex(ValueError, "frozen"):
                    research.evaluate_test(Path(directory), Path(directory), True, {})
                load.assert_not_called()

    def test_reject_reopening_first_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research.save_json(root / "model.json", {})
            research.save_json(root / "test_access.json", {"first": "preserve"})
            with patch.object(research, "verify_freeze"), patch.object(research, "load_dates") as load:
                with self.assertRaisesRegex(ValueError, "已打开"):
                    research.evaluate_test(root, root, True, {})
                load.assert_not_called()
            self.assertEqual(research.read_json(root / "test_access.json"), {"first": "preserve"})

    def test_access_record_precedes_download_and_survives_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research.save_json(root / "model.json", {})
            research.save_json(root / "freeze.json", {})
            def download_failure(*args):
                self.assertTrue((root / "test_access.json").exists())
                raise OSError("network interrupted")
            with patch.object(research, "verify_freeze"), patch.object(research, "load_dates", side_effect=download_failure):
                with self.assertRaises(OSError):
                    research.evaluate_test(root, root, False, {"test_dates": ["2025-09-06"]})
            self.assertTrue((root / "test_access.json").exists())

    def test_reproduction_checks_original_freeze_link_before_data_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {"test_dates": ["2025-09-06"]}
            research.save_json(root / "model.json", {})
            research.save_json(root / "freeze.json", {"timestamp": "original"})
            original_hash = research.sha256_file(root / "freeze.json")
            for changed in ("freeze_metadata", "access_dates", "original_result_model"):
                research.save_json(root / "freeze.json", {"timestamp": "original"})
                research.save_json(root / "test_access.json", {"freeze_sha256": original_hash, "dates": plan["test_dates"]})
                (root / "test.json").unlink(missing_ok=True)
                if changed == "freeze_metadata":
                    research.save_json(root / "freeze.json", {"timestamp": "rewritten"})
                elif changed == "access_dates":
                    research.save_json(root / "test_access.json", {"freeze_sha256": original_hash, "dates": ["2025-09-09"]})
                else:
                    research.save_json(root / "test.json", {"freeze_sha256": original_hash, "model_sha256": "changed"})
                with self.subTest(changed=changed), patch.object(research, "verify_freeze"), patch.object(research, "load_dates") as load:
                    with self.assertRaisesRegex(ValueError, "停止读取数据"):
                        research.evaluate_test(root, root, True, plan, reproduce=True)
                    load.assert_not_called()

    def test_development_wont_overwrite_history_or_relabel_cached_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {"test_dates": ["2025-09-06"]}
            (root / "ETHUSDT-aggTrades-2025-09-06.zip").touch()
            with patch.object(research, "load_dates") as load:
                with self.assertRaisesRegex(ValueError, "测试档案已存在"):
                    research.develop(root, root, True, plan)
                load.assert_not_called()
            (root / "freeze.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "已有冻结"):
                research.develop(root, root, True, plan)

    def test_seed_means_keep_days_and_cases_separate(self):
        runs = [{"case": case, "date": day, "strategy": "flow", "seed": seed, "pnl": pnl}
                for case, day, seed, pnl in [("primary", "a", 1, 1), ("primary", "a", 2, 3),
                                            ("primary", "b", 1, 10), ("sensitivity", "a", 1, 8)]]
        results = research.summarize_runs(runs)["daily_seed_means"]
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["pnl"], 2)
        self.assertEqual(results[0]["seed_replicates"], 2)
        self.assertEqual(results[1]["pnl"], 10)

    def test_entire_staged_pipeline_and_reproduction_offline(self):
        plan = research.load_plan()
        seen = []
        def fake_day(day, raw_dir, offline):
            seen.append(day)
            rows = [{"date": day, "decision_timestamp_us": i * 5_000_000,
                     "reference_price": 100 + i * .01, "next_reference_price": 100 + (i + 1) * .01,
                     "imbalance": (i - 1) * .5, "target_return_bps": 10000 * ((100 + (i + 1) * .01) / (100 + i * .01) - 1),
                     "next_customer_side": "buy" if i % 2 else "sell", "reference_age_us": 1,
                     "next_reference_age_us": 1} for i in range(3)]
            return rows, {"missing_aggregate_ids": 0}, {"sha256": "fixture-" + day}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(research.flow_data, "load_day", side_effect=fake_day), contextlib.redirect_stdout(io.StringIO()):
                research.develop(root, root, True, plan)
                self.assertEqual(seen, plan["train_dates"] + plan["validation_dates"])
                self.assertEqual(research.read_json(root / "model.json")["n"], 9)
                research.evaluate_test(root, root, False, plan)
                self.assertEqual(seen[-3:], plan["test_dates"])
                original_test = (root / "test.json").read_bytes()
                original_access = (root / "test_access.json").read_bytes()
                research.evaluate_test(root, root, True, plan, reproduce=True)
                self.assertTrue(research.read_json(root / "reproduction_check.json")["test_metrics_identical"])
                self.assertEqual(original_test, (root / "test.json").read_bytes())
                self.assertEqual(original_access, (root / "test_access.json").read_bytes())
                self.assertEqual(len((root / "test_runs.csv").read_text().splitlines()), 136)


if __name__ == "__main__":
    unittest.main()
