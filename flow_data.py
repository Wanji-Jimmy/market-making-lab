"""固定五秒时钟：仅用过去成交流量构造特征，线性预测下一格成交价变化。

本模块不选择训练日期、不调整超参数、不运行交易策略。调用方负责冻结划分：
train=2025-09-01..03，validation=04..05，test=06..08。曾看过的训练日不是未见测试。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import real_data


BUCKET_US = 5_000_000
BUCKETS_PER_DAY = 17_280
TRAIN_DATES = ("2025-09-01", "2025-09-02", "2025-09-03")
VALIDATION_DATES = ("2025-09-04", "2025-09-05")
TEST_DATES = ("2025-09-06", "2025-09-07", "2025-09-08")
PREDICTOR_NAMES = ("zero", "train_mean", "model_raw", "model_clipped")


def age_summary(values: list[int]) -> dict:
    if not values:
        return {"n": 0, "min_us": None, "mean_us": None, "p50_us": None, "p95_us": None, "max_us": None}
    ordered = sorted(values)
    return {"n": len(values), "min_us": ordered[0], "mean_us": math.fsum(values) / len(values),
        "p50_us": ordered[math.ceil(.50 * len(values)) - 1],
        "p95_us": ordered[math.ceil(.95 * len(values)) - 1], "max_us": ordered[-1]}


def build_day_rows(trades: Iterable[real_data.Trade], day: str) -> tuple[list[dict], dict]:
    """在 t 用 [t−5s,t) 特征解释 [t,t+5s) 的末价；不跨空格、不跨日。"""
    start, end = real_data.day_bounds(day)
    buckets: dict[int, dict] = {}
    previous_id = previous_time = None
    n = gaps = gap_events = timestamp_ties = 0
    for trade in trades:
        if not start <= trade.timestamp_us < end:
            raise ValueError("成交不在指定 UTC 日期内")
        if not all(math.isfinite(value) and value > 0 for value in (trade.price, trade.quantity)):
            raise ValueError("成交价格和数量必须是有限正数")
        if previous_id is not None:
            if trade.trade_id <= previous_id:
                raise ValueError("aggregate ID 必须严格递增，不接受重复或倒序")
            if trade.timestamp_us < previous_time:
                raise ValueError("时间戳不得倒序；相同时间戳允许")
            gap = trade.trade_id - previous_id - 1
            gaps += gap
            gap_events += int(gap > 0)
            timestamp_ties += int(trade.timestamp_us == previous_time)
        previous_id, previous_time = trade.trade_id, trade.timestamp_us
        n += 1
        index = (trade.timestamp_us - start) // BUCKET_US
        if index not in buckets:
            buckets[index] = {"first_timestamp_us": trade.timestamp_us, "last_timestamp_us": trade.timestamp_us,
                "first_price": trade.price, "last_price": trade.price,
                "first_side": "sell" if trade.buyer_is_maker else "buy",
                "buy_volume_eth": 0.0, "sell_volume_eth": 0.0, "aggregate_rows": 0}
        bucket = buckets[index]
        bucket["aggregate_rows"] += 1
        bucket["last_timestamp_us"] = trade.timestamp_us
        bucket["last_price"] = trade.price
        side_key = "sell_volume_eth" if trade.buyer_is_maker else "buy_volume_eth"
        bucket[side_key] += trade.quantity
    if not buckets:
        raise ValueError("指定日期没有成交")
    rows = []
    excluded_gap = excluded_day_edge = 0
    feature_ages = []
    for index, current in buckets.items():
        decision = start + (index + 1) * BUCKET_US
        feature_ages.append(decision - current["last_timestamp_us"])
        if decision >= end:
            excluded_day_edge += 1
            continue
        following = buckets.get(index + 1)
        if following is None:
            excluded_gap += 1
            continue
        buy, sell = current["buy_volume_eth"], current["sell_volume_eth"]
        volume = buy + sell
        reference, next_reference = current["last_price"], following["last_price"]
        rows.append({"date": day, "decision_timestamp_us": decision,
            "reference_price": reference, "imbalance": (buy - sell) / volume,
            "target_return_bps": 10_000 * (next_reference / reference - 1),
            "next_reference_price": next_reference, "next_customer_side": following["first_side"],
            "reference_age_us": decision - current["last_timestamp_us"],
            "next_reference_age_us": decision + BUCKET_US - following["last_timestamp_us"],
            "current_aggregate_rows": current["aggregate_rows"], "current_volume_eth": volume,
            "current_first_timestamp_us": current["first_timestamp_us"],
            "current_last_timestamp_us": current["last_timestamp_us"],
            "next_first_timestamp_us": following["first_timestamp_us"],
            "next_last_timestamp_us": following["last_timestamp_us"]})
    quality = {"date": day, "aggregate_rows": n, "bucket_width_us": BUCKET_US,
        "expected_buckets": BUCKETS_PER_DAY, "nonempty_buckets": len(buckets),
        "empty_buckets": BUCKETS_PER_DAY - len(buckets), "labeled_rows": len(rows),
        "excluded_missing_adjacent_next_bucket": excluded_gap, "excluded_day_edge": excluded_day_edge,
        "missing_aggregate_ids": gaps, "id_gap_events": gap_events, "duplicate_ids": 0,
        "out_of_order_ids": 0, "out_of_order_timestamps": 0, "adjacent_equal_timestamps": timestamp_ties,
        "feature_reference_ages_all_nonempty": age_summary(feature_ages),
        "feature_reference_ages_labeled": age_summary([row["reference_age_us"] for row in rows]),
        "label_reference_ages_labeled": age_summary([row["next_reference_age_us"] for row in rows]),
        "age_quantiles": "nearest rank", "future_age_filter": None,
        "interval_rule": "At t: feature [t-5s,t), label [t,t+5s); require both adjacent buckets nonempty; no cross-day or forward-fill",
        "selection_note": "Labeled-row availability conditions on a nonempty next bucket, not known at decision time.",
        "next_customer_side_note": "First observed next-bucket aggressor; future environment field, never a forecasting feature."}
    return rows, quality


def load_day(day: str, raw_dir: Path, offline: bool = False) -> tuple[list[dict], dict, dict]:
    archive, source = real_data.fetch_day(day, Path(raw_dir), offline)
    rows, quality = build_day_rows(real_data.read_archive(archive, day), day)
    return rows, quality, source


def validate_row(row: dict) -> tuple[float, float]:
    x, y = float(row["imbalance"]), float(row["target_return_bps"])
    if not math.isfinite(x) or not -1 <= x <= 1 or not math.isfinite(y):
        raise ValueError("特征须为 [-1,1] 有限数，标签须为有限数")
    return x, y


def fit_ols(rows: list[dict]) -> dict:
    """调用方只传入训练行。常数特征时退化成训练均值，不使用未来划分。"""
    if not rows:
        raise ValueError("OLS 训练数据不能为空")
    xy = [validate_row(row) for row in rows]
    x_mean = math.fsum(x for x, _ in xy) / len(xy)
    y_mean = math.fsum(y for _, y in xy) / len(xy)
    variance_sum = math.fsum((x - x_mean) ** 2 for x, _ in xy)
    slope = math.fsum((x - x_mean) * (y - y_mean) for x, y in xy) / variance_sum if variance_sum else 0.0
    return {"intercept_bps": y_mean - slope * x_mean, "slope_bps_per_imbalance": slope, "n": len(xy)}


def predict(model: dict, imbalance: float, clip_bps: float | None = 2.0) -> float:
    if not math.isfinite(imbalance) or not -1 <= imbalance <= 1:
        raise ValueError("预测特征须在 [-1,1]")
    prediction = model["intercept_bps"] + model["slope_bps_per_imbalance"] * imbalance
    if not math.isfinite(prediction):
        raise ValueError("模型预测不是有限数")
    if clip_bps is None:
        return prediction
    if not math.isfinite(clip_bps) or clip_bps < 0:
        raise ValueError("clip_bps 须为非负有限数或 None")
    return max(-clip_bps, min(clip_bps, prediction))


def empty_metrics() -> dict:
    return {"n": 0, "sum_target_bps": 0.0, "sum_imbalance": 0.0,
        "predictors": {name: {"sum_squared_error": 0.0, "sum_absolute_error": 0.0,
            "direction_target_nonzero_n": 0, "direction_correct_n": 0, "direction_prediction_nonzero_n": 0}
            for name in PREDICTOR_NAMES}}


def finalize_metrics(report: dict) -> dict:
    n = report["n"]
    report["mean_target_bps"] = report["sum_target_bps"] / n if n else None
    report["mean_imbalance"] = report["sum_imbalance"] / n if n else None
    report["direction_rule"] = "Exclude exact-zero labels; exact-zero predictions on nonzero labels count incorrect and reduce coverage. No significance test."
    for metrics in report["predictors"].values():
        metrics["mse"] = metrics["sum_squared_error"] / n if n else None
        metrics["mae"] = metrics["sum_absolute_error"] / n if n else None
        directional_n = metrics["direction_target_nonzero_n"]
        metrics["directional_accuracy"] = metrics["direction_correct_n"] / directional_n if directional_n else None
        metrics["directional_coverage"] = metrics["direction_prediction_nonzero_n"] / directional_n if directional_n else None
    return report


def forecast_metrics(rows: list[dict], model: dict, train_mean_bps: float) -> dict:
    if not math.isfinite(train_mean_bps):
        raise ValueError("训练均值须为有限数")
    report = empty_metrics()
    for row in rows:
        x, y = validate_row(row)
        predictions = {"zero": 0.0, "train_mean": train_mean_bps,
            "model_raw": predict(model, x, None), "model_clipped": predict(model, x, 2.0)}
        report["n"] += 1
        report["sum_target_bps"] += y
        report["sum_imbalance"] += x
        for name, prediction in predictions.items():
            metrics = report["predictors"][name]
            error = prediction - y
            metrics["sum_squared_error"] += error * error
            metrics["sum_absolute_error"] += abs(error)
            if y != 0:
                metrics["direction_target_nonzero_n"] += 1
                metrics["direction_prediction_nonzero_n"] += int(prediction != 0)
                metrics["direction_correct_n"] += int(prediction * y > 0)
    return finalize_metrics(report)


def merge_metrics(reports: Iterable[dict]) -> dict:
    """按原始误差和计数合并，不能平均各日的 MSE 或正确率。"""
    result = empty_metrics()
    for report in reports:
        for key in ("n", "sum_target_bps", "sum_imbalance"):
            result[key] += report[key]
        for name in PREDICTOR_NAMES:
            for key in result["predictors"][name]:
                result["predictors"][name][key] += report["predictors"][name][key]
    return finalize_metrics(result)
