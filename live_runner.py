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

    print("=" * 65)
    print("🚀 HARMONIC NANO-GRID V4 EXECUTION RUNNER")
    print(f"Asset: {args.instrument} | Multiplier: ${mult}/pt | Fee: ${fee}/RT")
    print(f"Container: {args.low:.2f} - {args.high:.2f} (Δ {args.high - args.low:.2f} pts)")
    print(f"Source: {args.source.upper()} | Execution: {'🚨 LIVE REAL MONEY' if args.live_exec else '🛡️ PAPER DEMO (51428111)'}")
    print(f"Timeframe: {args.tf} | Directional Breakers Configured: {'YES' if args.brk_low_front else 'GEOMETRIC DEFAULT'}")
    print("=" * 65)

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
            "status": "WORKING" if r["side"] != "MID" and r["cap"] > 0 else "EXIT_ONLY"
        })

    start_dashboard_server(port=8080)

    t_start = time.time()
    tf_rule = TF_LIFECYCLE.get(args.tf, TF_LIFECYCLE["15m"])
    max_duration = tf_rule["max_s"]
    freeze_at = max_duration - tf_rule["freeze_buffer_s"]

    p_eq = args.low + 0.5 * (args.high - args.low)
    swing_amplitude = 0.38 * (args.high - args.low)  # Oscillate across L3 to L13

    state = {
        "status": "ACTIVE_QUOTING",
        "paused": False,
        "flatten": False,
        "last_price": p_eq,
        "position": 0,
        "active_orders": 12,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0
    }

    broker = None
    if args.source == "ironbeam":
        try:
            from src.adapters.ironbeam import IronbeamAdapter
            broker = IronbeamAdapter(is_live_exec=args.live_exec)
            if not broker.authenticate():
                print("[Runner] ❌ Ironbeam authentication failed!")
                return
            broker.start_websocket()
            print(f"[Runner] ✅ Connected to {broker.exec_venue} (Account {broker.account_id})")
        except Exception as e:
            print(f"[Runner] ❌ Broker init error: {e}")
    else:
        print("[Runner] 📼 REPLAY SIMULATOR ACTIVE — generating continuous grid price oscillation.")

    print("\n🟢 Engine running — press Ctrl+C to stop.")
    try:
        step_counter = 0
        while not state["flatten"]:
            elapsed = time.time() - t_start
            rem_freeze = max(0, freeze_at - elapsed)
            rem_flatten = max(0, max_duration - elapsed)

            if elapsed >= max_duration:
                print(f"\n⏰ [T_flatten] Lifespan ({max_duration}s) reached! Flattening...")
                state["status"] = "FLATTENED"
                state["flatten"] = True
            elif elapsed >= freeze_at and state["status"] == "ACTIVE_QUOTING":
                print(f"\n❄️ [T_freeze] Entering HARVEST_ONLY mode...")
                state["status"] = "HARVEST_ONLY_FREEZE"

            # Price Feed Logic
            if broker:
                state["last_price"] = broker.latest_price if hasattr(broker, "latest_price") else 0.0
                state["active_orders"] = len(broker.active_orders) if hasattr(broker, "active_orders") else 0
                state["position"] = broker.positions.get(args.instrument, 0) if hasattr(broker, "positions") else 0
            else:
                # Replay Price Sweep: Smooth sinusoidal trajectory across harmonic rungs
                sim_offset = math.sin(step_counter * 0.12) * swing_amplitude
                raw_sim_price = p_eq + sim_offset
                state["last_price"] = round(round(raw_sim_price / 0.25) * 0.25, 2)
                step_counter += 1

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
                "venue": "REPLAY SIM" if args.source == "replay" else (getattr(broker, "exec_venue", "PAPER DEMO") if broker else "PAPER DEMO"),
                "account": "SIM-REPLAY-01" if args.source == "replay" else (getattr(broker, "account_id", "51428111") if broker else "51428111"),
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

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[Runner] Terminating...")
    finally:
        if broker and hasattr(broker, "cancel_all"):
            broker.cancel_all()
        print("[Runner] Session ended.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Harmonic Nano-Grid V4 Live Runner")
    parser.add_argument("--source", type=str, default="replay", choices=["ironbeam", "replay"])
    parser.add_argument("--instrument", type=str, default="MNQ")
    parser.add_argument("--low", type=float, required=True, help="Macro range low (L0)")
    parser.add_argument("--high", type=float, required=True, help="Macro range high (L16)")
    parser.add_argument("--live-exec", action="store_true")
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
