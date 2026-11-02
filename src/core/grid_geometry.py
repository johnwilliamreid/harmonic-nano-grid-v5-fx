import math
from typing import Dict, Any, Optional

def round_entry(price: float, side: str, tick_size: float = 0.25) -> float:
    if side.upper() == "LONG":
        return round(math.ceil(round(price, 6) / tick_size) * tick_size, 2)
    elif side.upper() == "SHORT":
        return round(math.floor(round(price, 6) / tick_size) * tick_size, 2)
    return round(round(price / tick_size) * tick_size, 2)

def round_tp(price: float, side: str, tick_size: float = 0.25) -> float:
    if side.upper() == "LONG":
        return round(math.floor(round(price, 6) / tick_size) * tick_size, 2)
    elif side.upper() == "SHORT":
        return round(math.ceil(round(price, 6) / tick_size) * tick_size, 2)
    return round(round(price / tick_size) * tick_size, 2)

def round_sl(price: float, side: str, tick_size: float = 0.25) -> float:
    if side.upper() == "LONG":
        return round(math.floor(round(price, 6) / tick_size) * tick_size, 2)
    elif side.upper() == "SHORT":
        return round(math.ceil(round(price, 6) / tick_size) * tick_size, 2)
    return round(round(price / tick_size) * tick_size, 2)

class HarmonicGridGeometry:
    NOMINAL_WEIGHTS = {
        1: 0, 2: 3, 3: 2, 4: 2, 5: 1, 6: 1, 7: 1,
        8: 0,
        9: 1, 10: 1, 11: 1, 12: 2, 13: 2, 14: 3, 15: 0
    }

    def __init__(
        self,
        range_low: float,
        range_high: float,
        resolution: int = 16,
        tick_size: float = 0.25,
        multiplier: float = 2.00,
        fee_per_rt: float = 1.94,
        r_budget: float = 200.00
    ):
        self.range_low = range_low
        self.range_high = range_high
        self.resolution = resolution
        self.tick_size = tick_size
        self.multiplier = multiplier
        self.fee_per_rt = fee_per_rt
        self.r_budget = r_budget

        self.delta_p = range_high - range_low
        self.s_step = self.delta_p / float(resolution)
        self.s_harvest = 2.0 * self.s_step

        self.sl_low = round_sl(self.range_low - self.s_step, "LONG", self.tick_size)
        self.sl_high = round_sl(self.range_high + self.s_step, "SHORT", self.tick_size)

    def generate_grid(
        self,
        brk_low_back: Optional[float] = None,
        brk_low_front: Optional[float] = None,
        brk_low_poc: Optional[float] = None,
        brk_high_front: Optional[float] = None,
        brk_high_back: Optional[float] = None,
        brk_high_poc: Optional[float] = None,
    ) -> Dict[int, Dict[str, Any]]:
        raw_prices = {i: self.range_low + (i * self.s_step) for i in range(self.resolution + 1)}

        snapped_l5 = round_entry(raw_prices[5], "LONG", self.tick_size)
        snapped_l4 = round_entry(raw_prices[4], "LONG", self.tick_size)
        snapped_l3 = round_entry(raw_prices[3], "LONG", self.tick_size)
        if brk_low_front is not None and brk_low_back is not None:
            mt_low = brk_low_back + 0.5 * (brk_low_front - brk_low_back)
            snapped_l5 = round_entry(brk_low_front, "LONG", self.tick_size)
            poc_low = brk_low_poc if brk_low_poc is not None else (brk_low_back + 0.75 * (brk_low_front - brk_low_back))
            snapped_l4 = round_entry(poc_low, "LONG", self.tick_size)
            snapped_l3 = round_entry(mt_low, "LONG", self.tick_size)
            if snapped_l3 < mt_low:
                snapped_l3 = round_entry(mt_low + self.tick_size, "LONG", self.tick_size)

        snapped_l11 = round_entry(raw_prices[11], "SHORT", self.tick_size)
        snapped_l12 = round_entry(raw_prices[12], "SHORT", self.tick_size)
        snapped_l13 = round_entry(raw_prices[13], "SHORT", self.tick_size)
        if brk_high_front is not None and brk_high_back is not None:
            mt_high = brk_high_front + 0.5 * (brk_high_back - brk_high_front)
            snapped_l11 = round_entry(brk_high_front, "SHORT", self.tick_size)
            poc_high = brk_high_poc if brk_high_poc is not None else (brk_high_front + 0.25 * (brk_high_back - brk_high_front))
            snapped_l12 = round_entry(poc_high, "SHORT", self.tick_size)
            snapped_l13 = round_entry(mt_high, "SHORT", self.tick_size)
            if snapped_l13 > mt_high:
                snapped_l13 = round_entry(mt_high - self.tick_size, "SHORT", self.tick_size)

        rungs = {}
        for i in range(1, self.resolution):
            w_i = self.NOMINAL_WEIGHTS.get(i, 0)
            if i in (1, 15): side, cap, role = ("LONG" if i == 1 else "SHORT"), 0, "Buffer"
            elif i == 2: side, cap, role = "LONG", 1, "Discount Sweep Wick"
            elif i == 3: side, cap, role = "LONG", 1, "Breaker Core 50% MT"
            elif i == 4: side, cap, role = "LONG", 2, "Breaker Interior Shelf"
            elif i == 5: side, cap, role = "LONG", 3, "Breaker Front Edge"
            elif i in (6, 7): side, cap, role = "LONG", 9999, "Discount Transit"
            elif i == 8: side, cap, role = "MID", 0, "Equilibrium Exit"
            elif i in (9, 10): side, cap, role = "SHORT", 9999, "Premium Transit"
            elif i == 11: side, cap, role = "SHORT", 3, "Breaker Front Edge"
            elif i == 12: side, cap, role = "SHORT", 2, "Breaker Interior Shelf"
            elif i == 13: side, cap, role = "SHORT", 1, "Breaker Core 50% MT"
            elif i == 14: side, cap, role = "SHORT", 1, "Premium Sweep Wick"

            if i == 3: p = snapped_l3
            elif i == 4: p = snapped_l4
            elif i == 5: p = snapped_l5
            elif i == 11: p = snapped_l11
            elif i == 12: p = snapped_l12
            elif i == 13: p = snapped_l13
            elif i == 8: p = round(round(raw_prices[i] / self.tick_size) * self.tick_size, 2)
            else: p = round_entry(raw_prices[i], side, self.tick_size)

            if side == "LONG":
                target_idx = i + 2
                tp_price = round_tp(p + self.s_harvest, "LONG", self.tick_size)
                sl_price = self.sl_low
                dist_stop = abs(p - sl_price)
                dist_tp = abs(tp_price - p)
                risk_dollar = round((dist_stop * self.multiplier) + self.fee_per_rt, 2)
                reward_dollar = round((dist_tp * self.multiplier) - self.fee_per_rt, 2)
            elif side == "SHORT":
                target_idx = i - 2
                tp_price = round_tp(p - self.s_harvest, "SHORT", self.tick_size)
                sl_price = self.sl_high
                dist_stop = abs(sl_price - p)
                dist_tp = abs(p - tp_price)
                risk_dollar = round((dist_stop * self.multiplier) + self.fee_per_rt, 2)
                reward_dollar = round((dist_tp * self.multiplier) - self.fee_per_rt, 2)
            else:
                target_idx = 8
                tp_price = p
                sl_price = p
                dist_stop = 0.0
                dist_tp = 0.0
                risk_dollar = 0.0
                reward_dollar = 0.0

            # Dynamic Decimal Multiplier & Integer Contract Sizing
            if cap > 0 and risk_dollar > 0:
                base_dist = 8.0 * self.s_step
                base_k = (base_dist * self.multiplier) + self.fee_per_rt
                decimal_mult = round(base_k / risk_dollar, 4)
                exact_qty = self.r_budget / risk_dollar
                contracts = max(1, round(exact_qty))
                total_risk = round(contracts * risk_dollar, 2)
                total_reward = round(contracts * reward_dollar, 2)
            else:
                decimal_mult = 0.0
                exact_qty = 0.0
                contracts = 0
                total_risk = 0.0
                total_reward = 0.0
            rr_ratio = round(reward_dollar / risk_dollar, 2) if risk_dollar > 0 else 0.0

            rungs[i] = {
                "index": i, "side": side, "price": p, "tp": tp_price, "sl": sl_price,
                "target_rung": target_idx, "cap": cap, "rotations": 0, "status": "PENDING",
                "role": role, "raw_price": round(raw_prices[i], 2),
                "dist_stop": round(dist_stop, 2), "risk_dollar": total_risk, "unit_risk": risk_dollar,
                "reward_dollar": total_reward, "unit_reward": reward_dollar, "rr_ratio": rr_ratio,
                "weight": w_i, "contracts": contracts, "decimal_multiplier": decimal_mult, "exact_qty": round(exact_qty, 4), "decimal_multiplier": decimal_mult, "exact_qty": round(exact_qty, 4), "decimal_multiplier": decimal_mult, "exact_qty": round(exact_qty, 4)
            }
        return rungs
