"""Hand-computable price-risk, train-only and daily-bootstrap invariants."""

import copy
import csv
from datetime import date, timedelta
import json
import math
from pathlib import Path
from statistics import NormalDist, stdev
import tempfile
import unittest
from unittest.mock import patch

import empirical_research as research


def fixture(days=2, evaluation_multiplier=1.0):
    rows=[]
    for asset_index,asset in enumerate(research.ASSETS):
        for start,phase in ((date(2026,6,1),"train"),(date(2026,8,1),"evaluation")):
            for d in range(days):
                # Alternate deterministic minute returns; day prices reset.
                rate=0.0001*(1+asset_index)*(1+d*0.1)
                multiplier=evaluation_multiplier if phase=="evaluation" else 1.0
                closes=tuple((100+asset_index*50)*math.exp(rate*multiplier*(i%11))
                             for i in range(1440))
                rows.append(dict(asset=asset,date=(start+timedelta(days=d)).isoformat(),closes=closes))
    return dict(days=rows,audit=dict(fixture=True),sources=[],manifest_sha256=None,
                source_manifest="fixture:no-archive")


class ReturnAndBoundTests(unittest.TestCase):
    def test_hand_computable_log_returns_and_counts(self):
        closes=(100,110,121)
        self.assertEqual(len(research.within_day_returns(closes,1)),2)
        for value in research.within_day_returns(closes,1):
            self.assertAlmostEqual(value,math.log(1.1))
        self.assertAlmostEqual(research.within_day_returns(closes,2)[0],math.log(1.21))
        for horizon in research.HORIZONS:
            self.assertEqual(len(research.within_day_returns([100]*1440,horizon)),1440-horizon)

    def test_days_are_never_concatenated_across_midnight(self):
        first=research.within_day_returns((100,100,100),1)
        second=research.within_day_returns((1000,1000,1000),1)
        self.assertEqual(first+second,(0,0,0,0))
        self.assertNotEqual(first+second,research.within_day_returns((100,100,100,1000,1000,1000),1))

    def test_nearest_rank_is_not_interpolated(self):
        self.assertEqual(research.nearest_rank(range(1,21),0.95),19)
        self.assertEqual(research.nearest_rank((1,2,3),0.95),3)
        self.assertEqual(research.nearest_rank((0,0,1),0.5),0)

    def test_thresholds_use_sample_sigma_and_fixed_horizon_rules(self):
        training={1:(-0.01,0,0.01),3:(-0.03,0,0.02),10:(-0.04,0.02,0.05)}
        bounds,diagnostics=research.calibrate_training(training)
        sigma=stdev(training[1])
        self.assertAlmostEqual(sigma,0.01)
        for h in research.HORIZONS:
            self.assertAlmostEqual(bounds[h]["unit_sigma"],sigma*math.sqrt(h))
            self.assertAlmostEqual(bounds[h]["gaussian95"],NormalDist().inv_cdf(0.975)*sigma*math.sqrt(h))
            self.assertEqual(bounds[h]["empirical95"],max(abs(x) for x in training[h]))
        self.assertAlmostEqual(diagnostics[3]["sqrt_h_q95_scaling_ratio"],0.03/(0.01*math.sqrt(3)))

    def test_coverage_equality_sides_and_excess_denominators(self):
        result=research.bound_statistics((-0.2,-0.1,0,0.1,0.3),0.1)
        self.assertEqual((result["n"],result["covered"],result["exceedances"]),(5,3,2))
        self.assertEqual((result["upward_exceedances"],result["downward_exceedances"]),(1,1))
        self.assertAlmostEqual(result["coverage"],0.6)
        self.assertAlmostEqual(result["excess_sum_bps"],3000)
        self.assertAlmostEqual(result["mean_excess_bps"],600)
        self.assertAlmostEqual(result["mean_excess_given_exceedance_bps"],1500)
        self.assertEqual(result["upward_exceedance_rate"],0.2)
        no_excess=research.bound_statistics((-0.1,0,0.1),0.1)
        self.assertEqual(no_excess["mean_excess_bps"],0)
        self.assertIsNone(no_excess["mean_excess_given_exceedance_bps"])

    def test_zero_volatility_and_zero_quantile_scaling_are_explicit(self):
        bounds,stats=research.calibrate_training({h:(0,0,0) for h in research.HORIZONS})
        self.assertTrue(all(value==0 for values in bounds.values() for value in values.values()))
        self.assertTrue(all(stats[h]["sqrt_h_q95_scaling_ratio"] is None for h in stats))

    def test_bad_price_horizon_threshold_inputs_rejected(self):
        for closes in ((1,0,2),(1,float("inf"),2),(1,True,2)):
            with self.subTest(closes=closes),self.assertRaises(ValueError):
                research.within_day_returns(closes,1)
        for horizon in (0,True,1.5,4):
            with self.subTest(horizon=horizon),self.assertRaises(ValueError):
                research.within_day_returns((1,2,3),horizon)
        for bound in (-1,float("inf"),True):
            with self.assertRaises(ValueError):
                research.bound_statistics((0,1),bound)
        with self.assertRaises(ValueError):
            research.calibrate_training({1:(0,1),3:(0,1)})


class BootstrapTests(unittest.TestCase):
    def test_noncircular_complete_blocks_and_truncated_final_block(self):
        draws=research.moving_block_indices(61,block_days=7,resamples=20,seed=100628)
        self.assertEqual(draws,research.moving_block_indices(61,block_days=7,resamples=20,seed=100628))
        self.assertNotEqual(draws,research.moving_block_indices(61,block_days=7,resamples=20,seed=100629))
        for draw in draws:
            self.assertEqual(len(draw),61)
            for start in range(0,61,7):
                block=draw[start:start+7]
                self.assertTrue(0<=block[0]<=54)
                self.assertEqual(block,tuple(range(block[0],block[0]+len(block))))
                self.assertLessEqual(block[-1],60)

    def test_coverage_resamples_counts_as_ratio_and_type7_percentiles(self):
        daily=({"n":10,"covered":10},{"n":30,"covered":0})
        low,high=research.coverage_interval(daily,((0,1),(0,0),(1,1)))
        self.assertAlmostEqual(low,0.0125)
        self.assertAlmostEqual(high,0.9625)
        self.assertEqual(research.linear_percentile((0,10),0.25),2.5)

    def test_invalid_bootstrap_and_denominators_rejected(self):
        for kwargs in (dict(days=3,block_days=7),dict(days=0),dict(days=True),dict(days=7,resamples=0)):
            with self.assertRaises(ValueError):
                research.moving_block_indices(**kwargs)
        with self.assertRaises(ValueError):
            research.coverage_interval((dict(n=0,covered=0),),((0,),))
        with self.assertRaises(ValueError):
            research.coverage_interval((dict(n=2,covered=3),),((0,),))
        with self.assertRaises(ValueError):
            research.coverage_interval((dict(n=2,covered=1),),((1,),))


class RunnerFixtureTests(unittest.TestCase):
    def run_fixture(self,output,**kwargs):
        arguments=dict(data=fixture(),days_per_phase=2,bootstrap_block_days=1,bootstrap_resamples=12)
        arguments.update(kwargs)
        return research.run_empirical_research(output,**arguments)

    def test_fixture_outputs_smoke_calendar_schema_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"smoke"
            manifest=self.run_fixture(output)
            self.assertEqual(manifest["run_classification"],"smoke")
            self.assertIn("data_override",manifest["overrides"])
            self.assertEqual(manifest["validation"]["selected_asset_days"],8)
            self.assertEqual(manifest["validation"]["daily_rows"],72)
            self.assertEqual(manifest["validation"]["summary_rows"],18)
            self.assertEqual(manifest["validation"]["calibration_rows"],6)
            self.assertEqual(len(manifest["output_files"]),5)
            for filename,record in manifest["output_files"].items():
                self.assertEqual(research.sha256(output/filename),record["sha256"])
            with (output/"daily.csv").open() as stream:
                daily=list(csv.DictReader(stream))
            self.assertEqual({row["phase"] for row in daily},{"train","evaluation"})
            key=lambda row:(row["asset"],row["date"],row["horizon_minutes"],row["rule"])
            self.assertEqual(len({key(row) for row in daily}),72)
            for row in daily:
                self.assertEqual(int(row["n"]),1440-int(row["horizon_minutes"]))
                self.assertEqual(int(row["exceedances"]),int(row["upward_exceedances"])+int(row["downward_exceedances"]))

    def test_future_evaluation_perturbation_does_not_change_training_thresholds(self):
        with tempfile.TemporaryDirectory() as temporary:
            left,right=Path(temporary)/"left",Path(temporary)/"right"
            before=self.run_fixture(left,data=fixture(evaluation_multiplier=1))
            after=self.run_fixture(right,data=fixture(evaluation_multiplier=20))
            self.assertEqual((left/"calibration.csv").read_bytes(),(right/"calibration.csv").read_bytes())
            self.assertEqual(before["calibration_sha256"],after["calibration_sha256"])
            self.assertNotEqual((left/"summary.csv").read_bytes(),(right/"summary.csv").read_bytes())

    def test_missing_duplicate_unexpected_keys_and_incomplete_days_rejected(self):
        protocol=research.load_protocol()
        data=fixture()["days"]
        for changed in (data[:-1],data+[data[0]],
                [dict(data[0],date="2026-05-31")]+data[1:],
                [dict(data[0],asset="OTHER")]+data[1:],
                [dict(data[0],closes=(100,)*1439)]+data[1:]):
            with self.assertRaises(ValueError):
                research.validate_days(changed,protocol,2)
        with self.assertRaises(ValueError):
            research.validate_days(data,protocol,61)

    def test_nonempty_outputs_refused_before_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)
            (output/"keep.txt").write_text("original")
            with patch("empirical_data.load_existing_minutes",side_effect=AssertionError("loader called")):
                with self.assertRaises(FileExistsError):
                    research.run_empirical_research(output)
            self.assertEqual((output/"keep.txt").read_text(),"original")

    def test_source_change_rejected_without_writing_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"rejected"
            with patch.object(research,"source_hashes",side_effect=[{"before":"a"},{"after":"b"}]):
                with self.assertRaises(RuntimeError):
                    self.run_fixture(output)
            self.assertFalse(output.exists())

    def test_planned_split_and_default_protocol_stay_fixed(self):
        protocol=research.load_protocol()
        self.assertEqual(len(research.date_range("2026-06-01","2026-07-31")),61)
        self.assertEqual(len(research.date_range("2026-08-01","2026-09-30")),61)
        self.assertLess(protocol["split"]["training_end"],protocol["split"]["evaluation_start"])
        original=copy.deepcopy(protocol)
        original["split"]["training_end"]="2026-08-01"
        with tempfile.TemporaryDirectory() as temporary:
            changed_protocol=Path(temporary)/"changed.json"
            changed_protocol.write_text(json.dumps(original))
            with patch.object(research,"PROTOCOL_PATH",changed_protocol):
                with self.assertRaises(ValueError):
                    research.load_protocol()


if __name__=="__main__":
    unittest.main()
