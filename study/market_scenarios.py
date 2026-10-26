"""Controlled synthetic environments for research, separate from legacy results.

Every stream consumes a fixed budget per step, irrespective of scenario or
policy. Changing the information mix cannot change the exogenous price path.
Customers may be model-informed; the quoting policy never sees their future
information or these random streams.
"""

import math
import random

from simulator import Event, Scenario, _integer, _number, _tick


def generate_controlled_market(scenario, seed, initial_mid=100.0, tick_size=0.01):
    if not isinstance(scenario, Scenario):
        raise ValueError("scenario must be a Scenario")
    _integer("seed", seed)
    _number("initial_mid", initial_mid)
    _number("tick_size", tick_size)
    if tick_size <= 0 or initial_mid < max(1.0, 2 * tick_size):
        raise ValueError("invalid initial price or tick size")
    streams = {name: random.Random(f"mm-research-v2:{seed}:{name}")
               for name in ("price", "arrival", "side", "urgency", "information")}
    mid = _tick(initial_mid, tick_size)
    floor = max(2, math.ceil(1.0 / tick_size)) * tick_size
    events = []
    for step in range(1, scenario.steps + 1):
        shock = streams["price"].gauss(0, 1)
        jump_draw = streams["price"].random()
        jump_sign = 1 if streams["price"].random() < 0.5 else -1
        arrives = streams["arrival"].random()
        side_draw = streams["side"].random()
        urgency_draw = streams["urgency"].random()
        information_draw = streams["information"].random()
        information_noise = streams["information"].gauss(0, 0.1)
        jump = jump_sign * scenario.jump_size if jump_draw < scenario.jump_probability else 0.0
        next_mid = max(floor, _tick(mid + scenario.drift
                                   + scenario.volatility * shock + jump, tick_size))
        side, limit, informed = None, None, False
        if arrives < scenario.arrival_probability:
            informed = information_draw < scenario.informed_fraction
            if informed:
                signal = next_mid - mid + information_noise
                side = "buy" if signal >= 0 else "sell"
                limit = max(tick_size, mid + signal)
            else:
                side = "buy" if side_draw < scenario.buy_probability else "sell"
                if scenario.demand_model == "exponential":
                    urgency = -scenario.urgency_scale * math.log1p(-urgency_draw)
                else:
                    urgency = 2 * scenario.urgency_scale * urgency_draw
                limit = max(tick_size, mid + (urgency if side == "buy" else -urgency))
        events.append(Event(step, mid, next_mid, side, limit, informed))
        mid = next_mid
    return events
