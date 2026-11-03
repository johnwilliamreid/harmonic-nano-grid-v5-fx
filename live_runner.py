from dashboard import start_dashboard_server, update_telemetry
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

def run_live_session(args):
    mult = MULTIPLIERS.get(args.instrument, 2.0)
    fee = COMMISSION_PER_RT.get(args.instrument, 1.94)

    print("=" * 70)
    print("🚀 HARMONIC NANO-GRID V4 EXECUTION RUNNER")
    print(f"Asset: {args.instrument} | Multiplier: ${mult}/pt | Fee: ${fee}/RT")
    print(f"Container: {args.low:.2f} - {args.high:.2f} (Δ {args.high - args.low:.2f} pts)")
    print(f"Source: {args.source.upper()} | Execution: {'🚨 LIVE REALMONEY' if args.live_exec else '🛡️ PAPER DEMO (51428111)'}")
    print(f"Timeframe: {args.tf} | Directional Breakers: {'YES' if args.brk_low_front else 'GEOMETRIC DEFAULT'}")
    print("=" * 70)

    # 1. Initialize Grid Geometry & Generate Proposal
    geom = HarmonicGridGeometry(
        range_low=args.low,
        range_high=args.high,
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
            "decimal_multiplier": r.get("decimal_multiplier", 1.0),
            "exact_qty": r.get("exact_qty", 0.0),
            "unit_risk": r.get("unit_risk", 0.0),
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

            print("[Runner] ⏳ Awaiting first CME quote...")
            for _ in range(15):
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
        "status": "AWAITING_ARM" if getattr(args, "wait_arm", False) else "ACTIVE_QUOTING",
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
            if hasattr(broker, "last_price") and broker.last_price > 0:
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

    def rearm_profile(opt_key: str):
        nonlocal active_profile, grid_dict, grid_by_idx
        if opt_key not in proposal["options"]:
            return False
        opt = proposal["options"][opt_key]
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
            brk_low_back=args.brk_low_back or args.low,
            brk_low_front=args.brk_low_front,
            brk_low_poc=args.brk_low_poc,
            brk_high_front=args.brk_high_front,
            brk_high_back=args.brk_high_back or args.high,
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
                if grid_by_idx[idx]["status"] not in ("FILLED_IN_POSITION", "STOPPED_OUT"):
                    grid_by_idx[idx]["status"] = "IDLE" if new_r["cap"] > 0 else "EXIT_ONLY"

        state["status"] = "ACTIVE_QUOTING"
        return True

    # -----------------------------------------------------------------
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
                f"• Container: <code>{args.low:.2f} — {args.high:.2f}</code> (Δ <code>{args.high - args.low:.2f}</code>)\n"
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
    bridge.send_proposal_card(proposal)
    if not getattr(args, "wait_arm", False):
        bridge.send(
            f"⚡ <b>Auto-Arming Recommended Profile: {active_profile['name']}</b>\n"
            f"• Tap any alternate button above to re-scale ladder at any time."
        )

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
                bridge.send(
                    f"⏰ <b>Session Lifespan Reached ({args.tf})</b>\n"
                    f"• Container <code>{args.low:.2f} — {args.high:.2f}</code> expired.\n"
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
                                gain_pts = abs(fill_px - r["price"])
                                gain_usd = gain_pts * fill_qty * mult - (fee * fill_qty)
                                state["realized_pnl"] += gain_usd

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
    parser.add_argument("--instrument", type=str, default="MNQ", help="Contract symbol (MNQ, MES, NNQ)")
    parser.add_argument("--low", type=float, required=True, help="Container Range Low")
    parser.add_argument("--high", type=float, required=True, help="Container Range High")
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
