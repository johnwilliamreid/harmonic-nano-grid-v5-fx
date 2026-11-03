import math
from typing import Dict, Any, List, Optional

def round_entry(price: float, side: str, tick_size: float = 0.25) -> float:
    """Rounds passive limit entries conservatively to prevent taker fills."""
    ticks = price / tick_size
    if side == "LONG":
        return round(math.floor(ticks + 1e-9) * tick_size, 4)
    elif side == "SHORT":
        return round(math.ceil(ticks - 1e-9) * tick_size, 4)
    return round(round(ticks) * tick_size, 4)

class HarmonicGridGeometry:
    ROLES = {
        0: "STOP_LOW",
        1: "OUTER_BUFFER",
        2: "SWEEP_WICK",
        3: "BREAKER_MT",
        4: "QUARTILE_ANCHOR",
        5: "BREAKER_FRONT",
        6: "MID_TRANSIT",
        7: "SUB_EQ_PIVOT",
        8: "MEDIAN_EQUILIBRIUM",
        9: "PREM_EQ_PIVOT",
        10: "PREM_MID_TRANSIT",
        11: "PREM_BREAKER_FRONT",
        12: "PREM_QUARTILE_ANCHOR",
        13: "PREM_BREAKER_MT",
        14: "PREM_SWEEP_WICK",
        15: "PREM_OUTER_BUFFER",
        16: "STOP_HIGH"
    }

    TARGET_RUNGS = {
        2: 4, 3: 5, 4: 6, 5: 7, 6: 8, 7: 8,
        9: 8, 10: 8, 11: 9, 12: 10, 13: 11, 14: 12
    }

    def __init__(
        self,
        range_low: float,
        range_high: float,
        resolution: int = 16,
        tick_size: float = 0.25,
        multiplier: float = 2.0,
        fee_per_rt: float = 1.94,
        r_budget: Optional[float] = None,
        r_side_budget: float = 1200.0,
        instrument: str = "MNQ"
    ):
        self.range_low = float(range_low)
        self.range_high = float(range_high)
        self.resolution = resolution
        self.tick_size = tick_size
        self.multiplier = float(multiplier)
        self.fee_per_rt = float(fee_per_rt)
        self.r_side_budget = float(r_budget if r_budget is not None else r_side_budget)
        self.instrument = instrument.upper()

        self.delta_p = self.range_high - self.range_low
        self.s_step = self.delta_p / float(self.resolution)
        self.s_harvest = 2.0 * self.s_step

        self.sl_low = self.range_low - self.s_step
        self.sl_high = self.range_high + self.s_step

    def diagnose_structure(self, brk_front: Optional[float], brk_back: Optional[float]) -> Dict[str, Any]:
        """Diagnoses breaker size relative to harmonic levels (L) and determines Best Fit."""
        if brk_front is not None and brk_back is not None:
            width_pts = abs(float(brk_front) - float(brk_back))
            width_L = round(width_pts / self.s_step, 2)
        else:
            width_pts = 0.0
            width_L = 0.0

        if width_L < 3.0:
            regime = "NARROW"
            best_fit = "OPTION_C" # Compact 4-Level Core
        elif width_L <= 5.0:
            regime = "NORMAL"
            best_fit = "OPTION_A" # Full 6-Level Balanced
        else:
            regime = "WIDE"
            best_fit = "OPTION_B" # Pure 1/8 Backbone

        return {
            "width_pts": round(width_pts, 2),
            "width_L": width_L,
            "regime": regime,
            "best_fit": best_fit
        }

    def generate_proposal_package(
        self,
        brk_low_front: Optional[float] = None,
        brk_low_back: Optional[float] = None,
        brk_high_front: Optional[float] = None,
        brk_high_back: Optional[float] = None
    ) -> Dict[str, Any]:
        front = brk_high_front if brk_high_front is not None else brk_low_front
        back = brk_high_back if brk_high_back is not None else brk_low_back
        diag = self.diagnose_structure(front, back)

        configs = {
            "OPTION_A": {
                "name": "6-Level Balanced",
                "desc": "Full density with rotational chop capping",
                "discount_rungs": [2, 3, 4, 5, 6, 7],
                "premium_rungs": [9, 10, 11, 12, 13, 14],
                "l7_policy": "Armed (Cap: 4 rot | Fast 1L harvest)"
            },
            "OPTION_B": {
                "name": "1/8 Octave Backbone (Conservative)",
                "desc": "Pure 1/8 steps, zero midline chop risk",
                "discount_rungs": [2, 4, 6],
                "premium_rungs": [10, 12, 14],
                "l7_policy": "Dropped (Zero midline exposure)"
            },
            "OPTION_C": {
                "name": "4-Level Structural (Compact Core)",
                "desc": "Quartile anchor L4/L12 locked, outer wick dropped",
                "discount_rungs": [3, 4, 6, 7],
                "premium_rungs": [9, 10, 12, 13],
                "l7_policy": "Armed (Cap: 4 rot | Fast 1L harvest)"
            }
        }

        options = {}
        for opt_key, cfg in configs.items():
            r_list = cfg["discount_rungs"]
            N = len(r_list)
            r_target = self.r_side_budget / N
            ewr_pct = round((1.0 / N) * 100.0, 2)

            ladder_qty = []
            total_realized_risk = 0.0

            for r in r_list:
                d = r + 1
                dist_pts = d * self.s_step
                k_i = (dist_pts * self.multiplier) + self.fee_per_rt
                q_star = r_target / k_i
                q_int = max(1, round(q_star))
                realized_r = round(q_int * k_i, 2)
                total_realized_risk += realized_r
                ladder_qty.append(q_int)

            options[opt_key] = {
                "name": cfg["name"],
                "desc": cfg["desc"],
                "discount_rungs": cfg["discount_rungs"],
                "premium_rungs": cfg["premium_rungs"],
                "rung_labels": [f"L{r}" for r in r_list],
                "l7_policy": cfg["l7_policy"],
                "n_active": N,
                "ewr_target_pct": ewr_pct,
                "r_target": round(r_target, 2),
                "ladder_qty": ladder_qty,
                "total_contracts": sum(ladder_qty),
                "peak_risk": round(total_realized_risk, 2),
                "is_best_fit": (opt_key == diag["best_fit"])
            }

        return {
            "instrument": self.instrument,
            "range_low": self.range_low,
            "range_high": self.range_high,
            "delta_p": round(self.delta_p, 2),
            "s_step": round(self.s_step, 2),
            "s_harvest": round(self.s_harvest, 2),
            "sl_low": round(self.sl_low, 2),
            "sl_high": round(self.sl_high, 2),
            "r_side_budget": self.r_side_budget,
            "fee_per_rt": self.fee_per_rt,
            "diagnosis": diag,
            "options": options
        }

    def generate_grid(
        self,
        active_discount_rungs: Optional[List[int]] = None,
        active_premium_rungs: Optional[List[int]] = None,
        brk_low_front: Optional[float] = None,
        brk_low_back: Optional[float] = None,
        brk_low_poc: Optional[float] = None,
        brk_high_front: Optional[float] = None,
        brk_high_back: Optional[float] = None,
        brk_high_poc: Optional[float] = None
    ) -> Dict[int, Dict[str, Any]]:
        if active_discount_rungs is None:
            active_discount_rungs = [2, 3, 4, 5, 6, 7]
        if active_premium_rungs is None:
            active_premium_rungs = [9, 10, 11, 12, 13, 14]

        raw_prices = [round(self.range_low + i * self.s_step, 4) for i in range(17)]
        max_delta = 0.5 * self.s_step

        # Long Breaker snapping
        snapped_l3 = raw_prices[3]
        snapped_l4 = raw_prices[4]
        snapped_l5 = raw_prices[5]
        if brk_low_front is not None and brk_low_back is not None:
            mt_low = brk_low_back + 0.5 * (brk_low_front - brk_low_back)
            poc_low = brk_low_poc if brk_low_poc is not None else (brk_low_back + 0.75 * (brk_low_front - brk_low_back))

            cand_l5 = max(raw_prices[5] - max_delta, min(raw_prices[5] + max_delta, brk_low_front))
            cand_l4 = max(raw_prices[4] - max_delta, min(raw_prices[4] + max_delta, poc_low))
            cand_l3 = max(raw_prices[3] - max_delta, min(raw_prices[3] + max_delta, mt_low))

            snapped_l5 = round_entry(cand_l5, "LONG", self.tick_size)
            snapped_l4 = round_entry(min(cand_l4, snapped_l5 - self.tick_size), "LONG", self.tick_size)
            snapped_l3 = round_entry(min(cand_l3, snapped_l4 - self.tick_size), "LONG", self.tick_size)

        # Short Breaker snapping
        snapped_l11 = raw_prices[11]
        snapped_l12 = raw_prices[12]
        snapped_l13 = raw_prices[13]
        if brk_high_front is not None and brk_high_back is not None:
            mt_high = brk_high_front + 0.5 * (brk_high_back - brk_high_front)
            poc_high = brk_high_poc if brk_high_poc is not None else (brk_high_front + 0.25 * (brk_high_back - brk_high_front))

            cand_l11 = max(raw_prices[11] - max_delta, min(raw_prices[11] + max_delta, brk_high_front))
            cand_l12 = max(raw_prices[12] - max_delta, min(raw_prices[12] + max_delta, poc_high))
            cand_l13 = max(raw_prices[13] - max_delta, min(raw_prices[13] + max_delta, mt_high))

            snapped_l11 = round_entry(cand_l11, "SHORT", self.tick_size)
            snapped_l12 = round_entry(max(cand_l12, snapped_l11 + self.tick_size), "SHORT", self.tick_size)
            snapped_l13 = round_entry(max(cand_l13, snapped_l12 + self.tick_size), "SHORT", self.tick_size)

        prices = list(raw_prices)
        prices[3] = snapped_l3
        prices[4] = snapped_l4
        prices[5] = snapped_l5
        prices[11] = snapped_l11
        prices[12] = snapped_l12
        prices[13] = snapped_l13

        # Enforce strict monotonic progression
        for i in range(1, 16):
            if prices[i] <= prices[i - 1]:
                prices[i] = round(prices[i - 1] + self.tick_size, 4)

        n_disc = len(active_discount_rungs)
        n_prem = len(active_premium_rungs)
        r_target_disc = (self.r_side_budget / n_disc) if n_disc > 0 else 0.0
        r_target_prem = (self.r_side_budget / n_prem) if n_prem > 0 else 0.0

        grid = {}
        for idx in range(17):
            side = "LONG" if idx < 8 else ("SHORT" if idx > 8 else "MID")
            is_active = (idx in active_discount_rungs) if side == "LONG" else ((idx in active_premium_rungs) if side == "SHORT" else False)
            cap = 1 if is_active else 0
            if idx in (7, 9) and is_active:
                cap = 4

            px = prices[idx]
            role = self.ROLES.get(idx, "RUNWAY")
            target_rung = self.TARGET_RUNGS.get(idx, idx)

            if side == "LONG":
                dist_stop = abs(px - self.sl_low)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                tp_px = round_entry(px + self.s_harvest, "LONG", self.tick_size)
                r_target = r_target_disc
            elif side == "SHORT":
                dist_stop = abs(px - self.sl_high)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                tp_px = round_entry(px - self.s_harvest, "SHORT", self.tick_size)
                r_target = r_target_prem
            else:
                dist_stop = 0.0
                k_i = 0.0
                tp_px = px
                r_target = 0.0

            if cap > 0 and k_i > 0:
                exact_qty = r_target / k_i
                contracts = max(1, round(exact_qty))
                realized_risk = round(contracts * k_i, 2)
            else:
                exact_qty = 0.0
                contracts = 0
                realized_risk = 0.0

            reward_pts = abs(tp_px - px)
            reward_dollar = round(contracts * reward_pts * self.multiplier, 2)
            rr_ratio = round(reward_dollar / realized_risk, 2) if realized_risk > 0 else 0.0

            grid[idx] = {
                "index": idx,
                "role": role,
                "side": side,
                "price": px,
                "tp": tp_px,
                "sl": self.sl_low if side == "LONG" else (self.sl_high if side == "SHORT" else 0.0),
                "target_rung": target_rung,
                "cap": cap,
                "rotations": 0,
                "contracts": contracts,
                "exact_qty": round(exact_qty, 4),
                "unit_risk": round(k_i, 2),
                "risk_dollar": realized_risk,
                "reward_dollar": reward_dollar,
                "rr_ratio": rr_ratio,
                "weight": contracts,
                "decimal_multiplier": 1.0,
                "is_active": is_active
            }

        return grid
