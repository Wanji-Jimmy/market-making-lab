"""历史环境条件下的教学做市回放；不是历史限价单成交回测。

实际价格与下一段的首笔主动成交方向只提供外生环境。客户的价格底线、
一单位交易数量、路由给本做市商和即时成交均为假设。离散模型将假设成交
放在桶起点，随后持仓五秒；没有使用实际首笔主动成交在桶内的发生时间。
reference_price 是
最后成交价代理，不是中间价。每个日期从首个可用参考价归一化至100，
并在最后一个可用的下一参考价处按假设成本清仓，不冒称完整日末价格。

报价只读取当前参考价、当前库存、当前imbalance和已冻结的模型系数。
未来方向和价格保存在独立环境对象，不能传入报价函数。
"""

from dataclasses import asdict, dataclass
import math
import random

from simulator import Config, Quote, execute_trade


STRATEGIES = ("fixed", "inventory", "constant_wide", "flow", "intercept_only")
HORIZON_US = 5_000_000
TICK_SIZE = 0.0001
INITIAL_CASH = 10000.0
INVENTORY_LIMIT = 10
HALF_SPREAD_BPS = 2.0
WIDE_HALF_SPREAD_BPS = 3.0
INVENTORY_SKEW_BPS = 0.2
MAX_SIGNAL_BPS = 2.0
LIQUIDATION_HALF_SPREAD_BPS = 1.0


def _finite(name, value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return float(value)


def _integer(name, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _strategy(strategy):
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}")


def _frozen_model(model, strategy):
    """无信号策略不读取模型；截距消融也不读取斜率。"""
    if strategy not in ("flow", "intercept_only"):
        return {"intercept_bps": 0.0, "slope_bps_per_imbalance": 0.0}
    intercept = _finite("intercept_bps", model["intercept_bps"])
    slope = (_finite("slope_bps_per_imbalance", model["slope_bps_per_imbalance"])
             if strategy == "flow" else 0.0)
    return {"intercept_bps": intercept, "slope_bps_per_imbalance": slope}


def _round_quote(price, direction):
    # 微小容差仅避免浮点表示把正好位于tick上的价格推到相邻tick。
    units = price / TICK_SIZE
    ticks = math.floor(units + 1e-9) if direction == "down" else math.ceil(units - 1e-9)
    return round(ticks * TICK_SIZE, 10)


@dataclass(frozen=True)
class QuoteSnapshot:
    """冻结当前可见输入与报价；不包含未来价格、方向或收益标签。"""

    quote: Quote
    strategy: str
    reference_price: float
    inventory: int
    imbalance: float
    intercept_bps: float
    slope_bps_per_imbalance: float
    half_spread_bps: float
    raw_signal_bps: float
    model_signal_bps: float
    inventory_shift_bps: float
    desired_center_shift_bps: float
    applied_center_shift_bps: float
    quoted_half_spread_bps: float
    quoted_center_shift_bps: float


def make_policy_quote(reference_price, inventory, imbalance, model, strategy):
    """以当前参考价为基准使用bps，最后按固定0.0001价格网格舍入。"""
    _strategy(strategy)
    reference = _finite("reference_price", reference_price, TICK_SIZE)
    _integer("inventory", inventory)
    if abs(inventory) > INVENTORY_LIMIT:
        raise ValueError("inventory exceeds the configured limit")
    imbalance = _finite("imbalance", imbalance, -1.0, 1.0)
    frozen = _frozen_model(model, strategy)
    half_bps = WIDE_HALF_SPREAD_BPS if strategy == "constant_wide" else HALF_SPREAD_BPS
    half_spread = reference * half_bps / 10000.0
    if half_spread < TICK_SIZE:
        raise ValueError("normalized reference price is too small for the fixed tick")

    raw_signal = frozen["intercept_bps"] + frozen["slope_bps_per_imbalance"] * imbalance
    signal = max(-MAX_SIGNAL_BPS, min(MAX_SIGNAL_BPS, raw_signal))
    inventory_shift = 0.0 if strategy == "fixed" else -INVENTORY_SKEW_BPS * inventory
    desired_shift_bps = signal + inventory_shift
    desired_shift = reference * desired_shift_bps / 10000.0
    max_shift = half_spread - TICK_SIZE
    applied_shift = max(-max_shift, min(max_shift, desired_shift))
    center = reference + applied_shift
    bid = _round_quote(center - half_spread, "down")
    ask = _round_quote(center + half_spread, "up")
    quote = Quote(bid, ask, clipped=applied_shift != desired_shift)
    return QuoteSnapshot(
        quote=quote, strategy=strategy, reference_price=reference,
        inventory=inventory, imbalance=imbalance,
        intercept_bps=frozen["intercept_bps"],
        slope_bps_per_imbalance=frozen["slope_bps_per_imbalance"],
        half_spread_bps=half_bps, raw_signal_bps=raw_signal, model_signal_bps=signal,
        inventory_shift_bps=inventory_shift, desired_center_shift_bps=desired_shift_bps,
        applied_center_shift_bps=applied_shift / reference * 10000.0,
        quoted_half_spread_bps=(ask - bid) / (2 * reference) * 10000.0,
        quoted_center_shift_bps=((ask + bid) / 2 - reference) / reference * 10000.0,
    )


@dataclass(frozen=True)
class EnvironmentEvent:
    date: str
    decision_timestamp_us: int
    reference_price: float
    next_reference_price: float
    raw_reference_price: float
    raw_next_reference_price: float
    imbalance: float
    target_return_bps: float
    customer_side: str | None
    synthetic_urgency_bps: float | None
    synthetic_limit_price: float | None
    limit_price_clipped: bool
    reference_age_us: int
    next_reference_age_us: int


def prepare_environment(rows, seed=7001, urgency_bps=3.0):
    """先按日期/桶生成随机意愿；策略和成交结果不会影响随机数消耗。"""
    _integer("seed", seed)
    urgency_bps = _finite("urgency_bps", urgency_bps, 0.0)
    rows = list(rows)
    if not rows:
        raise ValueError("rows must contain at least one decision")
    first_raw = _finite("reference_price", rows[0]["reference_price"])
    if first_raw <= 0:
        raise ValueError("reference_price must be positive")
    date = rows[0]["date"]
    if not isinstance(date, str) or not date:
        raise ValueError("date must be a nonempty string")
    rng = random.Random(seed)
    events = []
    previous_timestamp = None
    previous_next_reference = None
    for row in rows:
        if row["date"] != date:
            raise ValueError("replay_day accepts exactly one date; each date starts flat")
        timestamp = _integer("decision_timestamp_us", row["decision_timestamp_us"], 0)
        if previous_timestamp is not None and timestamp < previous_timestamp + HORIZON_US:
            raise ValueError("decision windows must be ordered and non-overlapping")
        reference_raw = _finite("reference_price", row["reference_price"])
        next_raw = _finite("next_reference_price", row["next_reference_price"])
        if reference_raw <= 0 or next_raw <= 0:
            raise ValueError("reference prices must be positive")
        reference = reference_raw / first_raw * 100.0
        next_reference = next_raw / first_raw * 100.0
        if (previous_timestamp is not None and timestamp == previous_timestamp + HORIZON_US
                and not math.isclose(reference, previous_next_reference, rel_tol=0.0, abs_tol=1e-9)):
            raise ValueError("contiguous decision reference must match the previous next reference")
        imbalance = _finite("imbalance", row["imbalance"], -1.0, 1.0)
        target = _finite("target_return_bps", row["target_return_bps"])
        side = row["next_customer_side"]
        if side not in (None, "buy", "sell"):
            raise ValueError("next_customer_side must be buy, sell or None")
        ref_age = _integer("reference_age_us", row["reference_age_us"], 0)
        next_age = _integer("next_reference_age_us", row["next_reference_age_us"], 0)
        urgency = limit = None
        limit_clipped = False
        if side is not None:
            urgency = rng.expovariate(1.0 / urgency_bps) if urgency_bps else 0.0
            raw_limit = reference * (1.0 + (1 if side == "buy" else -1) * urgency / 10000.0)
            limit = max(TICK_SIZE, raw_limit)
            limit_clipped = limit != raw_limit
        events.append(EnvironmentEvent(
            date=date, decision_timestamp_us=timestamp,
            reference_price=reference, next_reference_price=next_reference,
            raw_reference_price=reference_raw, raw_next_reference_price=next_raw,
            imbalance=imbalance, target_return_bps=target, customer_side=side,
            synthetic_urgency_bps=urgency, synthetic_limit_price=limit,
            limit_price_clipped=limit_clipped,
            reference_age_us=ref_age, next_reference_age_us=next_age,
        ))
        previous_timestamp = timestamp
        previous_next_reference = next_reference
    return tuple(events)


def replay_day(rows, model, strategy, seed=7001, fee_bps=0.25, urgency_bps=3.0, record=False):
    """返回{"summary":指标字典,"trace":记录或空列表}，每天独立起止平仓。

    缺失决策区间不发生成交，但库存持续存在。账本先从上次next_reference
    重估至本次reference，再报价/成交/重估至本次next_reference。末尾清仓
    使用最后可用的参考代理价，不表示历史收盘时真的能按此价成交。
    """
    _strategy(strategy)
    fee_bps = _finite("fee_bps", fee_bps, 0.0)
    if not isinstance(record, bool):
        raise ValueError("record must be a boolean")
    frozen_model = _frozen_model(model, strategy)
    events = prepare_environment(rows, seed, urgency_bps)
    cash, inventory = INITIAL_CASH, 0
    equity = peak_equity = INITIAL_CASH
    max_drawdown = 0.0
    previous_reference = events[0].reference_price
    previous_timestamp = events[0].decision_timestamp_us - HORIZON_US
    execution_edge = inventory_revaluation = fees = 0.0
    gap_revaluation = interval_revaluation = 0.0
    max_residual = 0.0
    fills = dealer_buys = dealer_sells = eligible = 0
    quote_clips = blocked_inventory = blocked_cash = 0
    sum_abs_inventory = time_inventory_us = 0.0
    max_abs_inventory = gap_count = gap_duration_us = 0
    half_spread_sum = signal_sum = applied_shift_sum = quoted_shift_sum = 0.0
    trace = []

    def mark_drawdown(value):
        nonlocal peak_equity, max_drawdown
        peak_equity = max(peak_equity, value)
        max_drawdown = max(max_drawdown, peak_equity - value)

    for index, event in enumerate(events, start=1):
        q_before = inventory
        equity_before = equity
        gap_us = event.decision_timestamp_us - previous_timestamp - HORIZON_US
        if gap_us:
            gap_count += 1
            gap_duration_us += gap_us
        gap_part = q_before * (event.reference_price - previous_reference)
        equity_at_decision = cash + q_before * event.reference_price
        gap_residual = abs(equity_at_decision - equity_before - gap_part)
        if gap_residual > 1e-7:
            raise ArithmeticError("gap revaluation does not reconcile with the ledger")
        mark_drawdown(equity_at_decision)

        snapshot = make_policy_quote(event.reference_price, inventory, event.imbalance,
                                     frozen_model, strategy)
        config = Config(
            initial_cash=INITIAL_CASH, initial_inventory=0,
            half_spread=event.reference_price * snapshot.half_spread_bps / 10000.0,
            skew_strength=event.reference_price * INVENTORY_SKEW_BPS / 10000.0,
            inventory_limit=INVENTORY_LIMIT,
            fee_per_trade=event.reference_price * fee_bps / 10000.0,
            liquidation_half_spread=event.reference_price * LIQUIDATION_HALF_SPREAD_BPS / 10000.0,
            tick_size=TICK_SIZE, allow_short=True,
        )
        # 从此行开始，成交环境才使用下一桶的真实主动方向及假设价格底线。
        trade = execute_trade(cash, inventory, snapshot.quote, event.customer_side,
                              event.synthetic_limit_price, config)
        cash, inventory = trade["cash"], trade["inventory"]
        edge = (trade["quantity"] * (event.reference_price - trade["execution_price"])
                if trade["filled"] else 0.0)
        period_part = inventory * (event.next_reference_price - event.reference_price)
        net_change = gap_part + edge + period_part - trade["fee"]
        equity_after_execution = cash + inventory * event.reference_price
        mark_drawdown(equity_after_execution)
        equity = cash + inventory * event.next_reference_price
        residual = abs(equity - equity_before - net_change)
        max_residual = max(max_residual, residual, gap_residual)
        if residual > 1e-7:
            raise ArithmeticError("step P&L does not reconcile with the ledger")
        mark_drawdown(equity)

        execution_edge += edge
        gap_revaluation += gap_part
        interval_revaluation += period_part
        inventory_revaluation += gap_part + period_part
        fees += trade["fee"]
        eligible += int(event.customer_side is not None)
        fills += int(trade["filled"])
        dealer_buys += int(trade["quantity"] == 1)
        dealer_sells += int(trade["quantity"] == -1)
        quote_clips += int(snapshot.quote.clipped)
        blocked_inventory += int(trade["reason"] == "inventory_limit")
        blocked_cash += int(trade["reason"] == "insufficient_cash")
        sum_abs_inventory += abs(inventory)
        time_inventory_us += abs(q_before) * gap_us + abs(inventory) * HORIZON_US
        max_abs_inventory = max(max_abs_inventory, abs(inventory))
        half_spread_sum += snapshot.quoted_half_spread_bps
        signal_sum += snapshot.model_signal_bps
        applied_shift_sum += snapshot.applied_center_shift_bps
        quoted_shift_sum += snapshot.quoted_center_shift_bps

        if record:
            trace.append({
                "step": index, **asdict(event),
                "quote_inputs": {
                    "reference_price": snapshot.reference_price,
                    "inventory": snapshot.inventory,
                    "imbalance": snapshot.imbalance,
                    "intercept_bps": snapshot.intercept_bps,
                    "slope_bps_per_imbalance": snapshot.slope_bps_per_imbalance,
                    "strategy": snapshot.strategy,
                },
                "bid": snapshot.quote.bid, "ask": snapshot.quote.ask,
                "quote_clipped": snapshot.quote.clipped,
                "model_signal_bps": snapshot.model_signal_bps,
                "inventory_shift_bps": snapshot.inventory_shift_bps,
                "applied_center_shift_bps": snapshot.applied_center_shift_bps,
                "quoted_half_spread_bps": snapshot.quoted_half_spread_bps,
                "quoted_center_shift_bps": snapshot.quoted_center_shift_bps,
                "gap_duration_us": gap_us, "inventory_before": q_before,
                **trade, "execution_edge": edge,
                "gap_inventory_revaluation": gap_part,
                "interval_inventory_revaluation": period_part,
                "inventory_revaluation": gap_part + period_part,
                "equity_at_decision": equity_at_decision,
                "equity_after_execution": equity_after_execution,
                "equity": equity, "net_change": net_change,
                "pnl_mtm": equity - INITIAL_CASH,
                "accounting_residual": residual,
            })
        previous_reference = event.next_reference_price
        previous_timestamp = event.decision_timestamp_us

    last_reference = events[-1].next_reference_price
    terminal_cost = abs(inventory) * last_reference * (LIQUIDATION_HALF_SPREAD_BPS + fee_bps) / 10000.0
    cash_after_liquidation = cash + inventory * last_reference - terminal_cost
    pnl_mtm = equity - INITIAL_CASH
    pnl_liquidated = cash_after_liquidation - INITIAL_CASH
    final_residual = abs(pnl_liquidated - (execution_edge + inventory_revaluation - fees - terminal_cost))
    max_residual = max(max_residual, final_residual)
    if final_residual > 1e-7:
        raise ArithmeticError("terminal liquidation P&L does not reconcile")
    max_drawdown_mtm = max_drawdown
    mark_drawdown(cash_after_liquidation)
    elapsed_us = events[-1].decision_timestamp_us + HORIZON_US - events[0].decision_timestamp_us
    summary = {
        "date": events[0].date, "strategy": strategy, "seed": seed,
        "fee_bps": fee_bps, "urgency_bps": float(urgency_bps),
        "intercept_bps": frozen_model["intercept_bps"],
        "slope_bps_per_imbalance": frozen_model["slope_bps_per_imbalance"],
        "decision_count": len(events), "eligible_opportunities": eligible,
        "fill_count": fills, "dealer_buy_count": dealer_buys, "dealer_sell_count": dealer_sells,
        "fills_per_eligible_opportunity": fills / eligible if eligible else 0.0,
        "pnl_liquidated": pnl_liquidated, "pnl_mtm": pnl_mtm,
        "execution_edge": execution_edge, "inventory_revaluation": inventory_revaluation,
        "gap_inventory_revaluation": gap_revaluation,
        "interval_inventory_revaluation": interval_revaluation,
        "fees": fees, "terminal_cost": terminal_cost,
        "mean_abs_inventory": sum_abs_inventory / len(events),
        "time_weighted_mean_abs_inventory": time_inventory_us / elapsed_us,
        "max_abs_inventory": max_abs_inventory,
        "max_drawdown": max_drawdown, "max_drawdown_mtm": max_drawdown_mtm,
        "quote_clips": quote_clips, "blocked_inventory": blocked_inventory,
        "blocked_cash": blocked_cash,
        "mean_quoted_half_spread_bps": half_spread_sum / len(events),
        "mean_center_signal_bps": signal_sum / len(events),
        "mean_applied_center_shift_bps": applied_shift_sum / len(events),
        "mean_quoted_center_shift_bps": quoted_shift_sum / len(events),
        "accounting_residual": max_residual, "final_accounting_residual": final_residual,
        "initial_equity": INITIAL_CASH, "initial_inventory": 0,
        "cash_before_liquidation": cash, "inventory_before_liquidation": inventory,
        "cash_after_liquidation": cash_after_liquidation, "inventory_after_liquidation": 0,
        "initial_raw_reference_price": events[0].raw_reference_price,
        "final_reference_price": last_reference,
        "first_decision_timestamp_us": events[0].decision_timestamp_us,
        "last_valuation_timestamp_us": events[-1].decision_timestamp_us + HORIZON_US,
        "elapsed_seconds": elapsed_us / 1_000_000,
        "gap_count": gap_count, "gap_seconds": gap_duration_us / 1_000_000,
        "max_reference_age_us": max(e.reference_age_us for e in events),
        "max_next_reference_age_us": max(e.next_reference_age_us for e in events),
        "synthetic_limit_clips": sum(e.limit_price_clipped for e in events),
        "terminal_scope": "Last available next reference proxy, not necessarily the UTC day close; assumed liquidation.",
        "fill_timing_assumption": "Hypothetical fills occur at bucket decision; post-fill inventory is held for the full 5 seconds. Actual intrabucket timing is unused.",
        "max_drawdown_scope": "Observed ledger marks at decision, modeled execution, next reference, and terminal liquidation; not an intrabucket market-path maximum.",
        "interpretation": "Historical-environment toy simulation; willingness, size, routing and fills are assumed, not observed execution.",
    }
    return {"summary": summary, "trace": trace}
