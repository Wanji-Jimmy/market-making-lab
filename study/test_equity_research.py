"""Synthetic fixtures for stock windows, frozen bounds and BBO endpoints."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import equity_research as research


def fixture(sessions=2,evaluation_multiplier=1.0,invalid_index=None):
    dates=research.load_protocol()["session_dates"]
    days=[]
    for asset_index,asset in enumerate(research.ASSETS):
        for phase,selected in (("train",dates[:20][:sessions]),("evaluation",dates[20:][:sessions])):
            for d,day in enumerate(selected):
                scale=evaluation_multiplier if phase=="evaluation" else 1.0
                mids=tuple((100+asset_index*50)*math.exp(0.0001*(1+d*0.1)*scale*(i%11)) for i in range(390))
                valid=tuple(i!=invalid_index for i in range(390))
                bids=tuple(mid-0.01 if valid[i] else None for i,mid in enumerate(mids))
                asks=tuple(mid+0.01 if valid[i] else None for i,mid in enumerate(mids))
                days.append(dict(asset=asset,date=day,bids=bids,asks=asks,
                    mids=tuple(mid if valid[i] else None for i,mid in enumerate(mids)),
                    spreads=tuple(asks[i]-bids[i] if valid[i] else None for i in range(390)),
                    valid_mask=valid,structurally_valid=True))
    return dict(days=days,audit=dict(fixture=True),sources=[],source_manifest="fixture:no-files",manifest_sha256=None)


class WindowTests(unittest.TestCase):
    def test_hand_logmid_returns_and_complete_twenty_session_counts(self):
        actual=research.admissible_windows((100,110,121),(True,True,True),1)
        self.assertEqual([origin for origin,_ in actual],[0,1])
        self.assertTrue(all(math.isclose(value,math.log(1.1)) for _,value in actual))
        for horizon in research.HORIZONS:
            per_session=research.admissible_windows((100,)*390,(True,)*390,horizon)
            self.assertEqual(len(per_session),390-horizon)
            self.assertEqual(20*len(per_session),20*(390-horizon))

    def test_all_intermediate_minutes_required_no_gap_bridging(self):
        self.assertEqual(research.admissible_windows((100,None,121),(True,False,True),2),())
        valid=[True]*390
        valid[100]=False
        for horizon in research.HORIZONS:
            windows=research.admissible_windows([100]*390,valid,horizon)
            self.assertEqual(len(windows),390-horizon-(horizon+1))
            self.assertTrue(all(not origin<=100<=origin+horizon for origin,_ in windows))

    def test_no_overnight_return(self):
        one=research.admissible_windows((100,)*390,(True,)*390,10)
        two=research.admissible_windows((1000,)*390,(True,)*390,10)
        self.assertEqual(len(one)+len(two),760)
        self.assertTrue(all(value==0 for _,value in one+two))

    def test_locked_quotes_retained_and_bad_masks_rejected(self):
        day=dict(mids=(100,100,100),bids=(100,100,100),asks=(100,100,100),valid_mask=(True,True,True))
        result=research.endpoint_statistics(day,1)
        self.assertEqual(result["n"],2)
        self.assertEqual(result["origin_halfspread_sum_bps"],0)
        self.assertEqual(result["old_bid_above_future_mid_count"],0)
        self.assertEqual(result["old_ask_below_future_mid_count"],0)
        self.assertEqual(result["old_bid_touch_or_cross_future_ask_count"],2)
        self.assertEqual(result["old_ask_touch_or_cross_future_bid_count"],2)
        for mids,mask in (((100,100),(True,1)),((100,None),(True,True)),((100,100),(True,))):
            with self.assertRaises(ValueError):
                research.admissible_windows(mids,mask,1)


class EndpointAndBootstrapTests(unittest.TestCase):
    def test_hand_endpoint_strict_vs_touch_and_halfspread_denominator(self):
        day=dict(mids=(100,102,100),bids=(99,101,100),asks=(101,103,100),valid_mask=(True,True,True))
        result=research.endpoint_statistics(day,1)
        self.assertEqual(result["n"],2)
        self.assertTrue(all(result[name+"_count"]==1 for name in research.ENDPOINT_NAMES))
        self.assertAlmostEqual(result["origin_halfspread_sum_bps"],100+10000/102)

    def test_session_bootstrap_shared_repeatable_blocks(self):
        draws=research.moving_block_indices(20,block_days=5,resamples=12,seed=100629)
        self.assertEqual(draws,research.moving_block_indices(20,block_days=5,resamples=12,seed=100629))
        for draw in draws:
            self.assertEqual(len(draw),20)
            for offset in range(0,20,5):
                block=draw[offset:offset+5]
                self.assertTrue(0<=block[0]<=15)
                self.assertEqual(block,tuple(range(block[0],block[0]+5)))
        rows=[dict(n=10,covered=5)]*20
        self.assertEqual(research.stock_coverage_interval(rows,draws),(0.5,0.5,12))

    def test_zero_observation_sessions_retained_and_undefined_draws_counted(self):
        rows=(dict(n=0,covered=0),dict(n=2,covered=1))
        result=research.stock_coverage_interval(rows,((0,0),(0,1),(1,1)))
        self.assertEqual(result,(0.5,0.5,2))
        self.assertEqual(research.stock_coverage_interval((rows[0],),((0,),)),(None,None,0))
        stats=research.safe_statistics((),0.01,389)
        self.assertEqual(stats["n"],0)
        self.assertEqual(stats["skipped_invalid_windows"],389)
        self.assertIsNone(stats["mean_excess_bps"])
        self.assertIsNone(stats["coverage"])


class RunnerTests(unittest.TestCase):
    def run_fixture(self,output,**kwargs):
        arguments=dict(data=fixture(),sessions_per_phase=2,bootstrap_block_sessions=1,bootstrap_resamples=12)
        arguments.update(kwargs)
        return research.run_equity_research(output,**arguments)

    def read(self,path):
        with path.open() as stream:
            return list(csv.DictReader(stream))

    def test_smoke_schema_segment_partition_and_candidate_denominators(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"smoke"
            manifest=self.run_fixture(output)
            self.assertEqual(manifest["run_classification"],"smoke")
            self.assertEqual(manifest["validation"]["daily_rows"],72)
            self.assertEqual(manifest["validation"]["summary_rows"],18)
            self.assertEqual(manifest["validation"]["session_segment_rows"],54)
            self.assertEqual(manifest["validation"]["endpoint_quote_rows"],6)
            summary=self.read(output/"summary.csv")
            segments=self.read(output/"session_segments.csv")
            endpoint=self.read(output/"endpoint_quotes.csv")
            for row in summary:
                horizon=int(row["horizon_minutes"])
                self.assertEqual(int(row["n"]),2*(390-horizon))
                matching=[s for s in segments if (s["asset"],s["horizon_minutes"],s["rule"])==(row["asset"],row["horizon_minutes"],row["rule"])]
                self.assertEqual(sum(int(s["n"]) for s in matching),int(row["n"]))
                by_name={s["segment"]:s for s in matching}
                self.assertEqual(int(by_name["first_30"]["candidate_windows"]),60)
                self.assertEqual(int(by_name["core_330"]["candidate_windows"]),660)
                self.assertEqual(int(by_name["last_30"]["candidate_windows"]),2*(30-horizon))
                self.assertTrue(all("coverage_ci_low" not in s for s in matching))
            for row in endpoint:
                self.assertEqual(int(row["n"]),int(row["admissible_n"]))
                for name in research.ENDPOINT_NAMES:
                    self.assertAlmostEqual(float(row[name+"_fraction"]),int(row[name+"_count"])/int(row["n"]))
            for filename,record in manifest["output_files"].items():
                self.assertEqual(research.sha256(output/filename),record["sha256"])

    def test_invalid_quote_windows_are_skipped_and_reported_not_interpolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"invalid-quotes"
            self.run_fixture(output,data=fixture(invalid_index=100))
            for row in self.read(output/"summary.csv"):
                h=int(row["horizon_minutes"])
                self.assertEqual(int(row["skipped_invalid_windows"]),2*(h+1))
                self.assertEqual(int(row["n"]),2*(390-h-h-1))

    def test_evaluation_price_perturbation_leaves_calibration_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            left,right=Path(temporary)/"left",Path(temporary)/"right"
            before=self.run_fixture(left)
            after=self.run_fixture(right,data=fixture(evaluation_multiplier=20))
            self.assertEqual((left/"calibration.csv").read_bytes(),(right/"calibration.csv").read_bytes())
            self.assertEqual(before["calibration_sha256"],after["calibration_sha256"])
            self.assertNotEqual((left/"summary.csv").read_bytes(),(right/"summary.csv").read_bytes())

    def test_actual_loader_metadata_contract_accepts_coverage_and_quality_pairs(self):
        payload=fixture()
        payload.update(source_coverage="fixture:coverage",coverage_sha256=None,
                       source_quality="fixture:quality",quality_sha256=None)
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"contract"
            self.run_fixture(output,data=payload)
            import json
            audit=json.loads((output/"data_audit.json").read_text())
            self.assertEqual(audit["source_quality"],"fixture:quality")
            self.assertEqual(audit["source_coverage"],"fixture:coverage")

    def test_raw_quality_metadata_is_hashed_and_cannot_change_silently(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths={name:Path(temporary)/(name+".json") for name in ("manifest","coverage","quality")}
            for name,path in paths.items():
                path.write_text(name)
            payload=dict(sources=[])
            for path_key,hash_key,name in (("source_manifest","manifest_sha256","manifest"),
                    ("source_coverage","coverage_sha256","coverage"),("source_quality","quality_sha256","quality")):
                payload[path_key]=str(paths[name]);payload[hash_key]=research.sha256(paths[name])
            self.assertEqual(len(research.provenance_hashes(payload)),3)
            paths["quality"].write_text("changed")
            with self.assertRaises(RuntimeError):
                research.provenance_hashes(payload)

    def test_missing_duplicate_structural_and_crossed_valid_sessions_rejected(self):
        protocol=research.load_protocol()
        days=fixture()["days"]
        crossed=copy.deepcopy(days)
        bids=list(crossed[0]["bids"]);bids[0]=100000
        crossed[0]["bids"]=tuple(bids)
        for changed in (days[:-1],days+[days[0]],
                        [dict(days[0],structurally_valid=False)]+days[1:],
                        [dict(days[0],date="2021-01-18")]+days[1:],crossed):
            with self.assertRaises(ValueError):
                research.validate_sessions(changed,protocol,2)
        with self.assertRaises(ValueError):
            research.validate_sessions(days,protocol,20)

    def test_nonempty_and_changed_source_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"existing";output.mkdir();(output/"keep").write_text("keep")
            with self.assertRaises(FileExistsError):
                self.run_fixture(output)
            self.assertEqual((output/"keep").read_text(),"keep")
            rejected=Path(temporary)/"rejected"
            with patch.object(research,"source_hashes",side_effect=[{"before":"a"},{"after":"b"}]):
                with self.assertRaises(RuntimeError):
                    self.run_fixture(rejected)
            self.assertFalse(rejected.exists())

    def test_cli_separate_input_paths_preserve_tables_and_check_provenance(self):
        from test_equity_data import fixture_sources
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            sources=root/"raw";sources.mkdir()
            fixture_sources(sources)
            settings=dict(sessions_per_phase=2,bootstrap_block_sessions=1,bootstrap_resamples=12)
            baseline=root/"baseline"
            research.run_equity_research(baseline,source_dir=sources,**settings)
            metadata=root/"metadata";metadata.mkdir()
            acquisition=metadata/"acquisition.json"
            coverage=metadata/"coverage.json"
            (sources/"acquisition_manifest.json").rename(acquisition)
            (sources/"equity_results.json").rename(coverage)
            quality=metadata/"quality.json"
            quality.write_text(json.dumps(dict(source="fixture:portable-source")))
            before={str(p):research.sha256(p) for folder in (sources,metadata) for p in folder.iterdir()}
            output=root/"relocated"
            arguments=["--source-dir",str(sources),"--acquisition-manifest",str(acquisition),
                "--coverage-manifest",str(coverage),"--quality-report",str(quality),
                "--output-dir",str(output),"--sessions-per-phase","2", "--bootstrap-block-sessions","1",
                "--bootstrap-resamples","12"]
            with redirect_stdout(io.StringIO()):
                research.main(arguments)
            for filename in ("daily.csv","summary.csv","calibration.csv","session_segments.csv","endpoint_quotes.csv"):
                self.assertEqual((baseline/filename).read_bytes(),(output/filename).read_bytes())
            audit=json.loads((output/"data_audit.json").read_text())
            self.assertEqual(audit["source_manifest"],str(acquisition))
            self.assertEqual(audit["source_coverage"],str(coverage))
            self.assertEqual(audit["source_quality"],str(quality))
            self.assertEqual(audit["quality_sha256"],research.sha256(quality))
            self.assertEqual(len(audit["raw_dependency_sha256"]),83)
            self.assertEqual(before,{str(p):research.sha256(p) for folder in (sources,metadata) for p in folder.iterdir()})
            manifest=json.loads((output/"manifest.json").read_text())
            self.assertEqual(set(manifest["overrides"]),set(settings))
            raw=sources/"EBAY_20210104.csv"
            original=raw.read_bytes()
            raw.write_bytes(original+b"\n")
            rejected=root/"bad-checksum"
            with self.assertRaisesRegex(ValueError,"CSV checksum mismatch"):
                research.run_equity_research(rejected,source_dir=sources,manifest_path=acquisition,
                    coverage_path=coverage,quality_path=quality,**settings)
            self.assertFalse(rejected.exists())
            raw.unlink()
            with self.assertRaisesRegex(FileNotFoundError,"required stock CSV"):
                research.run_equity_research(rejected,source_dir=sources,manifest_path=acquisition,
                    coverage_path=coverage,quality_path=quality,**settings)
            self.assertFalse(rejected.exists())

    def test_missing_cli_input_leaves_output_absent_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            sources=root/"raw"
            output=root/"result"
            arguments=["--source-dir",str(sources),"--output-dir",str(output)]
            for expected in ("--source-dir","--acquisition-manifest","--coverage-manifest"):
                error=io.StringIO()
                with redirect_stderr(error),self.assertRaises(SystemExit) as raised:
                    research.main(arguments)
                self.assertEqual(raised.exception.code,1)
                self.assertIn(expected,error.getvalue())
                self.assertFalse(output.exists())
                if expected=="--source-dir":
                    sources.mkdir()
                elif expected=="--acquisition-manifest":
                    (sources/"acquisition_manifest.json").write_text('{"files": []}')

    def test_fixture_cannot_silently_ignore_explicit_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"result"
            for name in ("source_dir","manifest_path","coverage_path","quality_path"):
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,"cannot be combined"):
                    self.run_fixture(output,**{name:Path(temporary)})
                self.assertFalse(output.exists())


if __name__=="__main__":
    unittest.main()
