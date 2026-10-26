"""Post-trade markouts and inference over independent simulated price paths.

Markout horizons count event rows, not clock time. None denotes an undefined
mean/interval; a fill without a future observation is censored, never a zero.
Bootstrap intervals below are 95% percentile intervals over whole paths.
"""

from collections.abc import Mapping
import math
import random
import statistics


def _integer(name, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _finite(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _sum(values):
    try:
        return _finite("aggregate", math.fsum(values))
    except OverflowError as error:
        raise ValueError("aggregate must be finite") from error


def _field(row, key, index):
    try:
        return row[key]
    except KeyError as error:
        raise ValueError(f"row {index} is missing {key}") from error


def _percentile(sorted_values, fraction):
    """Linearly interpolate between ordered bootstrap statistics."""
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return (1 - weight) * sorted_values[lower] + weight * sorted_values[upper]


def _interval(values):
    ordered = sorted(values)
    return _percentile(ordered, 0.025), _percentile(ordered, 0.975)


def fill_markouts(trace, horizons=(1, 5, 20)):
    """Return flat, fill-weighted markout aggregates for one recorded path.

    For a maker fill at row i, horizon h uses row i+h-1's next_mid. Maker
    quantity +1 is a buy and -1 is a sell. The signed mid move is
    quantity * (future_mid - current_mid); execution markout additionally
    contains the entry edge and excludes fees and later liquidation costs.
    Thus mid_move + edge = execution_markout. These are post-hoc diagnostics,
    never inputs to quoting decisions.

    Each h-prefixed group contains eligible count, censored fill count,
    mid_move/execution_markout/edge sums and means, plus informed/uninformed
    eligible counts and mid-move sums/means. A censored fill contributes to
    none of these sums/counts except censored. All undefined means are None.
    """
    try:
        horizons = tuple(horizons)
    except TypeError as error:
        raise ValueError("horizons must contain positive unique integers") from error
    if not horizons:
        raise ValueError("horizons must not be empty")
    for horizon in horizons:
        _integer("horizon", horizon, 1)
    if len(set(horizons)) != len(horizons):
        raise ValueError("horizons must be unique")

    rows = list(trace)
    fills = []
    next_mids = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"row {index} must be a mapping")
        mid = _finite(f"row {index} mid_price", _field(row, "mid_price", index))
        next_mids.append(_finite(
            f"row {index} next_mid", _field(row, "next_mid", index)))
        filled = _field(row, "filled", index)
        if not isinstance(filled, bool):
            raise ValueError(f"row {index} filled must be a boolean")
        if not filled:
            continue
        quantity = _finite(f"row {index} quantity", _field(row, "quantity", index))
        if quantity not in (-1, 1):
            raise ValueError(f"row {index} filled quantity must be +1 or -1")
        execution = _finite(
            f"row {index} execution_price", _field(row, "execution_price", index))
        edge = _finite(f"row {index} edge", _field(row, "edge", index))
        expected_edge = _finite("calculated edge", quantity * (mid - execution))
        if not math.isclose(edge, expected_edge, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"row {index} edge does not match quantity and prices")
        informed = _field(row, "informed", index)
        if not isinstance(informed, bool):
            raise ValueError(f"row {index} informed must be a boolean")
        fills.append((index, mid, quantity, execution, edge, informed))

    result = {}
    for horizon in horizons:
        moves, executions, edges = [], [], []
        informed_moves, uninformed_moves = [], []
        censored = 0
        for index, mid, quantity, execution, edge, informed in fills:
            future_index = index + horizon - 1
            if future_index >= len(rows):
                censored += 1
                continue
            future_mid = next_mids[future_index]
            move = _finite("mid move", quantity * (future_mid - mid))
            markout = _finite("execution markout", quantity * (future_mid - execution))
            if not math.isclose(move + edge, markout, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("mid move + edge does not equal execution markout")
            moves.append(move)
            executions.append(markout)
            edges.append(edge)
            (informed_moves if informed else uninformed_moves).append(move)
        prefix = f"h{horizon}_"
        count = len(moves)
        result[prefix + "count"] = count
        result[prefix + "censored"] = censored
        for name, values in (("mid_move", moves), ("execution_markout", executions),
                             ("edge", edges)):
            total = _sum(values)
            result[prefix + name + "_sum"] = total
            result[prefix + name + "_mean"] = total / count if count else None
        for name, values in (("informed", informed_moves),
                             ("uninformed", uninformed_moves)):
            total = _sum(values)
            result[prefix + name + "_count"] = len(values)
            result[prefix + name + "_mid_move_sum"] = total
            result[prefix + name + "_mid_move_mean"] = (
                total / len(values) if values else None)
    return result


def paired_difference(left, right, *, resamples=2000, seed=1):
    """Summarise left-minus-right differences for matching independent seeds.

    Paths, not fills/timepoints, are the independent observations. The standard
    error is sample SD / sqrt(path count); intervals resample complete paired
    differences. probability_positive is the *observed path fraction* whose
    difference is strictly positive. It is descriptive, not a p-value, a
    bootstrap probability, or the probability a strategy is truly superior.
    With one paired path, SE and interval endpoints are undefined (None).
    """
    _integer("resamples", resamples, 1)
    _integer("seed", seed)
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        raise ValueError("left and right must be seed-to-value mappings")
    for mapping in (left, right):
        for path_seed in mapping:
            _integer("path seed", path_seed)
    if not left or set(left) != set(right):
        raise ValueError("left and right must have equal nonempty seed sets")
    differences = []
    for path_seed in sorted(left):
        lhs = _finite(f"left[{path_seed}]", left[path_seed])
        rhs = _finite(f"right[{path_seed}]", right[path_seed])
        differences.append(_finite("paired difference", lhs - rhs))
    count = len(differences)
    mean = _sum(differences) / count
    result = dict(count=count, mean=mean, standard_error=None,
                  ci_low=None, ci_high=None,
                  probability_positive=sum(value > 0 for value in differences) / count)
    if count == 1:
        return result
    result["standard_error"] = _finite(
        "standard error", statistics.stdev(differences) / math.sqrt(count))
    if all(value == differences[0] for value in differences):
        result["ci_low"] = result["ci_high"] = differences[0]
        return result
    rng = random.Random(seed)
    means = [_sum(differences[rng.randrange(count)] for _ in range(count)) / count
             for _ in range(resamples)]
    result["ci_low"], result["ci_high"] = _interval(means)
    return result


def pooled_markout_interval(path_rows, horizon, *, resamples=1000, seed=1):
    """Pool signed mid moves and bootstrap the ratio over whole path rows.

    Each row must contain h{horizon}_count and h{horizon}_mid_move_sum, as
    returned by fill_markouts. All paths remain in the resampling population,
    including zero-fill paths. Draws with no eligible fills have no defined
    ratio and are excluded from the percentile interval; valid_resamples
    reports the number retained. With no eligible fills, mean and interval
    endpoints are None. A one-path bootstrap is necessarily degenerate.
    """
    _integer("horizon", horizon, 1)
    _integer("resamples", resamples, 1)
    _integer("seed", seed)
    count_key = f"h{horizon}_count"
    sum_key = f"h{horizon}_mid_move_sum"
    aggregates = []
    for index, row in enumerate(path_rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"row {index} must be a mapping")
        count = _integer(count_key, _field(row, count_key, index), 0)
        total = _finite(sum_key, _field(row, sum_key, index))
        if count == 0 and total != 0:
            raise ValueError(f"row {index} has a nonzero sum but no fills")
        aggregates.append((count, total))
    count_paths = len(aggregates)
    total_fills = sum(count for count, _ in aggregates)
    total_move = _sum(total for _, total in aggregates)
    result = dict(count_paths=count_paths, total_fills=total_fills,
                  mean=total_move / total_fills if total_fills else None,
                  ci_low=None, ci_high=None, valid_resamples=0)
    if not total_fills:
        return result
    rng = random.Random(seed)
    ratios = []
    for _ in range(resamples):
        indices = [rng.randrange(count_paths) for _ in range(count_paths)]
        fills = sum(aggregates[index][0] for index in indices)
        if fills:
            ratios.append(_sum(aggregates[index][1] for index in indices) / fills)
    result["valid_resamples"] = len(ratios)
    if ratios:
        result["ci_low"], result["ci_high"] = _interval(ratios)
    return result
