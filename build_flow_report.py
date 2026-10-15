"""从已有冻结结果生成确定性Markdown报告；不下载、拟合或执行报价策略。

本文件属于结果展示，不在最初的计算源代码冻结范围内。它只核对已保存
产物、求既存路径的均值/配对差，并格式化报告。每份输入只读取一次。
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent
DEFAULT_RESULTS = ROOT / "flow_research_results"
POLICIES = ("fixed", "inventory", "constant_wide", "flow", "intercept_only")
POLICY_LABELS = {
    "fixed": "固定价差", "inventory": "库存调整", "constant_wide": "固定加宽＋库存",
    "flow": "流量信号＋库存", "intercept_only": "仅截距＋库存",
}
PREDICTORS = ("zero", "train_mean", "model_raw", "model_clipped")
PREDICTOR_LABELS = {
    "zero": "零预测", "train_mean": "训练均值", "model_raw": "线性模型（原值）",
    "model_clipped": "线性模型（±2bp截断）",
}
METRICS = (
    "pnl_liquidated", "pnl_mtm", "execution_edge", "inventory_revaluation",
    "gap_inventory_revaluation", "interval_inventory_revaluation", "fees", "terminal_cost",
    "fill_count", "eligible_opportunities", "fills_per_eligible_opportunity",
    "mean_abs_inventory", "time_weighted_mean_abs_inventory", "max_abs_inventory",
    "max_drawdown", "max_drawdown_mtm", "accounting_residual",
    "gap_count", "gap_seconds", "elapsed_seconds",
)
INPUT_FILES = ("development.json", "model.json", "freeze.json", "test_access.json", "test.json", "test_runs.csv")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nfmt(value, digits=4, signed=False):
    if value is None:
        return "—"
    return format(value, ("+" if signed else "") + f".{digits}f")


def pct(value):
    return "—" if value is None else f"{100 * value:.2f}%"


def table(headers, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(cell(item) for item in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return lines + [""]


def mean_metric(rows, field):
    return statistics.fmean(float(row[field]) for row in rows)


def utc_microseconds(value):
    return datetime.fromtimestamp(int(value) / 1_000_000, timezone.utc).isoformat()


def load_bundle(results_dir: Path, root: Path = ROOT) -> dict:
    """只读取已保存结果；输入缺失或清单不一致时拒绝生成漂亮但错误的报告。"""
    blobs = {name: (results_dir / name).read_bytes() for name in INPUT_FILES}
    receipt = results_dir / "verification.json"
    if receipt.exists():
        blobs["verification.json"] = receipt.read_bytes()
    bundle = {name.removesuffix(".json"): json.loads(raw) for name, raw in blobs.items() if name.endswith(".json")}
    bundle["input_hashes"] = {name: digest(raw) for name, raw in blobs.items()}
    plan_bytes = (root / "research/FLOW_SIGNAL_PLAN.json").read_bytes()
    bundle["plan"] = json.loads(plan_bytes)
    bundle["plan_hash"] = digest(plan_bytes)
    bundle["generator_hash"] = digest(Path(__file__).read_bytes())
    csv_rows = list(csv.DictReader(io.StringIO(blobs["test_runs.csv"].decode("utf-8"))))
    for row in csv_rows:
        row["seed"] = int(row["seed"])
        for field in (*METRICS, "fee_bps", "urgency_bps", "first_decision_timestamp_us", "last_valuation_timestamp_us"):
            row[field] = float(row[field])
            if not math.isfinite(row[field]):
                raise ValueError(f"Non-finite CSV field: {field}")
    bundle["runs"] = csv_rows
    validate_bundle(bundle, root)
    return bundle


def validate_bundle(bundle, root=ROOT):
    plan, model, freeze = bundle["plan"], bundle["model"], bundle["freeze"]
    access, test, hashes = bundle["test_access"], bundle["test"], bundle["input_hashes"]
    expected_hashes = {
        "plan_sha256": bundle["plan_hash"], "model_sha256": hashes["model.json"],
        "development_sha256": hashes["development.json"],
    }
    for key, expected in expected_hashes.items():
        if freeze[key] != expected:
            raise ValueError(f"Freeze linkage mismatch: {key}")
    if access["freeze_sha256"] != hashes["freeze.json"] or test["freeze_sha256"] != hashes["freeze.json"]:
        raise ValueError("Test/access record does not refer to the loaded freeze")
    if test["model_sha256"] != hashes["model.json"]:
        raise ValueError("Test result does not refer to the loaded model")
    if "verification" in bundle and bundle["verification"]["original_test_sha256"] != hashes["test.json"]:
        raise ValueError("Verification receipt does not refer to the loaded test result")
    if list(model["train_dates"]) != list(plan["train_dates"]):
        raise ValueError("Model training dates differ from the plan")
    for dates in (access["dates"], freeze["test_dates"], sorted(test["forecast"]["daily"])):
        if list(dates) != list(plan["test_dates"]):
            raise ValueError("Test dates differ from the plan")
    for split, plan_key in (("train", "train_dates"), ("validation", "validation_dates")):
        if sorted(bundle["development"][split]["daily"]) != list(plan[plan_key]):
            raise ValueError(f"Development dates differ from the plan: {split}")
    if tuple(plan["policies"]) != POLICIES:
        raise ValueError("Report policy set differs from the plan")
    if datetime.fromisoformat(freeze["frozen_at_utc"]) > datetime.fromisoformat(access["first_access_started_at_utc"]):
        raise ValueError("Freeze occurred after first recorded test access")
    for name, expected in freeze["source_sha256"].items():
        if digest((root / name).read_bytes()) != expected:
            raise ValueError(f"Frozen computational source has changed: {name}")

    settings = plan["replay"]
    cases = [("primary", seed, settings["fee_bps"], settings["urgency_bps"])
             for seed in settings["primary_seeds"]]
    cases += [(case["name"], settings["sensitivity_seed"], case["fee_bps"], case["urgency_bps"])
              for case in settings["sensitivity_cases"]]
    expected = {(day, case, policy, seed): (fee, urgency)
                for day in plan["test_dates"] for case, seed, fee, urgency in cases for policy in POLICIES}
    actual = {}
    for row in bundle["runs"]:
        key = (row["date"], row["case"], row["strategy"], row["seed"])
        if key in actual:
            raise ValueError(f"Duplicate run in CSV: {key}")
        actual[key] = (row["fee_bps"], row["urgency_bps"])
        residual = abs(row["pnl_liquidated"] - row["execution_edge"] - row["inventory_revaluation"] + row["fees"] + row["terminal_cost"])
        if residual > 1e-7:
            raise ValueError(f"Saved P&L decomposition fails: {key}")
    if actual != expected:
        raise ValueError("Saved runs are incomplete or differ from the planned cases")

    daily_map = {}
    for row in test["policy"]["daily_seed_means"]:
        key = (row["case"], row["date"], row["strategy"])
        if key in daily_map:
            raise ValueError("Duplicate daily policy summary")
        daily_map[key] = row
    expected_daily = {(case, day, policy) for day, case, policy, _ in expected}
    if set(daily_map) != expected_daily:
        raise ValueError("CSV and JSON daily policy coverage differ")
    for key, saved in daily_map.items():
        rows = [row for row in bundle["runs"] if (row["case"], row["date"], row["strategy"]) == key]
        if saved["seed_replicates"] != len(rows):
            raise ValueError("CSV and JSON seed counts differ")
        for field in METRICS:
            if not math.isclose(mean_metric(rows, field), saved[field], rel_tol=1e-10, abs_tol=1e-9):
                raise ValueError(f"CSV and JSON policy metric differ: {key}, {field}")


def policy_rows(bundle, strategy, case="primary", seed=None):
    return [row for row in bundle["runs"] if row["strategy"] == strategy and row["case"] == case
            and (seed is None or row["seed"] == seed)]


def equal_day_means(rows, dates):
    return {field: statistics.fmean(mean_metric([row for row in rows if row["date"] == day], field)
                                    for day in dates) for field in METRICS}


def paired_rows(bundle, comparison, case="primary", seed=None):
    base = {(row["date"], row["seed"]): row for row in policy_rows(bundle, comparison, case, seed)}
    flow = policy_rows(bundle, "flow", case, seed)
    if {(row["date"], row["seed"]) for row in flow} != set(base):
        raise ValueError("Pairing lacks a matching date/seed")
    return [{"date": row["date"], "seed": row["seed"],
             **{field: float(row[field]) - float(base[row["date"], row["seed"]][field]) for field in METRICS}}
            for row in flow]


def forecast_verdict(bundle):
    forecasts = bundle["test"]["forecast"]["pooled"]["predictors"]
    clipped = forecasts["model_clipped"]["mse"]
    zero, mean = forecasts["zero"]["mse"], forecasts["train_mean"]["mse"]
    differences = (clipped - zero, clipped - mean)
    if all(delta < 0 for delta in differences):
        conclusion = "截断线性模型在这三个测试日的池化MSE低于两个固定基线。"
    elif all(delta > 0 for delta in differences):
        conclusion = "截断线性模型在这三个测试日的池化MSE高于两个固定基线，未显示该指标上的预测改善。"
    elif all(delta == 0 for delta in differences):
        conclusion = "截断线性模型与两个固定基线的测试池化MSE相同。"
    else:
        conclusion = "截断线性模型相对两个固定基线的测试池化MSE结果不一致，不能概括为同时优于两者。"
    mse_change = (clipped / zero - 1) * 100 if zero else None
    mae = forecasts["model_clipped"]["mae"]
    zero_mae = forecasts["zero"]["mae"]
    mae_change = (mae / zero_mae - 1) * 100 if zero_mae else None
    text = conclusion + f" 模型减零预测为 {nfmt(differences[0], 6, True)} bp²，模型减训练均值为 {nfmt(differences[1], 6, True)} bp²；负值才表示误差较低。"
    text += f" 相对零预测，**MSE变化{nfmt(mse_change, 4, True)}%，MAE变化{nfmt(mae_change, 4, True)}%**（MAE从{nfmt(zero_mae, 6)}变为{nfmt(mae, 6)}bp）。"
    if mse_change is not None and mae_change is not None and mse_change * mae_change < 0:
        text += " 两种误差指标方向相反，不能概括为预测全面改善。"
    return text


def render_report(bundle):
    plan, model = bundle["plan"], bundle["model"]
    development, test, freeze, access = bundle["development"], bundle["test"], bundle["freeze"], bundle["test_access"]
    days = list(plan["test_dates"])
    settings = plan["replay"]
    primary = {policy: equal_day_means(policy_rows(bundle, policy), days) for policy in POLICIES}
    comparisons = {policy: paired_rows(bundle, policy) for policy in ("inventory", "intercept_only")}
    paired_overall = {policy: equal_day_means(rows, days) for policy, rows in comparisons.items()}
    daily_deltas = {day: mean_metric([row for row in comparisons["inventory"] if row["date"] == day], "pnl_liquidated") for day in days}
    negative_days = [day for day, value in daily_deltas.items() if value < 0]
    sensitivity_cases = [{"name": "primary", "fee_bps": settings["fee_bps"], "urgency_bps": settings["urgency_bps"]}, *settings["sensitivity_cases"]]
    sensitivity_metrics = {case["name"]: {policy: equal_day_means(policy_rows(bundle, policy, case["name"], settings["sensitivity_seed"]), days)
                                           for policy in POLICIES} for case in sensitivity_cases}
    dd_delta = primary["flow"]["max_drawdown"] - primary["inventory"]["max_drawdown"]
    main_wide_relation = "高于" if primary["constant_wide"]["pnl_liquidated"] > primary["flow"]["pnl_liquidated"] else "不高于"
    low = sensitivity_metrics["willingness_low"]
    low_wide_relation = "低于" if low["constant_wide"]["pnl_liquidated"] < low["flow"]["pnl_liquidated"] else "不低于"
    lines = [
        "# 从成交流量到报价：冻结后的测试结果", "",
        "本报告把两个问题分开：上一段主动成交量能否改善下一段成交价变化的预测；把这一预测用于报价后，在明确假设的成交环境中会发生什么。**价格和方向来自公开历史记录，客户意愿、数量、路由和我们的成交是假设；以下PnL不是USDT收益或真实历史成交回测。**", "",
        "## 本轮结论", "",
        forecast_verdict(bundle), "",
        f"在五个意愿种子的主实验中，先在每天内平均，再对三个日期等权：flow相对库存策略的平均PnL差为 **{nfmt(paired_overall['inventory']['pnl_liquidated'], 6, True)}**；相对仅截距策略为 **{nfmt(paired_overall['intercept_only']['pnl_liquidated'], 6, True)}**。两种比较必须同时读：前者包含完整信号，后者进一步分离imbalance斜率。", "",
        f"这一增量并非每日一致：**{len(days)}个测试日中有{len(negative_days)}天flow减库存策略的日均差为负**（{', '.join(negative_days) or '无'}）。flow的平均最大回撤为{nfmt(primary['flow']['max_drawdown'])}，库存策略为{nfmt(primary['inventory']['max_drawdown'])}，差为{nfmt(dd_delta, 5, True)}；正值表示这项风险指标变差。", "",
        f"简单加宽对照的主平均PnL为{nfmt(primary['constant_wide']['pnl_liquidated'])}，{main_wide_relation}flow的{nfmt(primary['flow']['pnl_liquidated'])}。但把假设意愿均值降为1.5bp后（同为种子7001），加宽对照为{nfmt(low['constant_wide']['pnl_liquidated'])}，{low_wide_relation}flow的{nfmt(low['flow']['pnl_liquidated'])}；此时flow减库存策略也为{nfmt(low['flow']['pnl_liquidated'] - low['inventory']['pnl_liquidated'], 5, True)}。**没有一个在这些条件下始终占优的策略，效果依赖日期和人工意愿假设。**", "",
        "这是已声明日期、参数和假设下的描述性结果。没有根据测试结果选择赢家、重新调参或构造种子置信区间；正的模拟PnL差额也不构成现实盈利证据。", "",
        "## 研究顺序与冻结记录", "",
    ]
    lines += table(["划分", "UTC日期", "用途"], [
        ["训练", ", ".join(plan["train_dates"]), "仅此划分拟合OLS系数与训练均值"],
        ["验证", ", ".join(plan["validation_dates"]), "诊断报告；不依据结果选择模型或参数"],
        ["测试", ", ".join(days), "冻结计算与模型后首次打开；此后复算不再是新的未见测试"],
    ])
    lines += [
        "9月1—3日此前已用于分钟级诊断，因此明确作为开发数据。计划在本项目打开9月4—8日新档案前另行发布；这属于项目内预先声明的留出评价，不称正式注册研究。见[研究计划](../research/FLOW_SIGNAL_PLAN.json)与[研究日志](../research/RESEARCH_JOURNAL.md)。", "",
        f"- 计算冻结时间：`{freeze['frozen_at_utc']}`。",
        f"- 首次测试访问开始：`{access['first_access_started_at_utc']}`。",
        f"- 本报告对应的测试产物时间：`{test['generated_at_utc']}`。",
        "- 生成本报告时已校验模型、开发结果、计划、计算源文件和首次测试访问记录之间的SHA256关联，并核对CSV与JSON汇总一致。", "",
        "报告生成器在计算冻结之后编写，属于展示层；它不纳入原冻结计算源文件，不重新取数、拟合或执行策略，仅从保存结果计算均值、配对差并排版。", "",
        "## 特征、模型与预测评价", "",
        "在决策时点t，特征来自 `[t−5s,t)` 的主动买卖ETH成交量差除以总量。参考价为严格早于t的最后成交价；目标为 `[t,t+5s)` 最后成交价相对参考价的变化（bp）。二者都不是观察到的中间价。只保留同日相邻两桶都有成交的行，不跨日、不向前填充缺失桶。", "",
        f"训练行数 **{model['n']:,}**。冻结模型：", "",
        "```text",
        f"raw predicted return (bp) = {model['intercept_bps']:.10f} + ({model['slope_bps_per_imbalance']:.10f}) × imbalance",
        f"quote signal (bp) = clip(raw predicted return, -{plan['signal_clip_bps']:g}, +{plan['signal_clip_bps']:g})",
        f"training-mean baseline (bp) = {model['train_mean_bps']:.10f}",
        "```", "",
        f"在imbalance∈[−1,1]内，冻结线性模型的预测范围为[{model['intercept_bps'] - abs(model['slope_bps_per_imbalance']):.6f}, {model['intercept_bps'] + abs(model['slope_bps_per_imbalance']):.6f}]bp。" +
        ("这完全落在±2bp以内，因此本轮原值与截断结果相同；不能把表现归功于截断。" if abs(model['intercept_bps']) + abs(model['slope_bps_per_imbalance']) <= plan['signal_clip_bps'] else "范围可能触及±2bp，原值与截断结果需分别读取。"), "",
        "池化预测误差按所有有效行的误差和、计数计算，不是逐日MSE的简单平均。MSE单位为bp²，MAE单位为bp；数值越低越好。", "",
    ]
    forecast_rows = []
    for split_name, report in (("训练", development["train"]["pooled"]),
                               ("验证", development["validation"]["pooled"]), ("测试", test["forecast"]["pooled"])):
        for predictor in PREDICTORS:
            metrics = report["predictors"][predictor]
            forecast_rows.append([split_name, PREDICTOR_LABELS[predictor], f"{report['n']:,}",
                                  nfmt(metrics["mse"], 6), nfmt(metrics["mae"], 6),
                                  pct(metrics["directional_accuracy"]), pct(metrics["directional_coverage"])])
    lines += table(["划分", "预测", "行数", "MSE", "MAE", "方向正确率", "方向覆盖率"], forecast_rows)
    lines += [
        "方向统计排除实际收益恰好为零的行；在剩余行中，预测恰好为零算方向不正确，并降低方向覆盖率。因此零预测的方向正确率和覆盖率按定义为0%，不是说它主动预测了错误方向；不能据此把任何非零预测的较高正确率视为有用信号。应结合两个常数基线的MSE/MAE判断。", "",
        f"测试总计{test['forecast']['pooled']['n']:,}行，其中{test['forecast']['pooled']['predictors']['zero']['direction_target_nonzero_n']:,}行实际收益非零；方向统计的分母是后者。", "",
        "### 每个测试日的预测", "",
    ]
    daily_forecast = []
    for day in days:
        report = test["forecast"]["daily"][day]
        for predictor in PREDICTORS:
            metrics = report["predictors"][predictor]
            daily_forecast.append([day, PREDICTOR_LABELS[predictor], f"{report['n']:,}",
                                   nfmt(metrics["mse"], 6), nfmt(metrics["mae"], 6),
                                   pct(metrics["directional_accuracy"]), pct(metrics["directional_coverage"])])
    lines += table(["日期", "预测", "行数", "MSE", "MAE", "方向正确率", "方向覆盖率"], daily_forecast)

    lines += [
        "## 五种报价策略的假设环境实验", "",
        f"每个日期的首个有效参考价归一化为100，初始现金{settings['initial_cash']:g}、库存0，单次交易一单位，库存上限±{settings['inventory_limit']}。价差、信号、库存偏移和费用按当前归一化参考价的bp计算；固定价格tick为{settings['normalized_tick']:g}。跨日期不延续库存。", "",
    ]
    lines += table(["策略", "半价差", "库存偏移", "额外中心信号"], [
        ["fixed", "2bp", "无", "无"],
        ["inventory", "2bp", "−0.2bp × q", "无"],
        ["constant_wide", "3bp", "−0.2bp × q", "无"],
        ["flow", "2bp", "−0.2bp × q", "截断的截距＋imbalance斜率"],
        ["intercept_only", "2bp", "−0.2bp × q", "只保留截断的训练截距"],
    ])
    lines += [
        f"主实验成交费用为{settings['fee_bps']:g}bp，客户意愿距离服从均值{settings['urgency_bps']:g}bp的指数分布。下一桶首笔实际主动成交方向只用来确定假设客户买/卖；其价格底线、数量、向本做市商路由及执行都不是历史观察值。五种策略在相同日期和种子下共享预生成环境。", "",
        "报价中心的总偏移被限制在±（半价差−一个tick）内，报价包围参考价；所以相对该参考价的成交优势被人为限制为非负。没有订单簿、队列、竞争者、延迟或冲击模型。", "",
        "假设成交被放在桶起点，随后持有库存五秒，没有使用实际首笔主动成交在桶内的具体时刻。缺失决策区间不发生成交，但保留库存并补计参考价变化。末尾在最后可用的下一参考价处按1bp＋成交费清仓，该时点不一定是UTC日末。", "",
        "### 日期等权的主结果", "",
        f"每个策略：三个日期×{len(settings['primary_seeds'])}个意愿种子。先计算同一天的种子均值，再对三个日期等权。种子只改变同一市场路径上的假设意愿，不能把15次运行当15个独立市场样本。所有PnL均已扣费用和末尾清仓成本，单位为归一化的假设货币。", "",
    ]
    policy_headers = ["策略", "PnL", "成交数", "机会成交比例", "平均绝对库存", "时间加权绝对库存", "平均路径最大绝对库存", "平均最大回撤"]
    def policy_values(label, metrics):
        return [label, nfmt(metrics["pnl_liquidated"]), nfmt(metrics["fill_count"], 1),
                pct(metrics["fills_per_eligible_opportunity"]), nfmt(metrics["mean_abs_inventory"], 3),
                nfmt(metrics["time_weighted_mean_abs_inventory"], 3), nfmt(metrics["max_abs_inventory"], 2),
                nfmt(metrics["max_drawdown"], 4)]
    lines += table(policy_headers, [policy_values(policy, primary[policy]) for policy in POLICIES])
    lines += [
        "路径最大库存、最大回撤列为各次路径指标的均值，不是全样本最大值。回撤仅在决策、假设成交后、下一参考价和清仓时估值，不能恢复桶内最高回撤。时间加权库存包含缺失区间的持仓，仍采用上述桶起点假设成交约定。", "",
        "### 每日主结果（同日五个种子均值）", "",
    ]
    daily_rows = []
    daily_lookup = {(row["date"], row["strategy"]): row for row in test["policy"]["daily_seed_means"] if row["case"] == "primary"}
    for day in days:
        for policy in POLICIES:
            daily_rows.append([day, *policy_values(policy, daily_lookup[day, policy])])
    lines += table(["日期", *policy_headers], daily_rows)

    lines += [
        "## 配对差额与盈亏分解", "",
        "每个差额都是同日、同意愿种子的 `flow − 对照`，随后先日内平均、再日期等权。费用和清仓两列已取负号，因而各贡献可以直接相加：", "",
        "```text",
        "ΔPnL = Δ成交优势 + Δ库存重估 − Δ成交费用 − Δ清仓成本",
        "```", "",
        "库存重估同时包含桶内与缺失区间。分项是代数记账归因，不是互相独立的因果效应；成交客户与成交数变化也影响各项。", "",
    ]
    paired_table = []
    for comparison in ("inventory", "intercept_only"):
        groups = [(day, equal_day_means([r for r in comparisons[comparison] if r["date"] == day], [day])) for day in days]
        groups.append(("三日等权", paired_overall[comparison]))
        for day, values in groups:
            paired_table.append([f"flow − {comparison}", day, nfmt(values["pnl_liquidated"], 5, True),
                                 nfmt(values["execution_edge"], 5, True), nfmt(values["inventory_revaluation"], 5, True),
                                 nfmt(-values["fees"], 5, True), nfmt(-values["terminal_cost"], 5, True),
                                 nfmt(values["fill_count"], 1, True), nfmt(values["mean_abs_inventory"], 4, True)])
    lines += table(["比较", "日期", "ΔPnL", "Δ成交优势", "Δ库存重估", "−Δ费用", "−Δ清仓", "Δ成交数", "Δ平均绝对库存"], paired_table)
    lines += [
        "数值四舍五入后可能有末位加总差。只有三个测试日期，时间相关性也未消除；不计算种子层面的显著性、p值或置信区间。", "",
        "## 费用与意愿敏感性", "",
        f"这一节所有条件都只用种子 **{settings['sensitivity_seed']}**，并对三个日期等权。第一行重新取主参数下的同一个种子作为对照，不能拿其余单种子情景与上面的五种子主均值直接比较。参数在看测试结果之前声明，没有从表中挑最赚钱的设置。", "",
    ]
    sensitivity_rows = []
    for case in sensitivity_cases:
        case_metrics = sensitivity_metrics[case["name"]]
        sensitivity_rows.append([case["name"], f"{case['fee_bps']:g} / {case['urgency_bps']:g}",
                                 *[nfmt(case_metrics[p]["pnl_liquidated"], 4) for p in POLICIES],
                                 nfmt(case_metrics["flow"]["pnl_liquidated"] - case_metrics["inventory"]["pnl_liquidated"], 5, True),
                                 nfmt(case_metrics["flow"]["pnl_liquidated"] - case_metrics["intercept_only"]["pnl_liquidated"], 5, True)])
    lines += table(["条件", "费用/意愿均值(bp)", *POLICIES, "flow−inventory", "flow−intercept"], sensitivity_rows)
    lines += [
        "为什么加宽报价可能在这里占优？只看一个极简机会：库存为0、参考价不变、忽略tick/清仓及后续持仓风险，客户意愿距离U服从均值u的指数分布。半价差h的接受概率就是exp(−h/u)，因此每个机会的毛成交优势为h×exp(−h/u)；每笔费用为f时，净值为(h−f)×exp(−h/u)。在这些限制条件下，它在h=u+f处达到最大。", "",
        "这解释了本模型为何会机械地偏好某些宽度：主参数u=3bp、f=0.25bp，与低意愿u=1.5bp提供不同的取舍。它不是经验估计、完整库存策略的最优解或现实报价建议。**正的总体PnL首先依赖模型给出的意愿分布与包围参考价的报价约束，不能用其大小为真实信号背书。**", "",
    ]

    lines += ["## 数据质量、缺失区间与样本选择", ""]
    quality_rows = []
    source_rows = []
    for label, report, manifest in (("训练", development["train"], "development.json"),
                                    ("验证", development["validation"], "development.json"),
                                    ("测试", test["forecast"], "test.json")):
        for day in sorted(report["quality"]):
            quality = report["quality"][day]
            quality_rows.append([label, day, f"{quality['aggregate_rows']:,}", quality["nonempty_buckets"],
                                 quality["empty_buckets"], quality["labeled_rows"], quality["excluded_missing_adjacent_next_bucket"],
                                 quality["missing_aggregate_ids"],
                                 nfmt(quality["feature_reference_ages_labeled"]["p95_us"] / 1000, 2),
                                 nfmt(quality["label_reference_ages_labeled"]["p95_us"] / 1000, 2)])
            source = report["sources"][day]
            source_rows.append([day, f"[ZIP]({source['url']})", f"[CHECKSUM]({source['checksum_url']})",
                                f"`{source['sha256'][:16]}`", f"[完整清单]({manifest})"])
    lines += table(["划分", "日期", "聚合成交条数", "非空桶", "空桶", "有效标签行", "缺相邻桶排除", "成交ID缺口", "当前参考age P95(ms)", "下一参考age P95(ms)"], quality_rows)
    lines += [
        "参考age表示最后成交距对应桶边界的时间；不代表交易所报价延迟。未来参考价的age仅作质量描述，没有用未来age筛选样本。重复ID、倒序ID/时间在读入阶段会报错；ID缺口也会停止本轮分析。", "",
        "**有效行的定义以“下一桶非空”为条件，这是决策时不能预知的事后可用性条件。** 报价函数不读取未来方向或价格，但这份机会样本也不能冒充完整在线订单流。被排除时段不会自动获得假设成交；已有库存继续承担下一有效参考价之前的价格变化。", "",
    ]
    gap_rows = []
    for day in days:
        run = next(row for row in policy_rows(bundle, "fixed", "primary", settings["sensitivity_seed"]) if row["date"] == day)
        gap_rows.append([day, int(run["gap_count"]), nfmt(run["gap_seconds"], 0), nfmt(run["elapsed_seconds"], 0),
                         utc_microseconds(run["first_decision_timestamp_us"]), utc_microseconds(run["last_valuation_timestamp_us"])])
    lines += table(["测试日", "决策缺口数", "缺口秒数", "首尾覆盖秒数", "首个决策UTC", "最后估值UTC"], gap_rows)
    largest_residual = max(row["accounting_residual"] for row in bundle["runs"])
    lines += [f"全部 **{len(bundle['runs'])}** 次保存的策略运行均通过账本核对；最大记录残差为 `{largest_residual:.3e}`。缺口价格变化已经纳入库存重估，而不是悄悄删去。", "",
              "## 原始来源与可复算文件", "",
              "原始来源为Binance公开现货ETHUSDT聚合成交档案，逐日与官方CHECKSUM核对。下表SHA256为前16位，完整摘要、来源URL、拉取时间与压缩大小保存在对应清单。原始ZIP不提交到代码库。", ""]
    lines += table(["日期", "原始档案", "官方校验", "SHA256前16位", "本地清单"], source_rows)
    lines += [
        "复算保持首次测试文件，另写复算结果；不会重置“这些日期已经看过”的事实。缓存存在时：", "",
        "```bash",
        "python3 flow_research.py --stage reproduce --offline",
        "python3 build_flow_report.py",
        "```", "",
        "本报告输入文件及其完整SHA256：", "",
    ]
    if "verification" in bundle:
        receipt = bundle["verification"]
        lines += [f"[已保存的复算回执](verification.json)：模型与开发结果复建一致={receipt['model_and_development_reproduced']}，测试指标一致={receipt['test_metrics_identical']}；本地测试通过{receipt['local_unit_tests_passed']}项。另附[该版本CI记录]({receipt['ci_url']})。", ""]
    lines += table(["文件", "SHA256"], [[f"[{name}]({name})", f"`{value}`"] for name, value in bundle["input_hashes"].items()])
    lines += [
        f"计划SHA256：`{bundle['plan_hash']}`。", "",
        f"本报告生成器SHA256：`{bundle['generator_hash']}`。相同输入与生成器会产生相同Markdown；报告不插入新的生成时钟。", "",
        "## 可以支持与仍不能支持的判断", "",
        "本轮可以描述：这个单特征、训练期冻结的预测在三个后续日期上，相对两个常数预测的误差如何；在相同历史参考价格、方向及假设意愿下，完整信号、仅截距和库存策略的账本如何不同。", "",
        "仍不能据此识别我们的真实成交、赚取实际买卖价差、队列位置、可交易收益、因果关系或跨市场泛化。订单簿、费用制度、延迟、冲击、融资/借券等都尚未建模。下轮若提出新假设，必须另记新计划并找新的未见日期，不把本轮测试重新标成未见。", "",
    ]
    return "\n".join(lines)


def build_report(results_dir: Path = DEFAULT_RESULTS) -> Path:
    results_dir = Path(results_dir)
    bundle = load_bundle(results_dir)
    destination = results_dir / "report.md"
    destination.write_text(render_report(bundle), encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    print(build_report(args.results_dir))


if __name__ == "__main__":
    main()
