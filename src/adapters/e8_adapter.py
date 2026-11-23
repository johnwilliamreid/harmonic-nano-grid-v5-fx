def _extract_boid(res, fallback_oid):
    if not res:
        return None
    import json
    parsed = res
    if isinstance(res, str):
        try:
            parsed = json.loads(res)
        except Exception:
            parsed = res
    if isinstance(parsed, dict):
        if parsed.get('error') or parsed.get('status') == 'error':
            return None
        order_obj = parsed.get('order') if isinstance(parsed.get('order'), dict) else parsed
        return str(order_obj.get('id') or order_obj.get('orderId') or order_obj.get('order_id') or fallback_oid)
    return str(parsed)

"""
E8 Markets MCP Adapter for NanoGrid Perpetual Engine
Interfaces with E8 Terminal via official MCP HTTP Gateway (Hyperliquid backend).
"""

import os
import time
import json
import queue
import logging
import threading
import requests
from typing import Dict, Any, List, Optional

logger = logging.getLogger("e8_adapter")


class E8Adapter:
    MCP_URL = "https://trade.e8markets.com/api/mcp"

    def __init__(self, is_live_exec: bool = False):
        self.is_live_exec = is_live_exec
        self.api_key = os.getenv("E8_API_KEY", "")
        self.account_id = os.getenv("E8_ACCOUNT_ID", "").strip() or "e0db8c92-1194-4caa-a6fc-3d17cd2cec14"
        self.exec_venue = "E8 Markets (Hyperliquid)"

        self.current_symbol = "HL_PERP_BTC"
        self.last_price = 0.0
        self.position = 0.0
        
        # Order Tracking identical to IronbeamAdapter
        self.active: Dict[str, Dict[str, Any]] = {}
        self._oid_map: Dict[str, str] = {}  # clientOrderId -> brokerOrderId
        self._fill_queue: queue.Queue = queue.Queue()
        self._filled_oids = set()
        
        self._rpc_id = 1
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        with self._lock:
            self._rpc_id += 1
            return self._rpc_id

    def _call_tool(self, name: str, arguments: Dict[str, Any]) -> Optional[Any]:
        """Dispatches a standard JSON-RPC 2.0 tools/call to E8 MCP server."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        }
        try:
            r = requests.post(self.MCP_URL, headers=headers, json=payload, timeout=12)
            if r.status_code == 429:
                print(f"[E8] ⚠️ Rate limit encountered. Sleeping 5s...")
                time.sleep(5)
                return None
            if r.status_code != 200:
                print(f"[E8] ❌ Tool call error {name} ({r.status_code}): {r.text[:140]}")
                return None

            data = r.json()
            if "error" in data:
                print(f"[E8] ❌ RPC Error [{name}]: {data['error']}")
                return None

            result = data.get("result", {})
            # Handle standard MCP text content response
            if "content" in result and isinstance(result["content"], list):
                for item in result["content"]:
                    if item.get("type") == "text":
                        text_val = item.get("text", "")
                        try:
                            return json.loads(text_val)
                        except Exception:
                            return text_val
            return result
        except Exception as e:
            print(f"[E8] ⚠️ Tool call exception {name}: {e}")
            return None

    def authenticate(self) -> bool:
        """Validates API Key and verifies accountId access."""
        if not self.api_key:
            print("[E8] ❌ Missing E8_API_KEY in environment (.env)")
            return False

        print(f"[E8] 🔑 Verifying access for account {self.account_id}...")
        res = self._call_tool("e8_account_info", {"accountId": self.account_id} if self.account_id else {})
        if res:
            balance = res.get("balance", "N/A")
            equity = res.get("equity", "N/A")
            print(f"[E8] ✅ Authenticated successfully. Balance: ${balance} | Equity: ${equity}")
            return True
        
        # Fallback test with accounts list
        accs = self._call_tool("e8_accounts_list", {})
        if accs:
            print(f"[E8] ✅ Authenticated via account list: {accs}")
            return True

        print("[E8] ❌ Authentication failed. Check your API key and permissions.")
        return False

    def _normalize_symbol(self, symbol: Optional[str] = None) -> str:
        sym = symbol or self.current_symbol
        if not sym:
            return "HL_PERP_BTC"
        clean = sym.upper().replace("-USD", "").replace("-", "_").strip()
        
        # Explicit alias map for E8 Hyperliquid / HIP-3 assets
        alias_map = {
            # HIP-3 RWAs (trade.xyz)
            "XYZ100": "HL_HIP3_XYZ_XYZ100",
            "NQ": "HL_HIP3_XYZ_XYZ100",
            "NASDAQ": "HL_HIP3_XYZ_XYZ100",
            "NAS100": "HL_HIP3_XYZ_XYZ100",
            "SP500": "HL_HIP3_XYZ_SP500",
            "ES": "HL_HIP3_XYZ_SP500",
            "SPX": "HL_HIP3_XYZ_SP500",
            "GOLD": "HL_HIP3_XYZ_GOLD",
            "XAU": "HL_HIP3_XYZ_GOLD",
            "XAUUSD": "HL_HIP3_XYZ_GOLD",
            "NVDA": "HL_HIP3_XYZ_NVDA",
            # Crypto Perps
            "BTC": "HL_PERP_BTC",
            "ETH": "HL_PERP_ETH",
            "SOL": "HL_PERP_SOL",
            "XRP": "HL_PERP_XRP",
        }
        
        if clean in alias_map:
            return alias_map[clean]
        if clean.startswith("HL_PERP_") or clean.startswith("HL_HIP3_"):
            return clean
        return f"HL_PERP_{clean}"

    def set_instrument(self, symbol: str):
        self.current_symbol = self._normalize_symbol(symbol)
        print(f"[E8] 🎯 Instrument set to {self.current_symbol}")
        self._refresh_asset_specs()

    def _refresh_asset_specs(self):
        res = self._call_tool("e8_trade_asset_get", {"symbol": self.current_symbol})
        if res and isinstance(res, dict):
            px = res.get("price") or res.get("lastPrice") or res.get("ask") or res.get("bid")
            if px:
                self.last_price = float(px)
                print(f"[E8] 📊 Live reference price for {self.current_symbol}: {self.last_price:.2f}")

    def start_websocket(self):
        """Starts background fill & price polling loop."""
        if self._running:
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._poll_thread.start()
        print("[E8] 🔄 Background state polling loop started.")

    def _worker_loop(self):
        """Monitors order fill states and account position periodically."""
        while self._running:
            try:
                # 1. Update Market Price
                self._refresh_asset_specs()

                # 2. Check Working Orders & Fills
                if self.active:
                    orders_res = self._call_tool("e8_orders_list", {
                        "accountId": self.account_id,
                        "symbol": self.current_symbol,
                        "pageSize": 50
                    })
                    order_items = []
                    if isinstance(orders_res, dict):
                        order_items = orders_res.get("orders", [])
                    elif isinstance(orders_res, list):
                        order_items = orders_res

                    for ord_info in order_items:
                        raw_id = ord_info.get("id") or ord_info.get("orderId") or ord_info.get("order_id")
                        boid = str(raw_id) if raw_id is not None else ""
                        cl_oid = str(ord_info.get("clientOrderId") or ord_info.get("client_order_id") or "")
                        status = str(ord_info.get("status", "")).upper()

                        if (boid and boid in self._filled_oids) or (cl_oid and cl_oid in self._filled_oids):
                            continue

                        if (boid and boid in self.active) or (cl_oid and cl_oid in self._oid_map):
                            if status in ("FILLED", "SETTLED"):
                                if boid: self._filled_oids.add(boid)
                                if cl_oid: self._filled_oids.add(cl_oid)
                                mapped_boid = self._oid_map.pop(cl_oid, None) if cl_oid else None
                                active_rec = self.active.pop(boid, None) or (self.active.pop(mapped_boid, None) if mapped_boid else None) or {}
                                our_oid = active_rec.get("our_oid") or cl_oid
                                if our_oid:
                                    self._filled_oids.add(our_oid)
                                    self._oid_map.pop(our_oid, None)
                                fill_px = float(ord_info.get("avgFillPrice") or ord_info.get("fillPrice") or ord_info.get("limitPrice") or ord_info.get("askPrice") or active_rec.get("price", 0.0))
                                fill_qty = float(ord_info.get("filledAmount") or ord_info.get("amount") or active_rec.get("size", 0.0))
                                side = ord_info.get("side", active_rec.get("side", "LONG")).upper()

                                # Contract requires order_id to be our client order ID (GRID_ / TP_ / SL_)
                                engine_oid = our_oid if (our_oid and (our_oid.startswith("GRID_") or our_oid.startswith("TP_") or our_oid.startswith("SL_"))) else (cl_oid or boid)
                                fill_event = {
                                    "order_id": engine_oid,
                                    "broker_order_id": boid,
                                    "side": side,
                                    "price": fill_px,
                                    "size": fill_qty,
                                    "client_order_id": engine_oid
                                }
                                self._fill_queue.put(fill_event)
                                print(f"[E8] 🎯 Order Fill Acknowledged: {our_oid} ({side} {fill_qty} @ {fill_px:.2f})")

                # 3. Update Net Position
                pos_res = self._call_tool("e8_positions_list", {"accountId": self.account_id})
                if pos_res and isinstance(pos_res, dict):
                    positions = pos_res.get("positions", [])
                    net_size = 0.0
                    for p in positions:
                        if p.get("symbol") == self.current_symbol:
                            amt = float(p.get("size") or p.get("amount") or 0.0)
                            side = str(p.get("side", "")).upper()
                            net_size += amt if side in ("BUY", "LONG") else -amt
                    self.position = net_size

            except Exception as e:
                logger.warning(f"[E8] Worker exception: {e}")

            time.sleep(2.0)

    # ------------------------------------------------------------------
    # Engine Interface Compliance
    # ------------------------------------------------------------------
    def drain_fills(self) -> List[Dict[str, Any]]:
        drained = []
        while not self._fill_queue.empty():
            try:
                drained.append(self._fill_queue.get_nowait())
            except queue.Empty:
                break
        return drained

    def get_position(self) -> float:
        return self.position

    def place_limit_order(self, oid: str, side: str, price: float, size: float, reduce_only: bool = False, instrument: Optional[str] = None, tif: str = "GTC", tp: Optional[float] = None, sl: Optional[float] = None) -> bool:
        sym = self._normalize_symbol(instrument)
        order_side = "buy" if side.upper() in ("LONG", "BUY") else "sell"

        args = {
            "accountId": self.account_id,
            "symbol": sym,
            "side": order_side,
            "type": "limit",
            "amount": float(size),
            "limitPrice": float(price),
            "clientOrderId": str(oid)
        }
        if tp is not None and tp > 0:
            args["takeProfit"] = float(tp)
        if sl is not None and sl > 0:
            args["stopLoss"] = float(sl)

        if not self.is_live_exec:
            boid = f"SIM_{oid}"
            self.active[boid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": sym}
            self._oid_map[oid] = boid
            print(f"[E8] 🧪 [DRY-RUN] Limit {side} {size} @ {price:.2f} ({oid})")
            return boid

        res = self._call_tool("e8_order_place", args)
        if res:
            import json
            parsed = res
            if isinstance(res, str):
                try:
                    parsed = json.loads(res)
                except Exception:
                    parsed = res

            boid = None
            if isinstance(parsed, dict):
                if parsed.get("error") or parsed.get("status") == "error":
                    print(f"[E8] ❌ API order placement error: {parsed}")
                    return None
                boid = str(parsed.get("id") or parsed.get("orderId") or parsed.get("order_id") or oid)
            elif isinstance(parsed, (str, int)):
                val_str = str(parsed)
                if any(err_kw in val_str.lower() for err_kw in ("error", "invalid", "not found", "rejected", "failed")):
                    print(f"[E8] ❌ API order placement error: {val_str}")
                    return None
                boid = val_str

            if boid:
                self.active[boid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": sym}
                self._oid_map[oid] = boid
                print(f"[E8] ✅ Limit Order Placed: {oid} -> Broker ID: {boid} ({side} {size} @ {price:.2f})")
                return boid
        return None
        return False

    def place_stop_order(self, oid: str, side: str, price: float, size: float, instrument: Optional[str] = None, tif: str = "GTC") -> bool:
        sym = self._normalize_symbol(instrument)
        order_side = "buy" if side.upper() in ("LONG", "BUY") else "sell"

        args = {
            "accountId": self.account_id,
            "symbol": sym,
            "side": order_side,
            "type": "stop",
            "amount": float(size),
            "limitPrice": float(price),
            "clientOrderId": str(oid)
        }

        if not self.is_live_exec:
            boid = f"SIM_{oid}"
            self.active[boid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": sym}
            self._oid_map[oid] = boid
            print(f"[E8] 🧪 [DRY-RUN] Stop {side} {size} @ {price:.2f} ({oid})")
            return True

        res = self._call_tool("e8_order_place", args)
        if res:
            boid = _extract_boid(res, oid)
            self.active[boid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": sym}
            self._oid_map[oid] = boid
            print(f"[E8] ✅ Stop Order Placed: {oid} -> Broker ID: {boid} ({side} {size} @ {price:.2f})")
            return True
        return False

    def cancel_order(self, oid_or_boid: str) -> bool:
        boid = self._oid_map.get(oid_or_boid, oid_or_boid)
        if not self.is_live_exec:
            self.active.pop(boid, None)
            self._oid_map.pop(oid_or_boid, None)
            return True

        res = self._call_tool("e8_order_cancel", {"accountId": self.account_id, "orderId": boid})
        if res is not None:
            if "error" in str(res).lower() and "not found" not in str(res).lower():
                print(f"[E8] ⚠️ Cancel failed for {boid}: {res}")
                return False
            self.active.pop(boid, None)
            self._oid_map.pop(oid_or_boid, None)
            return True
        return False

    def cancel_all(self, *args, **kwargs):
        """Cancels all active resting orders."""
        if not self.is_live_exec:
            self.active.clear()
            self._oid_map.clear()
            return

        orders_res = self._call_tool("e8_orders_list", {
            "accountId": self.account_id,
            "symbol": self.current_symbol,
            "status": "VALIDATED"
        })
        import json
        if isinstance(orders_res, str):
            try:
                orders_res = json.loads(orders_res)
            except Exception:
                orders_res = []

        if isinstance(orders_res, dict):
            order_items = orders_res.get("orders") or orders_res.get("data") or []
        elif isinstance(orders_res, list):
            order_items = orders_res
        else:
            order_items = []

        for ord_info in order_items:
            if isinstance(ord_info, dict):
                boid = ord_info.get("id") or ord_info.get("orderId")
            elif isinstance(ord_info, (str, int)):
                boid = str(ord_info)
            else:
                boid = None

            if boid:
                try:
                    self._call_tool("e8_order_cancel", {"orderId": str(boid)})
                except Exception as ce:
                    print(f"[E8] ⚠️ Cancel error for {boid}: {ce}")
        self.active.clear()
        self._oid_map.clear()
        print("[E8] 🧹 All resting orders canceled.")

    def flatten_position(self, instrument: Optional[str] = None, *args, **kwargs):
        """Emergency circuit breaker: closes active position at market."""
        if not self.is_live_exec:
            self.position = 0.0
            return

        print("[E8] 🚨 FLATTENING POSITION (e8_positions_close_all)... ")
        try:
            res = self._call_tool("e8_positions_close_all", {
                "accountId": self.account_id,
                "slippage": 100
            })
            print(f"[E8] ✅ Positions close all result: {res}")
        except Exception as e:
            print(f"[E8] ⚠️ e8_positions_close_all failed, falling back to e8_position_close: {e}")
            self._call_tool("e8_position_close", {
                "accountId": self.account_id,
                "symbol": self._normalize_symbol(instrument) if instrument else self.current_symbol,
                "slippage": 100
            })
        self.position = 0.0

    def check_limits(self) -> Dict[str, Any]:
        """Queries daily and total drawdown safety room."""
        res = self._call_tool("e8_account_limits", {"accountId": self.account_id})
        return res if isinstance(res, dict) else {}
