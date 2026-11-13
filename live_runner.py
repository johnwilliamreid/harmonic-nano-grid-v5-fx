from src.core.dispatcher import MultiAccountDispatcher
from dashboard import set_fleet_flatten_callback, set_account_action_callback, set_account_budget_callback, set_account_instrument_callback
from src.core.interfaces import OrderIntent
import json
import os
import sys
import time
import math
import argparse
import datetime
from typing import Any, Optional, Dict, List, Tuple
from dotenv import load_dotenv

from dashboard import start_dashboard_server, update_telemetry, set_arm_callback, set_flatten_callback, set_deploy_callback
import dashboard

from src.config import MULTIPLIERS, COMMISSION_PER_RT, TICK_SIZES
from src.core.grid_geometry import HarmonicGridGeometry
from src.core.data_engine import GridDataEngine
from src.core.directional_bias import DirectionalBiasCalculator
from src.adapters.telegram_adapter import TelegramBridge
from src.core.data_store import DataStore

load_dotenv()

def get_live_remaining_risk(grid_dict, instrument="MNQ", directive=None):
    """Calculates single-side directional remaining catastrophic risk directly from rung definitions."""
    if not grid_dict:
        return {"long": 0.0, "short": 0.0, "max": 0.0}
    long_rem = 0.0
    short_rem = 0.0
    
    # If directive disarms a side, honor it immediately
    disarm_longs = (directive == "DISARM_LONGS")
    disarm_shorts = (directive == "DISARM_SHORTS")

    for idx, r in grid_dict.items():
        side = r.get("side")
        if side not in ("LONG", "SHORT"):
            continue
        if side == "LONG" and disarm_longs:
            continue
        if side == "SHORT" and disarm_shorts:
            continue

        contracts = r.get("contracts", 0)
        cap = r.get("cap", 4)
        rots = r.get("rotations", 0)
        status = str(r.get("status", "")).upper()
        
        if "DISARM" in status or "INVALID" in status or contracts <= 0:
            continue

        open_pos = abs(r.get("open_position", 0) or r.get("position", 0))
        is_exhausted = (cap > 0 and rots >= cap) or ("EXHAUST" in status) or ("EXIT_ONLY" in status)

        # Use explicitly stored risk_dollar if open_pos is 0 and not exhausted
        if open_pos == 0 and not is_exhausted and "risk_dollar" in r and r["risk_dollar"] > 0:
            rung_risk = float(r["risk_dollar"])
        else:
            unit_k = float(r.get("unit_risk") or (r.get("risk_dollar", 0.0) / contracts if contracts > 0 else 0.0))
            qty = open_pos if is_exhausted else max(contracts, open_pos)
            rung_risk = qty * unit_k

        if side == "LONG":
            long_rem += rung_risk
        elif side == "SHORT":
            short_rem += rung_risk

    long_rem = round(long_rem, 2)
    short_rem = round(short_rem, 2)
    max_rem = round(max(long_rem, short_rem), 2)
    return {"long": long_rem, "short": short_rem, "max": max_rem}

def get_live_remaining_risk_summary(grid_dict, instrument="MNQ"):
    r = get_live_remaining_risk(grid_dict, instrument)
    return f"\n📉 <b>Remaining Risk:</b> 🟢 L: <b>${r['long']:.2f}</b> | 🔴 S: <b>${r['short']:.2f}</b> (Max: <b>${r['max']:.2f}</b>)"

# Symmetrical 16x Timeframe Lifespan standard (Freeze at 14x)
TF_LIFECYCLE = {
    "4h":  {"max_s": 230400, "freeze_buffer_s": 28800}, # 64 hrs max (16x4h), 8h freeze cutoff
    "4H":  {"max_s": 230400, "freeze_buffer_s": 28800},
    "1hr": {"max_s": 57600,  "freeze_buffer_s": 7200},  # 16 hrs max (16x1h), 2h freeze cutoff
    "1h":  {"max_s": 57600,  "freeze_buffer_s": 7200},
    "1H":  {"max_s": 57600,  "freeze_buffer_s": 7200},
    "15m": {"max_s": 14400,  "freeze_buffer_s": 1800},  # 4 hrs max (16x15m), 30m freeze cutoff
    "4m":  {"max_s": 3840,   "freeze_buffer_s": 480},   # 64 min max (16x4m), 8m freeze cutoff
    "1m":  {"max_s": 960,    "freeze_buffer_s": 120},   # 16 min max (16x1m), 2m freeze cutoff
}

def format_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

def derive_asymmetric_rungs(geom, low, high, brk_low_front, brk_low_back, brk_high_front, brk_high_back):
    f_low_px = brk_low_front or (low + 0.3125 * (high - low))
    b_low_px = brk_low_back or low
    f_high_px = brk_high_front or (high - 0.3125 * (high - low))
    b_high_px = brk_high_back or high

    d_front = max(1, min(7, int(round((f_low_px - geom.range_low) / geom.s_step))))
    d_mt = max(1, min(d_front - 1, int(round(((b_low_px + 0.5 * (f_low_px - b_low_px)) - geom.range_low) / geom.s_step))))

    s_front = max(9, min(15, int(round((f_high_px - geom.range_low) / geom.s_step))))
    s_mt = max(s_front + 1, min(15, int(round(((f_high_px + 0.5 * (b_high_px - f_high_px)) - geom.range_low) / geom.s_step))))

    d_rungs = [d_front, d_mt]
    for mid in range(d_mt + 1, d_front):
        d_rungs.append(mid)
    d_rungs = sorted(list(set(d_rungs)))

    s_rungs = [s_front, s_mt]
    for mid in range(s_front + 1, s_mt):
        s_rungs.append(mid)
    s_rungs = sorted(list(set(s_rungs)))

    return {
        "SNAPPED": {
            "name": "Breaker Snapped (Mitigation Priority)",
            "discount_rungs": d_rungs,
            "premium_rungs": s_rungs
        },
        "LEAN_2_RUNG": {
            "name": "Lean Mitigation Core",
            "discount_rungs": [d_front, d_mt],
            "premium_rungs": [s_front, s_mt]
        }
    }


def dispatch_order_intent(intent: OrderIntent, broker) -> Optional[str]:
    """
    Converts a normalized OrderIntent into multi-account fleet execution calls
    or single broker fallback.
    """
    if not broker:
        return None

    # Fleet Dispatcher delegation
    if hasattr(broker, "dispatch_intent"):
        res = broker.dispatch_intent(intent)
        # Return a truthy composite ID if at least one account succeeded
        valid_orders = [f"{k}:{v}" for k, v in res.items() if v]
        return ",".join(valid_orders) if valid_orders else None

    inst = intent.instrument or "MNQ"
    tif = intent.metadata.get("tif", "GTC")

    if intent.intent_type in ("QUOTE_ENTRY", "TP_HARVEST"):
        reduce_only = intent.metadata.get("reduce_only", False)
        oid = intent.metadata.get("oid") or f"{intent.intent_type}_{inst}_L{intent.rung_idx}_{int(time.time())}"
        try:
            return broker.place_limit_order(
                oid=oid,
                side=intent.side,
                price=intent.price,
                size=intent.size,
                reduce_only=reduce_only,
                instrument=inst,
                tif=tif
            )
        except TypeError:
            # Fallback if adapter does not support reduce_only kwarg
            return broker.place_limit_order(
                oid=oid,
                side=intent.side,
                price=intent.price,
                size=intent.size,
                instrument=inst,
                tif=tif
            )

    elif intent.intent_type == "SL_STOP":
        oid = intent.metadata.get("oid") or f"SL_{inst}_L{intent.rung_idx}_{int(time.time())}"
        if hasattr(broker, "place_stop_order"):
            return broker.place_stop_order(
                oid=oid,
                side=intent.side,
                price=intent.price,
                size=intent.size,
                instrument=inst,
                tif=tif
            )

    elif intent.intent_type == "CANCEL":
        oid = intent.metadata.get("oid")
        if oid and hasattr(broker, "cancel_order"):
            return "CANCELLED" if broker.cancel_order(oid) else None

    return None


def run_live_session(args):
    fleet_dispatcher = None
    if getattr(args, "fleet", True):
        try:
            import os
            accts_path = "accounts.json"
            if os.path.exists(accts_path):
                fleet_dispatcher = MultiAccountDispatcher(accts_path)
                set_fleet_flatten_callback(fleet_dispatcher.flatten_all_fleet)
                set_account_action_callback(fleet_dispatcher.account_action)
                set_account_budget_callback(fleet_dispatcher.update_account_budget)
                set_account_instrument_callback(fleet_dispatcher.update_account_instrument)
                print(f"[Runner] 🌐 Fleet Dispatcher initialized with {len(fleet_dispatcher.accounts)} accounts!")
        except Exception as e:
            print(f"[Runner] ⚠️ Fleet Dispatcher init warning: {e}")
    mult = MULTIPLIERS.get(args.instrument, 2.0)
    fee = COMMISSION_PER_RT.get(args.instrument, 1.68 if args.instrument == "NNQ" else 1.86)

    has_init_range = (args.low is not None and args.high is not None and args.high > args.low)
    init_low = args.low if has_init_range else 27000.00
    init_high = args.high if has_init_range else 28000.00

    print("=" * 70)
    print("🚀 HARMONIC NANO-GRID V5 EXECUTION RUNNER")
    print(f"Asset: {args.instrument} | Multiplier: ${mult}/pt | Fee: ${fee}/RT")
    if has_init_range:
        print(f"Container: {args.low:.2f} - {args.high:.2f} (Δ {args.high - args.low:.2f} pts)")
    else:
        print("Container: STANDBY (Awaiting Range Definition via Cockpit/Telegram)")
    print(f"Source: {args.source.upper()} | Execution: {'🚨 LIVE REALMONEY' if args.live_exec else '🛡️ PAPER DEMO (51428111)'}")
    print(f"Timeframe: {args.tf} | Directional Breakers: {'YES' if args.brk_low_front else 'GEOMETRIC DEFAULT'}")
    print("=" * 70)

    # 1. Initialize Grid Geometry & Generate Proposal
    geom = HarmonicGridGeometry(
        range_low=init_low,
        range_high=init_high,
        resolution=16,
        multiplier=mult,
        fee_per_rt=fee,
        r_side_budget=args.risk,
        instrument=args.instrument
    )

    proposal = geom.generate_proposal_package(
        brk_low_front=args.brk_low_front,
        brk_low_back=args.brk_low_back,
        brk_high_front=args.brk_high_front,
        brk_high_back=args.brk_high_back
    )
    custom_profiles = derive_asymmetric_rungs(
        geom, init_low, init_high,
        args.brk_low_front, args.brk_low_back,
        args.brk_high_front, args.brk_high_back
    )

    startup_mode = getattr(args, "profile", "SNAPPED")
    if startup_mode in custom_profiles:
        active_profile = custom_profiles[startup_mode]
    elif startup_mode in proposal.get("options", {}):
        active_profile = proposal["options"][startup_mode]
    else:
        best_fit = proposal["diagnosis"]["best_fit"]
        active_profile = proposal["options"][best_fit]

    grid_dict = geom.generate_grid(
        active_discount_rungs=active_profile["discount_rungs"],
        active_premium_rungs=active_profile["premium_rungs"],
        brk_low_back=args.brk_low_back or args.low,
        brk_low_front=args.brk_low_front,
        brk_low_poc=args.brk_low_poc,
        brk_high_front=args.brk_high_front,
        brk_high_back=args.brk_high_back or args.high,
        brk_high_poc=args.brk_high_poc
    )

    # 2. Compute Initial Directional Bias
    bias_calc = DirectionalBiasCalculator(default_timeframe=args.tf)
    b_low = args.brk_low_front or (init_low + 0.3125 * (init_high - init_low))
    b_high = args.brk_high_front or (init_low + 0.6875 * (init_high - init_low))
    bias_data = bias_calc.compute_bias(
        range_low=init_low,
        range_high=init_high,
        breaker_low_front=b_low,
        breaker_high_front=b_high,
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
        geo_p = round(geom.range_low + idx * geom.s_step, 2)
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
            "decimal_multiplier": r.get("decimal_multiplier", 1.0),
            "exact_qty": r.get("exact_qty", 0.0),
            "unit_risk": r.get("unit_risk", 0.0),
            "entry_boid": None,
            "tp_boid": None,
            "sl_boid": None,
            "fill_price": None,
            "fill_qty": 0,
            "booked_pnl": 0.0,
            "floating_pnl": 0.0,
            "status": "IDLE" if r["side"] != "MID" and r["cap"] > 0 else "EXIT_ONLY"
        }
        grid_by_idx[idx] = item
        grid_list.append(item)

    t_start = time.time()
    tf_rule = TF_LIFECYCLE.get(args.tf, TF_LIFECYCLE["15m"])
    max_duration = tf_rule["max_s"]
    freeze_at = max_duration - tf_rule["freeze_buffer_s"]
    p_eq = init_low + 0.5 * (init_high - init_low)

    # Initialize Persistent SQLite WAL DataStore
    data_store = DataStore(timeframe=args.tf)
    data_store.register_grid_levels(grid_by_idx, session_id=f"{args.instrument}_{args.tf}_{int(t_start)}")

    start_dashboard_server(port=getattr(args, "port", 8090))

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

            print("[Runner] ⏳ Awaiting first CME quote...")
            for _ in range(15):
                if hasattr(broker, "last_price") and broker.last_price is not None and broker.last_price > 0:
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

    state = {
        "status": ("AWAITING_ARM" if getattr(args, "wait_arm", False) else "ACTIVE_QUOTING") if has_init_range else "AWAITING_RANGE",
        "paused": False,
        "flatten": False,
        "last_price": getattr(broker, "last_price", p_eq) if broker else p_eq,
        "position": 0,
        "active_orders": 0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "active_profile_name": active_profile["name"],
        "side_risk": args.risk
    }

    active_brackets = {}

    def get_fresh_market_price() -> float:
        if broker:
            if hasattr(broker, "last_price") and broker.last_price is not None and broker.last_price > 0:
                return broker.last_price
            if hasattr(broker, "_ref_price"):
                ref = broker._ref_price(args.instrument)
                if ref and ref > 0:
                    broker.last_price = ref
                    return ref
        return state["last_price"]

    def is_order_strictly_passive(side: str, price: float, market_price: float) -> bool:
        if market_price <= 0:
            return False
        if side.upper() in ("LONG", "BUY"):
            return price <= (market_price - 0.50)
        elif side.upper() in ("SHORT", "SELL"):
            return price >= (market_price + 0.50)
        return False

    def rearm_profile(opt_key: str, force: bool = False, brk_low_front=None, brk_low_back=None, brk_high_front=None, brk_high_back=None):
        nonlocal active_profile, grid_dict, grid_by_idx

        current_name = state.get("active_profile_name")
        if not force and opt_key in ("SNAPPED", "LEAN_2_RUNG", "OPTION_A", "OPTION_B", "OPTION_C", "OPTION_D", "OPTION_E", "OPTION_F_TREND_BREAKER", "OPTION_G_MT_CORE", "OPTION_H_MT_SPAN"):
            if current_name and (opt_key in current_name or (opt_key == "SNAPPED" and "Mitigation Priority" in current_name)):
                print(f"[RE-ARM GUARD] Grid already armed with {current_name}. Skipping redundant order cycling.")
                return True

        cur_low = geom.range_low
        cur_high = geom.range_high
        b_lf = brk_low_front if brk_low_front is not None else args.brk_low_front
        b_lb = brk_low_back if brk_low_back is not None else args.brk_low_back
        b_hf = brk_high_front if brk_high_front is not None else args.brk_high_front
        b_hb = brk_high_back if brk_high_back is not None else args.brk_high_back

        custom_profiles = derive_asymmetric_rungs(
            geom, cur_low, cur_high,
            b_lf, b_lb, b_hf, b_hb
        )
        if opt_key in proposal.get("options", {}):
            opt = proposal["options"][opt_key]
        elif opt_key in custom_profiles:
            opt = custom_profiles[opt_key]
        else:
            print(f"[RE-ARM ERROR] Unknown profile: {opt_key}")
            return False
        active_profile = opt
        state["active_profile_name"] = opt["name"]

        print(f"\n🔄 [TELEGRAM RE-ARM] Swapping grid to: {opt['name']}")
        if broker:
            for idx, r in grid_by_idx.items():
                if r.get("entry_boid") and r["status"] == "WORKING":
                    broker.cancel_order(r["entry_boid"])
                    r["entry_boid"] = None
                    r["status"] = "IDLE"

        new_grid = geom.generate_grid(
            active_discount_rungs=opt["discount_rungs"],
            active_premium_rungs=opt["premium_rungs"],
            target_mapping=opt.get("target_mapping"),
            brk_low_back=b_lb or cur_low,
            brk_low_front=b_lf,
            brk_low_poc=args.brk_low_poc,
            brk_high_front=b_hf,
            brk_high_back=b_hb or cur_high,
            brk_high_poc=args.brk_high_poc
        )

        for idx, new_r in new_grid.items():
            if idx in grid_by_idx:
                grid_by_idx[idx]["contracts"] = new_r["contracts"]
                grid_by_idx[idx]["cap"] = new_r["cap"]
                grid_by_idx[idx]["risk_dollar"] = new_r["risk_dollar"]
                grid_by_idx[idx]["reward_dollar"] = new_r["reward_dollar"]
                grid_by_idx[idx]["rr_ratio"] = new_r["rr_ratio"]
                grid_by_idx[idx]["tp"] = new_r["tp"]
                grid_by_idx[idx]["sl"] = new_r["sl"]
                grid_by_idx[idx]["price"] = new_r["price"]
                grid_by_idx[idx]["role"] = new_r["role"]
                grid_by_idx[idx]["snap_delta"] = new_r["snap_delta"]
                if grid_by_idx[idx]["status"] not in ("FILLED_IN_POSITION", "STOPPED_OUT"):
                    grid_by_idx[idx]["status"] = "IDLE" if new_r["cap"] > 0 else "EXIT_ONLY"

        state["status"] = "ACTIVE_QUOTING"
        return True

    def on_dashboard_arm(data: dict):
        nonlocal geom, proposal, active_profile, grid_dict, grid_by_idx, t_start, freeze_at, max_duration, state, mult, fee
        try:
            mode = data.get("mode", "SNAPPED")
            posture = data.get("posture", "DIRECTIVE")
            print(f"\n🌐 [DASHBOARD ARM] Processing arm command: mode={mode}, posture={posture}")

            if mode == "CANCEL" or posture == "FLATTEN":
                print("\n🚨 [EMERGENCY FLATTEN] Canceling all working orders and closing open positions...")
                if broker:
                    try:
                        broker.cancel_all()
                    except Exception as e:
                        print(f"[Broker] ⚠️ Cancel all error: {e}")

                    curr_pos = getattr(broker, "net_position", 0) or state.get("position", 0)
                    if curr_pos != 0:
                        flat_side = "SELL" if curr_pos > 0 else "BUY"
                        flat_qty = abs(curr_pos)
                        print(f"[Runner] 🚨 Liquidating {curr_pos} open position via MARKET {flat_side} {flat_qty}...")
                        try:
                            broker.place_market_order(flat_side, flat_qty)
                        except Exception as e:
                            print(f"[Broker] ⚠️ Market flatten error: {e}")

                for r in grid_by_idx.values():
                    r["entry_boid"] = None
                    r["status"] = "STANDBY"
                    r["contracts"] = 0

                state["status"] = "FLATTENED_DISARMED"
                state["panic_flatten_requested"] = True

                if hasattr(dashboard, "_TELEMETRY"):
                    dashboard._TELEMETRY["lifecycle"]["status"] = "FLATTENED_DISARMED"
                    dashboard._TELEMETRY["active_orders"] = 0
                    dashboard._TELEMETRY["position"] = 0
                    for r in dashboard._TELEMETRY.get("grid", []):
                        r["status"] = "STANDBY"
                        r["contracts"] = 0

                print("✅ [EMERGENCY FLATTEN] Complete. Engine is disarmed in STANDBY mode.")
                return

            setup = getattr(dashboard, "_TELEMETRY", {}).get("pending_setup")
            geom_changed = False
            b_lf, b_lb, b_hf, b_hb = None, None, None, None

            if setup and "low" in setup and "high" in setup:
                n_inst = str(setup.get("instrument", args.instrument)).strip().upper()
                if n_inst != args.instrument:
                    print(f"\n🔀 [INSTRUMENT SWITCH] Changing active asset from {args.instrument} to {n_inst}...")
                    args.instrument = n_inst
                    if broker and hasattr(broker, "set_instrument"):
                        broker.set_instrument(args.instrument)

                mult = MULTIPLIERS.get(args.instrument, 2.0 if args.instrument == "MNQ" else 0.2)
                fee = COMMISSION_PER_RT.get(args.instrument, 1.68 if args.instrument == "NNQ" else 1.86)

                if "touches" in setup: args.brk_touches = int(setup["touches"])
                if "dir_recent" in setup: args.brk_dir = str(setup["dir_recent"])
                if "elapsed_minutes" in setup: args.brk_age = float(setup["elapsed_minutes"]) * 60.0
                if setup.get("brk_low_front"): args.brk_low_front = float(setup["brk_low_front"])
                if setup.get("brk_low_back"): args.brk_low_back = float(setup["brk_low_back"])
                if setup.get("brk_high_front"): args.brk_high_front = float(setup["brk_high_front"])
                if setup.get("brk_high_back"): args.brk_high_back = float(setup["brk_high_back"])
                n_low = float(setup["low"])
                n_high = float(setup["high"])
                n_risk = float(setup.get("risk", args.risk))
                n_tf = str(setup.get("tf", args.tf)).strip()
                args.risk = n_risk
                args.tf = n_tf
                b_lf = setup.get("brk_low_front")
                b_lb = setup.get("brk_low_back")
                b_hf = setup.get("brk_high_front")
                b_hb = setup.get("brk_high_back")

                print(f"📐 [DASHBOARD ARM] Adopting newly staged container: {n_low:.2f} — {n_high:.2f} (Risk: ${n_risk}, TF: {n_tf})")
                geom = HarmonicGridGeometry(
                    range_low=n_low,
                    range_high=n_high,
                    resolution=16,
                    multiplier=mult,
                    fee_per_rt=fee,
                    r_side_budget=n_risk,
                    instrument=args.instrument
                )
                proposal = geom.generate_proposal_package(
                    brk_low_front=b_lf,
                    brk_low_back=b_lb,
                    brk_high_front=b_hf,
                    brk_high_back=b_hb
                )
                geom_changed = True

                t_start = time.time()
                tf_key = args.tf if args.tf in TF_LIFECYCLE else "15m"
                tf_rule = TF_LIFECYCLE.get(tf_key, TF_LIFECYCLE["15m"])
                max_duration = tf_rule["max_s"]
                freeze_at = max_duration - tf_rule["freeze_buffer_s"]
                print(f"⏱️ [LIFECYCLE ADOPTED] Timeframe: {args.tf} | Lifespan: {max_duration}s ({max_duration/60:.1f}m) | Freeze at: {freeze_at}s")

            if broker and geom_changed:
                print("[Runner] 🧹 Canceling all resting limit orders from prior container...")
                try:
                    broker.cancel_all()
                except Exception as e:
                    print(f"[Broker] ⚠️ Cancel error: {e}")
                for r in grid_by_idx.values():
                    r["entry_boid"] = None
                    r["status"] = "STANDBY"

            rearm_profile(
                mode,
                force=True,
                brk_low_front=b_lf,
                brk_low_back=b_lb,
                brk_high_front=b_hf,
                brk_high_back=b_hb
            )

            try:
                session_tag = f"{args.instrument}_{args.tf}_{int(time.time())}"
                data_store.register_grid_levels(grid_by_idx, session_id=session_tag)
                print(f"[DataStore] 📝 Re-registered grid levels for session {session_tag}")
            except Exception as e:
                print(f"[DataStore] ⚠️ Error re-registering levels: {e}")

            state["posture"] = posture
            state["status"] = "ARMED_STANDBY"
            if hasattr(dashboard, "_TELEMETRY"):
                dashboard._TELEMETRY["lifecycle"]["status"] = "ARMED_STANDBY"
            print(f"⚡ [DASHBOARD ARM] Grid PRE-ARMED in STANDBY with posture={posture}. Awaiting Deploy.")
            try:
                s_uid = f"SESSION_{args.instrument}_{int(time.time())}"
                data_engine.start_session(
                    session_uid=s_uid,
                    geom=geom,
                    instrument=args.instrument,
                    posture=posture,
                    profile_name=active_profile.get("name", "Harmonic Profile"),
                    risk=args.risk,
                    timeframe=args.tf
                )
            except Exception as e:
                print(f"[DataEngine] ⚠️ Arming hook warning: {e}")
            if bridge:
                r_stats = get_live_remaining_risk(grid_by_idx, args.instrument, directive=bias_data.get('directive'))
                bridge.send(
                    f"⚡ <b>HARMONIC GRID PRE-ARMED (STANDBY)</b>\n"
                    f"───────────────────────────────────\n"
                    f"• Instrument: <code>{args.instrument}</code> ({args.tf})\n"
                    f"• Boundaries: <code>{geom.range_low:.2f} — {geom.range_high:.2f}</code> (Δ {geom.range_high - geom.range_low:.2f} pts)\n"
                    f"• Equilibrium (L8): <code>{round((geom.range_low + geom.range_high)/2.0, 2):.2f}</code>\n"
                    f"• Profile: <b>{active_profile.get('name', 'Staged Profile')}</b> (Posture: <code>{posture}</code>)\n"
                    f"• Directional Bias: <b>{bias_data.get('regime', 'NEUTRAL')}</b> (Directive: <code>{bias_data.get('directive', 'BALANCED')}</code>)\n"
                    f"• Max Single-Side Risk: <b>${r_stats['max']:.2f}</b> (🟢 L: ${r_stats['long']:.2f} | 🔴 S: ${r_stats['short']:.2f})\n\n"
                    f"Awaiting activation via Cockpit <b>⚡ DEPLOY GRID</b> button."
                )

        except Exception as e:
            import traceback
            print(f"❌ [DASHBOARD ARM ERROR] Exception during arm: {e}")
            traceback.print_exc()

    set_arm_callback(on_dashboard_arm)

    def on_dashboard_flatten():
        nonlocal state
        print('\n🚨 [DASHBOARD PANIC] Emergency flatten invoked from Cockpit UI!')
        state['panic_flatten_requested'] = True

    set_flatten_callback(on_dashboard_flatten)
    def on_dashboard_deploy():
        nonlocal state
        print("[DASHBOARD DEPLOY] ⚡ Deploy triggered! Transitioning to ACTIVE_QUOTING.")
        state["status"] = "ACTIVE_QUOTING"
        if hasattr(dashboard, "_TELEMETRY"):
            dashboard._TELEMETRY["lifecycle"]["status"] = "ACTIVE_QUOTING"
        if bridge:
            bridge.send(
                f"🚀 <b>GRID DEPLOYED & ACTIVE QUOTING</b>\n"
                f"───────────────────────────────────\n"
                f"• Instrument: <code>{args.instrument}</code>\n"
                f"• Passive entry limit orders are now resting on <b>{getattr(broker, 'exec_venue', 'PAPER DEMO')}</b>.\n"
                f"• Session Lifespan: <code>{int(max_duration/60)} mins</code> | Freeze at: <code>{int(freeze_at/60)} mins</code>"
            )

    set_deploy_callback(on_dashboard_deploy)


    def handle_lifecycle_decision(action: str):
        nonlocal state, max_duration, t_start, freeze_at
        print(f"\n🎯 [LIFECYCLE DECISION] User selected: {action}")
        
        if action in ("act_flatten_now", "FLATTEN_NOW"):
            state["panic_flatten_requested"] = True
            if broker:
                try:
                    broker.flatten_position(instrument=args.instrument)
                except Exception as e:
                    print(f"Flatten error: {e}")
            state["status"] = "FLATTENED_DISARMED"
            if hasattr(dashboard, "_TELEMETRY"):
                dashboard._TELEMETRY["lifecycle"]["status"] = "FLATTENED_DISARMED"
                dashboard._TELEMETRY["lifecycle"]["show_expiry_modal"] = False
            if bridge:
                bridge.send("🚨 <b>Session Flattened & Disarmed</b> by user command.")

        elif action in ("act_keep_open", "KEEP_OPEN"):
            state["status"] = "EXTENDED_KEEP_OPEN"
            if hasattr(dashboard, "_TELEMETRY"):
                dashboard._TELEMETRY["lifecycle"]["status"] = "EXTENDED_KEEP_OPEN"
                dashboard._TELEMETRY["lifecycle"]["show_expiry_modal"] = False
            if bridge:
                bridge.send("⏳ <b>Orders Frozen — In-Flight Brackets Kept Open</b> until natural TP/SL resolution.")

        elif action in ("act_extend_cycle", "EXTEND_CYCLE"):
            tf_rule = TF_LIFECYCLE.get(args.tf, TF_LIFECYCLE["15m"])
            extension_s = tf_rule.get("max_s", 960) // 4
            max_duration += extension_s
            freeze_at += extension_s
            state["status"] = "ACTIVE_QUOTING"
            if hasattr(dashboard, "_TELEMETRY"):
                dashboard._TELEMETRY["lifecycle"]["status"] = "ACTIVE_QUOTING"
                dashboard._TELEMETRY["lifecycle"]["show_expiry_modal"] = False
            if bridge:
                bridge.send(f"➕ <b>Session Extended by {extension_s//60} mins</b>. Quotes remaining active.")

    def handle_telegram_command(cmd_text: str, payload_data: Any = None):
        cmd = cmd_text.strip()
        print(f"\n📩 [Telegram Input]: {cmd}")

        if cmd in ("act_flatten_now", "act_keep_open", "act_extend_cycle"):
            handle_lifecycle_decision(cmd)
            return

        if cmd.startswith("arm:"):
            parts = cmd.split(":")
            opt_key = parts[2]
            success = rearm_profile(opt_key)
            if success:
                bridge.send(
                    f"🚀 <b>Profile Activated: {active_profile['name']}</b>\n"
                    f"• Ladder Sizes: <code>{active_profile['ladder_qty']}</code> cts\n"
                    f"• Active Rungs: <code>{', '.join(active_profile['rung_labels'])}</code>\n"
                    f"• Side Risk Allocated: <b>${active_profile['peak_risk']:.2f}</b>\n"
                    f"• Quoting engine is now LIVE on CME."
                )
            return

        if cmd.startswith("/status"):
            now_t = time.time()
            el = now_t - t_start
            rf = max(0, freeze_at - el)
            rt = max(0, max_duration - el)
            pos = broker.get_position() if (broker and hasattr(broker, "get_position")) else state["position"]
            px = get_fresh_market_price()

            msg = (
                f"📊 <b>ENGINE STATUS — {args.instrument}</b>\n"
                f"───────────────────────────────────\n"
                f"• Venue: <code>{getattr(broker, 'exec_venue', 'PAPER DEMO')}</code>\n"
                f"• CME Market Price: <code>{px:.2f}</code>\n"
                f"• Net Position: <b>{pos}</b> contracts\n"
                f"• Active Profile: <b>{state['active_profile_name']}</b>\n"
                f"• Container: <code>{geom.range_low:.2f} — {geom.range_high:.2f}</code> (Δ <code>{geom.range_high - geom.range_low:.2f}</code>)\n"
                f"• Time to Freeze: <code>{format_hms(rf)}</code> | Hard Expiry: <code>{format_hms(rt)}</code>\n"
                f"• Engine State: <code>{state['status']}</code>\n"
                f"• Realized PnL: <b>${state['realized_pnl']:.2f}</b>"
            )
            bridge.send(msg)
            return

        if cmd.startswith("/flatten") or cmd.startswith("/panic") or cmd.startswith("panic:flatten"):
            state["status"] = "FLATTENED"
            state["flatten"] = True
            if broker:
                broker.cancel_all()
                broker.flatten_position(instrument=args.instrument)
            bridge.send(
                "🚨 <b>EMERGENCY FLATTEN EXECUTED</b>\n"
                "• All working limit orders cancelled.\n"
                "• Open positions liquidated at market.\n"
                "• Engine paused."
            )
            return

        if cmd.startswith("/risk"):
            parts = cmd.split()
            if len(parts) > 1:
                try:
                    new_risk = float(parts[1])
                    state["side_risk"] = new_risk
                    geom.r_side_budget = new_risk
                    rearm_profile(proposal["diagnosis"]["best_fit"])
                    bridge.send(f"✅ Side risk budget updated to <b>${new_risk:,.2f}</b>. Ladder rescaled.")
                except Exception as e:
                    bridge.send(f"⚠️ Error parsing risk: {e}")
            return

        if cmd.startswith("/newrange") or cmd.startswith("/propose"):
            parts = cmd.split()
            if len(parts) >= 3:
                try:
                    n_low = float(parts[1])
                    n_high = float(parts[2])
                    n_risk = float(parts[3]) if len(parts) > 3 else state["side_risk"]
                    n_geom = HarmonicGridGeometry(
                        range_low=n_low,
                        range_high=n_high,
                        multiplier=mult,
                        fee_per_rt=fee,
                        r_side_budget=n_risk,
                        instrument=args.instrument
                    )
                    n_pkg = n_geom.generate_proposal_package()
                    bridge.send_proposal_card(n_pkg)
                except Exception as e:
                    bridge.send(f"⚠️ Error formulating proposal: {e}")
            else:
                bridge.send("Usage: <code>/newrange [low] [high] [risk]</code>")
            return

    data_engine = GridDataEngine()
    bridge = TelegramBridge(command_handler=handle_telegram_command)
    bridge.start_polling()

    (getattr(bridge, "send_proposal_card", None) or getattr(bridge, "send_proposal", None) or getattr(bridge, "send_card", None) or (lambda p: print(f"[TelegramBridge] 📋 Proposal ready: {p}")))(proposal)
    if has_init_range and not getattr(args, "wait_arm", False):
        bridge.send(
            f"⚡ <b>Auto-Arming Recommended Profile: {active_profile['name']}</b>\n"
            f"• Tap any alternate button above to re-scale ladder at any time."
        )
    elif not has_init_range:
        print("[Runner] ⏸️ No initial container provided. Standby — enter range in Dashboard drawer or /newrange in Telegram.")
        bridge.send("⏸️ <b>Harmonic Nano-Grid Online in Standby</b>\n• Send <code>/newrange [low] [high]</code> or open Cockpit to stage.")

    last_hourly_alert_min = -1
    last_standby_ping_time = 0
    eq_alert_triggered = False
    prior_price_vs_eq = 0

    print("\n🟢 Engine running — monitoring quotes and fills. Press Ctrl+C to stop.")
    try:
        while not state["flatten"]:
            now = time.time()

            if state.get("panic_flatten_requested"):
                state["panic_flatten_requested"] = False
                state["status"] = "FLATTENED"
                state["paused"] = True
                print("\n🚨 [PANIC EXECUTION] Executing full cancellation & market liquidation on main thread...")
                if broker:
                    print("[Ironbeam] 🧹 Canceling all resting limit orders...")
                    broker.cancel_all(verbose=True)
                    if hasattr(broker, "flatten_position"):
                        broker.flatten_position(instrument=args.instrument)
                
                pnl_sym = "+" if state["realized_pnl"] >= 0 else ""
                cur_pos = broker.get_position() if (broker and hasattr(broker, "get_position")) else 0
                flatten_msg = (
                    "🚨 <b>COCKPIT FLATTEN EXECUTED</b>\n"
                    "───────────────────────────────────\n"
                    f"• Status: <b>PAUSED / ALL RESTING ORDERS CLEARED</b>\n"
                    f"• Live Instrument: <code>{args.instrument}</code>\n"
                    f"• Net Open Position: <b>{cur_pos} FLAT</b>\n"
                    f"• Realized PnL: <b>{pnl_sym}${state['realized_pnl']:.2f}</b>\n"
                    f"• Floating PnL: <b>${state['unrealized_pnl']:.2f}</b>\n\n"
                    "Quoting is halted. Re-arm via Cockpit drawer or <code>/newrange</code> in Telegram."
                )
                try:
                    bridge.send(flatten_msg)
                except Exception as e:
                    print(f"[Telegram] Failed to send flatten alert: {e}")

                for idx, r in grid_by_idx.items():
                    r["status"] = "FLATTENED"
                    r["entry_boid"] = None

            elapsed = now - t_start
            rem_freeze = max(0, freeze_at - elapsed)
            rem_flatten = max(0, max_duration - elapsed)

            # 1. Lifecycle Expiration Check (Discretionary Alert Mode)
            if elapsed >= max_duration and state.get("status") not in ("AWAITING_FLATTEN_DECISION", "EXTENDED_KEEP_OPEN"):
                print(f"\n⏱️ [LIFECYCLE EXPIRED] Reached max duration ({max_duration}s). Awaiting user decision...")
                state["status"] = "AWAITING_FLATTEN_DECISION"
                
                for r in grid_by_idx.values():
                    if r.get("entry_boid") and r.get("status") == "WORKING":
                        try:
                            broker.cancel_order(r["entry_boid"])
                            r["entry_boid"] = None
                            r["status"] = "STANDBY"
                        except Exception:
                            pass

                curr_pos = getattr(broker, "net_position", 0) or state.get("position", 0)
                pos_str = f"Open Position: <b>{curr_pos} contracts</b>" if curr_pos != 0 else "Net Position: <b>FLAT</b>"
                alert_text = (
                    f"⏰ <b>SESSION LIFECYCLE EXPIRED ({args.instrument} • {args.tf})</b>\n"
                    f"───────────────────────────────────\n"
                    f"• Lifespan of <b>{max_duration//60} mins</b> reached.\n"
                    f"• {pos_str}\n\n"
                    f"Select action:"
                )
                btns = [
                    [("🚨 Flatten All Now", "act_flatten_now"), ("⏳ Keep Open (Hold Brackets)", "act_keep_open")],
                    [("➕ Extend Session (+1 Cycle)", "act_extend_cycle")]
                ]
                if bridge:
                    bridge.send(alert_text, buttons=btns)

                if hasattr(dashboard, "_TELEMETRY"):
                    dashboard._TELEMETRY["lifecycle"]["status"] = "AWAITING_FLATTEN_DECISION"
                    dashboard._TELEMETRY["lifecycle"]["show_expiry_modal"] = True

            elif elapsed >= freeze_at and state["status"] == "ACTIVE_QUOTING":
                print(f"\n❄️ [T_freeze] HARVEST_ONLY mode (Canceling all unfilled entry quotes)...")
                state["status"] = "HARVEST_ONLY_FREEZE"
                for idx, r in grid_by_idx.items():
                    if r["status"] == "WORKING" and r["entry_boid"] and broker:
                        broker.cancel_order(r["entry_boid"])
                        r["status"] = "FROZEN"
                bridge.send(
                    f"❄️ <b>Freeze Phase Active ({args.tf})</b>\n"
                    f"• Unfilled entry quotes canceled.\n"
                    f"• Managing open inventory in Harvest-Only mode until session close."
                )

            # 2. Update Market State
            live_px = get_fresh_market_price()
            state["last_price"] = live_px

            if broker:
                state["active_orders"] = len(broker.active) if hasattr(broker, "active") else 0
                state["position"] = broker.get_position() if hasattr(broker, "get_position") else getattr(broker, "position", 0)

            total_floating_pnl = 0.0
            for idx, r in grid_by_idx.items():
                if r["status"] == "FILLED_IN_POSITION" and r.get("fill_price") and live_px > 0:
                    entry_px = r["fill_price"]
                    qty = r.get("fill_qty") or r.get("contracts", 1)
                    if r["side"] == "LONG":
                        pts = live_px - entry_px
                    else:
                        pts = entry_px - live_px
                    mtm = round(pts * qty * mult, 2)
                    r["floating_pnl"] = mtm
                    total_floating_pnl += mtm
                elif r["status"] != "FILLED_IN_POSITION":
                    r["floating_pnl"] = 0.0

            state["unrealized_pnl"] = round(total_floating_pnl, 2)

            if live_px > 0:
                data_store.evaluate_level_touches(current_price=live_px, high=live_px, low=live_px)
                data_store.update_price_excursion(current_price=live_px)

            # Periodic & Event-Driven Telegram Alerts
            now_dt = datetime.datetime.now()
            if now_dt.minute == 50 and last_hourly_alert_min != 50 and state["status"] in ("ACTIVE_QUOTING", "HARVEST_ONLY_FREEZE"):
                last_hourly_alert_min = 50
                cur_pos = broker.get_position() if (broker and hasattr(broker, "get_position")) else state["position"]
                pnl_sym = "+" if state["unrealized_pnl"] >= 0 else ""
                bk_sym = "+" if state["realized_pnl"] >= 0 else ""
                
                hourly_msg = (
                    f"⏱️ <b>HOURLY REPORT ({now_dt.strftime('%H:%M')} EST)</b>\n"
                    f"───────────────────────────────────\n"
                    f"• CME Market Price: <code>{live_px:.2f}</code>\n"
                    f"• Open Position: <b>{cur_pos}</b> contracts\n"
                    f"• Floating MTM: <b>{pnl_sym}${state['unrealized_pnl']:.2f}</b>\n"
                    f"• Booked Realized: <b>{bk_sym}${state['realized_pnl']:.2f}</b>\n"
                    f"• Active Profile: <code>{state['active_profile_name']}</code>\n"
                    f"• Time to Lifespan Cutoff: <code>{format_hms(rem_flatten)}</code>\n"
                    f"• Invalidation State: <code>{state['status']}</code>"
                )
                flatten_btn = [[("🚨 Panic Flatten Now", "panic:flatten")]]
                bridge.send(hourly_msg, buttons=flatten_btn)

            if now_dt.minute != 50:
                last_hourly_alert_min = -1

            # Periodic Standby Heartbeat (every 30 mins when idle / disarmed)
            now_ts = time.time()
            is_idle = state.get("status") in (
                "STANDBY",
                "INVALIDATED_STOPPED",
                "FLATTENED_DISARMED",
                "FLATTENED",
                "DISARMED",
            )
            if is_idle and (now_ts - last_standby_ping_time) >= 1800:
                last_standby_ping_time = now_ts
                cur_pos = broker.get_position() if (broker and hasattr(broker, "get_position")) else state.get("position", 0)
                venue_name = getattr(broker, "exec_venue", "Broker")
                idle_msg = (
                    "💤 <b>[STANDBY HEARTBEAT] Nano-Grid Idle</b>\n"
                    "───────────────────────────────────\n"
                    f"• Status: <code>{state.get('status', 'STANDBY')}</code>\n"
                    f"• Venue: <b>{venue_name}</b>\n"
                    f"• Live Price: <code>{live_px:.2f}</code>\n"
                    f"• Open Inventory: <b>{cur_pos}</b>\n"
                    "• Action: Stage via Cockpit (:8080) or /newrange [low] [high]"
                )
                try:
                    bridge.send(idle_msg)
                except Exception as e:
                    print(f"[Telegram] Failed to send standby heartbeat: {e}")

            p_eq = round((geom.range_low + geom.range_high) / 2.0, 2)
            if live_px > 0:
                cur_vs_eq = 1 if live_px >= p_eq else -1
                if prior_price_vs_eq != 0 and cur_vs_eq != prior_price_vs_eq and not eq_alert_triggered:
                    eq_alert_triggered = True
                    cur_pos = broker.get_position() if (broker and hasattr(broker, "get_position")) else state["position"]
                    pnl_sym = "+" if state["unrealized_pnl"] >= 0 else ""
                    direction_tag = "BULLISH TRANSIT" if cur_vs_eq == 1 else "BEARISH TRANSIT"
                    
                    eq_msg = (
                        f"⚖️ <b>MEDIAN EQUILIBRIUM CROSSED (L8: {p_eq:.2f})</b>\n"
                        f"───────────────────────────────────\n"
                        f"• Flow Direction: <b>{direction_tag}</b>\n"
                        f"• Live Price: <code>{live_px:.2f}</code>\n"
                        f"• Open Inventory: <b>{cur_pos}</b> contracts\n"
                        f"• Floating PnL: <b>{pnl_sym}${state['unrealized_pnl']:.2f}</b>\n"
                        f"• Rotations at Eq are 0-cost passthroughs."
                    )
                    flatten_btn = [[("🚨 Flatten at Equilibrium", "panic:flatten")]]
                    bridge.send(eq_msg, buttons=flatten_btn)
                
                if abs(live_px - p_eq) > geom.s_step:
                    eq_alert_triggered = False
                prior_price_vs_eq = cur_vs_eq

            # 3. Process Inbound Fills
            if broker and hasattr(broker, "drain_fills"):
                fills = broker.drain_fills()
                for fl in fills:
                    oid = fl.get("order_id", "")
                    fill_side = fl.get("side", "")
                    fill_px = fl.get("price", 0.0)
                    fill_qty = fl.get("size", 0)
                    print(f"\n⚡ [FILL EVENT] {fill_side} {fill_qty} @ {fill_px:.2f} (oid={oid})")

                    if oid.startswith("GRID_"):
                        parts = oid.split("_")
                        try:
                            idx = int(parts[2].replace("L", ""))
                            r = grid_by_idx.get(idx)
                            if r:
                                r["status"] = "FILLED_IN_POSITION"
                                r["entry_boid"] = None
                                r["fill_price"] = fill_px if fill_px > 0 else r["price"]
                                r["fill_qty"] = fill_qty

                                bracket_side = "SELL" if r["side"] == "LONG" else "BUY"
                                ts = int(time.time())

                                tp_oid = f"TP_{args.instrument}_L{idx}_{ts}"
                                tp_intent = OrderIntent(
                                    intent_type="TP_HARVEST",
                                    rung_idx=idx,
                                    side=bracket_side,
                                    price=r["tp"],
                                    size=fill_qty,
                                    instrument=args.instrument,
                                    metadata={"oid": tp_oid, "reduce_only": True, "tif": "GTC"}
                                )
                                tp_boid = dispatch_order_intent(tp_intent, broker)

                                sl_oid = f"SL_{args.instrument}_L{idx}_{ts}"
                                sl_intent = OrderIntent(
                                    intent_type="SL_STOP",
                                    rung_idx=idx,
                                    side=bracket_side,
                                    price=r["sl"],
                                    size=fill_qty,
                                    instrument=args.instrument,
                                    metadata={"oid": sl_oid, "tif": "GTC"}
                                )
                                sl_boid = dispatch_order_intent(sl_intent, broker)

                                r["tp_boid"] = tp_boid
                                r["sl_boid"] = sl_boid
                                active_brackets[idx] = {
                                    "tp_boid": tp_boid, "sl_boid": sl_boid,
                                    "tp_oid": tp_oid, "sl_oid": sl_oid,
                                    "size": fill_qty, "side": r["side"]
                                }
                                print(f"🎯 [Bracket Deployed L{idx}] TP Limit: {r['tp']:.2f} | Stop: {r['sl']:.2f}")

                                cycle_id = f"CYC_{args.instrument}_L{idx}_{ts}"
                                r["cycle_id"] = cycle_id
                                data_store.start_trade_cycle(
                                    cycle_id=cycle_id,
                                    instrument=args.instrument,
                                    rung_index=idx,
                                    side=r["side"],
                                    entry_price=r["fill_price"],
                                    target_tp=r["tp"],
                                    stop_loss=r["sl"],
                                    contracts=fill_qty
                                )
                                try:
                                    data_engine.record_cycle_entry(
                                        cycle_id=cycle_id,
                                        rung_idx=idx,
                                        side=r["side"],
                                        fill_px=r["fill_price"],
                                        tp_px=r["tp"],
                                        sl_px=r["sl"],
                                        s_harvest=geom.s_harvest
                                    )
                                except Exception as e:
                                    pass
                                data_store.log_order(tp_oid, args.instrument, bracket_side, r["tp"], fill_qty, "LIMIT", "PLACED", "Harvest TP Bracket")
                                data_store.log_order(sl_oid, args.instrument, bracket_side, r["sl"], fill_qty, "STOP", "PLACED", "Catastrophic Stop Bracket")

                                bridge.send(
                                    f"🎯 <b>FILL: {r['side']} L{idx} ({r['role']})</b>\n"
                                    f"• Filled: <code>{fill_qty}</code> cts @ <code>{fill_px:.2f}</code>\n"
                                    f"• TP Limit resting @ <code>{r['tp']:.2f}</code>\n"
                                    f"• Invalidation Stop @ <code>{r['sl']:.2f}</code>"
                                )

                        except Exception as e:
                            print(f"[Runner] ❌ Error handling entry fill {oid}: {e}")

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
                                gain_pts = abs(fill_px - (r.get("fill_price") or r["price"]))
                                gain_usd = gain_pts * fill_qty * mult - (fee * fill_qty)
                                state["realized_pnl"] += gain_usd
                                r["booked_pnl"] = r.get("booked_pnl", 0.0) + gain_usd
                                r["floating_pnl"] = 0.0
                                if r.get("cycle_id"):
                                    data_store.record_harvest(r["cycle_id"], fill_px)
                                    try:
                                        data_engine.record_cycle_exit(
                                            cycle_id=r["cycle_id"],
                                            exit_px=fill_px,
                                            exit_type="TP_HARVEST",
                                            net_pnl=gain_usd,
                                            fee=fee * fill_qty
                                        )
                                    except Exception as e:
                                        pass
                                r["fill_price"] = None

                                print(f"🏆 [HARVEST COMPLETE] L{idx} harvested at {fill_px:.2f}! Total rotations: {r['rotations']}/{r['cap']}")
                                rem_summary = get_live_remaining_risk_summary(grid_by_idx)
                                bridge.send(
                                    f"🏆 <b>HARVESTED: L{idx} ({r['side']})</b>\n"
                                    f"• Exit: <code>{fill_qty}</code> cts @ <code>{fill_px:.2f}</code> (+{gain_pts:.2f} pts)\n"
                                    f"• Gain: <b>+${gain_usd:,.2f}</b> net\n"
                                    f"• Rotations: <code>{r['rotations']}/{r['cap']}</code>"
                                    f"{' (LEVEL CAPPED)' if r['rotations'] >= r['cap'] else ''}"
                                    f"{rem_summary}"
                                )
                        except Exception as e:
                            print(f"[Runner] ❌ Error handling TP fill {oid}: {e}")

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
                                if r.get("cycle_id"):
                                    data_store.close_trade_cycle(r["cycle_id"], fill_px, final_status="STOPPED_OUT")
                                    try:
                                        loss_pts = abs(fill_px - (r.get("fill_price") or r["price"]))
                                        c_qty = r.get("contracts", 1)
                                        loss_usd = -round(loss_pts * c_qty * mult + (fee * c_qty), 2)
                                        data_engine.record_cycle_exit(
                                            cycle_id=r["cycle_id"],
                                            exit_px=fill_px,
                                            exit_type="SL_STOP",
                                            net_pnl=loss_usd,
                                            fee=fee * c_qty
                                        )
                                    except Exception as e:
                                        pass
                                print(f"🛑 [STOPPED OUT] L{idx} hit catastrophic stop at {fill_px:.2f}!")

                                state["status"] = "INVALIDATED_STOPPED"
                                for other_idx, other_r in grid_by_idx.items():
                                    if other_r["status"] in ("WORKING", "IDLE", "AWAITING_RETRACEMENT"):
                                        if other_r.get("entry_boid") and broker:
                                            broker.cancel_order(other_r["entry_boid"])
                                        other_r["status"] = "INVALIDATED"

                                rem_summary = get_live_remaining_risk_summary(grid_by_idx)
                                bridge.send(
                                    f"🛑 <b>STOPPED OUT: L{idx} @ {fill_px:.2f}</b>\n"
                                    f"🚨 <b>CONTAINER INVALIDATION LOCKDOWN</b>\n"
                                    f"• All resting entries canceled.\n"
                                    f"• Review market structure or submit <code>/newrange</code>."
                                    f"{rem_summary}"
                                )
                        except Exception as e:
                            print(f"[Runner] ❌ Error handling SL fill {oid}: {e}")

            # 4. Passive Order Dispatch & Retracement Quoting
            if broker and state["status"] == "ACTIVE_QUOTING" and live_px > 0:
                directive = bias_data.get("directive", "TWO_SIDED_BALANCED")
                current_posture = state.get("posture", "DIRECTIVE")

                for idx, r in grid_by_idx.items():
                    if r["cap"] == 0 or r["contracts"] == 0 or r["rotations"] >= r["cap"]:
                        continue

                    if r["status"] in ("FILLED_IN_POSITION", "STOPPED_OUT", "INVALIDATED", "WORKING"):
                        continue

                    # Posture and directive rules
                    if current_posture == "DIRECTIVE":
                        if "DISARM_SHORTS" in directive and r["side"] == "SHORT":
                            r["status"] = "DISARMED_BIAS"
                            continue
                        if "DISARM_LONGS" in directive and r["side"] == "LONG":
                            r["status"] = "DISARMED_BIAS"
                            continue
                    elif current_posture == "LONGS_ONLY" and r["side"] == "SHORT":
                        r["status"] = "DISARMED_POSTURE"
                        continue
                    elif current_posture == "SHORTS_ONLY" and r["side"] == "LONG":
                        r["status"] = "DISARMED_POSTURE"
                        continue

                    current_market_px = get_fresh_market_price()
                    is_passive = is_order_strictly_passive(r["side"], r["price"], current_market_px)

                    if is_passive:
                        oid = f"GRID_{args.instrument}_L{r['index']}_{int(now)}"
                        order_side = "BUY" if r["side"].upper() in ("LONG", "BUY") else "SELL"
                        entry_intent = OrderIntent(
                            intent_type="QUOTE_ENTRY",
                            rung_idx=r["index"],
                            side=order_side,
                            price=r["price"],
                            tp_price=r.get("tp"),
                            sl_price=r.get("sl"),
                            size=r["contracts"],
                            instrument=args.instrument,
                            rung_weight=r.get("weight", 0.25),
                            metadata={"oid": oid, "tif": "GTC"}
                        )
                        try:
                            boid = dispatch_order_intent(entry_intent, broker)
                            if boid:
                                r["entry_boid"] = boid
                                r["status"] = "WORKING"
                                print(f"🚀 [ORDER WORKING] {order_side} {r['contracts']} ct L{r['index']} @ {r['price']:.2f} (oid={boid})")
                            else:
                                r["status"] = "REJECTED"
                                print(f"⚠️ [ORDER REJECTED] {order_side} {r['contracts']} ct L{r['index']} @ {r['price']:.2f}")
                        except Exception as e:
                            r["status"] = "REJECTED"
                            print(f"❌ [ORDER ERROR] L{r['index']} placement failed: {e}")
                    else:
                        r["status"] = "AWAITING_RETRACEMENT"

            # 5. Live Directional Bias & Telemetry Broadcast
            live_brk_age = args.brk_age + elapsed
            cur_low = geom.range_low
            cur_high = geom.range_high
            cur_span = cur_high - cur_low
            bias_update = bias_calc.compute_bias(
                range_low=geom.range_low,
                range_high=geom.range_high,
                breaker_low_front=args.brk_low_front or (geom.range_low + 0.3125 * (geom.range_high - geom.range_low)),
                breaker_high_front=args.brk_high_front or (geom.range_high - 0.3125 * (geom.range_high - geom.range_low)),
                last_breaker_dir=args.brk_dir,
                elapsed_seconds=args.brk_age + elapsed,
                mitigation_touches=args.brk_touches,
                current_price=live_px if live_px > 0 else None,
                timeframe=args.tf,
                prior_regime=bias_data.get("regime", "NEUTRAL_EQUILIBRIUM")
            )
            bias_data.update(bias_update)
            bias_data["elapsed_s"] = live_brk_age
            bias_data["timeframe"] = args.tf
            bias_data["dir_last"] = args.brk_dir
            bias_data["touches"] = args.brk_touches
            bias_data["k"] = bias_calc.TIMEFRAME_DECAY.get(args.tf, 0.00025)

            if live_px > 0:
                try:
                    data_engine.track_virtual_rung_touches(live_px, now, geom, args.instrument)
                    data_engine.on_market_tick(live_px, now)
                except Exception as e:
                    pass

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
                    "low": geom.range_low, "high": geom.range_high,
                    "delta_p": geom.range_high - geom.range_low,
                    "p_eq": round((geom.range_low + geom.range_high) / 2.0, 2),
                    "s_step": geom.s_step, "s_harvest": geom.s_harvest
                },
                "bias": {
                    **bias_data,
                    "s_bias": round(bias_data.get("s_bias", 0.0), 2),
                    "s_spatial": round(bias_data.get("s_spatial", 0.0), 2),
                    "s_chrono": round(bias_data.get("s_chrono", 0.0), 2),
                    "s_formation": round(bias_data.get("s_formation", 0.0), 2),
                    "s_integrity": round(bias_data.get("s_integrity", 0.0), 2),
                    "s_touch_reaction": round(bias_data.get("s_touch_reaction", 0.0), 2),
                    "s_price_disp": round(bias_data.get("s_price_disp", 0.0), 2),
                    "open_corridor": round(bias_data.get("open_corridor", 0.0), 2),
                    "regime": bias_data.get("regime", "NEUTRAL_EQUILIBRIUM"),
                    "directive": bias_data.get("directive", "TWO_SIDED_BALANCED"),
                },
                "remaining_risk": get_live_remaining_risk(grid_by_idx, args.instrument),
                "lifecycle": {
                    "tf": args.tf,
                    "status": state["status"],
                    "elapsed_str": format_hms(elapsed),
                    "to_freeze_str": format_hms(rem_freeze),
                    "to_flatten_str": format_hms(rem_flatten),
                    "t_max_min": int(max_duration / 60),
                    "t_freeze_min": int((max_duration - freeze_at) / 60)
                },
                "accounts": fleet_dispatcher.get_fleet_telemetry() if fleet_dispatcher else [],
            "grid": list(grid_by_idx.values())
            })

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n🛑 Manual interrupt detected. Stopping runner...")
    finally:
        bridge.polling_active = False
        if broker:
            print("[Runner] 🧹 Cancelling all resting orders...")
            broker.cancel_all()
        try:
            print("[DataStore] 💾 Flushing SQLite audit logs to disk...")
            data_store.close()
        except Exception as e:
            print(f"[DataStore] ⚠️ Flush error: {e}")
        print("[Runner] 👋 Session closed cleanly.")

def main():
    parser = argparse.ArgumentParser(description="Harmonic NANO-GRID V5 Live Runner")
    parser.add_argument("--profile", type=str, default="SNAPPED", help="Initial grid profile (SNAPPED, OPTION_A, OPTION_B, OPTION_C, LEAN_2_RUNG)")
    parser.add_argument("--fleet", action="store_true", default=True, help="Enable Multi-Account Fleet Execution")
    parser.add_argument("--port", type=int, default=8090, help="Dashboard HTTP port")
    parser.add_argument("--instrument", type=str, default="MNQ", help="Contract symbol (MNQ, MES, NNQ)")
    parser.add_argument("--low", type=float, default=None, help="Container Range Low (optional, can stage via UI/TG)")
    parser.add_argument("--high", type=float, default=None, help="Container Range High (optional, can stage via UI/TG)")
    parser.add_argument("--risk", type=float, default=1200.0, help="Total Side Risk Budget in USD")
    parser.add_argument("--tf", type=str, default="15m", help="Container Timeframe (4h, 1h, 15m, 4m, 1m)")
    parser.add_argument("--source", type=str, default="ironbeam", choices=["ironbeam", "paper"], help="Data/Execution engine")
    parser.add_argument("--live-exec", action="store_true", help="Arm live real-money execution on CME")
    parser.add_argument("--wait-arm", action="store_true", help="Wait for explicit Telegram arm tap before quoting")
    parser.add_argument("--brk-low-front", type=float, default=None, help="Long breaker front edge")
    parser.add_argument("--brk-low-back", type=float, default=None, help="Long breaker back edge")
    parser.add_argument("--brk-low-poc", type=float, default=None, help="Long breaker POC")
    parser.add_argument("--brk-high-front", type=float, default=None, help="Short breaker front edge")
    parser.add_argument("--brk-high-back", type=float, default=None, help="Short breaker back edge")
    parser.add_argument("--brk-high-poc", type=float, default=None, help="Short breaker POC")
    parser.add_argument("--brk-dir", type=str, default="UP", choices=["UP", "DOWN"])
    parser.add_argument("--brk-age", type=float, default=0.0, help="Initial age of breaker in seconds")
    parser.add_argument("--brk-touches", type=int, default=0, help="Initial mitigation touches")

    args = parser.parse_args()
    run_live_session(args)

if __name__ == "__main__":
    main()
