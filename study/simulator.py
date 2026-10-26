from dataclasses import dataclass
from decimal import Decimal
import math
import random


def _number(name, value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


def _integer(name, value, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def _tick(price, tick_size, direction="nearest"):
    # Tolerance handles floating-point error at tick boundaries.
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
            raise ValueError("tick_size must be > 0 and half_spread must be >= tick_size")
        if not isinstance(self.allow_short, bool):
            raise ValueError("allow_short must be a boolean")
        if abs(self.initial_inventory) > self.inventory_limit:
            raise ValueError("initial_inventory exceeds inventory_limit")
        if not self.allow_short and self.initial_inventory < 0:
            raise ValueError("initial_inventory cannot be negative when allow_short=False")


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
            raise ValueError("scenario name must be a nonempty string")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("scenario label must be a nonempty string")
        _integer("steps", self.steps, 1)
        _number("drift", self.drift)
        for name in ("volatility", "urgency_scale", "jump_size"):
            _number(name, getattr(self, name), 0)
        for name in ("arrival_probability", "buy_probability",
                     "informed_fraction", "jump_probability"):
            _number(name, getattr(self, name), 0, 1)
        if self.demand_model not in ("exponential", "uniform"):
            raise ValueError("demand_model must be exponential or uniform")


@dataclass(frozen=True)
class RiskParameters:
    """Frozen model inputs, not estimates made from future path observations.

    Volatility is in price units per square-root step, risk aversion in
    inverse price units, and arrival_decay in inverse price units. These
    parameters define an AS-inspired benchmark, not a calibrated optimum.
    """

    risk_aversion: float = 0.0005
    volatility: float = 1.2
    arrival_decay: float = 2 / 3

    def __post_init__(self):
        for name in ("risk_aversion", "volatility", "arrival_decay"):
            _number(name, getattr(self, name), 0)
        if self.risk_aversion <= 0 or self.arrival_decay <= 0:
            raise ValueError("risk_aversion and arrival_decay must be > 0")


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
            raise ValueError("event reference prices must be positive")
        _validate_customer(self.customer_side, self.limit_price)
        if not isinstance(self.informed, bool):
            raise ValueError("informed must be a boolean")
        if self.customer_side is None and self.informed:
            raise ValueError("informed must be False when there is no customer")


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    clipped: bool = False
    requested_shift: float = 0.0
    requested_width: float | None = None

    def __post_init__(self):
        _number("bid", self.bid)
        _number("ask", self.ask)
        if not 0 < self.bid < self.ask:
            raise ValueError("quotes must satisfy 0 < bid < ask")
        if not isinstance(self.clipped, bool):
            raise ValueError("clipped must be a boolean")
        _number("requested_shift", self.requested_shift)
        if self.requested_width is not None:
            _number("requested_width", self.requested_width, 0)


def _validate_customer(side, limit_price):
    if side not in (None, "buy", "sell"):
        raise ValueError("customer_side must be buy, sell or None")
    if side is None:
        if limit_price is not None:
            raise ValueError("limit_price must be None when there is no customer")
    else:
        _number("limit_price", limit_price)
        if limit_price <= 0:
            raise ValueError("customer limit_price must be positive")


def _validate_inventory(inventory, config):
    _integer("inventory", inventory)
    if abs(inventory) > config.inventory_limit:
        raise ValueError("current inventory exceeds inventory_limit")
    if inventory < 0 and not config.allow_short:
        raise ValueError("current inventory cannot be negative when allow_short=False")


def _validate_strategy(strategy):
    if strategy not in ("fixed", "inventory", "as_symmetric", "as_inventory"):
        raise ValueError("unknown quoting strategy")


def make_quotes(mid_price, inventory, config, strategy, *,
                remaining_steps=None, risk=None):
    _validate_strategy(strategy)
    _validate_inventory(inventory, config)
    _number("mid_price", mid_price)
    if mid_price <= config.tick_size:
        raise ValueError("mid_price must exceed tick_size to permit a positive bid")
    if strategy in ("as_symmetric", "as_inventory"):
        if not isinstance(risk, RiskParameters):
            raise ValueError("AS-inspired quotes require explicit RiskParameters")
        _integer("remaining_steps", remaining_steps, 0)
        variance_budget = risk.volatility ** 2 * remaining_steps
        inventory_loading = risk.risk_aversion * variance_budget
        half_width = (0.5 * inventory_loading
                      + math.log1p(risk.risk_aversion / risk.arrival_decay)
                      / risk.risk_aversion)
        shift = -inventory_loading * inventory if strategy == "as_inventory" else 0.0
        return _matched_tick_quote(mid_price, half_width, shift, config.tick_size)
    k = config.skew_strength if strategy == "inventory" else 0.0
    desired_shift = -k * inventory
    # Clip the shift so quotes straddle the mid; round bid down and ask up.
    max_shift = config.half_spread - config.tick_size
    shift = max(-max_shift, min(max_shift, desired_shift))
    centre = mid_price + shift
    raw_bid = centre - config.half_spread
    bid = _tick(max(config.tick_size, raw_bid), config.tick_size, "down")
    ask = _tick(centre + config.half_spread, config.tick_size, "up")
    clipped = shift != desired_shift or raw_bid < config.tick_size
    return Quote(bid, ask, clipped, desired_shift, 2 * config.half_spread)


def _matched_tick_quote(mid_price, half_width, requested_shift, tick_size):
    """Keep the integer total width identical in the AS ablation pair.

    Round half-width outward once, then translate both sides by the same
    integer tick count. Even total width avoids a systematic half-tick
    centre bias for the symmetric control on an on-tick reference price.
    """
    _number("half_width", half_width, 0)
    _number("requested_shift", requested_shift)
    width_ticks = 2 * max(1, math.ceil(half_width / tick_size - 1e-10))
    mid_ticks = mid_price / tick_size
    symmetric_bid = math.floor(mid_ticks - width_ticks / 2 + 1e-10)
    shift_ticks = round(requested_shift / tick_size)
    requested_bid = symmetric_bid + shift_ticks
    # Positive prices and at least one tick away from the current mid.
    lower_bid = max(1, math.ceil(mid_ticks + 1 - 1e-10) - width_ticks)
    upper_bid = math.floor(mid_ticks - 1 + 1e-10)
    if lower_bid > upper_bid:
        raise ValueError("no positive two-sided quote at this tick and mid")
    bid_ticks = max(lower_bid, min(upper_bid, requested_bid))
    bid = float(Decimal(bid_ticks) * Decimal(str(tick_size)))
    ask = float(Decimal(bid_ticks + width_ticks) * Decimal(str(tick_size)))
    return Quote(bid, ask, bid_ticks != requested_bid,
                 requested_shift, 2 * half_width)


def execute_trade(cash, inventory, quote, customer_side, limit_price, config):
    _number("cash", cash, 0)
    _validate_inventory(inventory, config)
    _validate_customer(customer_side, limit_price)
    result = dict(cash=cash, inventory=inventory, filled=False, quantity=0,
                  execution_price=None, fee=0.0, reason="no_customer")
    if customer_side is None:
        return result

    # Quantity is from the maker's perspective: +1 bought, -1 sold.
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
    _integer("seed", seed)
    _number("initial_mid", initial_mid)
    _number("tick_size", tick_size)
    if tick_size <= 0 or initial_mid < max(1.0, 2 * tick_size):
        raise ValueError("tick_size must be > 0 and initial_mid must be >= max(1, 2*tick)")
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
                # Informed customers see the next price; the quoting function cannot.
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


def simulate(events, config, strategy, record=True, *, risk=None):
    _validate_strategy(strategy)
    if not isinstance(record, bool):
        raise ValueError("record must be a boolean")
    if not events:
        raise ValueError("events cannot be empty: initial inventory needs a reference price")
    if not isinstance(events[0], Event):
        raise ValueError("every item in events must be an Event")
    if strategy in ("as_symmetric", "as_inventory") and not isinstance(risk, RiskParameters):
        raise ValueError("AS-inspired simulation requires explicit RiskParameters")
    cash, inventory = float(config.initial_cash), config.initial_inventory
    initial_equity = cash + inventory * events[0].mid_price
    equity = peak_equity = initial_equity
    trace = []
    trades = quote_clips = blocked_inventory = 0
    total_fees = spread_capture = inventory_pnl = 0.0
    inventory_carry_pnl = fill_revaluation_pnl = 0.0
    sum_quote_width = sum_abs_quote_shift = sum_requested_abs_shift = 0.0
    sum_abs_inventory = max_drawdown = 0.0
    max_abs_inventory = abs(inventory)
    previous_event = None

    for event_index, event in enumerate(events):
        if not isinstance(event, Event):
            raise ValueError("every item in events must be an Event")
        if previous_event is not None:
            if event.step <= previous_event.step:
                raise ValueError("event steps must be strictly increasing")
            if not math.isclose(event.mid_price, previous_event.next_mid,
                                rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("discontinuous price path: mid_price must equal the previous next_mid")
        inventory_before = inventory
        quote = make_quotes(event.mid_price, inventory, config, strategy,
                            remaining_steps=len(events) - event_index, risk=risk)
        trade = execute_trade(cash, inventory, quote, event.customer_side,
                              event.limit_price, config)
        cash, inventory = trade["cash"], trade["inventory"]

        # Fill first, then apply the price move to the updated inventory.
        edge = 0.0
        if trade["filled"]:
            edge = trade["quantity"] * (event.mid_price - trade["execution_price"])
        holding_change = inventory * (event.next_mid - event.mid_price)
        carry_change = inventory_before * (event.next_mid - event.mid_price)
        fill_change = trade["quantity"] * (event.next_mid - event.mid_price)
        if not math.isclose(holding_change, carry_change + fill_change,
                            rel_tol=1e-9, abs_tol=1e-7):
            raise ArithmeticError("inventory revaluation split does not reconcile")
        net_change = edge + holding_change - trade["fee"]
        new_equity = cash + inventory * event.next_mid
        if not math.isclose(new_equity - equity, net_change, rel_tol=1e-9, abs_tol=1e-7):
            raise ArithmeticError("ledger mismatch: wealth change must equal spread capture + inventory P&L - fees")
        equity = new_equity
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        trades += int(trade["filled"])
        quote_clips += int(quote.clipped)
        blocked_inventory += int(trade["reason"] == "inventory_limit")
        total_fees += trade["fee"]
        spread_capture += edge
        inventory_pnl += holding_change
        inventory_carry_pnl += carry_change
        fill_revaluation_pnl += fill_change
        actual_width = quote.ask - quote.bid
        actual_shift = (quote.bid + quote.ask) / 2 - event.mid_price
        sum_quote_width += actual_width
        sum_abs_quote_shift += abs(actual_shift)
        sum_requested_abs_shift += abs(quote.requested_shift)
        sum_abs_inventory += abs(inventory)
        max_abs_inventory = max(max_abs_inventory, abs(inventory))
        if record:
            trace.append(dict(
                step=event.step, mid_price=event.mid_price, next_mid=event.next_mid,
                customer_side=event.customer_side, limit_price=event.limit_price,
                informed=event.informed, bid=quote.bid, ask=quote.ask, clipped=quote.clipped,
                requested_shift=quote.requested_shift, requested_width=quote.requested_width,
                actual_shift=actual_shift, actual_width=actual_width,
                cash=cash, inventory_before=inventory_before,
                inventory=inventory, filled=trade["filled"],
                quantity=trade["quantity"], execution_price=trade["execution_price"],
                fee=trade["fee"], reason=trade["reason"], equity=equity,
                pnl=equity - initial_equity, edge=edge, inventory_pnl=holding_change,
                inventory_carry_pnl=carry_change, fill_revaluation_pnl=fill_change,
                net_change=net_change,
            ))
        previous_event = event

    liquidation_cost = abs(inventory) * (config.liquidation_half_spread + config.fee_per_trade)
    final_cash = cash + inventory * events[-1].next_mid - liquidation_cost
    metrics = dict(
        pnl_mtm=equity - initial_equity, pnl_liquidated=final_cash - initial_equity,
        trades=trades, mean_abs_inventory=sum_abs_inventory / len(events),
        max_abs_inventory=max_abs_inventory, final_inventory=inventory,
        total_fees=total_fees, spread_capture=spread_capture, inventory_pnl=inventory_pnl,
        inventory_carry_pnl=inventory_carry_pnl, fill_revaluation_pnl=fill_revaluation_pnl,
        liquidation_cost=liquidation_cost, max_drawdown=max_drawdown,
        quote_clips=quote_clips, blocked_inventory=blocked_inventory,
        mean_quote_width=sum_quote_width / len(events),
        mean_abs_quote_shift=sum_abs_quote_shift / len(events),
        mean_requested_abs_shift=sum_requested_abs_shift / len(events),
        initial_equity=initial_equity, final_equity=equity, final_cash=cash,
        final_cash_after_liquidation=final_cash, inventory_after_liquidation=0,
    )
    return {"metrics": metrics, "trace": trace}
