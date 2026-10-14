"""真实未聚合成交：固定 2025-09-01 ETHUSDT，1/5/30 秒带方向价格响应。

只做描述性诊断和两种官方数据格式之间的对账，不模拟挂单成交或收益。
"""

from __future__ import annotations

import argparse
from array import array
from bisect import bisect_left
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
import math
from pathlib import Path
from typing import Iterable, Sequence
import zipfile

import real_data


DAY = "2025-09-01"
SYMBOL = "ETHUSDT"
HORIZONS_SECONDS = (1, 5, 30)
MAX_LOOKUP_DELAY_US = 1_000_000
QUOTE_ABS_TOLERANCE = 1e-8
QUOTE_REL_TOLERANCE = 1e-10
RECON_REL_TOLERANCE = 1e-10
RECON_ABS_TOLERANCES = {"quantity": 1e-6, "notional": 1e-4, "price": 1e-8}


@dataclass(frozen=True, slots=True)
class Tick:
    trade_id: int
    price: float
    quantity: float
    quote_quantity: float
    timestamp_us: int
    buyer_is_maker: bool
    best_match: bool

    @property
    def sign(self) -> int:
        return -1 if self.buyer_is_maker else 1


def parse_tick(row: list[str], day: str = DAY) -> Tick:
    if len(row) != 7:
        raise ValueError(f"逐笔 trades 应有 7 列，实际 {len(row)} 列")
    tick = Tick(int(row[0]), float(row[1]), float(row[2]), float(row[3]), int(row[4]),
                real_data.parse_bool(row[5]), real_data.parse_bool(row[6]))
    if tick.trade_id < 0:
        raise ValueError("成交 ID 不得为负")
    if not all(math.isfinite(x) and x > 0 for x in (tick.price, tick.quantity, tick.quote_quantity)):
        raise ValueError("price、qty、quoteQty 必须是有限正数")
    if not math.isclose(tick.quote_quantity, tick.price * tick.quantity,
                        abs_tol=QUOTE_ABS_TOLERANCE, rel_tol=QUOTE_REL_TOLERANCE):
        raise ValueError("quoteQty 与 price×qty 不一致，超过明确容差")
    start, end = real_data.day_bounds(day)
    if not start <= tick.timestamp_us < end:
        hint = "（疑似毫秒而非微秒）" if start <= tick.timestamp_us * 1000 < end else ""
        raise ValueError(f"时间戳不在 {day} UTC 内{hint}")
    return tick


def fetch_ticks(raw_dir: Path, offline: bool = False) -> tuple[Path, dict]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    name = f"{SYMBOL}-trades-{DAY}.zip"
    url = f"https://data.binance.vision/data/spot/daily/trades/{SYMBOL}/{name}"
    archive = raw_dir / name
    checksum = raw_dir / (name + ".CHECKSUM")
    receipt = raw_dir / (name + ".download.json")
    if not all(path.exists() for path in (archive, checksum, receipt)):
        if offline:
            raise FileNotFoundError(f"离线逐笔缓存不完整：{name}")
        real_data.download(url + ".CHECKSUM", checksum, max_bytes=4096)
        real_data.download(url, archive)
        digest = real_data.verify_checksum(archive, checksum.read_text(encoding="utf-8"))
        metadata = {"date": DAY, "dataset": "trades", "url": url, "checksum_url": url + ".CHECKSUM",
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": digest,
            "compressed_bytes": archive.stat().st_size}
        receipt.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata = json.loads(receipt.read_text(encoding="utf-8"))
    digest = real_data.verify_checksum(archive, checksum.read_text(encoding="utf-8"))
    if metadata.get("sha256") != digest or metadata.get("url") != url:
        raise ValueError("逐笔缓存下载记录与实际文件不一致")
    return archive, metadata


def read_ticks(archive: Path, day: str = DAY) -> Iterable[Tick]:
    expected = f"{SYMBOL}-trades-{day}.csv"
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist() != [expected]:
            raise ValueError("逐笔 ZIP 必须只包含预期 CSV，禁止任意路径解压")
        if bundle.getinfo(expected).file_size > real_data.MAX_CSV_BYTES:
            raise ValueError("CSV 大小超过预设上限")
        with bundle.open(expected) as binary, io.TextIOWrapper(binary, encoding="utf-8", newline="") as text:
            for line, row in enumerate(csv.reader(text), 1):
                try:
                    yield parse_tick(row, day)
                except ValueError as exc:
                    raise ValueError(f"{archive.name} 第 {line} 行：{exc}") from exc


def empty_totals() -> dict:
    return {"trade_count": 0, "buy_trade_count": 0, "sell_trade_count": 0,
        "quantity_eth": 0.0, "buy_quantity_eth": 0.0, "sell_quantity_eth": 0.0,
        "notional_usdt": 0.0, "buy_notional_usdt": 0.0, "sell_notional_usdt": 0.0,
        "first_price": None, "last_price": None}


def add_totals(totals: dict, count: int, price: float, quantity: float, notional: float, sign: int) -> None:
    side = "buy" if sign == 1 else "sell"
    totals["trade_count"] += count
    totals[f"{side}_trade_count"] += count
    totals["quantity_eth"] += quantity
    totals[f"{side}_quantity_eth"] += quantity
    totals["notional_usdt"] += notional
    totals[f"{side}_notional_usdt"] += notional
    if totals["first_price"] is None:
        totals["first_price"] = price
    totals["last_price"] = price


def load_ticks(ticks: Iterable[Tick], day: str = DAY) -> tuple[dict, dict, dict]:
    """只保留响应计算必需的紧凑数组；不输出逐笔原始记录。"""
    data = {"timestamps": array("q"), "prices": array("d"), "quantities": array("d"), "signs": array("b")}
    totals, minutes = empty_totals(), {}
    previous_id = previous_time = first_id = None
    gaps = gap_events = ties = 0
    max_quote_error = 0.0
    for tick in ticks:
        if previous_id is not None:
            if tick.trade_id <= previous_id:
                kind = "重复" if tick.trade_id == previous_id else "倒序"
                raise ValueError(f"逐笔成交 ID {kind}，停止分析：{tick.trade_id}")
            if tick.timestamp_us < previous_time:
                raise ValueError("逐笔时间戳倒序，停止分析")
            ties += int(tick.timestamp_us == previous_time)
            gap = tick.trade_id - previous_id - 1
            gap_events += int(gap > 0)
            gaps += gap
        else:
            first_id = tick.trade_id
        previous_id, previous_time = tick.trade_id, tick.timestamp_us
        data["timestamps"].append(tick.timestamp_us)
        data["prices"].append(tick.price)
        data["quantities"].append(tick.quantity)
        data["signs"].append(tick.sign)
        max_quote_error = max(max_quote_error, abs(tick.quote_quantity - tick.price * tick.quantity))
        minute = tick.timestamp_us // real_data.MINUTE_US
        if minute not in minutes:
            minutes[minute] = empty_totals()
        for accumulator in (totals, minutes[minute]):
            add_totals(accumulator, 1, tick.price, tick.quantity, tick.quote_quantity, tick.sign)
    if not totals["trade_count"]:
        raise ValueError("逐笔数据为空")
    quality = {"date": day, "timestamp_unit": "microseconds", "rows": totals["trade_count"],
        "first_trade_id": first_id, "last_trade_id": previous_id, "missing_trade_ids": gaps,
        "id_gap_events": gap_events, "duplicate_ids": 0, "out_of_order_ids": 0, "out_of_order_timestamps": 0,
        "adjacent_equal_timestamps": ties, "active_minutes": len(minutes), "missing_minutes": 1440 - len(minutes),
        "first_timestamp_us": data["timestamps"][0], "last_timestamp_us": data["timestamps"][-1],
        "quote_product_max_absolute_error_usdt": max_quote_error, "totals": totals}
    return data, quality, minutes


def load_aggregate_reference(trades: Iterable[real_data.Trade]) -> tuple[dict, dict]:
    """从原始 aggTrades ZIP 独立重算，不读取之前生成的汇总结果。"""
    totals, minutes = empty_totals(), {}
    previous_id = previous_time = previous_last = first_underlying = None
    rows = gaps = underlying_gaps = 0
    for trade in trades:
        if previous_id is not None:
            if trade.trade_id <= previous_id or trade.timestamp_us < previous_time:
                raise ValueError("参考 aggTrades ID 或时间倒序/重复")
            if trade.first_trade_id <= previous_last:
                raise ValueError("参考 aggTrades 原始成交 ID 区间重叠/倒序")
            gaps += trade.trade_id - previous_id - 1
            underlying_gaps += trade.first_trade_id - previous_last - 1
        else:
            first_underlying = trade.first_trade_id
        previous_id, previous_time, previous_last = trade.trade_id, trade.timestamp_us, trade.last_trade_id
        rows += 1
        count = trade.last_trade_id - trade.first_trade_id + 1
        minute = trade.timestamp_us // real_data.MINUTE_US
        if minute not in minutes:
            minutes[minute] = empty_totals()
        for accumulator in (totals, minutes[minute]):
            add_totals(accumulator, count, trade.price, trade.quantity, trade.price * trade.quantity, trade.aggressor_sign)
    if not rows:
        raise ValueError("参考 aggTrades 为空")
    return {"aggregate_rows": rows, "missing_aggregate_ids": gaps, "underlying_id_gaps": underlying_gaps,
        "first_underlying_trade_id": first_underlying, "last_underlying_trade_id": previous_last, "totals": totals}, minutes


def forward_lookup(timestamps: Sequence[int], event_time: int, horizon_seconds: int,
                   day_end_us: int, max_delay_us: int = MAX_LOOKUP_DELAY_US) -> tuple[int | None, str, int | None]:
    """目标时间之后第一笔；相同时间多笔时取最小行序号/ID。"""
    if horizon_seconds <= 0 or max_delay_us < 0:
        raise ValueError("horizon 必须为正，最大延迟不得为负")
    target = event_time + horizon_seconds * 1_000_000
    if target >= day_end_us:
        return None, "outside_utc_day", None
    index = bisect_left(timestamps, target)
    if index == len(timestamps):
        return None, "no_future_trade", None
    delay = timestamps[index] - target
    if delay > max_delay_us:
        return None, "lookup_delay_exceeded", delay
    return index, "accepted", delay


def signed_response(price: float, future_price: float, sign: int) -> float:
    return sign * 10_000 * (future_price / price - 1)


def response_summary(data: dict, day: str = DAY) -> list[dict]:
    day_end = real_data.day_bounds(day)[1]
    output = []
    for horizon in HORIZONS_SECONDS:
        accumulators = {scope: {"events": 0, "retained": 0, "outside_utc_day": 0, "no_future_trade": 0,
            "lookup_delay_exceeded": 0, "sum": 0.0, "weighted_sum": 0.0, "quantity": 0.0,
            "positive": 0, "zero": 0, "delay_sum": 0, "delay_max": 0, "delay_min": None}
            for scope in ("all", "buy", "sell")}
        for i, event_time in enumerate(data["timestamps"]):
            index, reason, delay = forward_lookup(data["timestamps"], event_time, horizon, day_end)
            side = "buy" if data["signs"][i] == 1 else "sell"
            value = None if index is None else signed_response(data["prices"][i], data["prices"][index], data["signs"][i])
            for scope in ("all", side):
                accumulator = accumulators[scope]
                accumulator["events"] += 1
                if index is None:
                    accumulator[reason] += 1
                    continue
                accumulator["retained"] += 1
                accumulator["sum"] += value
                accumulator["weighted_sum"] += value * data["quantities"][i]
                accumulator["quantity"] += data["quantities"][i]
                accumulator["positive"] += int(value > 0)
                accumulator["zero"] += int(value == 0)
                accumulator["delay_sum"] += delay
                accumulator["delay_max"] = max(accumulator["delay_max"], delay)
                accumulator["delay_min"] = delay if accumulator["delay_min"] is None else min(accumulator["delay_min"], delay)
        for scope, acc in accumulators.items():
            n = acc["retained"]
            output.append({"horizon_seconds": horizon, "scope": scope, "events": acc["events"], "retained": n,
                "excluded_outside_utc_day": acc["outside_utc_day"], "excluded_no_future_trade": acc["no_future_trade"],
                "excluded_lookup_delay_exceeded": acc["lookup_delay_exceeded"],
                "event_weighted_mean_response_bps": acc["sum"] / n if n else None,
                "eth_volume_weighted_mean_response_bps": acc["weighted_sum"] / acc["quantity"] if n else None,
                "positive_count": acc["positive"], "zero_count": acc["zero"],
                "positive_fraction": acc["positive"] / n if n else None,
                "actual_lookup_delay_min_us": acc["delay_min"],
                "actual_lookup_delay_mean_us": acc["delay_sum"] / n if n else None,
                "actual_lookup_delay_max_us": acc["delay_max"] if n else None})
    return output


def compare_totals(scope: str, ticks: dict, aggregates: dict) -> list[dict]:
    checks = []
    for field in empty_totals():
        left, right = ticks.get(field), aggregates.get(field)
        if field.endswith("count"):
            tolerance = 0
        else:
            kind = "price" if "price" in field else "quantity" if "quantity" in field else "notional"
            tolerance = max(RECON_ABS_TOLERANCES[kind], RECON_REL_TOLERANCE * max(abs(left or 0), abs(right or 0)))
        difference = left - right if left is not None and right is not None else None
        passed = difference is not None and abs(difference) <= tolerance
        checks.append({"scope": scope, "field": field, "trades_value": left, "aggtrades_value": right,
            "difference": difference, "absolute_tolerance": tolerance, "passed": passed})
    return checks


def reconcile(tick_quality: dict, tick_minutes: dict, aggregate_quality: dict, aggregate_minutes: dict) -> dict:
    checks = compare_totals("day", tick_quality["totals"], aggregate_quality["totals"])
    for minute in sorted(set(tick_minutes) | set(aggregate_minutes)):
        scope = datetime.fromtimestamp(minute * 60, timezone.utc).isoformat()
        checks.extend(compare_totals(scope, tick_minutes.get(minute, {}), aggregate_minutes.get(minute, {})))
    for name, left, right in (("first_trade_id", tick_quality["first_trade_id"], aggregate_quality["first_underlying_trade_id"]),
                               ("last_trade_id", tick_quality["last_trade_id"], aggregate_quality["last_underlying_trade_id"])):
        checks.append({"scope": "day", "field": name, "trades_value": left, "aggtrades_value": right,
            "difference": left - right, "absolute_tolerance": 0, "passed": left == right})
    failures = [check for check in checks if not check["passed"]]
    return {"checks": checks, "check_count": len(checks), "failed_count": len(failures), "passed": not failures,
        "failures": failures, "minute_keys_match": set(tick_minutes) == set(aggregate_minutes),
        "relative_tolerance": RECON_REL_TOLERANCE, "absolute_tolerances": RECON_ABS_TOLERANCES,
        "count_tolerance": 0}


def render_report(result: dict) -> str:
    quality = result["tick_quality"]
    def display(value: float | None, format_spec: str, scale: float = 1) -> str:
        return "—" if value is None else format(value / scale, format_spec)

    lines = ["# 未聚合逐笔成交：成交后的带方向价格响应", "",
        f"本次逐笔诊断在读取其结果前固定为 **{SYMBOL}，{DAY} UTC 整日**，取原有样本的第一天。此前已查看过这一天的分钟级结果，因此这是探索性延伸，不能称作未见测试。来自官方 `trades` 文件，共 **{quality['rows']:,} 笔**；没有把一条聚合记录当作一笔原始成交。", "",
        "## 计算定义", "",
        "每笔成交的主动方向 s：主动买入 +1，主动卖出 −1。官方 `isBuyerMaker=true` 表示买方被动，因此主动方是卖方。", "",
        "固定观察 1、5、30 秒。对事件时刻 t，选择第一笔时间戳 ≥t+h 的成交（同时间多笔取最先的 ID），只接受超过目标时间不多于 1 秒的观察；目标越过 UTC 当日、没有随后成交或延迟过长均剔除，不拿最后价格填充。", "",
        "带方向响应 = s × 10,000 × (随后成交价 / 本次成交价 − 1)，单位 bp。正值表示随后成交价向主动方有利的方向变化。主结果每笔成交等权，另列以该笔 ETH 数量加权结果。", "",
        "这只是描述性成交价响应代理：不是中间价 markout、不是因果价格冲击，也不是自己的挂单成交记录或做市利润。结果使用未来价格作为研究的被解释变量，不把它当作当时可得的策略信号。", "",
        "## 结果", "", "| 目标秒数 | 方向 | 保留笔数 | 等权平均 bp | ETH 加权平均 bp | 平均查找延迟 ms | 最大延迟 ms |",
        "|---:|---|---:|---:|---:|---:|---:|"]
    for row in result["responses"]:
        lines.append(f"| {row['horizon_seconds']} | {row['scope']} | {row['retained']:,} | "
            f"{display(row['event_weighted_mean_response_bps'], '+.4f')} | "
            f"{display(row['eth_volume_weighted_mean_response_bps'], '+.4f')} | "
            f"{display(row['actual_lookup_delay_mean_us'], '.3f', 1000)} | "
            f"{display(row['actual_lookup_delay_max_us'], '.3f', 1000)} |")
    lines += ["", "| 目标秒数 | 全部事件 | 越过日界 | 无后续成交 | 延迟超过 1 秒 | 保留 |", "|---:|---:|---:|---:|---:|---:|"]
    for row in result["responses"]:
        if row["scope"] == "all":
            lines.append(f"| {row['horizon_seconds']} | {row['events']:,} | {row['excluded_outside_utc_day']:,} | {row['excluded_no_future_trade']:,} | {row['excluded_lookup_delay_exceeded']:,} | {row['retained']:,} |")
    lines += ["", "保留事件的实际响应时间为目标 h 加上述非负查找延迟。买/卖各自的剔除数、零响应笔数、正响应比例和延迟范围全部见 response_summary.csv；零响应没有删除。", "",
        "## 数据质量与独立格式对账", "",
        f"时间单位为微秒。缺失 ID {quality['missing_trade_ids']}、重复 ID {quality['duplicate_ids']}、倒序时间 {quality['out_of_order_timestamps']}、缺失分钟 {quality['missing_minutes']}。相邻相同时间戳 {quality['adjacent_equal_timestamps']:,} 次，是允许出现的情况。",
        f"单笔 quoteQty 与 price×qty 的最大绝对差 {quality['quote_product_max_absolute_error_usdt']:.12g} USDT。单笔容差采用 max({QUOTE_ABS_TOLERANCE}, {QUOTE_REL_TOLERANCE}×两者绝对值较大者)，不通过即停止。", "",
        "参考数据直接重新读取同日官方 aggTrades ZIP。将每条区间 lastTradeId−firstTradeId+1 相加得到原始成交数量；从两种格式分别累计买/卖/总 ETH 数量与 USDT 名义额，并比较全天和每个 UTC 分钟的数量、笔数、首末价格。", "",
        f"共 **{result['reconciliation']['check_count']:,} 项检查，失败 {result['reconciliation']['failed_count']:,} 项**。全部逐项差值与容差见 reconciliation.csv；未强制改值以获得一致。",
        "笔数和 ID 要求完全一致。其他汇总容差为 max(绝对容差, 1e−10×两者绝对值较大者)；ETH 数量绝对容差 1e−6、USDT 名义额 1e−4、价格 1e−8。累计使用双精度浮点，容差用于源字段小数舍入和加总误差。", "",
        "| 全天字段 | trades | aggTrades 重建 | 差值 |", "|---|---:|---:|---:|"]
    for check in result["reconciliation"]["daily_checks"]:
        lines.append(f"| {check['field']} | {check['trades_value']} | {check['aggtrades_value']} | {check['difference']} |")
    if result["reconciliation"]["failed_count"]:
        lines += ["", "**对账存在失败：当前数据不能视为完全一致。请先查看失败项，不据此作可靠的市场结论。**"]
    lines += ["", "## 解释范围", "",
        "单日、单个加密资产样本不能代表其他时期或银行 S&T。逐笔事件高度相关，响应窗口也重叠；样本笔数大不等于独立样本多，没有使用 IID 显著性检验。成交价包含买卖价跳动及其他微观结构噪声，正负响应也可能受市场趋势、同时发生的订单与新闻影响。", "",
        "没有最优买卖报价、订单簿、自己的排队位置或撤单记录。不能把主动方价格响应简单取负后当成自己的做市 PnL；实际是否成交、费用、库存及对冲均未建模。后续若研究预测，应另定义只含过去信息的特征与时间外测试。", "",
        "## 复现与来源", "",
        "运行 `python3 tick_data.py`；完整缓存下可用 `python3 tick_data.py --offline`。原始 ZIP 保留在 data/raw，输出只含汇总和对账，不复制逐笔数据。",
        "[官方格式说明](https://github.com/binance/binance-public-data)：现货 trades 为 7 列，2025 年起时间为微秒。", ""]
    for source in result["sources"]:
        lines += [f"- [ZIP]({source['url']}) · [CHECKSUM]({source['checksum_url']})",
            f"  - SHA256：`{source['sha256']}`；字节数 {source['compressed_bytes']:,}；获取时间 {source['fetched_at_utc']}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path(__file__).parent / "data" / "raw")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "tick_data_results")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(f"固定样本 {SYMBOL} trades {DAY}：下载/校验 ...", flush=True)
    archive, source = fetch_ticks(args.raw_dir, args.offline)
    print("读取未聚合逐笔成交 ...", flush=True)
    data, quality, minutes = load_ticks(read_ticks(archive))
    print(f"已验证 {quality['rows']:,} 笔；计算固定 1/5/30 秒响应 ...", flush=True)
    responses = response_summary(data)
    print("独立重读官方 aggTrades 原始格式并对账 ...", flush=True)
    reference_archive, reference_source = real_data.fetch_day(DAY, args.raw_dir, args.offline)
    aggregate_quality, aggregate_minutes = load_aggregate_reference(real_data.read_archive(reference_archive, DAY))
    reconciliation = reconcile(quality, minutes, aggregate_quality, aggregate_minutes)
    checks = reconciliation.pop("checks")
    reconciliation["daily_checks"] = [check for check in checks if check["scope"] == "day"]
    result = {"schema_version": 1, "symbol": SYMBOL, "fixed_date_utc": DAY,
        "sample_selection": "First day of existing sample selected and horizons fixed before this tick diagnostic; minute-level results for this date were previously inspected. Exploratory, NOT held-out.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": {name: real_data.sha256_file(Path(__file__).parent / name) for name in ("tick_data.py", "real_data.py")},
        "method": {"horizons_seconds": list(HORIZONS_SECONDS), "max_lookup_delay_us": MAX_LOOKUP_DELAY_US,
            "future_price": "First trade at or after event timestamp + horizon; first row/ID on time ties",
            "response": "aggressor_sign * 10000 * (future_trade_price / event_trade_price - 1)",
            "aggressor_sign": {"buy": 1, "sell": -1}, "primary_weighting": "each unaggregated trade equally",
            "secondary_weighting": "event ETH quantity", "midpoint_markout": False, "own_fill_model": None, "trading_pnl": None,
            "quote_product_abs_tolerance": QUOTE_ABS_TOLERANCE, "quote_product_rel_tolerance": QUOTE_REL_TOLERANCE},
        "sources": [source, reference_source], "tick_quality": quality, "aggregate_quality": aggregate_quality,
        "responses": responses, "reconciliation": reconciliation}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    real_data.write_csv(args.output / "response_summary.csv", responses)
    real_data.write_csv(args.output / "reconciliation.csv", checks)
    (args.output / "研究结果.md").write_text(render_report(result), encoding="utf-8")
    print(f"完成：{quality['rows']:,} 笔；对账 {reconciliation['check_count']:,} 项，失败 {reconciliation['failed_count']}。", flush=True)


if __name__ == "__main__":
    main()
