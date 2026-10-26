"""Stock price-risk statistics, extracted unchanged from the archived study."""

import csv
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import NormalDist, fmean, stdev

HORIZONS = (1, 3, 10)
RULES = ("unit_sigma", "gaussian95", "empirical95")
BPS = 10000.0


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _integer(name, value, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def nearest_rank(values, probability):
    values = tuple(values)
    if not values or not 0 < probability <= 1:
        raise ValueError("nearest rank needs data and probability in (0,1]")
    if any(not math.isfinite(x) for x in values):
        raise ValueError("quantile input must be finite")
    return sorted(values)[math.ceil(probability * len(values))-1]


def price_diagnostics(returns):
    absolute = tuple(abs(value) for value in returns)
    if not absolute:
        raise ValueError("price diagnostics need returns")
    return dict(mean_abs_return_bps=fmean(absolute)*BPS,
                q95_abs_return_bps=nearest_rank(absolute,0.95)*BPS,
                q99_abs_return_bps=nearest_rank(absolute,0.99)*BPS,
                max_abs_return_bps=max(absolute)*BPS)


def calibrate_training(training_by_horizon):
    """Accept training returns only; evaluation observations are not an input."""
    if set(training_by_horizon) != set(HORIZONS):
        raise ValueError("training calibration requires exactly 1/3/10 minute keys")
    inputs = {h:tuple(training_by_horizon[h]) for h in HORIZONS}
    if any(not values or any(not math.isfinite(r) for r in values)
           for values in inputs.values()) or len(inputs[1]) < 2:
        raise ValueError("training returns must be finite and have a sample variance")
    sigma = stdev(inputs[1])
    q95_1 = nearest_rank(tuple(abs(r) for r in inputs[1]),0.95)
    bounds, diagnostics = {}, {}
    for horizon, values in inputs.items():
        q95 = nearest_rank(tuple(abs(r) for r in values),0.95)
        unit = sigma*math.sqrt(horizon)
        bounds[horizon] = dict(unit_sigma=unit,
            gaussian95=NormalDist().inv_cdf(0.975)*unit, empirical95=q95)
        denominator = q95_1*math.sqrt(horizon)
        diagnostics[horizon] = dict(sigma_1min=sigma,sigma_1min_bps=sigma*BPS,
            n_sigma_1min=len(inputs[1]), n_train=len(values), **price_diagnostics(values),
            sqrt_h_q95_scaling_ratio=q95/denominator if denominator else None,
            **{rule+"_bound_bps":bound*BPS for rule,bound in bounds[horizon].items()})
    return bounds, diagnostics


def bound_statistics(returns, bound):
    """The excess mean is unconditional; a separate field is conditional."""
    returns = tuple(returns)
    if not returns or isinstance(bound,bool) or not isinstance(bound,(int,float)) \
            or not math.isfinite(bound) or bound < 0:
        raise ValueError("bound statistics need data and a finite nonnegative bound")
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("returns must be finite")
    n = len(returns)
    upward = sum(value > bound for value in returns)
    downward = sum(value < -bound for value in returns)
    exceedances = upward+downward
    excess = math.fsum(max(abs(value)-bound,0.0) for value in returns)*BPS
    return dict(bound_bps=bound*BPS,n=n,covered=n-exceedances,
        coverage=(n-exceedances)/n,exceedances=exceedances,
        exceedance_rate=exceedances/n,upward_exceedances=upward,
        downward_exceedances=downward,upward_exceedance_rate=upward/n,
        downward_exceedance_rate=downward/n,excess_sum_bps=excess,
        mean_excess_bps=excess/n,
        mean_excess_given_exceedance_bps=excess/exceedances if exceedances else None)


def moving_block_indices(days, *, block_days=7, resamples=2000, seed=100628):
    """Shared noncircular calendar-day draws, concatenated then truncated."""
    for name,value in (("days",days),("block_days",block_days),("resamples",resamples)):
        _integer(name,value)
    _integer("seed",seed,0)
    if block_days > days:
        raise ValueError("bootstrap block exceeds the number of evaluation days")
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        indices = []
        for _ in range(math.ceil(days/block_days)):
            start = rng.randrange(days-block_days+1)
            indices.extend(range(start,start+block_days))
        draws.append(tuple(indices[:days]))
    return tuple(draws)


def linear_percentile(values, probability):
    values = sorted(values)
    if not values or not 0 <= probability <= 1:
        raise ValueError("percentile needs data and p in [0,1]")
    index=(len(values)-1)*probability
    low,high=math.floor(index),math.ceil(index)
    return values[low]+(values[high]-values[low])*(index-low)


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing empty output table")
    keys=list(rows[0])
    if any(set(row)!=set(keys) for row in rows):
        raise ValueError("output table schema is inconsistent")
    with Path(path).open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=keys,lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
