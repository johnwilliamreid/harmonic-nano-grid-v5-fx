import math
from typing import Dict, Any, List, Optional
from src.config import TICK_SIZES

def round_to_tick(price: float, tick_size: float = 0.25) -> float:
    if tick_size <= 0:
        return price
    ticks = round(price / tick_size)
    return round(ticks * tick_size, 4)

def round_entry(price: float, side: str, tick_size: float = 0.25) -> float:
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

    @staticmethod
    def get_range_level(rung_idx: int) -> float:
        return round(rung_idx / 16.0, 4)

    def __init__(
        self,
        range_low: float,
        range_high: float,
        resolution: int = 16,
        tick_size: Optional[float] = None,
        multiplier: float = 0.20,
        fee_per_rt: float = 0.98,
        r_budget: Optional[float] = None,
        r_side_budget: float = 200.0,
        instrument: str = "NNQ"
    ):
        self.instrument = instrument.upper()
        self.tick_size = tick_size if tick_size is not None else TICK_SIZES.get(self.instrument, 0.50 if self.instrument == "NNQ" else 0.25)
        self.range_low = round_to_tick(range_low, self.tick_size)
        self.range_high = round_to_tick(range_high, self.tick_size)
        self.resolution = resolution
        self.multiplier = float(multiplier)
        self.fee_per_rt = float(fee_per_rt)
        self.r_side_budget = float(r_budget if r_budget is not None else r_side_budget)

        self.delta_p = self.range_high - self.range_low
        self.s_step = self.delta_p / float(self.resolution)
        self.s_harvest = round_to_tick(2.0 * self.s_step, self.tick_size)

        self.sl_low = round_to_tick(self.range_low - self.s_step, self.tick_size)
        self.sl_high = round_to_tick(self.range_high + self.s_step, self.tick_size)

    def snap_harmonic_before(self, price: float, side: str) -> float:
        raw_level = (price - self.range_low) / self.s_step
        if side.upper() == "SHORT":
            snapped_idx = math.floor(raw_level + 1e-9)
        else:
            snapped_idx = math.ceil(raw_level - 1e-9)
        snapped_price = self.range_low + (snapped_idx * self.s_step)
        return round_to_tick(snapped_price, self.tick_size)

    def diagnose_breaker(self, p1: Optional[float], p2: Optional[float], side: str = "LONG") -> Dict[str, Any]:
        if p1 is not None and p2 is not None:
            width_pts = abs(float(p1) - float(p2))
            width_L = round(width_pts / self.s_step, 2)
            if side == "LONG":
                front = max(float(p1), float(p2))
                back = min(float(p1), float(p2))
            else:
                front = min(float(p1), float(p2))
                back = max(float(p1), float(p2))
        else:
            width_pts = 0.0
            width_L = 0.0
            front = None
            back = None

        if width_L <= 0:
            regime = "UNDEFINED"
            fit = "OPTION_A"
        elif width_L < 3.0:
            regime = "NARROW"
            fit = "OPTION_C"
        elif width_L <= 5.0:
            regime = "NORMAL"
            fit = "OPTION_A"
        else:
            regime = "WIDE"
            fit = "OPTION_B"

        return {
            "front": front,
            "back": back,
            "width_pts": round(width_pts, 2),
            "width_L": width_L,
            "regime": regime,
            "best_fit": fit
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
        raw_prices = [round_to_tick(self.range_low + i * self.s_step, self.tick_size) for i in range(17)]
        max_snap_dist = 0.60 * self.s_step

        prices = list(raw_prices)
        snap_reason = {i: "Geometric Natural" for i in range(17)}
        snap_roles = {}

        # 1. Base roles: Equilibrium, Stops, and internal mid-range decimal fractions
        snap_roles[0] = "STOP_LOW"
        snap_roles[8] = "MEDIAN_EQUILIBRIUM"
        snap_roles[16] = "STOP_HIGH"

        for i in range(1, 8):
            snap_roles[i] = f"MID_RANGE ({i / 16.0:.4f})"
        for i in range(9, 16):
            snap_roles[i] = f"MID_RANGE ({i / 16.0:.4f})"

        # 2. Quartiles stay strictly geometric (tick rounding only, zero snap distortion)
        prices[4] = round_to_tick(raw_prices[4], self.tick_size)
        snap_roles[4] = "QUARTILE_ANCHOR (0.2500)"
        snap_reason[4] = "Harmonic Octave Quartile (25%)"

        prices[12] = round_to_tick(raw_prices[12], self.tick_size)
        snap_roles[12] = "PREM_QUARTILE_ANCHOR (0.7500)"
        snap_reason[12] = "Harmonic Octave Quartile (75%)"

        # 3. Demand Breaker Snapping & Dynamic Outer Sweep
        if brk_low_front is not None and brk_low_back is not None:
            f_low = max(float(brk_low_front), float(brk_low_back))
            b_low = min(float(brk_low_front), float(brk_low_back))
            mt_low = b_low + 0.5 * (f_low - b_low)

            d_front_idx = max(1, min(7, int(round((f_low - self.range_low) / self.s_step))))
            d_mt_idx = max(1, min(d_front_idx - 1, int(round((mt_low - self.range_low) / self.s_step))))

            # Breaker Front (Single Rung)
            cand_f = min(raw_prices[d_front_idx] + max_snap_dist, max(raw_prices[d_front_idx] - max_snap_dist, f_low))
            prices[d_front_idx] = round_entry(cand_f, "LONG", self.tick_size)
            snap_roles[d_front_idx] = "BREAKER_FRONT"
            snap_reason[d_front_idx] = "Demand Breaker Front Edge"

            # Breaker MT (Single Rung)
            cand_mt = min(raw_prices[d_mt_idx] + max_snap_dist, max(raw_prices[d_mt_idx] - max_snap_dist, mt_low))
            prices[d_mt_idx] = round_entry(cand_mt, "LONG", self.tick_size)
            snap_roles[d_mt_idx] = "BREAKER_MT"
            snap_reason[d_mt_idx] = "Demand Breaker 50% MT"

            # Intermediate rungs between MT and Front become BREAKER_ABSORPTION (unless it is L4 Quartile)
            for r_mid in range(d_mt_idx + 1, d_front_idx):
                if r_mid != 4:
                    snap_roles[r_mid] = "BREAKER_ABSORPTION"
                    snap_reason[r_mid] = "Breaker Absorption Core"

            # Sweep Wick: strictly outside the Breaker Mitigation line
            sweep_idx_long = max(1, d_mt_idx - 1)
            snap_roles[sweep_idx_long] = "SWEEP_WICK"
            snap_reason[sweep_idx_long] = "Outer Mitigation Liquidity Sweep"

            # Levels deeper than sweep: generic external runway
            for r_ext in range(1, sweep_idx_long):
                snap_roles[r_ext] = "EXT_RUNWAY"
                snap_reason[r_ext] = "External Buffer Runway"

        # 4. Supply Breaker Snapping & Dynamic Outer Sweep
        if brk_high_front is not None and brk_high_back is not None:
            f_high = min(float(brk_high_front), float(brk_high_back))
            b_high = max(float(brk_high_front), float(brk_high_back))
            mt_high = f_high + 0.5 * (b_high - f_high)

            s_front_idx = max(9, min(15, int(round((f_high - self.range_low) / self.s_step))))
            s_mt_idx = max(s_front_idx + 1, min(15, int(round((mt_high - self.range_low) / self.s_step))))

            # Breaker Front (Single Rung)
            cand_sf = max(raw_prices[s_front_idx] - max_snap_dist, min(raw_prices[s_front_idx] + max_snap_dist, f_high))
            prices[s_front_idx] = round_entry(cand_sf, "SHORT", self.tick_size)
            snap_roles[s_front_idx] = "PREM_BREAKER_FRONT"
            snap_reason[s_front_idx] = "Supply Breaker Front Edge"

            # Breaker MT (Single Rung)
            cand_smt = max(raw_prices[s_mt_idx] - max_snap_dist, min(raw_prices[s_mt_idx] + max_snap_dist, mt_high))
            prices[s_mt_idx] = round_entry(cand_smt, "SHORT", self.tick_size)
            snap_roles[s_mt_idx] = "PREM_BREAKER_MT"
            snap_reason[s_mt_idx] = "Supply Breaker 50% MT"

            # Intermediate rungs between Front and MT become PREM_BREAKER_ABSORPTION (unless it is L12 Quartile)
            for r_mid in range(s_front_idx + 1, s_mt_idx):
                if r_mid != 12:
                    snap_roles[r_mid] = "PREM_BREAKER_ABSORPTION"
                    snap_reason[r_mid] = "Prem Breaker Absorption Core"

            # Sweep Wick: strictly outside the Breaker Mitigation line
            sweep_idx_short = min(15, s_mt_idx + 1)
            snap_roles[sweep_idx_short] = "PREM_SWEEP_WICK"
            snap_reason[sweep_idx_short] = "Outer Mitigation Liquidity Sweep"

            # Levels higher than sweep: generic external runway
            for r_ext in range(sweep_idx_short + 1, 16):
                snap_roles[r_ext] = "PREM_EXT_RUNWAY"
                snap_reason[r_ext] = "External Buffer Runway"

        # Strictly enforce price monotonicity
        for i in range(1, 16):
            if prices[i] <= prices[i - 1]:
                prices[i] = round_to_tick(prices[i - 1] + self.tick_size, self.tick_size)

        if active_discount_rungs is None:
            active_discount_rungs = [2, 3, 4, 5, 6, 7]
        if active_premium_rungs is None:
            active_premium_rungs = [9, 10, 11, 12, 13, 14]

        n_disc = len(active_discount_rungs)
        n_prem = len(active_premium_rungs)
        r_target_disc = (self.r_side_budget / n_disc) if n_disc > 0 else 0.0
        r_target_prem = (self.r_side_budget / n_prem) if n_prem > 0 else 0.0

        grid = {}
        for idx in range(17):
            side = "LONG" if idx < 8 else ("SHORT" if idx > 8 else "MID")
            is_active = (idx in active_discount_rungs) if side == "LONG" else ((idx in active_premium_rungs) if side == "SHORT" else False)

            role = snap_roles.get(idx, f"MID_RANGE ({idx / 16.0:.4f})")
            if not is_active:
                cap = 0
            elif "BREAKER_FRONT" in role:
                cap = 3   # Primary edge rejection: 3 rotations
            elif "BREAKER_ABSORPTION" in role:
                cap = 2   # Inner core volume absorption: 2 rotations
            elif "BREAKER_MT" in role:
                cap = 1   # Structural exhaustion line: 1-shot limit
            elif "SWEEP_WICK" in role:
                cap = 1   # Outer boundary sweep: 1-shot limit
            elif "QUARTILE_ANCHOR" in role:
                # Quartile adopts the cap of its closest neighbor inward toward Equilibrium (L8)
                inward_neighbor_idx = (idx + 1) if idx < 8 else (idx - 1)
                neighbor_role = snap_roles.get(inward_neighbor_idx, "")
                if "BREAKER_FRONT" in neighbor_role:
                    cap = 3
                elif "BREAKER_ABSORPTION" in neighbor_role:
                    cap = 2
                elif "BREAKER_MT" in neighbor_role:
                    cap = 1
                else:
                    cap = 4   # Inherits Mid-Range corridor dynamic (4 rotations)
            else:
                cap = 4   # Mid-Range corridor and octave deciles: 4 rotations

            px = prices[idx]
            raw_px = raw_prices[idx]
            snap_delta = round_to_tick(px - raw_px, self.tick_size)
            target_rung = self.TARGET_RUNGS.get(idx, idx)
            range_lvl = self.get_range_level(idx)

            if side == "LONG":
                dist_stop = abs(px - self.sl_low)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                tp_px = round_to_tick(px + self.s_harvest, self.tick_size)
                sl_px = self.sl_low
                r_target = r_target_disc
            elif side == "SHORT":
                dist_stop = abs(px - self.sl_high)
                k_i = (dist_stop * self.multiplier) + self.fee_per_rt
                tp_px = round_to_tick(px - self.s_harvest, self.tick_size)
                sl_px = self.sl_high
                r_target = r_target_prem
            else:
                dist_stop = 0.0
                k_i = 0.0
                tp_px = px
                sl_px = 0.0
                r_target = 0.0

            if cap > 0 and k_i > 0:
                exact_qty = r_target / k_i
                contracts = max(1, round(exact_qty))
                realized_risk = round(contracts * k_i, 2)
            else:
                exact_qty = 0.0
                contracts = 0
                realized_risk = 0.0

            reward_pts = round_to_tick(abs(tp_px - px), self.tick_size)
            reward_dollar = round(contracts * reward_pts * self.multiplier, 2)
            rr_ratio = round(reward_dollar / realized_risk, 2) if realized_risk > 0 else 0.0

            grid[idx] = {
                "index": idx,
                "role": role,
                "side": side,
                "range_level": range_lvl,
                "price": px,
                "raw_price": raw_px,
                "snap_delta": snap_delta,
                "snap_reason": snap_reason.get(idx, role),
                "tp": tp_px,
                "sl": sl_px,
                "target_rung": target_rung,
                "cap": cap,
                "rotations": 0,
                "contracts": contracts,
                "exact_qty": round(exact_qty, 4),
                "unit_risk": round(k_i, 2),
                "k_i": k_i,
                "reward_pts": reward_pts,
                "risk_dollar": realized_risk,
                "reward_dollar": reward_dollar,
                "rr_ratio": rr_ratio,
                "weight": contracts,
                "decimal_multiplier": 1.0,
                "is_active": is_active
            }

        # Dynamic greedy budget optimizer: fill remaining budget without exceeding r_side_budget
        for active_side in ["LONG", "SHORT"]:
            active_rungs = [idx for idx, r in grid.items() if r.get("side") == active_side and r.get("cap", 0) > 0 and r.get("k_i", 0) > 0]
            if not active_rungs:
                continue

            # Step 1: If initial rounded contracts exceed budget, trim from highest-risk rung down
            current_risk = sum(grid[idx]["contracts"] * grid[idx]["k_i"] for idx in active_rungs)
            while current_risk > self.r_side_budget and any(grid[idx]["contracts"] > 1 for idx in active_rungs):
                candidates = [idx for idx in active_rungs if grid[idx]["contracts"] > 1]
                idx_to_trim = max(candidates, key=lambda i: grid[i]["k_i"])
                grid[idx_to_trim]["contracts"] -= 1
                current_risk = sum(grid[idx]["contracts"] * grid[idx]["k_i"] for idx in active_rungs)

            # Step 2: Utilize unused budget cash by adding contracts where unit risk k_i is lowest
            rem_budget = self.r_side_budget - current_risk
            while rem_budget > 0:
                eligible = [idx for idx in active_rungs if grid[idx]["k_i"] <= rem_budget]
                if not eligible:
                    break
                best_idx = min(eligible, key=lambda i: grid[i]["k_i"])
                grid[best_idx]["contracts"] += 1
                rem_budget -= grid[best_idx]["k_i"]

            # Step 3: Recompute final dollar values
            for idx in active_rungs:
                c = grid[idx]["contracts"]
                grid[idx]["risk_dollar"] = round(c * grid[idx]["k_i"], 2)
                grid[idx]["reward_dollar"] = round(c * grid[idx]["reward_pts"] * self.multiplier, 2)
                grid[idx]["rr_ratio"] = round(grid[idx]["reward_dollar"] / grid[idx]["risk_dollar"], 2) if grid[idx]["risk_dollar"] > 0 else 0.0

        return grid

    def compute_remaining_risk(self, grid_state: Dict[int, Dict[str, Any]]) -> Dict[str, float]:
        """Calculates live remaining risk by side taking into account exhausted rungs."""
        long_rem = 0.0
        short_rem = 0.0

        for idx, r in grid_state.items():
            side = r.get("side", "MID")
            if side not in ("LONG", "SHORT"):
                continue

            cap = r.get("cap", 0)
            rots = r.get("rotations", 0)
            open_pos = r.get("open_position", 0) or r.get("position", 0)
            k_i = r.get("k_i", r.get("unit_risk", 0.0))
            order_qty = r.get("contracts", 0)

            # If rung holds an active open position, that risk is locked until TP/SL
            pos_risk = abs(open_pos) * k_i

            # If rung has remaining rotations allowed, pending limit orders still carry risk
            unexhausted = (rots < cap) if cap > 0 else False
            pending_risk = order_qty * k_i if unexhausted and (open_pos == 0) else 0.0

            rung_live_risk = pos_risk + pending_risk

            if side == "LONG":
                long_rem += rung_live_risk
            elif side == "SHORT":
                short_rem += rung_live_risk

        return {
            "long_rem_risk": round(long_rem, 2),
            "short_rem_risk": round(short_rem, 2),
            "max_side_rem_risk": round(max(long_rem, short_rem), 2)
        }

    def generate_proposal_package(
        self,
        brk_low_front: Optional[float] = None,
        brk_low_back: Optional[float] = None,
        brk_low_poc: Optional[float] = None,
        brk_high_front: Optional[float] = None,
        brk_high_back: Optional[float] = None,
        brk_high_poc: Optional[float] = None
    ) -> Dict[str, Any]:
        d_diag = self.diagnose_breaker(brk_low_front, brk_low_back, side="LONG")
        s_diag = self.diagnose_breaker(brk_high_front, brk_high_back, side="SHORT")

        max_L = max(d_diag["width_L"], s_diag["width_L"])
        if max_L <= 0:
            composite_regime = "NORMAL"
            composite_fit = "OPTION_A"
        elif max_L < 3.0:
            composite_regime = "NARROW"
            composite_fit = "OPTION_C"
        elif max_L <= 5.0:
            composite_regime = "NORMAL"
            composite_fit = "OPTION_A"
        else:
            composite_regime = "WIDE"
            composite_fit = "OPTION_B"

        # Derive dynamic breaker-centered rungs for Option D
        d_front_idx = 5
        if brk_low_front is not None:
            d_front_idx = max(2, min(7, int(round((float(brk_low_front) - self.range_low) / self.s_step))))
        s_front_idx = 11
        if brk_high_front is not None:
            s_front_idx = max(9, min(14, int(round((float(brk_high_front) - self.range_low) / self.s_step))))

        # Build dynamic cluster including 6/7 and 9/10 whenever breaker sits adjacent
        opt_d_disc = sorted(list(set([d_front_idx, max(2, d_front_idx - 1), 4, 6, 7])))
        opt_d_disc = [r for r in opt_d_disc if 1 <= r <= 7]
        
        opt_d_prem = sorted(list(set([s_front_idx, min(14, s_front_idx + 1), 9, 10, 12])))
        opt_d_prem = [r for r in opt_d_prem if 9 <= r <= 15]

        # Derive dynamic breaker-centered rungs
        d_front_idx = 5
        if brk_low_front is not None:
            d_front_idx = max(2, min(7, int(round((float(brk_low_front) - self.range_low) / self.s_step))))
        d_mt_idx = max(1, d_front_idx - 1)
        if brk_low_front is not None and brk_low_back is not None:
            mt_l = min(float(brk_low_front), float(brk_low_back)) + 0.5 * abs(float(brk_low_front) - float(brk_low_back))
            d_mt_idx = max(1, min(d_front_idx - 1, int(round((mt_l - self.range_low) / self.s_step))))

        s_front_idx = 11
        if brk_high_front is not None:
            s_front_idx = max(9, min(14, int(round((float(brk_high_front) - self.range_low) / self.s_step))))
        s_mt_idx = min(15, s_front_idx + 1)
        if brk_high_front is not None and brk_high_back is not None:
            mt_h = min(float(brk_high_front), float(brk_high_back)) + 0.5 * abs(float(brk_high_front) - float(brk_high_back))
            s_mt_idx = max(s_front_idx + 1, min(15, int(round((mt_h - self.range_low) / self.s_step))))

        opt_d_disc = sorted(list(set([d_front_idx, max(2, d_front_idx - 1), 4, 6, 7])))
        opt_d_disc = [r for r in opt_d_disc if 1 <= r <= 7]
        
        opt_d_prem = sorted(list(set([s_front_idx, min(14, s_front_idx + 1), 9, 10, 12])))
        opt_d_prem = [r for r in opt_d_prem if 9 <= r <= 15]

        configs = {
            "OPTION_A": {
                "name": "6-Level Balanced",
                "desc": "Full density with rotational chop capping",
                "discount_rungs": [2, 3, 4, 5, 6, 7],
                "premium_rungs": [9, 10, 11, 12, 13, 14],
                "l7_policy": "Armed (Cap: 4 rot)"
            },
            "OPTION_B": {
                "name": "Conservative Backbone (3-Rung)",
                "desc": "Pure 1/8 steps, zero midline chop risk",
                "discount_rungs": [2, 4, 6],
                "premium_rungs": [10, 12, 14],
                "l7_policy": "Dropped (Zero midline exposure)"
            },
            "OPTION_C": {
                "name": "Structural Core (4-Rung)",
                "desc": "Quartile anchor L4/L12 locked, outer wick dropped",
                "discount_rungs": [3, 4, 6, 7],
                "premium_rungs": [9, 10, 12, 13],
                "l7_policy": "Armed (Cap: 4 rot)"
            },
            "OPTION_D": {
                "name": "Breaker Snapped (Ratio-Driven Allocation)",
                "desc": "Structure-anchored with heavy midline velocity ratio",
                "discount_rungs": opt_d_disc,
                "premium_rungs": opt_d_prem,
                "l7_policy": "Velocity Sized (Cap: 4 rot)",
                "is_ratio_weighted": True
            },
            "OPTION_E": {
                "name": "Lean Mitigation Core (2-Rung)",
                "desc": "Tight 2-rung defense at Breaker Front and MT",
                "discount_rungs": sorted(list(set([d_front_idx, d_mt_idx]))),
                "premium_rungs": sorted(list(set([s_front_idx, s_mt_idx]))),
                "l7_policy": "Bypassed"
            }
        }

        full_grid = self.generate_grid(
            active_discount_rungs=[2, 3, 4, 5, 6, 7],
            active_premium_rungs=[9, 10, 11, 12, 13, 14],
            brk_low_front=brk_low_front,
            brk_low_back=brk_low_back,
            brk_low_poc=brk_low_poc,
            brk_high_front=brk_high_front,
            brk_high_back=brk_high_back,
            brk_high_poc=brk_high_poc
        )

        options = {}
        for opt_key, cfg in configs.items():
            d_rungs = cfg["discount_rungs"]
            p_rungs = cfg["premium_rungs"]
            N = len(d_rungs)
            r_target = self.r_side_budget / N

            k_weights_disc = []
            for r in d_rungs:
                rung_item = full_grid[r]
                dist_pts = abs(rung_item["price"] - self.sl_low)
                k_i = (dist_pts * self.multiplier) + self.fee_per_rt
                k_weights_disc.append(k_i)

            if cfg.get("is_ratio_weighted", False):
                # Ratio-driven allocation: allocate by structural role weight rather than pure distance-to-stop
                weights = []
                for r in d_rungs:
                    rg_role = full_grid[r]["role"]
                    if r in [6, 7, 9, 10]:  # Midline rotational velocity corridor
                        weights.append(1.8)
                    elif "FRONT" in rg_role or "MT" in rg_role:  # Structural breaker edge
                        weights.append(1.6)
                    elif "QUARTILE" in rg_role:
                        weights.append(1.2)
                    else:
                        weights.append(1.0)
                sum_w = sum(weights)
                budget_shares = [(w / sum_w) * self.r_side_budget for w in weights]
                ladder_qty = [max(1, round(b_share / k)) for b_share, k in zip(budget_shares, k_weights_disc)]
            else:
                ladder_qty = [max(1, round(r_target / k)) for k in k_weights_disc]

            total_catastrophic_risk = sum(q * k for q, k in zip(ladder_qty, k_weights_disc))
            while total_catastrophic_risk > self.r_side_budget and any(q > 1 for q in ladder_qty):
                # When trimming, trim from lowest weight/efficiency rungs first instead of automatically starving middle rungs
                candidates = [i for i, q in enumerate(ladder_qty) if q > 1]
                idx_to_trim = min(candidates, key=lambda i: (1.0 / k_weights_disc[i]))
                ladder_qty[idx_to_trim] -= 1
                total_catastrophic_risk = sum(q * k for q, k in zip(ladder_qty, k_weights_disc))

            level_rules_long = []
            for r, q in zip(d_rungs, ladder_qty):
                rg = full_grid[r]
                snap_info = f" [Snap: {rg['snap_delta']:+.2f} | {rg['snap_reason']}]" if abs(rg["snap_delta"]) > 0 else f" [{rg['snap_reason']}]"
                level_rules_long.append(
                    f"L{r} ({rg['range_level']:.4f}) @ <b>{rg['price']:.2f}</b>{snap_info} -> TP: <code>{rg['tp']:.2f}</code> | max {q} ct, cap {rg['cap']} rot"
                )

            level_rules_short = []
            for r, q in zip(p_rungs, ladder_qty):
                rg = full_grid[r]
                snap_info = f" [Snap: {rg['snap_delta']:+.2f} | {rg['snap_reason']}]" if abs(rg["snap_delta"]) > 0 else f" [{rg['snap_reason']}]"
                level_rules_short.append(
                    f"L{r} ({rg['range_level']:.4f}) @ <b>{rg['price']:.2f}</b>{snap_info} -> TP: <code>{rg['tp']:.2f}</code> | max {q} ct, cap {rg['cap']} rot"
                )

            # 1. Harvest Yield per full rotation net of round-trip fees
            harvest_pts = self.s_harvest
            gross_yield = sum(q * harvest_pts * self.multiplier for q in ladder_qty)
            total_fees = sum(q * self.fee_per_rt for q in ladder_qty)
            net_harvest_yield = max(0.0, round(gross_yield - total_fees, 2))

            # 2. Breaker Body Coverage percentage
            breaker_rungs_active = 0
            breaker_rungs_total = 0
            for r in d_rungs:
                rg_role = full_grid[r]["role"]
                if "BREAKER" in rg_role or "FRONT" in rg_role or "MT" in rg_role:
                    breaker_rungs_active += 1
            for idx in range(1, 8):
                if "BREAKER" in full_grid[idx]["role"] or "FRONT" in full_grid[idx]["role"] or "MT" in full_grid[idx]["role"]:
                    breaker_rungs_total += 1
            
            coverage_pct = round((breaker_rungs_active / breaker_rungs_total) * 100) if breaker_rungs_total > 0 else 0
            coverage_label = f"{coverage_pct}%" if breaker_rungs_total > 0 else "N/A"

            # 3. Budget clearance status
            budget_delta = round(self.r_side_budget - total_catastrophic_risk, 2)
            fits_budget = total_catastrophic_risk <= self.r_side_budget

            options[opt_key] = {
                "name": cfg["name"],
                "harvest_yield_per_rot": net_harvest_yield,
                "breaker_coverage": coverage_label,
                "fits_budget": fits_budget,
                "budget_delta": budget_delta,
                "desc": cfg["desc"],
                "discount_rungs": d_rungs,
                "premium_rungs": p_rungs,
                "rung_labels_long": [f"L{r} ({self.get_range_level(r):.4f} @ {full_grid[r]['price']:.2f})" for r in d_rungs],
                "rung_labels_short": [f"L{r} ({self.get_range_level(r):.4f} @ {full_grid[r]['price']:.2f})" for r in p_rungs],
                "level_rules_long": level_rules_long,
                "level_rules_short": level_rules_short,
                "l7_policy": cfg["l7_policy"],
                "n_active": N,
                "ladder_qty": ladder_qty,
                "total_contracts": sum(ladder_qty),
                "total_catastrophic_risk": round(total_catastrophic_risk, 2),
                "is_best_fit": (opt_key == composite_fit)
            }

        return {
            "instrument": self.instrument,
            "tick_size": self.tick_size,
            "range_low": self.range_low,
            "range_high": self.range_high,
            "delta_p": round(self.delta_p, 2),
            "s_step": round_to_tick(self.s_step, self.tick_size),
            "s_harvest": self.s_harvest,
            "sl_low": self.sl_low,
            "sl_high": self.sl_high,
            "r_side_budget": self.r_side_budget,
            "fee_per_rt": self.fee_per_rt,
            "diagnosis": {
                "demand_breaker": d_diag,
                "supply_breaker": s_diag,
                "regime": composite_regime,
                "best_fit": composite_fit
            },
            "options": options
        }
