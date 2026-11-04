from dashboard import start_dashboard_server, update_telemetry, set_arm_callback, set_flatten_callback
import dashboard
import dashboard
import json
import os
import sys
import time
import math
import argparse
import datetime
from typing import Any, Optional, Dict, List, Tuple
from dotenv import load_dotenv

from src.config import MULTIPLIERS, COMMISSION_PER_RT, TICK_SIZES
from src.core.grid_geometry import HarmonicGridGeometry
from src.core.directional_bias import DirectionalBiasCalculator
from src.adapters.telegram_adapter import TelegramBridge

load_dotenv()

TF_LIFECYCLE = {
    "1hr": {"max_s": 43200, "freeze_buffer_s": 5400},  # 12 hrs max, 90m freeze
    "1h":  {"max_s": 43200, "freeze_buffer_s": 5400},  # 12 hrs max, 90m freeze
    "15m": {"max_s": 21600, "freeze_buffer_s": 3600},  # 6 hrs max, 60m freeze
    "5m":  {"max_s": 14400, "freeze_buffer_s": 2700},  # 4 hrs max, 45m freeze
    "1m":  {"max_s": 7200,  "freeze_buffer_s": 1200},  # 2 hrs max, 20m freeze
    "15s": {"max_s": 1200,  "freeze_buffer_s": 180},   # 20 min max, 3m freeze
    "5s":  {"max_s": 1200,  "freeze_buffer_s": 180},   # 20 min max, 3m freeze
    "1s":  {"max_s": 300,   "freeze_buffer_s": 45},    # 5 min max, 45s freeze
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

    # Demand rungs: Front, MT, plus any intermediate absorption rungs
    d_rungs = [d_front, d_mt]
    for mid in range(d_mt + 1, d_front):
        d_rungs.append(mid)
    d_rungs = sorted(list(set(d_rungs)))

    # Supply rungs: Front, MT, plus any intermediate absorption rungs
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

def run_live_session(args):
    mult = MULTIPLIERS.get(args.instrument, 2.0)
    fee = COMMISSION_PER_RT.get(args.instrument, 1.94)

    has_init_range = (args.low is not None and args.high is not None and args.high > args.low)
    init_low = args.low if has_init_range else 27000.00
    init_high = args.high if has_init_range else 28000.00

    print("=" * 70)
    print("🚀 HARMONIC NANO-GRID V4 EXECUTION RUNNER")
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

    t_start = time.time()
    tf_rule = TF_LIFECYCLE.get(args.tf, TF_LIFECYCLE["15m"])
    max_duration = tf_rule["max_s"]
    freeze_at = max_duration - tf_rule["freeze_buffer_s"]
    p_eq = init_low + 0.5 * (init_high - init_low)

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

        # If already on this profile and not forced, ignore redundant re-arm
        current_name = state.get("active_profile_name")
        if not force and opt_key in ("SNAPPED", "LEAN_2_RUNG", "OPTION_A", "OPTION_B", "OPTION_C"):
            if current_name and (opt_key in current_name or (opt_key == "SNAPPED" and "Mitigation Priority" in current_name)):
                print(f"[RE-ARM GUARD] Grid already armed with {current_name}. Skipping redundant order cycling.")
                return True

        # Use active container boundaries from geom
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

    # -----------------------------------------------------------------
    # --- Dashboard Arming Hook ---
    def on_dashboard_arm(data: dict):
        nonlocal geom, proposal, active_profile, grid_dict, grid_by_idx
        mode = data.get("mode", "SNAPPED")
        posture = data.get("posture", "DIRECTIVE")
        print(f"\n🌐 [DASHBOARD ARM] Received mode={mode}, posture={posture}")

        geom_changed = False
        b_lf, b_lb, b_hf, b_hb = None, None, None, None
        
        setup = getattr(dashboard, "_TELEMETRY", {}).get("pending_setup")
        if setup:
            b_lf = setup.get("brk_low_front")
            b_lb = setup.get("brk_low_back")
            b_hf = setup.get("brk_high_front")
            b_hb = setup.get("brk_high_back")

        # If a new container was submitted via the drawer, adopt the new geometry
        if hasattr(dashboard, "_PENDING_GEOM") and dashboard._PENDING_GEOM is not None:
            geom = dashboard._PENDING_GEOM
            dashboard._PENDING_GEOM = None  # Consume and clear
            print(f"📐 [DASHBOARD ARM] Updated container to: {geom.range_low} - {geom.range_high} (Δ {geom.range_high - geom.range_low:.2f})")
            proposal = geom.generate_proposal_package(
                brk_low_front=b_lf,
                brk_low_back=b_lb,
                brk_high_front=b_hf,
                brk_high_back=b_hb
            )
            geom_changed = True

        rearm_profile(
            mode,
            force=geom_changed,
            brk_low_front=b_lf,
            brk_low_back=b_lb,
            brk_high_front=b_hf,
            brk_high_back=b_hb
        )

    set_arm_callback(on_dashboard_arm)

    # --- Dashboard Flatten Hook ---
    def on_dashboard_flatten():
        nonlocal state
        print('\n🚨 [DASHBOARD PANIC] Emergency flatten invoked from Cockpit UI!')
        state['panic_flatten_requested'] = True

    set_flatten_callback(on_dashboard_flatten)
    # Telegram Bridge & Interactive Dispatcher
    # -----------------------------------------------------------------
    def handle_telegram_command(cmd_text: str, payload_data: Any = None):
        cmd = cmd_text.strip()
        print(f"\n📩 [Telegram Input]: {cmd}")

        # Case 1: Callback Arm Query (arm:PID:OPTION_KEY)
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

        # Case 2: Status Command
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

        # Case 3: Emergency Flatten
        if cmd.startswith("/flatten") or cmd.startswith("/panic"):
            state["status"] = "FLATTENED"
            state["flatten"] = True
            if broker:
                broker.cancel_all()
                broker.flatten_position(args.instrument)
            bridge.send(
                "🚨 <b>EMERGENCY FLATTEN EXECUTED</b>\n"
                "• All working limit orders cancelled.\n"
                "• Open positions liquidated at market.\n"
                "• Engine paused."
            )
            return

        # Case 4: Risk Budget Update
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

        # Case 5: New Range Proposal (/newrange or /propose)
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

    bridge = TelegramBridge(command_handler=handle_telegram_command)
    bridge.start_polling()

    # Dispatch Initial Telegram Proposal Card
    (getattr(bridge, "send_proposal_card", None) or getattr(bridge, "send_proposal", None) or getattr(bridge, "send_card", None) or (lambda p: print(f"[TelegramBridge] 📋 Proposal ready: {p}")))(proposal)
    if has_init_range and not getattr(args, "wait_arm", False):
        bridge.send(
            f"⚡ <b>Auto-Arming Recommended Profile: {active_profile['name']}</b>\n"
            f"• Tap any alternate button above to re-scale ladder at any time."
        )
    elif not has_init_range:
        print("[Runner] ⏸️ No initial container provided. Standby — enter range in Dashboard drawer or /newrange in Telegram.")
        bridge.send("⏸️ <b>Harmonic Nano-Grid Online in Standby</b>\n• Send <code>/newrange [low] [high]</code> or open Cockpit to stage.")

    print("\n🟢 Engine running — monitoring quotes and fills. Press Ctrl+C to stop.")
    try:
        while not state["flatten"]:
            now = time.time()

            # Handle Emergency Flatten Signal directly on the main thread without exiting
            if state.get("panic_flatten_requested"):
                state["panic_flatten_requested"] = False
                state["status"] = "FLATTENED"
                state["paused"] = True
                print("\n🚨 [PANIC EXECUTION] Executing full cancellation & market liquidation on main thread...")
                if broker:
                    print("[Ironbeam] 🧹 Canceling all resting limit orders...")
                    broker.cancel_all(verbose=True)
                    print("[Ironbeam] ⚡ Invoking broker.flatten_position()...")
                    broker.flatten_position()
                    time.sleep(1.0)
                    broker.cancel_all(verbose=True)

                for idx, r in grid_by_idx.items():
                    r["status"] = "FLATTENED"
                    r["entry_boid"] = None

                bridge.send(
                    "🚨 <b>EMERGENCY FLATTEN COMPLETED</b>\n"
                    "• All working CME orders cancelled.\n"
                    "• Open inventory flattened at market.\n"
                    "• Quoting engine disarmed & parked."
                )
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
                bridge.send(
                    f"⏰ <b>Session Lifespan Reached ({args.tf})</b>\n"
                    f"• Container <code>{geom.range_low:.2f} — {geom.range_high:.2f}</code> expired.\n"
                    f"• All orders cancelled and positions flattened.\n"
                    f"• Send <code>/newrange [low] [high]</code> to redeploy."
                )
                break
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

            # -----------------------------------------------------------------
            # 2. Update Market State
            # -----------------------------------------------------------------
            live_px = get_fresh_market_price()
            state["last_price"] = live_px

            if broker:
                state["active_orders"] = len(broker.active) if hasattr(broker, "active") else 0
                state["position"] = broker.get_position() if hasattr(broker, "get_position") else getattr(broker, "position", 0)

            # Compute Live Mark-to-Market Floating PnL across active rungs
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
                                r["fill_price"] = fill_px if fill_px > 0 else r["price"]
                                r["fill_qty"] = fill_qty

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

                                bridge.send(
                                    f"🎯 <b>FILL: {r['side']} L{idx} ({r['role']})</b>\n"
                                    f"• Filled: <code>{fill_qty}</code> cts @ <code>{fill_px:.2f}</code>\n"
                                    f"• TP Limit resting @ <code>{r['tp']:.2f}</code>\n"
                                    f"• Invalidation Stop @ <code>{r['sl']:.2f}</code>"
                                )

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
                                gain_pts = abs(fill_px - (r.get("fill_price") or r["price"]))
                                gain_usd = gain_pts * fill_qty * mult - (fee * fill_qty)
                                state["realized_pnl"] += gain_usd
                                r["booked_pnl"] = r.get("booked_pnl", 0.0) + gain_usd
                                r["floating_pnl"] = 0.0
                                r["fill_price"] = None

                                print(f"🏆 [HARVEST COMPLETE] L{idx} harvested at {fill_px:.2f}! Total rotations: {r['rotations']}/{r['cap']}")
                                bridge.send(
                                    f"🏆 <b>HARVESTED: L{idx} ({r['side']})</b>\n"
                                    f"• Exit: <code>{fill_qty}</code> cts @ <code>{fill_px:.2f}</code> (+{gain_pts:.2f} pts)\n"
                                    f"• Gain: <b>+${gain_usd:,.2f}</b> net\n"
                                    f"• Rotations: <code>{r['rotations']}/{r['cap']}</code>"
                                    f"{' (LEVEL CAPPED)' if r['rotations'] >= r['cap'] else ''}"
                                )
                        except Exception as e:
                            print(f"[Runner] ❌ Error handling TP fill {oid}: {e}")

                    # CASE C: Stop-Loss Filled -> Cancel TP, Mark Stopped, Trigger Invalidation Lockdown
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

                                state["status"] = "INVALIDATED_STOPPED"
                                for other_idx, other_r in grid_by_idx.items():
                                    if other_r["status"] in ("WORKING", "IDLE", "AWAITING_RETRACEMENT"):
                                        if other_r.get("entry_boid") and broker:
                                            broker.cancel_order(other_r["entry_boid"])
                                        other_r["status"] = "INVALIDATED"

                                bridge.send(
                                    f"🛑 <b>STOPPED OUT: L{idx} @ {fill_px:.2f}</b>\n"
                                    f"🚨 <b>CONTAINER INVALIDATION LOCKDOWN</b>\n"
                                    f"• All resting entries canceled.\n"
                                    f"• Review market structure or submit <code>/newrange</code>."
                                )
                        except Exception as e:
                            print(f"[Runner] ❌ Error handling SL fill {oid}: {e}")

            # -----------------------------------------------------------------
            # 4. Passive Order Dispatch & Retracement Quoting
            # -----------------------------------------------------------------
            if broker and state["status"] == "ACTIVE_QUOTING" and live_px > 0:
                directive = bias_data.get("directive", "TWO_SIDED_BALANCED")

                for idx, r in grid_by_idx.items():
                    if r["cap"] == 0 or r["contracts"] == 0 or r["rotations"] >= r["cap"]:
                        continue

                    if r["status"] in ("FILLED_IN_POSITION", "STOPPED_OUT", "INVALIDATED", "WORKING"):
                        continue

                    if directive == "DISARM_SHORTS" and r["side"] == "SHORT":
                        r["status"] = "DISARMED_BIAS"
                        continue
                    if directive == "DISARM_LONGS" and r["side"] == "LONG":
                        r["status"] = "DISARMED_BIAS"
                        continue

                    current_market_px = get_fresh_market_price()
                    is_passive = is_order_strictly_passive(r["side"], r["price"], current_market_px)

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
            cur_low = geom.range_low
            cur_high = geom.range_high
            cur_span = cur_high - cur_low
            bias_update = bias_calc.compute_bias(
                range_low=cur_low,
                range_high=cur_high,
                breaker_low_front=args.brk_low_front or (cur_low + 0.3125 * cur_span),
                breaker_high_front=args.brk_high_front or (cur_low + 0.6875 * cur_span),
                last_breaker_dir=args.brk_dir,
                elapsed_seconds=live_brk_age,
                mitigation_touches=args.brk_touches,
                timeframe=args.tf,
                prior_regime=bias_data.get("regime", "NEUTRAL_EQUILIBRIUM")
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
                    "low": geom.range_low, "high": geom.range_high,
                    "delta_p": geom.range_high - geom.range_low,
                    "p_eq": round((geom.range_low + geom.range_high) / 2.0, 2),
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

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n🛑 Manual interrupt detected. Stopping runner...")
    finally:
        bridge.polling_active = False
        if broker:
            print("[Runner] 🧹 Cancelling all resting orders...")
            broker.cancel_all()
        print("[Runner] 👋 Session closed cleanly.")

def main():
    parser = argparse.ArgumentParser(description="Harmonic Nano-Grid v4 Live Runner")
    parser.add_argument("--profile", type=str, default="SNAPPED", help="Initial grid profile (SNAPPED, OPTION_A, OPTION_B, OPTION_C, LEAN_2_RUNG)")
    parser.add_argument("--instrument", type=str, default="MNQ", help="Contract symbol (MNQ, MES, NNQ)")
    parser.add_argument("--low", type=float, default=None, help="Container Range Low (optional, can stage via UI/TG)")
    parser.add_argument("--high", type=float, default=None, help="Container Range High (optional, can stage via UI/TG)")
    parser.add_argument("--risk", type=float, default=1200.0, help="Total Side Risk Budget in USD")
    parser.add_argument("--tf", type=str, default="15m", help="Container Timeframe (1hr, 15m, 5m, 1m)")
    parser.add_argument("--source", type=str, default="paper", choices=["ironbeam", "paper"], help="Data/Execution engine")
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
