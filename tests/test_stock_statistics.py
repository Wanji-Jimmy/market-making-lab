"""Hand-computable calibration, coverage and bootstrap checks."""

import math
from statistics import NormalDist, stdev
import unittest

import stock_statistics as research
from equity_research import stock_coverage_interval


class BoundTests(unittest.TestCase):
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

    def test_invalid_bounds_and_calibration_rejected(self):
        for bound in (-1, float("inf"), True):
            with self.assertRaises(ValueError):
                research.bound_statistics((0, 1), bound)
        with self.assertRaises(ValueError):
            research.calibrate_training({1: (0, 1), 3: (0, 1)})


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

    def test_coverage_resamples_counts_and_interpolates_percentiles(self):
        daily = ({"n": 10, "covered": 10}, {"n": 30, "covered": 0})
        low, high, valid = stock_coverage_interval(daily, ((0, 1), (0, 0), (1, 1)))
        self.assertAlmostEqual(low, 0.0125)
        self.assertAlmostEqual(high, 0.9625)
        self.assertEqual(valid, 3)
        self.assertEqual(research.linear_percentile((0, 10), 0.25), 2.5)

    def test_invalid_bootstrap_and_counts_rejected(self):
        for kwargs in (dict(days=3, block_days=7), dict(days=0), dict(days=True), dict(days=7, resamples=0)):
            with self.assertRaises(ValueError):
                research.moving_block_indices(**kwargs)
        with self.assertRaises(ValueError):
            stock_coverage_interval((dict(n=2, covered=3),), ((0,),))
        with self.assertRaises(ValueError):
            stock_coverage_interval((dict(n=2, covered=1),), ((1,),))


if __name__ == "__main__":
    unittest.main()
