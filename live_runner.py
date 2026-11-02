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

    # 2. Compute Directional Bias
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

    # 3. Build Telemetry Matrix List
    grid_list = []
    for idx in sorted(grid_dict.keys()):
        r = grid_dict[idx]
        geo_p = round(args.low + idx * geom.s_step, 2)
        snap_delta = round(r["price"] - geo_p, 2)
        grid_list.append({
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
            "boid": None,
            "status": "ARMED" if r["side"] != "MID" and r["cap"] > 0 else "EXIT_ONLY"
        })

    start_dashboard_server(port=8080)

    # 4. Connect Broker & Wire Instrument BEFORE WebSocket
    broker = None
    if args.source == "ironbeam":
        try:
            from src.adapters.ironbeam import IronbeamAdapter
            broker = IronbeamAdapter(is_live_exec=args.live_exec)
            if not broker.authenticate():
                print("[Runner] ❌ Ironbeam authentication failed!")
                return
            
            # CRITICAL: set_instrument FIRST so data_symbol is configured
            broker.set_instrument(args.instrument)
            broker.start_websocket()
            print(f"[Runner] ✅ Connected to {broker.exec_venue} (Account {broker.account_id})")

            # Clean up any stale orders from prior test sessions
            print("[Runner] 🧹 Cancelling prior working orders...")
            broker.cancel_all()
            time.sleep(1.0)

            # Warm up price via REST snapshot if WS is still connecting
            if hasattr(broker, "_ref_price"):
                ref = broker._ref_price(args.instrument)
                if ref and ref > 0:
                    broker.last_price = ref
                    print(f"[Runner] 🎯 Reference snapshot price: {ref:.2f}")

        except Exception as e:
            print(f"[Runner] ❌ Broker init error: {e}")
            return

    # 5. Dispatch Initial Resting Grid Orders
    if broker and args.source == "ironbeam":
        print("\n⚡ [Dispatch] Arming resting limit orders into Ironbeam...")
        directive = bias_data.get("directive", "TWO_SIDED_BALANCED")
        
        for r in grid_list:
            if r["cap"] == 0 or r["contracts"] == 0:
                continue

            # Check Directional Bias Directive Gates
            if directive == "DISARM_SHORTS" and r["side"] == "SHORT":
                r["status"] = "DISARMED_BIAS"
                continue
            if directive == "DISARM_LONGS" and r["side"] == "LONG":
                r["status"] = "DISARMED_BIAS"
                continue

            oid = f"GRID_{args.instrument}_L{r['index']}_{int(time.time())}"
            try:
                boid = broker.place_limit_order(
                    oid=oid,
                    side=r["side"],
                    price=r["price"],
                    size=r["contracts"],
                    instrument=args.instrument,
                    tif="GTC"
                )
                if boid:
                    r["boid"] = boid
                    r["status"] = "WORKING"
                else:
                    r["status"] = "REJECTED"
            except Exception as e:
                print(f"[Runner] ❌ Failed to dispatch L{r['index']}: {e}")
                r["status"] = "ERROR"

    t_start = time.time()
    tf_rule = TF_LIFECYCLE.get(args.tf, TF_LIFECYCLE["15m"])
    max_duration = tf_rule["max_s"]
    freeze_at = max_duration - tf_rule["freeze_buffer_s"]
    p_eq = args.low + 0.5 * (args.high - args.low)

    state = {
        "status": "ACTIVE_QUOTING",
        "paused": False,
        "flatten": False,
        "last_price": p_eq,
        "position": 0,
        "active_orders": 0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0
    }

    print("\n🟢 Engine running — press Ctrl+C to stop.")
    try:
        while not state["flatten"]:
            elapsed = time.time() - t_start
            rem_freeze = max(0, freeze_at - elapsed)
            rem_flatten = max(0, max_duration - elapsed)

            # Lifecycle transitions
            if elapsed >= max_duration:
                print(f"\n⏰ [T_flatten] Session lifespan reached! Flattening all positions...")
                state["status"] = "FLATTENED"
                state["flatten"] = True
                if broker and hasattr(broker, "flatten_position"):
                    broker.flatten_position(args.instrument)
                if broker and hasattr(broker, "cancel_all"):
                    broker.cancel_all()
            elif elapsed >= freeze_at and state["status"] == "ACTIVE_QUOTING":
                print(f"\n❄️ [T_freeze] Entering HARVEST_ONLY mode (canceling entry quotes)...")
                state["status"] = "HARVEST_ONLY_FREEZE"
                if broker and hasattr(broker, "cancel_all"):
                    broker.cancel_all()

            # Read Real Broker State
            if broker:
                state["last_price"] = getattr(broker, "last_price", 0.0)
                state["active_orders"] = len(broker.active) if hasattr(broker, "active") else 0
                state["position"] = broker.get_position() if hasattr(broker, "get_position") else getattr(broker, "position", 0)

            # Recompute Live Directional Bias with Chrono Decay
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

            # Stream Telemetry to Cockpit
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
                "grid": grid_list
            })

            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[Runner] Graceful shutdown requested...")
    finally:
        if broker and hasattr(broker, "cancel_all"):
            print("[Runner] Cancelling all active grid orders...")
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
