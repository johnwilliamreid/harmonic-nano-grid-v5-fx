try:
    from src.adapters.hyperliquid_adapter import HyperliquidAdapter
except Exception:
    HyperliquidAdapter = None
from src.config import HL_PERP_SPECS
import time
import json
import os
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

from src.core.interfaces import BaseBrokerAdapter, OrderIntent
from src.config import MULTIPLIERS, COMMISSION_PER_RT
from src.adapters.topstepx_adapter import TopstepXAdapter
from src.adapters.ironbeam import IronbeamAdapter

class MultiAccountDispatcher:
    """
    Centralized execution dispatcher routing normalized OrderIntents
    to multiple broker accounts with independent dynamic sizing and risk bounds.
    """

    def __init__(self, config_path: str = "accounts.json"):
        self.config_path = config_path
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.load_accounts()

    def load_accounts(self):
        """Loads and initializes configured account adapters."""
        if not os.path.exists(self.config_path):
            print(f"[Dispatcher] ⚠️ Config {self.config_path} not found.")
            return

        with open(self.config_path, "r") as f:
            raw_cfg = json.load(f)

        for acct_id, cfg in raw_cfg.items():
            if not cfg.get("enabled", False):
                continue

            broker_type = cfg.get("broker", "").lower()
            adapter = None

            if broker_type == "topstepx":
                adapter = TopstepXAdapter(
                    api_key=cfg.get("credentials", {}).get("api_key", ""),
                    account_id=cfg.get("account_id", acct_id),
                    is_live_exec=False
                )
            elif broker_type == "ironbeam":
                adapter = IronbeamAdapter(is_live_exec=False)

            if adapter:
                self.accounts[acct_id] = {
                    "config": cfg,
                    "adapter": adapter,
                    "status": "HEALTHY",
                    "open_pos": 0,
                    "active_orders": {},
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0
                }
                adapter.connect()
                print(f"[Dispatcher] 🔗 Initialized account {acct_id} on {adapter.exec_venue}")

    def dispatch_intent(self, intent: OrderIntent) -> Dict[str, Optional[str]]:
        """
        Routes an abstract OrderIntent across all active accounts concurrently.
        Returns a dictionary mapping account_id -> broker_order_id.
        """
        results = {}

        for acct_id, acct in self.accounts.items():
            if acct["status"] != "HEALTHY":
                continue

            cfg = acct["config"]
            adapter: BaseBrokerAdapter = acct["adapter"]
            inst = cfg.get("instrument", intent.instrument or "MNQ")

            # 1. Calculate dynamic quantity for this account
            qty = self._calculate_size(intent, cfg, inst)

            # 2. Construct account-specific order ID
            ts = int(time.time())
            oid = f"{acct_id}_{inst}_L{intent.rung_idx}_{ts}"
            tif = intent.metadata.get("tif", "GTC")
            reduce_only = intent.metadata.get("reduce_only", False)

            try:
                if intent.intent_type in ("QUOTE_ENTRY", "TP_HARVEST"):
                    boid = adapter.place_limit_order(
                        oid=oid,
                        side=intent.side,
                        price=intent.price,
                        size=qty,
                        instrument=inst,
                        tif=tif
                    )
                    results[acct_id] = boid
                    if boid:
                        acct["active_orders"][intent.rung_idx] = boid

                elif intent.intent_type == "SL_STOP":
                    if hasattr(adapter, "place_stop_order"):
                        boid = adapter.place_stop_order(
                            oid=oid,
                            side=intent.side,
                            price=intent.price,
                            size=qty,
                            instrument=inst,
                            tif=tif
                        )
                    else:
                        boid = adapter.place_limit_order(
                            oid=oid,
                            side=intent.side,
                            price=intent.price,
                            size=qty,
                            instrument=inst,
                            tif=tif
                        )
                    results[acct_id] = boid
                    if boid:
                        acct["active_orders"][f"SL_{intent.rung_idx}"] = boid

                elif intent.intent_type == "CANCEL":
                    prev_boid = acct["active_orders"].get(intent.rung_idx)
                    if prev_boid:
                        adapter.cancel_order(prev_boid)
                        del acct["active_orders"][intent.rung_idx]
                    results[acct_id] = "CANCELLED"

            except Exception as e:
                print(f"[Dispatcher] ⚠️ Order dispatch error on {acct_id}: {e}")
                results[acct_id] = None

        return results

    def _calculate_size(self, intent: OrderIntent, cfg: Dict[str, Any], inst: str) -> int:
        """Dynamic integer sizing using the account risk budget."""
        side_budget = float(cfg.get("side_risk_budget", 500.0))
        mult = float(MULTIPLIERS.get(inst, 2.0))
        fee = float(COMMISSION_PER_RT.get(inst, 1.68 if inst == "NNQ" else 1.86))
        weight = intent.rung_weight or 0.25

        if intent.sl_price and intent.price:
            dist = abs(intent.price - intent.sl_price)
            unit_risk = (dist * mult) + fee
            if unit_risk > 0:
                raw_qty = (side_budget * weight) / unit_risk
                return max(1, int(round(raw_qty)))

        return max(1, int(round(intent.size)))

    def cancel_all_fleet(self):
        """Parallel cancel sweep across all registered venues."""
        with ThreadPoolExecutor(max_workers=max(1, len(self.accounts))) as executor:
            list(executor.map(lambda a: a["adapter"].cancel_all(), self.accounts.values()))
        for a in self.accounts.values():
            a["active_orders"].clear()
        print("[Dispatcher] 🧹 Fleet-wide working orders cancelled.")

    def flatten_all_fleet(self):
        """Parallel emergency liquidation across all active accounts."""
        print("[Dispatcher] 🚨 BROADCAST EMERGENCY FLATTEN INITIATED...")
        with ThreadPoolExecutor(max_workers=max(1, len(self.accounts))) as executor:
            list(executor.map(lambda a: a["adapter"].flatten_position(), self.accounts.values()))
        print("[Dispatcher] ✅ All accounts liquidated to flat.")

    def get_fleet_telemetry(self) -> List[Dict[str, Any]]:
        """Aggregates live account states for the Cockpit HUD table."""
        summary = []
        for acct_id, acct in self.accounts.items():
            cfg = acct.get("config", {})
            adapter = acct.get("adapter")
            pos = adapter.get_position() if (adapter and hasattr(adapter, "get_position")) else acct.get("open_pos", 0)
            summary.append({
                "id": acct_id,
                "venue": getattr(adapter, "exec_venue", cfg.get("broker", "DEMO")),
                "instrument": cfg.get("instrument", "MNQ"),
                "budget": float(cfg.get("side_risk_budget", 500.0)),
                "position": pos,
                "status": acct.get("status", "HEALTHY")
            })
        return summary

    def account_action(self, acct_id: str, action: str):
        """Executes per-account commands (PAUSE, RESUME, FLATTEN) from the Cockpit HUD."""
        acct = self.accounts.get(acct_id)
        if not acct:
            print(f"[Dispatcher] ⚠️ Account {acct_id} not found in fleet.")
            return

        action = action.upper()
        if action == "PAUSE":
            acct["status"] = "PAUSED"
            print(f"[Dispatcher] ⏸️ Account {acct_id} PAUSED by operator.")
        elif action == "RESUME":
            acct["status"] = "HEALTHY"
            print(f"[Dispatcher] ▶️ Account {acct_id} RESUMED by operator.")
        elif action == "FLATTEN":
            print(f"[Dispatcher] 🚨 FLATTENING individual account {acct_id}...")
            adapter = acct.get("adapter")
            if adapter and hasattr(adapter, "flatten_position"):
                adapter.flatten_position()
            acct["status"] = "FLAT"
            acct["open_pos"] = 0
            acct["active_orders"].clear()

    def update_account_budget(self, acct_id: str, new_budget: float) -> bool:
        """Dynamically updates an account risk budget in memory and saves to accounts.json."""
        acct = self.accounts.get(acct_id)
        if not acct:
            print(f"[Dispatcher] ⚠️ Cannot update budget: account {acct_id} not found.")
            return False

        new_budget = float(new_budget)
        acct["config"]["side_risk_budget"] = new_budget
        print(f"[Dispatcher] 💰 Risk budget for {acct_id} updated to ${new_budget:.2f}")

        try:
            cfg_file = getattr(self, "config_path", "accounts.json")
            if os.path.exists(cfg_file):
                with open(cfg_file, "r") as fp:
                    data = json.load(fp)
                if acct_id in data:
                    data[acct_id]["side_risk_budget"] = new_budget
                    with open(cfg_file, "w") as fp:
                        json.dump(data, fp, indent=2)
                    print(f"[Dispatcher] 💾 Saved updated budget for {acct_id} to {cfg_file}")
        except Exception as e:
            print(f"[Dispatcher] ⚠️ Failed to persist budget to {cfg_file}: {e}")

        return True

    def update_account_instrument(self, acct_id: str, new_instrument: str) -> bool:
        """Dynamically updates an account instrument in memory and saves to accounts.json."""
        acct = self.accounts.get(acct_id)
        if not acct:
            print(f"[Dispatcher] ⚠️ Cannot update instrument: account {acct_id} not found.")
            return False

        new_inst = str(new_instrument).strip().upper()
        acct["config"]["instrument"] = new_inst
        print(f"[Dispatcher] 🎯 Instrument for {acct_id} updated to {new_inst}")

        try:
            cfg_file = getattr(self, "config_path", "accounts.json")
            if os.path.exists(cfg_file):
                with open(cfg_file, "r") as fp:
                    data = json.load(fp)
                if acct_id in data:
                    data[acct_id]["instrument"] = new_inst
                    with open(cfg_file, "w") as fp:
                        json.dump(data, fp, indent=2)
                    print(f"[Dispatcher] 💾 Saved updated instrument for {acct_id} to {cfg_file}")
        except Exception as e:
            print(f"[Dispatcher] ⚠️ Failed to persist instrument to {cfg_file}: {e}")

        return True

    def _size_for_account(self, raw_qty: float, instrument: str) -> float:
        spec = HL_PERP_SPECS.get(instrument)
        if spec:
            step = spec["lot_step"]
            min_q = spec["min_qty"]
            decimals = max(0, len(str(step).split(".")[1]) if "." in str(step) else 0)
            sized = round(round(raw_qty / step) * step, decimals)
            return max(min_q, sized)
        return float(max(1, round(raw_qty)))

    def drain_fleet_fills(self) -> List[Dict[str, Any]]:
        all_fills = []
        for acct_id, acct in self.accounts.items():
            adapter = acct.get("adapter")
            if adapter and hasattr(adapter, "drain_fills"):
                for fl in adapter.drain_fills():
                    fl["account_id"] = acct_id
                    all_fills.append(fl)
        return all_fills

