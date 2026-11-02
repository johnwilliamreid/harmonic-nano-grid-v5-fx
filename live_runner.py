from dashboard import start_dashboard_server, update_telemetry
import json
import os
import sys
import time
import math
import argparse
import datetime
from dotenv import load_dotenv

from src.config import MULTIPLIERS, COMMISSION_PER_RT, TICK_SIZES
from src.core.grid_geometry import HarmonicGridGeometry
from src.core.directional_bias import DirectionalBiasCalculator

load_dotenv()

TF_LIFECYCLE = {
    "15m": {"max_s": 14400, "freeze_buffer_s": 2700},
    "5m":  {"max_s": 10800, "freeze_buffer_s": 1800},
    "1m":  {"max_s": 5400,  "freeze_buffer_s": 900},
    "15s": {"max_s": 1200,  "freeze_buffer_s": 180},
    "5s":  {"max_s": 600,   "freeze_buffer_s": 90},
    "1s":  {"max_s": 300,   "freeze_buffer_s": 30},
}

def format_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

def run_live_session(args):
    mult = MULTIPLIERS.get(args.instrument, 2.0)
    fee = COMMISSION_PER_RT.get(args.instrument, 1.94)

    print("=" * 70)
    print("🚀 HARMONIC NANO-GRID V4 EXECUTION RUNNER")
    print(f"Asset: {args.instrument} | Multiplier: ${mult}/pt | Fee: ${fee}/RT")
    print(f"Container: {args.low:.2f} - {args.high:.2f} (Δ {args.high - args.low:.2f} pts)")
    print(f"Source: {args.source.upper()} | Execution: {'🚨 LIVE REAL MONEY' if args.live_exec else '🛡️ PAPER DEMO (51428111)'}")
    print(f"Timeframe: {args.tf} | Directional Breakers: {'YES' if args.brk_low_front else 'GEOMETRIC DEFAULT'}")
    print("=" * 70)

    # 1. Initialize Grid Geometry
    geom = HarmonicGridGeometry(
        range_low=args.low,
        range_high=args.high,
        resolution=16,
        multiplier=mult,
        fee_per_rt=fee,
        r_budget=200.00
    )
    grid_dict = geom.generate_grid(
        brk_low_back=args.brk_low_back or args.low,
        brk_low_front=args.brk_low_front,
        brk_low_poc=args.brk_low_poc,
        brk_high_front=args.brk_high_front,
        brk_high_back=args.brk_high_back or args.high,
        brk_high_poc=args.brk_high_poc
    )

    # 2. Compute Initial Directional Bias
    bias_calc = DirectionalBiasCalculator(default_timeframe=args.tf)
    bias_data = bias_calc.compute_bias(
        range_low=args.low,
        range_high=args.high,
        breaker_low_front=args.brk_low_front or (args.low + 0.3125 * (args.high - args.low)),
        breaker_high_front=args.brk_high_front or (args.low + 0.6875 * (args.high - args.low)),
        last_breaker_dir=args.brk_dir,
        elapsed_seconds=args.brk_age,
        mitigation_touches=args.brk_touches,
        timeframe=args.tf
    )

    # 3. Build Internal State Matrix
    grid_by_idx = {}
    grid_list = []
    for idx in sorted(grid_dict.keys()):
        r = grid_dict[idx]
        geo_p = round(args.low + idx * geom.s_step, 2)
        snap_delta = round(r["price"] - geo_p, 2)
        item = {
            "index": idx,
            "side": r["side"],
            "role": r["role"],
            "price": r["price"],
            "geo_price": geo_p,
            "snap_delta": snap_delta,
            "tp": r["tp"],
            "sl": r["sl"],
            "target_rung": r["target_rung"],
            "cap": r["cap"],
            "rotations": r["rotations"],
            "risk_dollar": r.get("risk_dollar", 0.0),
            "reward_dollar": r.get("reward_dollar", 0.0),
            "rr_ratio": r.get("rr_ratio", 0.0),
            "contracts": r.get("contracts", 0),
            "weight": r.get("weight", 0),
            "entry_boid": None,
            "tp_boid": None,
            "sl_boid": None,
            "status": "IDLE" if r["side"] != "MID" and r["cap"] > 0 else "EXIT_ONLY"
        }
        grid_by_idx[idx] = item
        grid_list.append(item)

    start_dashboard_server(port=8080)

    # 4. Connect Broker
    broker = None
    if args.source == "ironbeam":
        try:
            from src.adapters.ironbeam import IronbeamAdapter
            broker = IronbeamAdapter(is_live_exec=args.live_exec)
            if not broker.authenticate():
                print("[Runner] ❌ Ironbeam authentication failed!")
                return
            
            broker.set_instrument(args.instrument)
            broker.start_websocket()
            print(f"[Runner] ✅ Connected to {broker.exec_venue} (Account {broker.account_id})")

            print("[Runner] 🧹 Cancelling prior working orders...")
            broker.cancel_all()
            time.sleep(1.0)

            # Wait up to 5 seconds for live WebSocket price or REST snapshot
            print("[Runner] ⏳ Awaiting first CME quote...")
            for _ in range(10):
                if hasattr(broker, "last_price") and broker.last_price > 0:
                    break
                if hasattr(broker, "_ref_price"):
                    ref = broker._ref_price(args.instrument)
                    if ref and ref > 0:
                        broker.last_price = ref
                        break
                time.sleep(0.5)

            print(f"[Runner] 🎯 Reference price: {getattr(broker, 'last_price', 0.0):.2f}")

        except Exception as e:
            print(f"[Runner] ❌ Broker init error: {e}")
            return

    t_start = time.time()
    tf_rule = TF_LIFECYCLE.get(args.tf, TF_LIFECYCLE["15m"])
    max_duration = tf_rule["max_s"]
    freeze_at = max_duration - tf_rule["freeze_buffer_s"]
    p_eq = args.low + 0.5 * (args.high - args.low)

    state = {
        "status": "ACTIVE_QUOTING",
        "paused": False,
        "flatten": False,
        "last_price": getattr(broker, "last_price", p_eq) if broker else p_eq,
        "position": 0,
        "active_orders": 0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0
    }

    # Tracking active brackets: idx -> {tp_boid, sl_boid, size, side}
    active_brackets = {}

    def is_order_passive(side: str, price: float, market_price: float) -> bool:
        """Enforces that limit orders strictly rest passively on the order book."""
        if market_price <= 0:
            return False
        if side.upper() in ("LONG", "BUY"):
            return price <= (market_price - 0.25)
        elif side.upper() in ("SHORT", "SELL"):
            return price >= (market_price + 0.25)
        return False

    print("\n🟢 Engine running — monitoring quotes and fills. Press Ctrl+C to stop.")
    try:
        while not state["flatten"]:
            now = time.time()
            elapsed = now - t_start
            rem_freeze = max(0, freeze_at - elapsed)
            rem_flatten = max(0, max_duration - elapsed)

            # -----------------------------------------------------------------
            # 1. Lifecycle Check
            # -----------------------------------------------------------------
            if elapsed >= max_duration:
                print(f"\n⏰ [T_flatten] Session lifespan reached! Flattening...")
                state["status"] = "FLATTENED"
                state["flatten"] = True
                if broker:
                    broker.flatten_position(args.instrument)
                    broker.cancel_all()
                break
            elif elapsed >= freeze_at and state["status"] == "ACTIVE_QUOTING":
                print(f"\n❄️ [T_freeze] HARVEST_ONLY mode (Canceling all unfilled entry quotes)...")
                state["status"] = "HARVEST_ONLY_FREEZE"
                # Cancel only initial entry quotes, leave active TP harvest limits intact
                for idx, r in grid_by_idx.items():
                    if r["status"] == "WORKING" and r["entry_boid"] and broker:
                        broker.cancel_order(r["entry_boid"])
                        r["status"] = "FROZEN"

            # -----------------------------------------------------------------
            # 2. Update Market State
            # -----------------------------------------------------------------
            if broker:
                if hasattr(broker, "last_price") and broker.last_price > 0:
                    state["last_price"] = broker.last_price
                state["active_orders"] = len(broker.active) if hasattr(broker, "active") else 0
                state["position"] = broker.get_position() if hasattr(broker, "get_position") else getattr(broker, "position", 0)

            live_px = state["last_price"]

            # -----------------------------------------------------------------
            # 3. Process Inbound Fills (TP / SL / Entry)
            # -----------------------------------------------------------------
            if broker and hasattr(broker, "drain_fills"):
                fills = broker.drain_fills()
                for fl in fills:
                    oid = fl.get("order_id", "")
                    fill_side = fl.get("side", "")
                    fill_px = fl.get("price", 0.0)
                    fill_qty = fl.get("size", 0)
                    print(f"\n⚡ [FILL EVENT] {fill_side} {fill_qty} @ {fill_px:.2f} (oid={oid})")

                    # CASE A: Entry Limit Filled -> Deploy TP + SL Bracket
                    if oid.startswith("GRID_"):
                        parts = oid.split("_")
                        try:
                            idx = int(parts[2].replace("L", ""))
                            r = grid_by_idx.get(idx)
                            if r:
                                r["status"] = "FILLED_IN_POSITION"
                                r["entry_boid"] = None

                                # Bracket Sides
                                bracket_side = "SELL" if r["side"] == "LONG" else "BUY"
                                ts = int(time.time())

                                # 1. Place Uncoupled 1/8 TP Limit Order
                                tp_oid = f"TP_{args.instrument}_L{idx}_{ts}"
                                tp_boid = broker.place_limit_order(
                                    oid=tp_oid,
                                    side=bracket_side,
                                    price=r["tp"],
                                    size=fill_qty,
                                    reduce_only=True,
                                    instrument=args.instrument,
                                    tif="GTC"
                                )

                                # 2. Place Invalidation Catastrophic Stop
                                sl_oid = f"SL_{args.instrument}_L{idx}_{ts}"
                                sl_boid = broker.place_stop_order(
                                    oid=sl_oid,
                                    side=bracket_side,
                                    price=r["sl"],
                                    size=fill_qty,
                                    instrument=args.instrument,
                                    tif="GTC"
                                )

                                r["tp_boid"] = tp_boid
                                r["sl_boid"] = sl_boid
                                active_brackets[idx] = {
                                    "tp_boid": tp_boid, "sl_boid": sl_boid,
                                    "tp_oid": tp_oid, "sl_oid": sl_oid,
                                    "size": fill_qty, "side": r["side"]
                                }
                                print(f"🎯 [Bracket Deployed L{idx}] TP Limit: {r['tp']:.2f} | Stop: {r['sl']:.2f}")

                        except Exception as e:
                            print(f"[Runner] ❌ Error handling entry fill {oid}: {e}")

                    # CASE B: Take-Profit Limit Filled -> Cancel Stop Loss & Increment Rotation
                    elif oid.startswith("TP_"):
                        parts = oid.split("_")
                        try:
                            idx = int(parts[2].replace("L", ""))
                            r = grid_by_idx.get(idx)
                            if r and idx in active_brackets:
                                brk = active_brackets.pop(idx)
                                if brk.get("sl_boid"):
                                    print(f"🧹 [OCO] Canceling paired stop {brk['sl_boid']} for L{idx}")
                                    broker.cancel_order(brk["sl_boid"])

                                r["rotations"] += 1
                                r["tp_boid"] = None
                                r["sl_boid"] = None
                                r["status"] = "ROTATED_HARVEST"
                                print(f"🏆 [HARVEST COMPLETE] L{idx} harvested at {fill_px:.2f}! Total rotations: {r['rotations']}/{r['cap']}")
                        except Exception as e:
                            print(f"[Runner] ❌ Error handling TP fill {oid}: {e}")

                    # CASE C: Stop-Loss Filled -> Cancel Take-Profit & Mark Invalidation
                    elif oid.startswith("SL_"):
                        parts = oid.split("_")
                        try:
                            idx = int(parts[2].replace("L", ""))
                            r = grid_by_idx.get(idx)
                            if r and idx in active_brackets:
                                brk = active_brackets.pop(idx)
                                if brk.get("tp_boid"):
                                    print(f"🧹 [OCO] Canceling paired TP {brk['tp_boid']} for L{idx}")
                                    broker.cancel_order(brk["tp_boid"])

                                r["tp_boid"] = None
                                r["sl_boid"] = None
                                r["status"] = "STOPPED_OUT"
                                print(f"🛑 [STOPPED OUT] L{idx} hit catastrophic stop at {fill_px:.2f}!")
                        except Exception as e:
                            print(f"[Runner] ❌ Error handling SL fill {oid}: {e}")

            # -----------------------------------------------------------------
            # 4. Passive Order Dispatch & Retracement Quoting
            # -----------------------------------------------------------------
            if broker and state["status"] == "ACTIVE_QUOTING" and live_px > 0:
                directive = bias_data.get("directive", "TWO_SIDED_BALANCED")

                for idx, r in grid_by_idx.items():
                    # Skip buffers, eq mid, or capped rungs
                    if r["cap"] == 0 or r["contracts"] == 0 or r["rotations"] >= r["cap"]:
                        continue

                    # If currently holding position or in bracket, do not re-quote entry
                    if r["status"] in ("FILLED_IN_POSITION", "STOPPED_OUT"):
                        continue

                    # Bias directive filtering
                    if directive == "DISARM_SHORTS" and r["side"] == "SHORT":
                        r["status"] = "DISARMED_BIAS"
                        continue
                    if directive == "DISARM_LONGS" and r["side"] == "LONG":
                        r["status"] = "DISARMED_BIAS"
                        continue

                    # Check passive quoting spread condition
                    is_passive = is_order_passive(r["side"], r["price"], live_px)

                    # If order is NOT working yet:
                    if r["status"] in ("IDLE", "AWAITING_RETRACEMENT", "ROTATED_HARVEST"):
                        if is_passive:
                            oid = f"GRID_{args.instrument}_L{r['index']}_{int(now)}"
                            boid = broker.place_limit_order(
                                oid=oid,
                                side=r["side"],
                                price=r["price"],
                                size=r["contracts"],
                                instrument=args.instrument,
                                tif="GTC"
                            )
                            if boid:
                                r["entry_boid"] = boid
                                r["status"] = "WORKING"
                            else:
                                r["status"] = "REJECTED"
                        else:
                            r["status"] = "AWAITING_RETRACEMENT"

            # -----------------------------------------------------------------
            # 5. Live Directional Bias & Telemetry Broadcast
            # -----------------------------------------------------------------
            live_brk_age = args.brk_age + elapsed
            bias_update = bias_calc.compute_bias(
                range_low=args.low,
                range_high=args.high,
                breaker_low_front=args.brk_low_front or (args.low + 0.3125 * (args.high - args.low)),
                breaker_high_front=args.brk_high_front or (args.low + 0.6875 * (args.high - args.low)),
                last_breaker_dir=args.brk_dir,
                elapsed_seconds=live_brk_age,
                mitigation_touches=args.brk_touches,
                timeframe=args.tf,
                prior_regime=bias_data["regime"]
            )
            bias_data.update(bias_update)
            bias_data["elapsed_s"] = live_brk_age
            bias_data["timeframe"] = args.tf
            bias_data["dir_last"] = args.brk_dir
            bias_data["touches"] = args.brk_touches
            bias_data["k"] = bias_calc.TIMEFRAME_DECAY.get(args.tf, 0.00025)

            update_telemetry({
                "venue": getattr(broker, "exec_venue", "PAPER DEMO") if broker else "PAPER DEMO",
                "account": getattr(broker, "account_id", "51428111") if broker else "51428111",
                "instrument": args.instrument,
                "live_price": state["last_price"],
                "position": state["position"],
                "active_orders": state["active_orders"],
                "realized_pnl": state["realized_pnl"],
                "unrealized_pnl": state["unrealized_pnl"],
                "container": {
                    "low": args.low, "high": args.high,
                    "delta_p": args.high - args.low,
                    "p_eq": p_eq,
                    "s_step": geom.s_step, "s_harvest": geom.s_harvest
                },
                "bias": bias_data,
                "lifecycle": {
                    "tf": args.tf,
                    "status": state["status"],
                    "elapsed_str": format_hms(elapsed),
                    "to_freeze_str": format_hms(rem_freeze),
                    "to_flatten_str": format_hms(rem_flatten),
                    "t_max_min": int(max_duration / 60),
                    "t_freeze_min": int((max_duration - freeze_at) / 60)
                },
                "grid": list(grid_by_idx.values())
            })

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[Runner] Graceful shutdown requested...")
    finally:
        if broker and hasattr(broker, "cancel_all"):
            print("[Runner] Cancelling working orders...")
            broker.cancel_all()
        print("[Runner] Session cleanly stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Harmonic Nano-Grid V4 Live Runner")
    parser.add_argument("--source", type=str, default="ironbeam", choices=["ironbeam", "replay"])
    parser.add_argument("--instrument", type=str, default="MNQ")
    parser.add_argument("--low", type=float, required=True, help="Macro range low (L0)")
    parser.add_argument("--high", type=float, required=True, help="Macro range high (L16)")
    parser.add_argument("--live-exec", action="store_true", help="Send REAL-MONEY orders to Ironbeam (LIVE)")
    parser.add_argument("--tf", type=str, default="15m", choices=["15m", "5m", "1m", "15s", "5s", "1s"])
    parser.add_argument("--brk-low-front", type=float, default=None)
    parser.add_argument("--brk-low-back", type=float, default=None)
    parser.add_argument("--brk-low-poc", type=float, default=None)
    parser.add_argument("--brk-high-front", type=float, default=None)
    parser.add_argument("--brk-high-back", type=float, default=None)
    parser.add_argument("--brk-high-poc", type=float, default=None)
    parser.add_argument("--brk-dir", type=str, default="BULLISH", choices=["BULLISH", "BEARISH"])
    parser.add_argument("--brk-touches", type=int, default=0, choices=[0, 1, 2])
    parser.add_argument("--brk-age", type=float, default=0.0)

    args = parser.parse_args()
    run_live_session(args)
