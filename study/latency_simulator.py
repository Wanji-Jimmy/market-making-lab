"""One-unit passive quotes with delayed atomic cancel-and-replace.

Time is the zero-based event-row index, independent of ``Event.step``. A
warm-start pair is live at row zero; each row requests a replacement pair
using only its current mid, current inventory, and remaining horizon.
Requests become effective after a fixed number of rows. Both sides are
replaced together, and a filled side stays depleted until replacement.
Because requests are pipelined unconditionally every row, replacements
activate every row after t >= latency; latency is not a per-fill replenishment
gap. The initial pair is already live as a warm-start assumption.

This is a stylised latency experiment, not an exchange, queue-position,
independent order-entry/cancel-feed, or full FIFO matching model. The two
buffered AS-inspired policies add a pre-chosen heuristic Brownian price
buffer, sigma * sqrt(latency), to the requested half-width. This buffer is
neither calibrated nor claimed optimal.

The preserved cash/inventory pre-fill veto is an idealised immediate
simulator risk gate. A rejected fill leaves live quantity unchanged and may
trade later. It is not exchange order acceptance or native risk reservation,
and may overstate control over real pending orders. Zero-latency recovery of
v2 depends on one exogenous customer per event and a refresh every row; it
does not establish equivalence to a general limit-order-book/FIFO model.
"""

from collections import deque
from dataclasses import dataclass
import math

from simulator import (
    Config, Event, RiskParameters, _integer, _matched_tick_quote, _number,
    execute_trade, make_quotes,
)


CORE_STRATEGIES = ("fixed", "inventory", "as_symmetric", "as_inventory")
BUFFERED_STRATEGIES = ("buffered_as_symmetric", "buffered_as_inventory")
STRATEGIES = CORE_STRATEGIES + BUFFERED_STRATEGIES


@dataclass(frozen=True)
class _Request:
    pair_id: int
    information_step: int
    due_step: int
    quote: object


@dataclass
class _ActivePair:
    pair_id: int
    information_step: int
    activation_step: int
    quote: object
    bid_live: bool = True
    ask_live: bool = True


def _requested_quote(mid, inventory, config, strategy, remaining_steps,
                     risk, latency_steps, buffer_multiplier):
    """Use raw AS width/shift before one final matched-tick construction."""
    core = strategy.removeprefix("buffered_")
    base = make_quotes(mid, inventory, config, core,
                       remaining_steps=remaining_steps, risk=risk)
    if strategy not in BUFFERED_STRATEGIES:
        return base
    buffer = buffer_multiplier * risk.volatility * math.sqrt(latency_steps)
    return _matched_tick_quote(mid, base.requested_width / 2 + buffer,
                               base.requested_shift, config.tick_size)


def _no_live_order(cash, inventory):
    return dict(cash=cash, inventory=inventory, filled=False, quantity=0,
                execution_price=None, fee=0.0, reason="no_live_order")


def simulate_latency(events, config, strategy, *, latency_steps, risk=None,
                     buffer_multiplier=1.0, record=True):
    """Return v2-compatible accounting plus passive-order diagnostics.

    Each row first activates due requests, then submits one new request.
    With zero latency that request activates before the customer arrives.
    The warm pair uses initial mid/inventory and the full remaining horizon,
    with information_step=activation_step=pair_id=0.
    Requests are pipelined every row, so once t >= latency_steps a new pair
    activates each row; a fill does not trigger its own latency-step wait.

    The v2 cash/inventory pre-fill veto remains an idealised immediate
    simulator risk gate: a rejection retains the live quantity, which may
    trade later. This is not exchange order acceptance or native risk
    reservation and may overstate real control over pending orders. Exact
    zero-latency recovery assumes one exogenous customer per event and
    refresh every row, not a general limit-order-book/FIFO model.

    ``quote_requests`` and ``quote_activations`` exclude the warm pair;
    ``warm_start_activations`` records it separately. ``quote_clips`` and
    ``mean_requested_abs_shift`` describe the per-row requests. Quote width
    and actual shift describe the last activated pair, even if one or both
    of its sides have been consumed. ``blocked_no_order`` counts customers
    whose required side is depleted, before any hypothetical price check.
    Fill-age means are None when there are no fills. The record flag changes
    trace retention only. No terminal pending request is activated early.
    """
    if strategy not in STRATEGIES:
        raise ValueError("unknown latency quoting strategy")
    if not isinstance(config, Config):
        raise ValueError("config must be a Config")
    _integer("latency_steps", latency_steps, 0)
    _number("buffer_multiplier", buffer_multiplier, 0)
    if not isinstance(record, bool):
        raise ValueError("record must be a boolean")
    if strategy not in ("fixed", "inventory") and not isinstance(risk, RiskParameters):
        raise ValueError("AS-inspired simulation requires explicit RiskParameters")
    events = tuple(events)
    if not events:
        raise ValueError("events must not be empty")
    previous_event = None
    for event in events:
        if not isinstance(event, Event):
            raise ValueError("every event must be an Event")
        if previous_event is not None:
            if event.step <= previous_event.step:
                raise ValueError("event steps must be strictly increasing")
            if not math.isclose(event.mid_price, previous_event.next_mid,
                                rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("discontinuous mid-price path")
        previous_event = event

    steps = len(events)
    cash, inventory = float(config.initial_cash), config.initial_inventory
    initial_equity = cash + inventory * events[0].mid_price
    equity = peak_equity = initial_equity
    warm_quote = _requested_quote(events[0].mid_price, inventory, config,
                                  strategy, steps, risk, latency_steps,
                                  buffer_multiplier)
    active = _ActivePair(0, 0, 0, warm_quote)
    pending = deque()
    trace = []
    trades = quote_clips = blocked_inventory = blocked_no_order = 0
    quote_activations = active_quote_clip_steps = 0
    active_stale_fills = negative_edge_fills = blocked_insufficient_cash = 0
    fill_information_age_sum = fill_resting_age_sum = 0
    two_sided_live_steps = 0
    total_fees = spread_capture = inventory_pnl = 0.0
    inventory_carry_pnl = fill_revaluation_pnl = 0.0
    sum_quote_width = sum_abs_quote_shift = sum_requested_abs_shift = 0.0
    sum_requested_quote_width = sum_requested_raw_width = 0.0
    sum_abs_inventory = max_drawdown = 0.0
    max_abs_inventory = abs(inventory)

    for t, event in enumerate(events):
        activations_this_step = 0
        while pending and pending[0].due_step <= t:
            due = pending.popleft()
            active = _ActivePair(due.pair_id, due.information_step, t, due.quote)
            activations_this_step += 1

        inventory_before = inventory
        requested = _requested_quote(event.mid_price, inventory, config,
                                     strategy, steps - t, risk, latency_steps,
                                     buffer_multiplier)
        request = _Request(t + 1, t, t + latency_steps, requested)
        pending.append(request)
        if latency_steps == 0:
            due = pending.popleft()
            active = _ActivePair(due.pair_id, due.information_step, t, due.quote)
            activations_this_step += 1
        quote_activations += activations_this_step

        quote = active.quote
        bid_live_before, ask_live_before = active.bid_live, active.ask_live
        information_age = t - active.information_step
        resting_age = t - active.activation_step
        required_live = (active.ask_live if event.customer_side == "buy"
                         else active.bid_live)
        if event.customer_side is not None and not required_live:
            trade = _no_live_order(cash, inventory)
        else:
            # Preserve the v2 hard cash/inventory checks. Stale quotes are
            # executed at their stored prices, never recentered at arrival.
            trade = execute_trade(cash, inventory, quote, event.customer_side,
                                  event.limit_price, config)
        filled_order_id = None
        if trade["filled"]:
            side = "bid" if trade["quantity"] == 1 else "ask"
            filled_order_id = f"{active.pair_id}:{side}"
            if side == "bid":
                active.bid_live = False
            else:
                active.ask_live = False
        cash, inventory = trade["cash"], trade["inventory"]

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
        if not math.isclose(new_equity - equity, net_change,
                            rel_tol=1e-9, abs_tol=1e-7):
            raise ArithmeticError("wealth change does not reconcile")
        equity = new_equity
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        trades += int(trade["filled"])
        quote_clips += int(requested.clipped)
        active_quote_clip_steps += int(quote.clipped)
        blocked_inventory += int(trade["reason"] == "inventory_limit")
        blocked_no_order += int(trade["reason"] == "no_live_order")
        blocked_insufficient_cash += int(trade["reason"] == "insufficient_cash")
        active_stale_fills += int(trade["filled"] and information_age > 0)
        negative_edge_fills += int(trade["filled"] and edge < 0)
        if trade["filled"]:
            fill_information_age_sum += information_age
            fill_resting_age_sum += resting_age
        two_sided_live_steps += int(bid_live_before and ask_live_before)
        total_fees += trade["fee"]
        spread_capture += edge
        inventory_pnl += holding_change
        inventory_carry_pnl += carry_change
        fill_revaluation_pnl += fill_change
        actual_width = quote.ask - quote.bid
        actual_shift = (quote.bid + quote.ask) / 2 - event.mid_price
        sum_quote_width += actual_width
        sum_abs_quote_shift += abs(actual_shift)
        sum_requested_abs_shift += abs(requested.requested_shift)
        sum_requested_quote_width += requested.ask - requested.bid
        sum_requested_raw_width += requested.requested_width
        sum_abs_inventory += abs(inventory)
        max_abs_inventory = max(max_abs_inventory, abs(inventory))
        if record:
            trace.append(dict(
                step=event.step, model_step=t,
                mid_price=event.mid_price, next_mid=event.next_mid,
                customer_side=event.customer_side, limit_price=event.limit_price,
                informed=event.informed, bid=quote.bid, ask=quote.ask,
                clipped=quote.clipped, requested_shift=quote.requested_shift,
                requested_width=quote.requested_width,
                actual_shift=actual_shift, actual_width=actual_width,
                cash=cash, inventory_before=inventory_before,
                inventory=inventory, filled=trade["filled"],
                quantity=trade["quantity"], execution_price=trade["execution_price"],
                fee=trade["fee"], reason=trade["reason"], equity=equity,
                pnl=equity - initial_equity, edge=edge, inventory_pnl=holding_change,
                inventory_carry_pnl=carry_change, fill_revaluation_pnl=fill_change,
                net_change=net_change,
                active_pair_id=active.pair_id,
                quote_information_step=active.information_step,
                quote_activation_step=active.activation_step,
                quote_information_age=information_age,
                quote_resting_age=resting_age,
                request_pair_id=request.pair_id, request_due_step=request.due_step,
                request_bid=requested.bid, request_ask=requested.ask,
                request_clipped=requested.clipped,
                request_requested_shift=requested.requested_shift,
                request_requested_width=requested.requested_width,
                active_bid=quote.bid, active_ask=quote.ask,
                bid_live_before=bid_live_before, ask_live_before=ask_live_before,
                bid_live_after=active.bid_live, ask_live_after=active.ask_live,
                filled_order_id=filled_order_id,
                fill_information_age=information_age if trade["filled"] else None,
                fill_resting_age=resting_age if trade["filled"] else None,
                quote_activations_this_step=activations_this_step,
                pending_quote_requests=len(pending),
            ))

    liquidation_cost = abs(inventory) * (config.liquidation_half_spread
                                         + config.fee_per_trade)
    final_cash = cash + inventory * events[-1].next_mid - liquidation_cost
    metrics = dict(
        pnl_mtm=equity - initial_equity, pnl_liquidated=final_cash - initial_equity,
        trades=trades, mean_abs_inventory=sum_abs_inventory / steps,
        max_abs_inventory=max_abs_inventory, final_inventory=inventory,
        total_fees=total_fees, spread_capture=spread_capture, inventory_pnl=inventory_pnl,
        inventory_carry_pnl=inventory_carry_pnl, fill_revaluation_pnl=fill_revaluation_pnl,
        liquidation_cost=liquidation_cost, max_drawdown=max_drawdown,
        quote_clips=quote_clips, blocked_inventory=blocked_inventory,
        mean_quote_width=sum_quote_width / steps,
        mean_abs_quote_shift=sum_abs_quote_shift / steps,
        mean_requested_abs_shift=sum_requested_abs_shift / steps,
        initial_equity=initial_equity, final_equity=equity, final_cash=cash,
        final_cash_after_liquidation=final_cash, inventory_after_liquidation=0,
        latency_steps=latency_steps, buffer_multiplier=buffer_multiplier,
        latency_buffer_half_width=(buffer_multiplier * risk.volatility
                                   * math.sqrt(latency_steps)
                                   if strategy in BUFFERED_STRATEGIES else 0.0),
        quote_requests=steps, quote_activations=quote_activations,
        warm_start_activations=1, pending_quote_requests=len(pending),
        request_quote_clips=quote_clips,
        active_quote_clip_steps=active_quote_clip_steps,
        mean_requested_quote_width=sum_requested_quote_width / steps,
        mean_requested_raw_width=sum_requested_raw_width / steps,
        active_stale_fills=active_stale_fills,
        negative_edge_fills=negative_edge_fills,
        blocked_no_order=blocked_no_order,
        blocked_insufficient_cash=blocked_insufficient_cash,
        fill_information_age_sum=fill_information_age_sum,
        fill_information_age_mean=fill_information_age_sum / trades if trades else None,
        fill_resting_age_sum=fill_resting_age_sum,
        fill_resting_age_mean=fill_resting_age_sum / trades if trades else None,
        two_sided_live_steps=two_sided_live_steps,
    )
    return {"metrics": metrics, "trace": trace}
