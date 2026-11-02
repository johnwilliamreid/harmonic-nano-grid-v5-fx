import math
from typing import Dict, Any, List, Optional
from src.config import tick_round, COMMISSION_PER_RT, MULTIPLIERS, TICK_SIZES

class HarmonicGridEngine:
    """
    Harmonic Nano-Grid Execution Engine.
    - Dual resolution: 1/16 (>90 pts) vs 1/8 (45-90 pts).
    - Hard risk ceiling of $200.00.
    - 2-contract rolling window router.
    - Trailing break-even ratchet (+1 step towards TP restores risk budget).
    - Lifecycle rotation caps per structural rung seeded from pre-session replay.
    - Directional bias hysteresis gating (asymmetric rung suppression).
    """

    LIFECYCLE_CAPS_16 = {
        1: 0, 2: 1, 3: 1, 4: 2, 5: 3, 6: 9999, 7: 9999,
        8: 0,  # Equilibrium exit terminal only
        9: 9999, 10: 9999, 11: 3, 12: 2, 13: 1, 14: 1, 15: 0
    }

    LIFECYCLE_CAPS_8 = {
        1: 1,    # Sweep (~L2)
        2: 2,    # Shelf (~L4)
        3: 9999, # Transit (~L6)
        4: 0,    # Equilibrium exit terminal only
        5: 9999, # Transit (~L10)
        6: 2,    # Shelf (~L12)
        7: 1     # Sweep (~L14)
    }

    def __init__(
        self,
        broker: Any,
        proposal: Dict[str, Any],
        multiplier: Optional[float] = None,
        initial_rotations: Optional[Dict[int, int]] = None,
        initial_mitigation_weights: Optional[Dict[int, float]] = None,
        bias_regime: str = "NEUTRAL"
    ):
        self.broker = broker
        self.p = proposal
        self.instrument = proposal.get("instrument", "MNQ")
        self.mult = multiplier if multiplier is not None else MULTIPLIERS.get(self.instrument, 2.0)
        self.comm_rt = COMMISSION_PER_RT.get(self.instrument, 1.94)
        self.tick_size = TICK_SIZES.get(self.instrument, 0.25)

        self.range_low = float(proposal["range_low"])
        self.range_high = float(proposal["range_high"])
        self.delta_p = self.range_high - self.range_low

        # Binary Resolution Toggle
        self.resolution = 16 if self.delta_p > 90.0 else 8
        self.s_step = self.delta_p / float(self.resolution)
        self.harvest_step = self.delta_p / 8.0  # Invariant uncoupled harvest target

        # Native Stop Envelopes
        self.stop_loss_long = tick_round(self.range_low - self.s_step, "SELL", self.tick_size)
        self.stop_loss_short = tick_round(self.range_high + self.s_step, "BUY", self.tick_size)

        # Rungs and Caps
        self.rungs = self._build_rungs()
        self.lifecycle_caps = (
            self.LIFECYCLE_CAPS_16 if self.resolution == 16 else self.LIFECYCLE_CAPS_8
        )

        # Seed pre-session lifecycle state
        self.rotations = {r["index"]: 0 for r in self.rungs}
        if initial_rotations:
            for idx, count in initial_rotations.items():
                if idx in self.rotations:
                    self.rotations[idx] = count

        self.mitigation_weight = {r["index"]: 1.0 for r in self.rungs}
        if initial_mitigation_weights:
            for idx, weight in initial_mitigation_weights.items():
                if idx in self.mitigation_weight:
                    self.mitigation_weight[idx] = weight

        self.bias_regime = bias_regime

        # State Tracking
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.open: Dict[int, Dict[str, Any]] = {}
        self.records: List[Dict[str, Any]] = []
        self.next_oid = 1
        self.deployed = False
        self.stopped = False
        self.stop_oid = {"LONG": None, "SHORT": None}

        self.max_concurrent_contracts = 2
        self.max_budget = 200.0

    def seed_from_pre_session(self, replay_result: Dict[str, Any]):
        seed_rotations = replay_result.get("rotations", {})
        for idx, count in seed_rotations.items():
            if idx in self.rotations:
                self.rotations[idx] = count

        seed_weights = replay_result.get("mitigation_weight", {})
        for idx, weight in seed_weights.items():
            if idx in self.mitigation_weight:
                self.mitigation_weight[idx] = weight

        if "regime" in replay_result:
            self.bias_regime = replay_result["regime"]

        print(
            f"[HarmonicEngine] 🌾 Seeded {len(seed_rotations)} rungs | "
            f"Regime: {self.bias_regime} | Pre-session rotations: {replay_result.get('total_rotations', 0)}"
        )

    def _new_oid(self) -> str:
        oid = f"HG{self.next_oid}"
        self.next_oid += 1
        return oid

    def _build_rungs(self) -> List[Dict[str, Any]]:
        rungs = []
        n = self.resolution
        for i in range(1, n):
            price = self.range_low + i * self.s_step
            if i < (n / 2):
                side = "LONG"
                entry = tick_round(price, "LONG", self.tick_size)
                tp = tick_round(entry + self.harvest_step, "SHORT", self.tick_size)
            elif i > (n / 2):
                side = "SHORT"
                entry = tick_round(price, "SHORT", self.tick_size)
                tp = tick_round(entry - self.harvest_step, "LONG", self.tick_size)
            else:
                side = "MID"
                entry = tp = tick_round(price, "LONG", self.tick_size)

            rungs.append({
                "index": i,
                "side": side,
                "price": entry,
                "tp": tp,
            })
        return rungs

    def active_risk(self) -> float:
        risk = 0.0
        for idx, pos in self.open.items():
            qty = pos.get("qty", 0)
            if qty <= 0 or pos.get("ratcheted", False):
                continue

            entry = pos["entry_avg"]
            stop_px = self.stop_loss_long if pos["side"] == "LONG" else self.stop_loss_short
            risk += abs(entry - stop_px) * qty * self.mult + (qty * self.comm_rt)

        for o in self.orders.values():
            if o.get("kind") == "ENTRY" and o.get("remaining", 0) > 0:
                stop_px = self.stop_loss_long if o["side"] == "LONG" else self.stop_loss_short
                risk += abs(o["price"] - stop_px) * o["remaining"] * self.mult + (o["remaining"] * self.comm_rt)

        return risk

    def open_contracts_count(self) -> int:
        return sum(p.get("qty", 0) for p in self.open.values() if p.get("qty", 0) > 0)

    def _placeable(self, rung: Dict[str, Any], current_price: float) -> bool:
        idx = rung["index"]
        side = rung["side"]

        if side == "MID":
            return False

        # Directional Hysteresis Gating
        if self.bias_regime == "STRONG_BULLISH" and side == "SHORT":
            return False
        if self.bias_regime == "STRONG_BEARISH" and side == "LONG":
            return False

        if self.rotations.get(idx, 0) >= self.lifecycle_caps.get(idx, 0):
            return False
        if self.mitigation_weight.get(idx, 1.0) < 0.3:
            return False
        if side == "LONG" and rung["price"] >= current_price:
            return False
        if side == "SHORT" and rung["price"] <= current_price:
            return False
        return True

    def _place_entry(self, rung: Dict[str, Any]):
        idx = rung["index"]
        side = rung["side"]
        price = rung["price"]
        tp = rung["tp"]

        est_risk = (
            abs(price - (self.stop_loss_long if side == "LONG" else self.stop_loss_short))
            * 1
            * self.mult
            + self.comm_rt
        )
        if (self.active_risk() + est_risk) > self.max_budget:
            return
        if self.open_contracts_count() >= self.max_concurrent_contracts:
            return

        oid = self._new_oid()
        boid = self.broker.place_limit_order(oid, side, price, 1)
        if boid:
            self.orders[oid] = {
                "kind": "ENTRY",
                "rung": idx,
                "side": side,
                "qty": 1,
                "remaining": 1,
                "price": price,
                "tp": tp,
            }

    def _place_tp_for_qty(self, idx: int, side: str, qty: int, target: float) -> Optional[str]:
        if qty <= 0:
            return None
        oid = self._new_oid()
        tp_side = "SHORT" if side == "LONG" else "LONG"
        boid = self.broker.place_limit_order(oid, tp_side, target, qty, reduce_only=True)
        if boid:
            self.orders[oid] = {
                "kind": "TP",
                "rung": idx,
                "side": tp_side,
                "qty": qty,
                "remaining": qty,
                "price": target,
                "target": target,
            }
            return oid
        return None

    def _sync_stops(self):
        for side in ("LONG", "SHORT"):
            qty = sum(pos.get("qty", 0) for pos in self.open.values() if pos.get("side") == side)
            old_oid = self.stop_oid.get(side)
            if old_oid:
                self.broker.cancel_order(old_oid)
                self.orders.pop(old_oid, None)
                self.stop_oid[side] = None

            if qty > 0:
                oid = self._new_oid()
                ord_side = "SELL" if side == "LONG" else "BUY"
                trig = self.stop_loss_long if side == "LONG" else self.stop_loss_short
                boid = self.broker.place_stop_order(oid, ord_side, trig, qty)
                if boid:
                    self.orders[oid] = {
                        "kind": "STOP",
                        "protects": side,
                        "side": ord_side,
                        "qty": qty,
                        "remaining": qty,
                        "price": trig,
                    }
                    self.stop_oid[side] = oid

    def deploy(self, current_price: float):
        self.deployed = True
        self.top_up(current_price)

    def top_up(self, current_price: float):
        if self.stopped:
            return

        for idx, pos in list(self.open.items()):
            qty = pos.get("qty", 0)
            if qty <= 0 or pos.get("ratcheted", False):
                continue
            entry = pos["entry_avg"]
            side = pos["side"]

            if side == "LONG" and current_price >= (entry + self.s_step):
                pos["ratcheted"] = True
            elif side == "SHORT" and current_price <= (entry - self.s_step):
                pos["ratcheted"] = True

        resting_rungs = {
            o["rung"] for o in self.orders.values()
            if o.get("kind") == "ENTRY" and o.get("remaining", 0) > 0
        }
        open_rungs = {idx for idx, pos in self.open.items() if pos.get("qty", 0) > 0}

        sorted_rungs = sorted(self.rungs, key=lambda r: abs(r["price"] - current_price))
        for rung in sorted_rungs:
            idx = rung["index"]
            if idx in resting_rungs or idx in open_rungs:
                continue
            if self._placeable(rung, current_price):
                self._place_entry(rung)

    def on_fills(self, ts: Optional[str] = None):
        new_fills = self.broker.drain_fills()

        for f in new_fills:
            oid = f.get("order_id")
            info = self.orders.get(oid)
            q = int(f.get("size", 0) or 0)
            price = float(f.get("price", 0.0) or 0.0)

            if not info or q <= 0:
                continue

            remaining_before = info.get("remaining", q)
            remaining_after = max(0, remaining_before - q)
            info["remaining"] = remaining_after
            full = remaining_after <= 0
            kind = info.get("kind")

            if kind == "ENTRY":
                idx = info["rung"]
                side = info["side"]
                pos = self.open.setdefault(idx, {
                    "side": side, "qty": 0, "entry_avg": 0.0, "tp_oids": set(), "ratcheted": False
                })

                old_qty = pos["qty"]
                new_qty = old_qty + q
                pos["entry_avg"] = ((pos["entry_avg"] * old_qty) + (price * q)) / float(new_qty)
                pos["qty"] = new_qty
                self.rotations[idx] = self.rotations.get(idx, 0) + 1

                self.records.append({
                    "type": "ENTRY", "side": side, "qty": q, "price": price,
                    "pnl": 0.0, "fee": 0.0, "level": idx, "ts": ts
                })

                tp_oid = self._place_tp_for_qty(idx, side, q, info.get("tp"))
                if tp_oid:
                    pos["tp_oids"].add(tp_oid)

                if full:
                    self.orders.pop(oid, None)
                self._sync_stops()

            elif kind == "TP":
                idx = info["rung"]
                pos = self.open.get(idx)
                if pos:
                    entry = pos.get("entry_avg", price)
                    side = "LONG" if info["side"] == "SHORT" else "SHORT"
                    pnl_gross = (price - entry) * q * self.mult if side == "LONG" else (entry - price) * q * self.mult
                    fee = q * self.comm_rt

                    self.records.append({
                        "type": "TP", "side": info["side"], "qty": q, "price": price,
                        "pnl": pnl_gross - fee, "fee": fee, "level": idx, "ts": ts
                    })

                    pos["qty"] = max(0, pos.get("qty", 0) - q)
                    if pos["qty"] <= 0:
                        self.open.pop(idx, None)

                if full:
                    self.orders.pop(oid, None)
                self._sync_stops()

            elif kind == "STOP":
                protected = info["protects"]
                remaining_fill = q
                for idx, pos in list(self.open.items()):
                    if pos.get("side") != protected or pos.get("qty", 0) <= 0:
                        continue
                    take = min(pos["qty"], remaining_fill)
                    remaining_fill -= take
                    entry = pos.get("entry_avg", price)
                    pnl_gross = (price - entry) * take * self.mult if protected == "LONG" else (entry - price) * take * self.mult
                    fee = take * self.comm_rt

                    self.records.append({
                        "type": "FLATTEN", "side": protected, "qty": take, "price": price,
                        "pnl": pnl_gross - fee, "fee": fee, "level": idx, "ts": ts,
                        "reason": "NATIVE_STOP_ENVELOPE", "via": "stop"
                    })

                    pos["qty"] -= take
                    for tp_oid in list(pos.get("tp_oids", set())):
                        self.broker.cancel_order(tp_oid)
                        self.orders.pop(tp_oid, None)
                    if pos["qty"] <= 0:
                        self.open.pop(idx, None)

                if full:
                    self.orders.pop(oid, None)
                    self.stop_oid[protected] = None
                self._sync_stops()

    def emergency_exit(self, reason: str, price: float, ts: Optional[str] = None):
        self.stopped = True
        self.broker.cancel_all()
        self.stop_oid = {"LONG": None, "SHORT": None}

        for idx, pos in list(self.open.items()):
            qty = pos.get("qty", 0)
            if qty <= 0:
                continue
            side = pos["side"]
            entry = pos.get("entry_avg", price)
            pnl_gross = (price - entry) * qty * self.mult if side == "LONG" else (entry - price) * qty * self.mult
            fee = qty * self.comm_rt
            self.records.append({
                "type": "FLATTEN", "side": side, "qty": qty, "price": price,
                "pnl": pnl_gross - fee, "fee": fee, "level": idx, "ts": ts,
                "reason": reason, "via": "market"
            })

        self.open.clear()
        self.broker.position = 0

    def kill_check(self, current_price: float, range_low: float, range_high: float, ts: Optional[str] = None) -> bool:
        if self.stopped:
            return False
        if current_price < self.stop_loss_long or current_price > self.stop_loss_short:
            self.emergency_exit("ENVELOPE_BREACH", current_price, ts)
            return True
        return False
