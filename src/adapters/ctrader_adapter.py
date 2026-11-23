import os, time, queue, threading, datetime
from typing import Optional, Dict, Any, List
from src.core.interfaces import BaseBrokerAdapter
from src.config import IC_FX_SPECS

class CTraderAdapter(BaseBrokerAdapter):
    def __init__(self, account_id: str = "", is_live_exec: bool = False):
        self.exec_venue = "CTRADER_LIVE" if is_live_exec else "CTRADER_DEMO"
        self.is_live_exec = is_live_exec
        self.client_id = os.getenv("CTRADER_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("CTRADER_CLIENT_SECRET", "").strip()
        self.access_token = os.getenv("CTRADER_ACCESS_TOKEN", "").strip()
        self.account_id = str(account_id or os.getenv("CTRADER_ACCOUNT_ID", "")).strip()
        self.env = os.getenv("CTRADER_ENV", "live" if is_live_exec else "demo").lower()
        self.current_symbol = "EURUSD"
        self.last_price: Optional[float] = None
        self.best_bid: Optional[float] = None
        self.best_ask: Optional[float] = None
        self.position: float = 0.0
        self.active: Dict[str, Dict[str, Any]] = {}
        self._oid_map: Dict[str, str] = {}
        self._pos_to_oid: Dict[int, str] = {}
        self._fill_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._seen_fills: set = set()
        self._open_positions: Dict[int, Dict[str, Any]] = {}
        self._broker_open_orders: set = set()
        self._order_ids: Dict[str, int] = {}
        self._symbol_ids: Dict[str, int] = {}
        self._id_to_symbol: Dict[int, str] = {}
        self._subscribed_ids: set = set()
        self.prices_by_symbol: Dict[str, float] = {}
        self._logged_spot_syms: set = set()
        self._client = None
        self._connected = False

    def is_rollover_blackout(self) -> bool:
        ict = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7)))
        mins = ict.hour * 60 + ict.minute
        return (3 * 60 + 50) <= mins <= (5 * 60 + 15)

    def check_spread_ok(self, max_spread: float) -> bool:
        if self.best_bid is None or self.best_ask is None:
            return True
        return (self.best_ask - self.best_bid) <= max_spread

    def set_instrument(self, instrument: str):
        self.current_symbol = str(instrument or "").upper()
        if self._connected and self._client and self.current_symbol:
            try:
                self.ensure_spot_subscriptions([self.current_symbol])
            except Exception:
                pass

    def connect(self) -> bool:
        if not (self.client_id and self.client_secret and self.access_token and self.account_id):
            print("[cTrader] ℹ️ Missing CTRADER_* credentials in .env — running in offline simulation mode.")
            self._connected = True
            return True
        try:
            from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAApplicationAuthReq
            from twisted.internet import reactor
            host = EndPoints.PROTOBUF_LIVE_HOST if self.env == "live" else EndPoints.PROTOBUF_DEMO_HOST
            self._client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
            def on_conn(client):
                req = ProtoOAApplicationAuthReq()
                req.clientId, req.clientSecret = self.client_id, self.client_secret
                print(f"[cTrader] 📡 TCP connected to {host}, sending AppAuth...")
                client.send(req, responseTimeoutInSeconds=20).addCallbacks(lambda _: self._auth_account(client), lambda f: print(f"[cTrader] ❌ AppAuth failed: {f.getErrorMessage()}"))
            self._client.setConnectedCallback(on_conn)
            self._client.setMessageReceivedCallback(lambda c, m: self._handle_msg(Protobuf.extract(m)))
            self._client.startService()
            if not reactor.running:
                threading.Thread(target=reactor.run, kwargs={"installSignalHandlers": False}, daemon=True).start()
            self._connected = True
            print(f"[cTrader] ✅ Connected to {host} (Account {self.account_id})")
            return True
        except Exception as e:
            print(f"[cTrader] ⚠️ Connection error: {e}")
            return False

    def _auth_account(self, client):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetAccountListByAccessTokenReq
        from ctrader_open_api.protobuf import Protobuf
        if str(self.account_id).isdigit():
            self._send_account_auth(client, int(self.account_id) if str(self.account_id).isdigit() else 0)
            return
        lreq = ProtoOAGetAccountListByAccessTokenReq()
        lreq.accessToken = self.access_token
        def on_list(msg):
            res = Protobuf.extract(msg)
            accts = getattr(res, "ctidTraderAccount", [])
            print(f"[cTrader] 📋 Found {len(accts)} authorized account(s):")
            target_id = None
            for a in accts:
                aid = int(a.ctidTraderAccountId)
                is_live = bool(getattr(a, "isLive", False))
                login = getattr(a, "traderLogin", "N/A")
                env_tag = "LIVE" if is_live else "DEMO"
                print(f"   • ctidTraderAccountId={aid} | login={login} | env={env_tag}")
                if (self.env == "live" and is_live) or (self.env != "live" and not is_live):
                    target_id = aid
            if not target_id and accts:
                target_id = int(accts[0].ctidTraderAccountId)
            if target_id:
                self.account_id = str(target_id)
                self._send_account_auth(client, target_id)
            else:
                print("[cTrader] ❌ No accounts returned for this Access Token.")
        client.send(lreq, responseTimeoutInSeconds=20).addCallbacks(
            on_list, lambda f: print(f"[cTrader] ❌ AccountList failed: {f.getErrorMessage()}")
        )

    def _send_account_auth(self, client, num_id: int):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAccountAuthReq, ProtoOASymbolsListReq
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId, req.accessToken = num_id, self.access_token
        def on_ok(_):
            self._connected = True
            print(f"[cTrader] 🔐 Account {num_id} authenticated ({self.env.upper()})")
            try:
                from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
                rreq = ProtoOAReconcileReq()
                rreq.ctidTraderAccountId = num_id
                client.send(rreq, responseTimeoutInSeconds=20).addErrback(
                    lambda f: print(f"[cTrader] ⚠️ Reconcile err: {f.getErrorMessage()}")
                )
            except Exception as re:
                print(f"[cTrader] ⚠️ Reconcile setup err: {re}")

            sreq = ProtoOASymbolsListReq()
            sreq.ctidTraderAccountId = num_id
            client.send(sreq, responseTimeoutInSeconds=20).addErrback(
                lambda f: print(f"[cTrader] ⚠️ SymbolsList err: {f.getErrorMessage()}")
            )
        client.send(req, responseTimeoutInSeconds=20).addCallbacks(
            on_ok, lambda f: print(f"[cTrader] ❌ AccountAuth failed ({num_id}): {f.getErrorMessage()}")
        )


    def _handle_msg(self, payload):
        name = type(payload).__name__
        if name == "ProtoOASymbolsListRes":
            for s in payload.symbol:
                sym_u = s.symbolName.upper()
                self._symbol_ids[sym_u] = s.symbolId
                self._id_to_symbol[s.symbolId] = sym_u
            for sym_to_sub in {self.current_symbol, "XAUUSD"}:
                if sym_to_sub and sym_to_sub in self._symbol_ids:
                    self._subscribe_spots(sym_to_sub)
        elif name == "ProtoOASpotEvent":
            sym = self._id_to_symbol.get(getattr(payload, "symbolId", 0), self.current_symbol)
            digits = 3 if "JPY" in sym else (2 if ("XAU" in sym or "XAG" in sym) else 5)
            scale = 10 ** digits
            raw_b = float(getattr(payload, "bid", 0) or 0)
            raw_a = float(getattr(payload, "ask", 0) or 0)
            eff_scale = scale
            probe_px = (raw_b or raw_a) / eff_scale if eff_scale else 0.0
            if ("XAU" in sym or "XAG" in sym) and probe_px > 20000.0:
                eff_scale = 100000.0
            raw_b = float(getattr(payload, "bid", 0) or 0)
            raw_a = float(getattr(payload, "ask", 0) or 0)
            eff_scale = scale
            probe = (raw_b or raw_a) / eff_scale if eff_scale else 0.0
            if ("XAU" in sym or "XAG" in sym) and probe > 20000.0:
                eff_scale = 100000.0
            b_val = raw_b / eff_scale if raw_b else 0.0
            a_val = raw_a / eff_scale if raw_a else 0.0
            if b_val and a_val:
                mid = round((b_val + a_val) / 2.0, digits)
            else:
                mid = b_val or a_val or self.prices_by_symbol.get(sym, 0.0)
            if mid > 0:
                self.prices_by_symbol[sym] = mid
            if sym == self.current_symbol:
                if b_val: self.best_bid = b_val
                if a_val: self.best_ask = a_val
                if mid > 0:
                    self.last_price = mid
                    if sym not in self._logged_spot_syms:
                        self._logged_spot_syms.add(sym)
                        print(f"[cTrader] 💹 First spot tick {sym}: bid={b_val} ask={a_val} mid={mid}")
        elif name == "ProtoOAReconcileRes":
            try:
                self._open_positions.clear()
                self._broker_open_orders.clear()
                for ord_item in getattr(payload, "order", []):
                    boid = int(getattr(ord_item, "orderId", 0) or 0)
                    if boid > 0:
                        self._broker_open_orders.add(boid)
                net_lots = 0.0
                for pos_obj in getattr(payload, "position", []):
                    pid = int(getattr(pos_obj, "positionId", 0) or 0)
                    td = getattr(pos_obj, "tradeData", None)
                    p_vol = int(getattr(pos_obj, "volume", 0) or (getattr(td, "volume", 0) if td else 0) or 0)
                    p_side = getattr(td, "tradeSide", None) if td else None
                    p_sym = self._id_to_symbol.get(getattr(td, "symbolId", 0) if td else 0, self.current_symbol)
                    if pid > 0 and p_vol > 0:
                        self._open_positions[pid] = {"symbol": p_sym, "volume": p_vol, "tradeSide": p_side}
                        units = IC_FX_SPECS.get(p_sym, {}).get("contract_units", 100000)
                        lots = round((p_vol / 100.0) / units, 2)
                        net_lots += (lots if p_side == 1 else -lots)
                self.position = round(net_lots, 2)
                print(f"[cTrader] 🔄 Reconciled {len(self._open_positions)} open position(s), net={self.position}")
            except Exception as e:
                print(f"[cTrader] ⚠️ Reconcile parse err: {e}")
        elif name == "ProtoOAExecutionEvent":
            ord_obj = getattr(payload, "order", None)
            pos_obj = getattr(payload, "position", None)
            cl_oid = getattr(ord_obj, "clientOrderId", "") if ord_obj else ""
            b_oid = getattr(ord_obj, "orderId", None) if ord_obj else None
            if b_oid:
                try:
                    et = getattr(payload, "executionType", 0)
                    if et in (2, 11):  # ACCEPTED / PARTIAL_FILL
                        self._broker_open_orders.add(int(b_oid))
                    elif et in (3, 5, 6, 7):  # FILLED / CANCELLED / EXPIRED / REJECTED
                        self._broker_open_orders.discard(int(b_oid))
                except Exception:
                    pass
            if cl_oid and b_oid:
                try:
                    self._order_ids[str(cl_oid)] = int(b_oid)
                    if str(cl_oid) in self._oid_map:
                        self._oid_map[str(cl_oid)] = str(b_oid)
                except Exception:
                    pass
            if pos_obj:
                try:
                    pid = int(getattr(pos_obj, "positionId", 0) or 0)
                    td = getattr(pos_obj, "tradeData", None)
                    p_vol = int(getattr(pos_obj, "volume", 0) or (getattr(td, "volume", 0) if td else 0) or 0)
                    p_side = getattr(td, "tradeSide", None) if td else None
                    p_sym = self._id_to_symbol.get(getattr(td, "symbolId", 0) if td else 0, self.current_symbol)
                    if pid > 0:
                        if p_vol > 0:
                            self._open_positions[pid] = {"symbol": p_sym, "volume": p_vol, "tradeSide": p_side}
                        else:
                            self._open_positions.pop(pid, None)
                except Exception:
                    pass
            if getattr(payload, "executionType", 0) not in (3, 11):
                return
            if True:
                deal = payload.deal
                fid = str(getattr(deal, "dealId", time.time()))
                if fid not in self._seen_fills:
                    self._seen_fills.add(fid)
                    ev_sym = self.current_symbol
                    try:
                        d_sid = int(getattr(deal, "symbolId", 0) or 0)
                        if not d_sid and ord_obj is not None:
                            td_obj = getattr(ord_obj, "tradeData", None)
                            d_sid = int(getattr(td_obj, "symbolId", 0) or 0)
                        if d_sid in self._id_to_symbol:
                            ev_sym = self._id_to_symbol[d_sid]
                    except Exception:
                        pass
                    units = IC_FX_SPECS.get(ev_sym, {}).get("contract_units", 100000)
                    raw_vol = float(getattr(deal, "filledVolume", 0) or 0)
                    lots = round((raw_vol / 100.0) / units, 2) if units > 0 else 0.0
                    if lots <= 0 and raw_vol > 0:
                        lots = 0.01
                    side = "BUY" if deal.tradeSide == 1 else "SELL"
                    self.position = round(self.position + (lots if side == "BUY" else -lots), 2)
                    exec_px = float(getattr(deal, "executionPrice", 0.0) or 0.0)
                    if ("XAU" in self.current_symbol or "XAG" in self.current_symbol) and exec_px > 20000.0:
                        exec_px = round(exec_px / 100000.0, 2)
                    elif "JPY" in self.current_symbol and exec_px > 2000.0:
                        exec_px = round(exec_px / 1000.0, 3)
                    elif exec_px > 100.0 and not any(k in self.current_symbol for k in ("XAU", "XAG", "JPY", "US")):
                        exec_px = round(exec_px / 100000.0, 5)
                    ev_oid = getattr(getattr(payload, "order", None), "clientOrderId", "") or ""
                    if not ev_oid and getattr(deal, "orderId", None):
                        rev = {str(v): k for k, v in self._oid_map.items()}
                        ev_oid = rev.get(str(deal.orderId), "")
                    d_pid = int(getattr(deal, "positionId", 0) or (getattr(pos_obj, "positionId", 0) if pos_obj else 0) or 0)
                    close_detail = getattr(deal, "closePositionDetail", None)
                    has_close = bool(close_detail and getattr(close_detail, "entryPrice", 0))
                    if ev_oid.startswith("GRID_") and d_pid > 0 and not has_close:
                        self._pos_to_oid[d_pid] = ev_oid
                        if d_pid not in self._open_positions:
                            self._open_positions[d_pid] = {
                                "symbol": ev_sym,
                                "volume": int(raw_vol) if raw_vol > 0 else self._lots_to_volume(lots, ev_sym),
                                "tradeSide": int(getattr(deal, "tradeSide", 1) or 1)
                            }
                    elif has_close:
                        if d_pid > 0:
                            self._open_positions.pop(d_pid, None)
                    if has_close and d_pid in self._pos_to_oid:
                        orig_oid = self._pos_to_oid.pop(d_pid)
                        parts = orig_oid.split("_")
                        if len(parts) >= 3:
                            gross_profit = float(getattr(close_detail, "grossProfit", 0) or 0)
                            prefix = "TP" if gross_profit >= 0 else "SL"
                            ev_oid = f"{prefix}_{parts[1]}_{parts[2]}_{int(time.time())}"
                    if not ev_oid:
                        ev_oid = fid
                    self._fill_queue.put({"order_id": ev_oid, "side": side, "price": exec_px, "size": lots})

    def ensure_spot_subscriptions(self, symbols: list):
        for sym in symbols:
            su = sym.upper()
            sid = self._symbol_ids.get(su)
            if sid and sid not in self._subscribed_ids and getattr(self, "_client", None) and self._connected:
                self._subscribed_ids.add(sid)
                self._subscribe_spots(su)

    def _subscribe_spots(self, symbol: str):
        sid = self._symbol_ids.get(symbol.upper())
        if self._client and sid:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASubscribeSpotsReq
            req = ProtoOASubscribeSpotsReq()
            req.ctidTraderAccountId = int(self.account_id) if str(self.account_id).isdigit() else 0
            req.symbolId.append(sid)
            self._client.send(req, responseTimeoutInSeconds=20).addErrback(lambda f: print(f"[cTrader] ⚠️ Request err: {f.getErrorMessage()}"))

    def start_websocket(self):
        if not self._connected: self.connect()

    def _lots_to_volume(self, lots: float, instrument: str) -> int:
        units = IC_FX_SPECS.get(instrument.upper(), {}).get("contract_units", 100000)
        return int(round(float(lots) * units * 100))

    def place_limit_order(self, oid: str, side: str, price: float, size: float, instrument: str = "EURUSD", tif: str = "GTC", reduce_only: bool = False, tp: Optional[float] = None, sl: Optional[float] = None) -> Optional[str]:
        inst = (instrument or self.current_symbol).upper()
        if self.is_rollover_blackout() and not reduce_only:
            print(f"[cTrader] 🛑 Rollover blackout (03:50-05:15 ICT) — skipped {oid}")
            return None
        if not self.is_live_exec or not self._client:
            boid = f"SIM_CT_{oid}"
            self.active[boid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": inst}
            self._oid_map[oid] = boid
            print(f"[cTrader] 🧪 [DRY-RUN] Limit {side} {size:.2f} lots {inst} @ {price} ({oid})")
            return boid
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAOrderType, ProtoOATradeSide
        sid = self._symbol_ids.get(inst)
        if not sid: return None
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId, req.symbolId = int(self.account_id) if str(self.account_id).isdigit() else 0, sid
        req.orderType = ProtoOAOrderType.LIMIT
        req.tradeSide = ProtoOATradeSide.BUY if side.upper() in ("BUY", "LONG") else ProtoOATradeSide.SELL
        req.volume, req.limitPrice, req.clientOrderId = self._lots_to_volume(size, inst), float(price), str(oid)
        if tp is not None and float(tp) > 0:
            rel_tp = int(round(abs(float(tp) - float(price)) * 100000))
            if rel_tp > 0:
                req.relativeTakeProfit = rel_tp
        if sl is not None and float(sl) > 0:
            rel_sl = int(round(abs(float(price) - float(sl)) * 100000))
            if rel_sl > 0:
                req.relativeStopLoss = rel_sl
        print(f"[cTrader] 📤 Sending LIMIT {side} {size} lots {inst} (vol={req.volume}) @ {price} [tp={tp}, sl={sl}] [{oid}]")
        self._client.send(req, responseTimeoutInSeconds=20).addErrback(lambda f: print(f"[cTrader] ⚠️ Request err: {f.getErrorMessage()}"))
        self._oid_map[oid] = oid
        return oid

    def amend_position_sl_tp(self, position_id: int, sl: Optional[float] = None, tp: Optional[float] = None) -> bool:
        if not self.is_live_exec or not self._client or not position_id:
            return False
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAmendPositionSLTPReq
        req = ProtoOAAmendPositionSLTPReq()
        req.ctidTraderAccountId = int(self.account_id) if str(self.account_id).isdigit() else 0
        req.positionId = int(position_id)
        if sl is not None and float(sl) > 0:
            req.stopLoss = float(sl)
        if tp is not None and float(tp) > 0:
            req.takeProfit = float(tp)
        self._client.send(req, responseTimeoutInSeconds=20).addErrback(lambda f: print(f"[cTrader] ⚠️ Amend SL/TP err: {f.getErrorMessage()}"))
        return True

    def place_stop_order(self, oid: str, side: str, price: float, size: float, instrument: str = "EURUSD", tif: str = "GTC") -> Optional[str]:
        inst = (instrument or self.current_symbol).upper()
        if not self.is_live_exec or not self._client:
            boid = f"SIM_STOP_{oid}"
            self.active[boid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": inst, "type": "STOP"}
            self._oid_map[oid] = boid
            print(f"[cTrader] 🧪 [DRY-RUN] Stop {side} {size} {inst} @ {price} ({oid})")
            return boid
        sid = self._symbol_ids.get(inst)
        if not sid:
            print(f"[cTrader] ⚠️ Unknown symbol {inst} for stop order")
            return None
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq
        req = ProtoOANewOrderReq()
        req.ctidTraderAccountId = int(self.account_id)
        req.symbolId = sid
        req.orderType = 3  # STOP
        req.tradeSide = 1 if side.upper() in ("BUY", "LONG") else 2
        req.volume = self._lots_to_volume(size, inst)
        req.stopPrice = float(price)
        req.clientOrderId = str(oid)[:50]
        self.active[oid] = {"side": side, "price": price, "size": size, "our_oid": oid, "instrument": inst, "type": "STOP"}
        self._oid_map[oid] = oid
        self._client.send(req, responseTimeoutInSeconds=20).addErrback(lambda f: print(f"[cTrader] ⚠️ Stop order err: {f.getErrorMessage()}"))
        print(f"[cTrader] 🛡️ Live STOP {side} {size} lots {inst} @ {price} (oid={oid})")
        return oid

    def cancel_order(self, oid: str) -> bool:
        mapped = self._oid_map.pop(oid, oid)
        num_oid = self._order_ids.pop(oid, None)
        if num_oid is None and str(mapped).isdigit():
            num_oid = int(mapped)
        self.active.pop(mapped, None)
        self.active.pop(oid, None)
        if self.is_live_exec and self._client and num_oid and str(self.account_id).isdigit():
            try:
                from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOACancelOrderReq
                req = ProtoOACancelOrderReq()
                req.ctidTraderAccountId = int(self.account_id)
                req.orderId = int(num_oid)
                print(f"[cTrader] ✖️ Cancelling orderId={num_oid} ({oid})")
                self._client.send(req, responseTimeoutInSeconds=20).addErrback(
                    lambda f: print(f"[cTrader] ⚠️ Cancel err: {f.getErrorMessage()}")
                )
            except Exception as e:
                print(f"[cTrader] ⚠️ Cancel exception: {e}")
        return True

    def cancel_all(self, *args, **kwargs) -> None:
        for oid in list(self._oid_map.keys()):
            self.cancel_order(oid)
        if self.is_live_exec and self._client and str(self.account_id).isdigit() and getattr(self, "_broker_open_orders", None):
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOACancelOrderReq
            for boid in list(self._broker_open_orders):
                try:
                    req = ProtoOACancelOrderReq()
                    req.ctidTraderAccountId = int(self.account_id)
                    req.orderId = int(boid)
                    print(f"[cTrader] ✖️ Cancelling resting broker orderId={boid}")
                    self._client.send(req, responseTimeoutInSeconds=20).addErrback(
                        lambda f: print(f"[cTrader] ⚠️ Cancel err: {f.getErrorMessage()}")
                    )
                except Exception:
                    pass
                self._broker_open_orders.discard(boid)
        self.active.clear()

    def drain_fills(self) -> List[Dict[str, Any]]:
        out = []
        while not self._fill_queue.empty():
            try: out.append(self._fill_queue.get_nowait())
            except queue.Empty: break
        return out

    def get_position(self, instrument: Optional[str] = None) -> float:
        return self.position

    def _send_close_positions(self, target_inst: str = "") -> int:
        from ctrader_open_api.messages import OpenApiMessages_pb2 as msgs
        close_cls = getattr(msgs, "ProtoOAClosePositionReq", None)
        if not close_cls or not getattr(self, "_open_positions", None):
            return 0
        closed = 0
        for pid, pinfo in list(self._open_positions.items()):
            sym = str(pinfo.get("symbol") or "").upper()
            if target_inst and sym and sym != target_inst:
                continue
            req = close_cls()
            req.ctidTraderAccountId = int(self.account_id)
            req.positionId = int(pid)
            if hasattr(req, "volume") and pinfo.get("volume"):
                req.volume = int(pinfo["volume"])
            print(f"[cTrader] 🚨 Closing positionId={pid} ({sym or target_inst}, vol={pinfo.get('volume')})")
            self._client.send(req, responseTimeoutInSeconds=20).addErrback(
                lambda f: print(f"[cTrader] ⚠️ ClosePosition err: {f.getErrorMessage()}")
            )
            self._open_positions.pop(pid, None)
            closed += 1
        return closed

    def flatten_position(self, instrument: Optional[str] = None, *args, **kwargs) -> None:
        self.cancel_all()
        target_inst = (instrument or "").upper().strip()
        if self.is_live_exec and self._client and str(self.account_id).isdigit():
            try:
                closed = self._send_close_positions(target_inst)
                from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileReq
                from ctrader_open_api import Protobuf
                rreq = ProtoOAReconcileReq()
                rreq.ctidTraderAccountId = int(self.account_id)
                def _on_reconcile_flatten(msg):
                    try:
                        res = Protobuf.extract(msg)
                        self._handle_msg(res)
                        self.cancel_all()
                        self._send_close_positions(target_inst)
                    except Exception as ex:
                        print(f"[cTrader] ⚠️ Reconcile flatten err: {ex}")
                self._client.send(rreq, responseTimeoutInSeconds=15).addCallback(_on_reconcile_flatten)
            except Exception as e:
                print(f"[cTrader] ⚠️ Flatten exception: {e}")
        self.position = 0.0
        print("[cTrader] 🛑 Flatten completed.")
