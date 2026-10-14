"""公开成交数据诊断；不模拟自己的成交、不计算交易利润。

固定样本在读取结果前声明：ETHUSDT，2025-09-01 至 2025-09-03（UTC）。
只用 Python 标准库。运行 python3 real_data.py 下载、校验和生成报告。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import ssl
import statistics
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


SYMBOL = "ETHUSDT"
DATES = ("2025-09-01", "2025-09-02", "2025-09-03")
BASE_URL = "https://data.binance.vision/data/spot/daily/aggTrades"
DOC_URL = "https://github.com/binance/binance-public-data"
API_DOC_URL = "https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market"
GROUPS = ("[-1,-0.5)", "[-0.5,-0.1)", "[-0.1,0.1]", "(0.1,0.5]", "(0.5,1]")
MINUTE_US = 60_000_000
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_CSV_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Trade:
    trade_id: int
    price: float
    quantity: float
    first_trade_id: int
    last_trade_id: int
    timestamp_us: int
    buyer_is_maker: bool
    best_match: bool

    @property
    def aggressor_sign(self) -> int:
        # 买方是被动挂单方，意味着主动方是卖方。
        return -1 if self.buyer_is_maker else 1


def day_bounds(day: str) -> tuple[int, int]:
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc)
    return int(start.timestamp()) * 1_000_000, int((start + timedelta(days=1)).timestamp()) * 1_000_000


def parse_bool(value: str) -> bool:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    raise ValueError(f"无效布尔值：{value!r}")


def parse_trade(row: list[str], day: str) -> Trade:
    """严格读取官方 8 列格式；2025 年现货时间戳必须是微秒。"""
    if len(row) != 8:
        raise ValueError(f"需要 8 列，实际 {len(row)} 列")
    trade = Trade(int(row[0]), float(row[1]), float(row[2]), int(row[3]),
                  int(row[4]), int(row[5]), parse_bool(row[6]), parse_bool(row[7]))
    if not all(math.isfinite(x) and x > 0 for x in (trade.price, trade.quantity)):
        raise ValueError("价格和数量必须是有限正数")
    if min(trade.trade_id, trade.first_trade_id, trade.last_trade_id) < 0:
        raise ValueError("成交 ID 不得为负")
    if trade.first_trade_id > trade.last_trade_id:
        raise ValueError("原始成交 ID 范围颠倒")
    start, end = day_bounds(day)
    if not start <= trade.timestamp_us < end:
        hint = "；疑似毫秒而非官方 2025 年微秒格式" if start <= trade.timestamp_us * 1000 < end else ""
        raise ValueError(f"时间戳不属于指定 UTC 日期 {day}{hint}")
    return trade


def imbalance_group(value: float) -> str:
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("成交量不平衡必须在 [-1, 1]")
    if value < -0.5:
        return GROUPS[0]
    if value < -0.1:
        return GROUPS[1]
    if value <= 0.1:
        return GROUPS[2]
    if value <= 0.5:
        return GROUPS[3]
    return GROUPS[4]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksum(path: Path, checksum_text: str) -> str:
    parts = checksum_text.strip().split()
    if len(parts) != 2 or parts[1].lstrip("*") != path.name:
        raise ValueError("CHECKSUM 文件名或格式不符")
    expected = parts[0].lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("CHECKSUM 不是 SHA256")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"SHA256 不匹配：{path.name}")
    return actual


def download(url: str, destination: Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
    """流式下载到临时文件，成功后替换；不读取账户或 API key。"""
    request = urllib.request.Request(url, headers={"User-Agent": "market-making-research/1.0"})
    temporary = destination.with_suffix(destination.suffix + ".part")
    size = 0
    context = ssl.create_default_context()
    # 部分 macOS 的 python.org 安装未配置 CA；补载操作系统证书包，保留完整 TLS 验证。
    if not ssl.get_default_verify_paths().cafile:
        for system_bundle in (Path("/etc/ssl/cert.pem"), Path("/etc/ssl/certs/ca-certificates.crt")):
            if system_bundle.is_file():
                context.load_verify_locations(cafile=str(system_bundle))
                break
    try:
        with urllib.request.urlopen(request, timeout=45, context=context) as response, temporary.open("wb") as output:
            if response.headers.get("Content-Length") and int(response.headers["Content-Length"]) > max_bytes:
                raise ValueError("远程文件超过预设大小上限")
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise ValueError("下载超过预设大小上限")
                output.write(block)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_day(day: str, raw_dir: Path, offline: bool = False) -> tuple[Path, dict]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    name = f"{SYMBOL}-aggTrades-{day}.zip"
    url = f"{BASE_URL}/{SYMBOL}/{name}"
    archive = raw_dir / name
    checksum = raw_dir / (name + ".CHECKSUM")
    receipt = raw_dir / (name + ".download.json")
    cache_hit = archive.exists() and checksum.exists() and receipt.exists()
    if not cache_hit:
        if offline:
            raise FileNotFoundError(f"离线缓存不完整：{name}")
        download(url + ".CHECKSUM", checksum, max_bytes=4096)
        download(url, archive)
        digest = verify_checksum(archive, checksum.read_text(encoding="utf-8"))
        info = {"date": day, "url": url, "checksum_url": url + ".CHECKSUM",
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": digest,
                "compressed_bytes": archive.stat().st_size}
        receipt.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    info = json.loads(receipt.read_text(encoding="utf-8"))
    digest = verify_checksum(archive, checksum.read_text(encoding="utf-8"))
    if info.get("sha256") != digest or info.get("url") != url:
        raise ValueError("下载记录和已校验文件不一致")
    return archive, info


def read_archive(archive: Path, day: str) -> Iterable[Trade]:
    # 不解压任何路径；只读取精确命名的单个 CSV 成员。
    expected_name = f"{SYMBOL}-aggTrades-{day}.csv"
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist() != [expected_name]:
            raise ValueError("ZIP 必须只包含预期命名的单个 CSV")
        if bundle.getinfo(expected_name).file_size > MAX_CSV_BYTES:
            raise ValueError("CSV 超过预设大小上限")
        with bundle.open(expected_name) as binary, io.TextIOWrapper(binary, encoding="utf-8", newline="") as text:
            for line_number, row in enumerate(csv.reader(text), 1):
                try:
                    yield parse_trade(row, day)
                except ValueError as exc:
                    raise ValueError(f"{archive.name} 第 {line_number} 行：{exc}") from exc


def aggregate_day(trades: Iterable[Trade], day: str) -> tuple[list[dict], dict]:
    """分钟标签是 UTC 左端点，区间为 [t, t+60 秒)，不填充缺失分钟。"""
    buckets: dict[int, dict] = {}
    count = gap_events = missing_ids = buyer_maker_count = 0
    previous_id = previous_time = first_id = first_time = None
    for trade in trades:
        if previous_id is not None:
            if trade.trade_id <= previous_id:
                kind = "重复" if trade.trade_id == previous_id else "倒序"
                raise ValueError(f"{day} aggregate ID {kind}：{trade.trade_id}；分析停止")
            gap = trade.trade_id - previous_id - 1
            gap_events += int(gap > 0)
            missing_ids += gap
            if trade.timestamp_us < previous_time:
                raise ValueError(f"{day} 时间戳倒序；分析停止")
        else:
            first_id, first_time = trade.trade_id, trade.timestamp_us
        previous_id, previous_time = trade.trade_id, trade.timestamp_us
        count += 1
        buyer_maker_count += int(trade.buyer_is_maker)
        minute = trade.timestamp_us // MINUTE_US
        if minute not in buckets:
            buckets[minute] = {"date": day, "minute_index": minute,
                "minute_utc": datetime.fromtimestamp(minute * 60, timezone.utc).isoformat(),
                "aggregate_rows": 0, "first_trade_price": trade.price,
                "last_trade_price": trade.price, "buy_volume_eth": 0.0, "sell_volume_eth": 0.0}
        bucket = buckets[minute]
        bucket["aggregate_rows"] += 1
        bucket["last_trade_price"] = trade.price
        key = "sell_volume_eth" if trade.buyer_is_maker else "buy_volume_eth"
        bucket[key] += trade.quantity
    if not count:
        raise ValueError(f"{day} 没有成交行")
    for minute, bucket in buckets.items():
        buy, sell = bucket["buy_volume_eth"], bucket["sell_volume_eth"]
        bucket["volume_eth"] = buy + sell
        bucket["imbalance"] = (buy - sell) / (buy + sell)
        bucket["imbalance_group"] = imbalance_group(bucket["imbalance"])
        bucket["same_minute_return_bps"] = 10_000 * (bucket["last_trade_price"] / bucket["first_trade_price"] - 1)
        following = buckets.get(minute + 1)
        bucket["next_minute_return_bps"] = (10_000 * (following["last_trade_price"] / bucket["last_trade_price"] - 1)
                                                  if following else None)
    rows = list(buckets.values())
    quality = {"date": day, "aggregate_rows": count, "first_aggregate_id": first_id,
        "last_aggregate_id": previous_id, "id_gap_events": gap_events, "missing_aggregate_ids": missing_ids,
        "duplicate_aggregate_ids": 0, "out_of_order_ids": 0, "out_of_order_timestamps": 0,
        "timestamp_unit": "microseconds", "first_timestamp_us": first_time, "last_timestamp_us": previous_time,
        "buyer_is_maker_rows": buyer_maker_count, "active_minutes": len(rows),
        "missing_minutes": 1440 - len(rows),
        "minutes_with_next_return": sum(x["next_minute_return_bps"] is not None for x in rows),
        "excluded_missing_or_terminal_next_minutes": sum(x["next_minute_return_bps"] is None for x in rows),
        "first_trade_price": rows[0]["first_trade_price"], "last_trade_price": rows[-1]["last_trade_price"],
        "volume_eth": sum(x["volume_eth"] for x in rows)}
    return rows, quality


def group_summary(rows: list[dict], scope: str) -> list[dict]:
    output = []
    for group in GROUPS:
        selected = [row for row in rows if row["imbalance_group"] == group]
        same = [row["same_minute_return_bps"] for row in selected]
        following = [row["next_minute_return_bps"] for row in selected if row["next_minute_return_bps"] is not None]
        output.append({"scope": scope, "imbalance_group": group, "minutes": len(selected),
            "next_return_count": len(following),
            "same_minute_mean_bps": statistics.mean(same) if same else None,
            "next_minute_mean_bps": statistics.mean(following) if following else None,
            "next_minute_median_bps": statistics.median(following) if following else None,
            "next_minute_positive_fraction": sum(x > 0 for x in following) / len(following) if following else None})
    return output


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render_report(result: dict) -> str:
    number = lambda x: "—" if x is None else f"{x:+.3f}"
    lines = ["# 真实公开成交数据：主动买卖不平衡与随后价格变化", "",
        "这是模拟器旁边的一项实证诊断。研究问题：一分钟内主动买入较多，是否意味着下一分钟价格仍会上涨？", "",
        f"样本在下载和分析之前固定为 **{SYMBOL}，{', '.join(DATES)}（UTC）**。未根据结果选择或替换日期。", "",
        f"共读取 **{result['total_aggregate_rows']:,} 条聚合成交记录**，形成 {result['active_minutes']:,} 个有成交的分钟；其中 {result['next_return_count']:,} 个有同日紧邻下一分钟。聚合记录不等于原始成交笔数。", "",
        "## 怎样计算", "",
        "官方 isBuyerMaker=true 表示买方是被动挂单方，因此主动成交方为卖方。以 ETH 数量累计主动买量 B、主动卖量 S，计算 I=(B−S)/(B+S)。这里不是订单簿深度不平衡。", "",
        "每个 UTC 分钟使用 [t,t+60秒)；P_t 是该分钟最后一笔成交价。下一分钟变化为 10,000×(P_{t+1}/P_t−1)，单位 bp（1 bp=0.01%）。I_t 完整形成后才观察下一分钟结果，时间上不偷看未来；这种对齐不代表因果识别。", "",
        "同分钟变化另用该分钟首笔到末笔成交价计算，它和 I_t 共享同一时间区间，不能冒充预测。缺失分钟不前向填充，无法匹配紧邻下一分钟及每天最后一分钟均不进入下一分钟统计，也不跨日连接。", "",
        "## 汇总结果", "",
        "| 成交量不平衡分组 | 分钟数 | 下一分钟样本数 | 同分钟平均变化 bp | 下一分钟平均变化 bp | 下一分钟中位数 bp |", "|---|---:|---:|---:|---:|---:|"]
    for item in result["groups"]:
        if item["scope"] == "all":
            lines.append(f"| {item['imbalance_group']} | {item['minutes']} | {item['next_return_count']} | {number(item['same_minute_mean_bps'])} | {number(item['next_minute_mean_bps'])} | {number(item['next_minute_median_bps'])} |")
    lines += ["", "这些是描述性分组均值，不是收益策略或显著性检验。只展示正结果会误导；完整五组、三个交易日均保留。分钟具有时间依赖，未使用把每分钟视作独立样本的置信区间。", "",
        "## 每日结果与数据质量", "", "| UTC 日期 | 聚合记录数 | 活跃分钟 | 下一分钟样本 | ID 缺口数 | 重复数 |", "|---|---:|---:|---:|---:|---:|"]
    for day in result["days"]:
        lines.append(f"| {day['date']} | {day['aggregate_rows']:,} | {day['active_minutes']} | {day['minutes_with_next_return']} | {day['missing_aggregate_ids']} | {day['duplicate_aggregate_ids']} |")
    lines += ["", "| UTC 日期 | 不平衡分组 | 下一分钟样本数 | 下一分钟平均变化 bp |", "|---|---|---:|---:|"]
    for item in result["groups"]:
        if item["scope"] != "all":
            lines.append(f"| {item['scope']} | {item['imbalance_group']} | {item['next_return_count']} | {number(item['next_minute_mean_bps'])} |")
    lines += ["", "## 能说明什么、不能说明什么", "",
        "这个模块把模型里的单笔虚构客户，连接到现实中成交流量的测量问题；它检验数据字段、方向、时间对齐及同时间相关和随后表现之间的差别。", "",
        "但它只有三个加密资产交易日，不能推广到其他时期、股票或银行 S&T。没有最优买卖报价、深度、排队位置和自己的订单，因此不计算自己的成交概率、不模拟限价单成交、不报告交易利润，也不把成交价变化称为中间价 markout。", "",
        "最后成交价可能受到买卖价跳动及微观结构噪声影响。分组差异也可能来自波动状态或其他混杂因素；未做时间外检验，不作 alpha 或获利结论。若要用于策略，需要先扩大预先定义的样本、设开发和留出期，再加入可验证的报价、费用与成交模型。", "",
        "## 来源与复现", "",
        f"- [Binance 官方公开数据说明]({DOC_URL})：2025-01-01 起现货归档时间戳为微秒，提供 ZIP 的 SHA256 CHECKSUM。",
        f"- [官方 aggTrades 字段说明]({API_DOC_URL})。",
        "- 运行 `python3 real_data.py`；有完整缓存时可用 `python3 real_data.py --offline`。",
        "- 源码 SHA256、各 ZIP SHA256、获取时间、行数见 `summary.json`；逐分钟特征见 `minute_features.csv`，全部分组见 `group_summary.csv`。",
        "- 归档按文件名精确读取，没有把 ZIP 内任意路径解压到磁盘。遇到重复、倒序、无效数值或错误日期即停止；有效结果中的零异常是检查所得。", ""]
    for source in result["sources"]:
        lines += [f"### {source['date']}", "", f"- [原始 ZIP]({source['url']}) · [官方 CHECKSUM]({source['checksum_url']})",
            f"- 获取时间：{source['fetched_at_utc']}", f"- 压缩文件字节数：{source['compressed_bytes']:,}",
            f"- SHA256：`{source['sha256']}`", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path(__file__).parent / "data" / "raw")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "real_data_results")
    parser.add_argument("--offline", action="store_true", help="只使用已下载且校验通过的缓存")
    args = parser.parse_args()
    minutes, days, sources, groups = [], [], [], []
    for day in DATES:
        print(f"读取固定样本 {SYMBOL} {day} ...", flush=True)
        archive, source = fetch_day(day, args.raw_dir, args.offline)
        rows, quality = aggregate_day(read_archive(archive, day), day)
        minutes.extend(rows)
        days.append(quality)
        sources.append(source)
        groups.extend(group_summary(rows, day))
        print(f"  SHA256 通过；{quality['aggregate_rows']:,} 行；{quality['active_minutes']} 个分钟。", flush=True)
    groups.extend(group_summary(minutes, "all"))
    result = {"schema_version": 1, "symbol": SYMBOL, "fixed_dates_utc": list(DATES),
        "sample_selection": "Dates and symbol fixed before inspecting downloaded data; no result-based replacements.",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sha256_file(Path(__file__)), "timestamp_unit": "microseconds",
        "method": {"imbalance": "(aggressor_buy_ETH - aggressor_sell_ETH) / total_ETH",
            "interval": "UTC [minute_start, minute_start+60 seconds)",
            "next_return_bps": "10000 * (next adjacent same-day minute last trade price / current last trade price - 1)",
            "same_return_bps": "10000 * (current minute last trade price / current minute first trade price - 1)",
            "groups": list(GROUPS), "no_forward_fill": True, "no_cross_day_join": True,
            "price_type": "last executed trade, NOT midpoint", "own_fill_model": None, "trading_pnl": None},
        "total_aggregate_rows": sum(x["aggregate_rows"] for x in days), "active_minutes": len(minutes),
        "next_return_count": sum(x["minutes_with_next_return"] for x in days),
        "sources": sources, "days": days, "groups": groups}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_csv(args.output / "minute_features.csv", minutes)
    write_csv(args.output / "group_summary.csv", groups)
    (args.output / "研究结果.md").write_text(render_report(result), encoding="utf-8")
    print(f"完成：{args.output / '研究结果.md'}", flush=True)


if __name__ == "__main__":
    main()
