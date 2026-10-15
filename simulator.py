"""单资产做市模拟：生成价格和客户订单，比较固定价差与库存调整报价。"""

from dataclasses import dataclass
from decimal import Decimal
import math
import random


def _number(name, value, minimum=None, maximum=None):
    """拒绝 NaN、无穷和误传的布尔值，避免错误悄悄进入账本。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是有限数值")
    if not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限数值")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} 必须 <= {maximum}")


def _integer(name, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}")


def _tick(price, tick_size, direction="nearest"):
    """价格在 tick 网格上；极小容差防止 98.8 / 0.01 的浮点贴边误差。"""
    units = price / tick_size
    if direction == "down":
        units = math.floor(units + 1e-10)
    elif direction == "up":
        units = math.ceil(units - 1e-10)
    else:
        units = round(units)
    return float(Decimal(units) * Decimal(str(tick_size)))


@dataclass(frozen=True)
class Config:
    initial_cash: float = 10000.0
    initial_inventory: int = 0
    half_spread: float = 1.0
    skew_strength: float = 0.1
    inventory_limit: int = 10
    fee_per_trade: float = 0.05
    liquidation_half_spread: float = 1.0
    tick_size: float = 0.01
    allow_short: bool = True

    def __post_init__(self):
        _number("initial_cash", self.initial_cash, 0)
        _integer("initial_inventory", self.initial_inventory)
        _integer("inventory_limit", self.inventory_limit, 1)
        for name in ("half_spread", "skew_strength", "fee_per_trade",
                     "liquidation_half_spread", "tick_size"):
            _number(name, getattr(self, name), 0)
        if self.tick_size <= 0 or self.half_spread < self.tick_size:
            raise ValueError("tick_size 必须 > 0，half_spread 必须 >= tick_size")
        if not isinstance(self.allow_short, bool):
            raise ValueError("allow_short 必须是布尔值")
        if abs(self.initial_inventory) > self.inventory_limit:
            raise ValueError("initial_inventory 超出库存上限")
        if not self.allow_short and self.initial_inventory < 0:
            raise ValueError("allow_short=False 时不能从负库存开始")


@dataclass(frozen=True)
class Scenario:
    name: str
    label: str
    steps: int = 300
    volatility: float = 0.2
    drift: float = 0.0
    arrival_probability: float = 0.8
    buy_probability: float = 0.5
    urgency_scale: float = 1.5
    informed_fraction: float = 0.0
    jump_probability: float = 0.0
    jump_size: float = 0.0
    demand_model: str = "exponential"

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("scenario name 必须是非空字符串")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("scenario label 必须是非空字符串")
        _integer("steps", self.steps, 1)
        _number("drift", self.drift)
        for name in ("volatility", "urgency_scale", "jump_size"):
            _number(name, getattr(self, name), 0)
        for name in ("arrival_probability", "buy_probability",
                     "informed_fraction", "jump_probability"):
            _number(name, getattr(self, name), 0, 1)
        if self.demand_model not in ("exponential", "uniform"):
            raise ValueError("demand_model 只能是 exponential 或 uniform")


@dataclass(frozen=True)
class Event:
    step: int
    mid_price: float
    next_mid: float
    customer_side: str | None
    limit_price: float | None
    informed: bool = False

    def __post_init__(self):
        _integer("step", self.step, 0)
        _number("mid_price", self.mid_price)
        _number("next_mid", self.next_mid)
        if self.mid_price <= 0 or self.next_mid <= 0:
            raise ValueError("事件参考价必须为正")
        _validate_customer(self.customer_side, self.limit_price)
        if not isinstance(self.informed, bool):
            raise ValueError("informed 必须是布尔值")
        if self.customer_side is None and self.informed:
            raise ValueError("没有客户时 informed 必须为 False")


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    clipped: bool = False

    def __post_init__(self):
        _number("bid", self.bid)
        _number("ask", self.ask)
        if not 0 < self.bid < self.ask:
            raise ValueError("报价必须满足 0 < bid < ask")
        if not isinstance(self.clipped, bool):
            raise ValueError("clipped 必须是布尔值")


def _validate_customer(side, limit_price):
    if side not in (None, "buy", "sell"):
        raise ValueError("customer_side 只能是 buy、sell 或 None")
    if side is None:
        if limit_price is not None:
            raise ValueError("没有客户时 limit_price 必须为 None")
    else:
        _number("limit_price", limit_price)
        if limit_price <= 0:
            raise ValueError("客户 limit_price 必须为正")


def _validate_inventory(inventory, config):
    _integer("inventory", inventory)
    if abs(inventory) > config.inventory_limit:
        raise ValueError("当前库存已经超出 inventory_limit")
    if inventory < 0 and not config.allow_short:
        raise ValueError("allow_short=False 时当前库存不能为负")


def _validate_strategy(strategy):
    if strategy not in ("fixed", "inventory"):
        raise ValueError("strategy 只能是 fixed 或 inventory")


def make_quotes(mid_price, inventory, config, strategy):
    """库存偏多就降低报价中心；限制偏移，使买卖报价始终包围参考价。"""
    _validate_strategy(strategy)
    _validate_inventory(inventory, config)
    _number("mid_price", mid_price)
    if mid_price <= config.tick_size:
        raise ValueError("mid_price 必须大于 tick_size 才能提供正的买价")
    k = config.skew_strength if strategy == "inventory" else 0.0
    desired_shift = -k * inventory
    max_shift = config.half_spread - config.tick_size
    shift = max(-max_shift, min(max_shift, desired_shift))
    centre = mid_price + shift
    raw_bid = centre - config.half_spread
    bid = _tick(max(config.tick_size, raw_bid), config.tick_size, "down")
    ask = _tick(centre + config.half_spread, config.tick_size, "up")
    clipped = shift != desired_shift or raw_bid < config.tick_size
    return Quote(bid, ask, clipped)


def execute_trade(cash, inventory, quote, customer_side, limit_price, config):
    """客户买对应做市商卖；按做市商报价成交，每次一股，手续费只收成交。"""
    _number("cash", cash, 0)
    _validate_inventory(inventory, config)
    _validate_customer(customer_side, limit_price)
    result = dict(cash=cash, inventory=inventory, filled=False, quantity=0,
                  execution_price=None, fee=0.0, reason="no_customer")
    if customer_side is None:
        return result

    # quantity 按做市商方向：买入 +1，卖出 -1。
    quantity = -1 if customer_side == "buy" else 1
    price = quote.ask if quantity == -1 else quote.bid
    accepted = price <= limit_price if quantity == -1 else price >= limit_price
    if not accepted:
        result["reason"] = "price_not_accepted"
        return result
    new_inventory = inventory + quantity
    lower_limit = -config.inventory_limit if config.allow_short else 0
    if not lower_limit <= new_inventory <= config.inventory_limit:
        result["reason"] = "inventory_limit"
        return result

    new_cash = cash - quantity * price - config.fee_per_trade
    if new_cash < -1e-9:
        result["reason"] = "insufficient_cash"
        return result
    result.update(cash=max(0.0, new_cash), inventory=new_inventory, filled=True,
                  quantity=quantity, execution_price=price,
                  fee=config.fee_per_trade, reason="filled")
    return result


def generate_market(scenario, seed, initial_mid=100.0, tick_size=0.01):
    """先生成完整外生环境；复用事件列表即可让两个策略面对同一市场。

    informed 客户使用下一次价格变化加噪声构造，是人为的逆向选择压力场景。
    此信息留在环境里，不传给 make_quotes；不能称为真实订单流模型。
    """
    _integer("seed", seed)
    _number("initial_mid", initial_mid)
    _number("tick_size", tick_size)
    if tick_size <= 0 or initial_mid < max(1.0, 2 * tick_size):
        raise ValueError("tick_size 必须 > 0，initial_mid 必须 >= max(1, 2*tick)")
    rng = random.Random(seed)
    mid = _tick(initial_mid, tick_size)
    price_floor = max(2, math.ceil(1.0 / tick_size)) * tick_size
    events = []
    for step in range(1, scenario.steps + 1):
        change = scenario.drift + rng.gauss(0, scenario.volatility)
        if rng.random() < scenario.jump_probability:
            change += rng.choice((-1, 1)) * scenario.jump_size
        next_mid = max(price_floor, _tick(mid + change, tick_size))
        side, limit_price, informed = None, None, False
        if rng.random() < scenario.arrival_probability:
            informed = rng.random() < scenario.informed_fraction
            if informed:
                signal = next_mid - mid + rng.gauss(0, 0.1)
                side = "buy" if signal >= 0 else "sell"
                limit_price = max(tick_size, mid + signal)
            else:
                side = "buy" if rng.random() < scenario.buy_probability else "sell"
                if scenario.urgency_scale == 0:
                    urgency = 0.0
                elif scenario.demand_model == "exponential":
                    urgency = rng.expovariate(1 / scenario.urgency_scale)
                else:
                    urgency = rng.uniform(0, 2 * scenario.urgency_scale)
                direction = 1 if side == "buy" else -1
                limit_price = max(tick_size, mid + direction * urgency)
        events.append(Event(step, mid, next_mid, side, limit_price, informed))
        mid = next_mid
    return events


def simulate(events, config, strategy, record=True):
    """逐步记账；同时报告期末按参考价估值和支付清仓成本后的结果。"""
    _validate_strategy(strategy)
    if not isinstance(record, bool):
        raise ValueError("record 必须是布尔值")
    if not events:
        raise ValueError("events 不能为空，初始库存需要一个参考价才能估值")
    if not isinstance(events[0], Event):
        raise ValueError("events 中每一项都必须是 Event")
    cash, inventory = float(config.initial_cash), config.initial_inventory
    initial_equity = cash + inventory * events[0].mid_price
    equity = peak_equity = initial_equity
    trace = []
    trades = quote_clips = blocked_inventory = 0
    total_fees = spread_capture = inventory_pnl = 0.0
    sum_abs_inventory = max_drawdown = 0.0
    max_abs_inventory = abs(inventory)
    previous_event = None

    for event in events:
        if not isinstance(event, Event):
            raise ValueError("events 中每一项都必须是 Event")
        if previous_event is not None:
            if event.step <= previous_event.step:
                raise ValueError("事件 step 必须严格递增")
            if not math.isclose(event.mid_price, previous_event.next_mid,
                                rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("价格路径不连续：当前 mid_price 必须等于上次 next_mid")
        quote = make_quotes(event.mid_price, inventory, config, strategy)
        trade = execute_trade(cash, inventory, quote, event.customer_side,
                              event.limit_price, config)
        cash, inventory = trade["cash"], trade["inventory"]

        # 顺序很重要：先成交，再由成交后的库存承担下一次价格变化。
        edge = 0.0
        if trade["filled"]:
            edge = trade["quantity"] * (event.mid_price - trade["execution_price"])
        holding_change = inventory * (event.next_mid - event.mid_price)
        net_change = edge + holding_change - trade["fee"]
        new_equity = cash + inventory * event.next_mid
        if not math.isclose(new_equity - equity, net_change, rel_tol=1e-9, abs_tol=1e-7):
            raise ArithmeticError("账本不平：财富变化不等于价差收益＋库存损益－费用")
        equity = new_equity
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        trades += int(trade["filled"])
        quote_clips += int(quote.clipped)
        blocked_inventory += int(trade["reason"] == "inventory_limit")
        total_fees += trade["fee"]
        spread_capture += edge
        inventory_pnl += holding_change
        sum_abs_inventory += abs(inventory)
        max_abs_inventory = max(max_abs_inventory, abs(inventory))
        if record:
            trace.append(dict(
                step=event.step, mid_price=event.mid_price, next_mid=event.next_mid,
                customer_side=event.customer_side, limit_price=event.limit_price,
                informed=event.informed, bid=quote.bid, ask=quote.ask, clipped=quote.clipped,
                cash=cash, inventory=inventory, filled=trade["filled"],
                quantity=trade["quantity"], execution_price=trade["execution_price"],
                fee=trade["fee"], reason=trade["reason"], equity=equity,
                pnl=equity - initial_equity, edge=edge, inventory_pnl=holding_change,
                net_change=net_change,
            ))
        previous_event = event

    # 强制平仓按每股固定滑价加手续费；多头卖出、空头买回都要支付。
    liquidation_cost = abs(inventory) * (config.liquidation_half_spread + config.fee_per_trade)
    final_cash = cash + inventory * events[-1].next_mid - liquidation_cost
    metrics = dict(
        pnl_mtm=equity - initial_equity, pnl_liquidated=final_cash - initial_equity,
        trades=trades, mean_abs_inventory=sum_abs_inventory / len(events),
        max_abs_inventory=max_abs_inventory, final_inventory=inventory,
        total_fees=total_fees, spread_capture=spread_capture, inventory_pnl=inventory_pnl,
        liquidation_cost=liquidation_cost, max_drawdown=max_drawdown,
        quote_clips=quote_clips, blocked_inventory=blocked_inventory,
        initial_equity=initial_equity, final_equity=equity, final_cash=cash,
        final_cash_after_liquidation=final_cash, inventory_after_liquidation=0,
    )
    return {"metrics": metrics, "trace": trace}
