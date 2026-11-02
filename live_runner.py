from dashboard import start_dashboard_server
import json, os
import sys
import os
import time
import argparse
import datetime
import pandas as pd
from dotenv import load_dotenv

from src.config import MULTIPLIERS, COMMISSION_PER_RT, TICK_SIZES
from src.broker import ReplayBroker
from src.core.grid_engine import HarmonicGridEngine
from src.core.pre_session_replay import PreSessionReplay
from src.adapters.telegram_adapter import TelegramBridge

load_dotenv()

def run_live_session(args):
    print("=" * 65)
    print("🚀 HARMONIC NANO-GRID V4 EXECUTION RUNNER")
    print(f"Asset: {args.instrument} | Multiplier: ${MULTIPLIERS[args.instrument]}/pt | Fee: ${COMMISSION_PER_RT[args.instrument]}/RT")
    print(f"Container: {args.low:.2f} - {args.high:.2f} (Δ {args.high - args.low:.2f} pts)")
    print(f"Source: {args.source} | Execution: {'🚨 LIVE REAL MONEY' if args.live_exec else '🛡️ PAPER DEMO (51428111)'}")
    print("=" * 65)

    state = {
        "paused": False,
        "flatten": False,
        "panic": False,
        "restart": False,
        "last_eq_alert_time": 0
    }

    def telegram_callback_handler(cmd, payload):
        print(f"[Telegram] Event received: {cmd}")
        if cmd in ("flat:arm", "flat:yes", "test:flatten", "/flatten"):
            state["flatten"] = True
        elif cmd in ("hold", "/pause"):
            state["paused"] = not state["paused"]
            print(f"[Runner] Execution paused state: {state['paused']}")
        elif cmd in ("/resume",):
            state["paused"] = False
        elif cmd in ("/panic",):
            state["panic"] = True

    bridge = TelegramBridge(command_handler=telegram_callback_handler)
    bridge.start_polling()

    # Broker Selection
    if args.source == "ironbeam":
        try:
            from src.adapters.ironbeam import IronbeamAdapter
            broker = IronbeamAdapter(is_live_exec=args.live_exec)
            if not broker.authenticate():
                bridge.send("❌ Ironbeam auth failed — aborting")
                return
            broker.start_websocket()
            bridge.send(
                f"🟢 <b>Harmonic Runner Initialized</b>\n"
                f"• Venue: {broker.exec_venue} | Account: <code>{broker.account_id}</code>\n"
                f"• Container: {args.low:.2f} - {args.high:.2f}\n"
                f"• Instrument: {args.instrument}"
            )
        except ImportError:
            print("⚠️ Ironbeam adapter not found in src/adapters/ironbeam.py. Falling back to ReplayBroker.")
            broker = ReplayBroker()
    else:
        broker = ReplayBroker()

    # Pre-Session Replay & Hydration
    replay = PreSessionReplay(
        range_low=args.low,
        range_high=args.high,
        resolution=16 if (args.high - args.low) > 90.0 else 8
    )
    backfill_data = replay.backfill_from_ironbeam(symbol=args.instrument, lookback_minutes=args.lookback)

    # Harmonic Grid Engine
    proposal = {
        "instrument": args.instrument,
        "range_low": args.low,
        "range_high": args.high
    }
    engine = HarmonicGridEngine(broker=broker, proposal=proposal)
    engine.seed_from_pre_session(backfill_data)

    bridge.send(
        f"📊 <b>Pre-Session Backfill Hydrated</b>\n"
        f"• Total Rotations: {backfill_data['total_rotations']}\n"
        f"• Directional Bias: {backfill_data['s_bias']:+.2f} ({backfill_data['regime']})\n"
        f"• Resolution: 1/{engine.resolution} Grid (Step: {engine.s_step:.2f} pts)\n"
        f"• Hard Risk Budget: ${engine.max_budget:.2f}"
    )

    initial_px = getattr(broker, 'last_price', None) or (args.low + args.high) / 2.0
    start_dashboard_server(8080)
    engine.deploy(initial_px)

    session_status = "COMPLETE"
    last_hour = None

    try:
        while True:
            if state["restart"] or state["panic"]:
                break

            current_price = getattr(broker, 'last_price', None)
            price_age = time.time() - getattr(broker, 'last_price_time', 0) if hasattr(broker, 'last_price_time') else 0

            # Dump live state for dashboard
            try:
                active_rung_orders = {}
                for oid, o in getattr(engine, "orders", {}).items():
                    r_idx = o.get("rung")
                    if r_idx is not None:
                        active_rung_orders[r_idx] = {
                            "order_id": oid,
                            "status": "WORKING"
                        }

                rung_list = []
                for r in getattr(engine, "rungs", []):
                    idx = r.get("index", 0)
                    order_info = active_rung_orders.get(idx, {})
                    rung_list.append({
                        "index": idx,
                        "side": r.get("side", "LONG"),
                        "price": float(r.get("price", 0.0)),
                        "tp": float(r.get("tp", 0.0)),
                        "cap": getattr(engine, "lifecycle_caps", {}).get(idx, 9999),
                        "rotations": getattr(engine, "rotations", {}).get(idx, 0),
                        "status": order_info.get("status", "PENDING"),
                        "order_id": order_info.get("order_id", "")
                    })

                bf = backfill_data if isinstance(backfill_data, dict) else {}
                pre_rots = bf.get("total_rotations", 0)
                tot_rots = sum(getattr(engine, "rotations", {}).values())

                snap = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "instrument": args.instrument,
                    "venue": getattr(broker, "exec_venue", "DEMO"),
                    "account": getattr(broker, "account_id", "51428111"),
                    "container_low": args.low,
                    "container_high": args.high,
                    "current_price": current_price,
                    "regime": getattr(engine, "bias_regime", getattr(engine, "regime", "ACTIVE")),
                    "s_bias": bf.get("s_bias", 0.0),
                    "total_rotations": tot_rots,
                    "pre_rotations": pre_rots,
                    "live_rotations": max(0, tot_rots - pre_rots),
                    "resolution": getattr(engine, "resolution", 16),
                    "s_step": getattr(engine, "s_step", 0.0),
                    "harvest_step": getattr(engine, "harvest_step", 0.0),
                    "stop_loss_long": getattr(engine, "stop_loss_long", 0.0),
                    "stop_loss_short": getattr(engine, "stop_loss_short", 0.0),
                    "max_budget": getattr(engine, "max_budget", 200.0),
                    "open_contracts": engine.open_contracts_count() if hasattr(engine, "open_contracts_count") else 0,
                    "active_risk": engine.active_risk() if hasattr(engine, "active_risk") else 0.0,
                    "total_pnl": sum(r.get("pnl", 0.0) for r in engine.records),
                    "tp_count": sum(1 for r in engine.records if r.get("type") == "TP"),
                    "rungs": rung_list,
                    "records": engine.records[-10:] if hasattr(engine, "records") else []
                }
                with open("state.json.tmp", "w") as sf:
                    json.dump(snap, sf)
                os.replace("state.json.tmp", "state.json")
            except Exception:
                pass

            if not current_price or price_age > 30:
                if args.source == "ironbeam":
                    time.sleep(1)
                    continue
                else:
                    current_price = initial_px

            if state["flatten"]:
                engine.emergency_exit("MANUAL_FLATTEN", current_price, ts=datetime.datetime.now().isoformat())
                state["flatten"] = False
                state["paused"] = True
                session_status = "MANUAL_FLATTEN"
                bridge.send(f"🟣 MANUAL FLATTEN executed @ {current_price:.2f}")
                break

            now = datetime.datetime.now()
            if last_hour is not None and now.hour != last_hour:
                tot_pnl = sum(r["pnl"] for r in engine.records)
                tot_fees = sum(r.get("fee", 0.0) for r in engine.records)
                tps = sum(1 for r in engine.records if r["type"] == "TP")
                bridge.send(
                    f"⏰ <b>{now.strftime('%H:00')} Status Update</b>\n"
                    f"Net: ${tot_pnl:.2f} (Fees ${tot_fees:.2f}) | TPs: {tps}\n"
                    f"Open Contracts: {engine.open_contracts_count()} | Risk at Stake: ${engine.active_risk():.2f}",
                    buttons=[[("🟣 FLATTEN ALL", "flat:arm"), ("⏸ HOLD", "hold")]]
                )
            last_hour = now.hour

            before = len(engine.records)
            engine.on_fills(ts=datetime.datetime.now().isoformat())
            if engine.records[before:]:
                for r in engine.records[before:]:
                    bridge.send(f"💹 <b>{r['type']}</b> {r['side']} {r['qty']} @ {r['price']:.2f} (PnL: {r.get('pnl', 0.0):+.2f})")

            if not state["paused"]:
                engine.top_up(current_price)

            if engine.kill_check(current_price, args.low, args.high, ts=datetime.datetime.now().isoformat()):
                session_status = "KILL_SWITCH"
                bridge.send(f"🔴 <b>KILL SWITCH TRIGGERED</b> @ {current_price:.2f}")
                break

            time.sleep(0.5 if args.source == "ironbeam" else 1.0)

    finally:
        broker.cancel_all()
        tot_pnl = sum(r["pnl"] for r in engine.records)
        tot_fees = sum(r.get("fee", 0.0) for r in engine.records)
        tps = sum(1 for r in engine.records if r["type"] == "TP")
        bridge.send(
            f"🏁 <b>Session Terminated ({session_status})</b>\n"
            f"• Net PnL: ${tot_pnl:.2f}\n"
            f"• Fees: ${tot_fees:.2f}\n"
            f"• TP Harvests: {tps}\n"
            f"• Remaining Exposure: {engine.open_contracts_count()} contracts"
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Harmonic Nano-Grid v4 Live Runner")
    parser.add_argument("--source", default="replay", choices=["replay", "ironbeam"])
    parser.add_argument("--live-exec", action="store_true", default=False, help="Real money execution (Defaults to Paper 51428111)")
    parser.add_argument("--instrument", default="MNQ", choices=["MNQ", "NNQ", "MES"])
    parser.add_argument("--low", type=float, default=21000.0)
    parser.add_argument("--high", type=float, default=21100.0)
    parser.add_argument("--lookback", type=int, default=120)
    args = parser.parse_args()

    run_live_session(args)
