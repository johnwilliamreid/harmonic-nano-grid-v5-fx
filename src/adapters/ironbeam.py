from src.core.interfaces import BaseBrokerAdapter
import os
import ssl
import time
import threading
import queue
import json
import requests
import pandas as pd
from src.core.interfaces import BaseBrokerAdapter
from dotenv import load_dotenv

load_dotenv()


class IronbeamAdapter(BaseBrokerAdapter):
    """Market data from LIVE; orders to DEMO by default.

    Hardened flatten v5:
    - Robust instrument detection from ANY response field shape.
    - Per-instrument position map; cross-product netting can never fake flat.
    - EVERY closing order's fate is resolved (filled / cancelled) before the
      next attempt is placed.
    - Race condition fix: _order_fate now waits for the fills endpoint to catch
      up before declaring an order cancelled.
    - Settle-and-reverify loop requires the position MAP to be empty.
    - Pause fill polling during flatten; paced cancel sweeps; phantom filter.
    """

    LIVE_BASE = "https://live.ironbeamapi.com/v2"
    DEMO_BASE = "https://demo.ironbeamapi.com/v2"
    LIVE_WS = "wss://live.ironbeamapi.com/v2/stream"

    EXCH_SYMBOLS = {
        "MNQ": "XCME:MNQ.Z26",
        "NNQ": "XCME:NNQ.Z26",
    }
    DATA_SYMBOLS = {
        "MNQ": "XCME:MNQ.Z26",
        "NNQ": "XCME:MNQ.Z26",
    }

    _DEAD_KEYWORDS = (
        'complete', 'fill', 'cancel', 'reject', 'expire', 'done', 'closed'
    )

    def __init__(self, is_live_exec=False):
        self.is_live_exec = is_live_exec
        self.exec_base = self.LIVE_BASE if is_live_exec else self.DEMO_BASE
        self.exec_venue = "LIVE (REAL MONEY)" if is_live_exec else "DEMO (paper)"

        self.data_symbol = os.environ.get("IRONBEAM_SYMBOL", "XCME:MNQ.Z26")
        self.current_instrument = "MNQ"

        self.live_token = None
        self.live_account = None
        self.exec_token = None
        self.account_id = None

        self.active = {}
        self._oid_map = {}
        self._fill_queue = queue.Queue()
        self.fills = []
        self.position = 0
        self.gross = 0
        self.position_map = {}
        self.last_price = None
        self.last_price_time = 0
        self.ws = None
        self.ws_thread = None
        self.fill_thread = None
        self.running = False
        self._fill_running = False
        self._poll_paused = False

        self.bar_queue = queue.Queue()
        self._current_bar = None
        self._bar_lock = threading.Lock()
        self._fill_lock = threading.Lock()
        self._seen_fills = set()
        self._unknown_keys = set()

        self._flattening = False
        self._flatten_guard = threading.Lock()
        self.last_flatten_msg = ""
        self.last_flatten_remaining = 0

        self._api_sleep = 0.15

    # ------------------------------------------------------------------
    # Instrument detection
    # ------------------------------------------------------------------
    def _row_instrument(self, row):
        if not isinstance(row, dict):
            return None
        for key in ("instrument", "symbol", "exchSym", "contractSymbol",
                    "product", "asset", "ticker", "exch_sym"):
            v = row.get(key)
            if isinstance(v, str):
                u = v.upper()
                for inst in ("NNQ", "MNQ"):
                    if inst in u:
                        return inst
        try:
            blob = json.dumps(row).upper()
        except Exception:
            return None
        for inst in ("NNQ", "MNQ"):
            if inst in blob:
                return inst
        return None

    def set_instrument(self, instrument):
        self.current_instrument = instrument
        self.data_symbol = self.DATA_SYMBOLS.get(instrument, "XCME:MNQ.Z26")
        print("[ironbeam] 🎯 Instrument switched to: %s (exchSym: %s)" % (
            instrument, self.data_symbol))
        if getattr(self, "running", False) and getattr(self, "stream_id", None) and self.live_token:
            try:
                for channel in ["quotes", "trades"]:
                    sub = f"{self.LIVE_BASE}/market/{channel}/subscribe/{self.stream_id}?symbols={self.data_symbol}"
                    rr = requests.get(sub, headers={"Authorization": f"Bearer {self.live_token}"}, timeout=5)
                    print("[ironbeam] %s Re-subscribed CME %s to %s -> HTTP %s" % (
                        "✅" if rr.status_code == 200 else "⚠️", channel, self.data_symbol, rr.status_code))
            except Exception as e:
                print(f"[ironbeam] ⚠️ Error re-subscribing quotes for {self.data_symbol}: {e}")

    def authenticate(self):
        lu = os.environ.get("IRONBEAM_LIVE_USERNAME")
        lp = os.environ.get("IRONBEAM_LIVE_PASSWORD")
        lk = os.environ.get("IRONBEAM_LIVE_API_KEY")
        du = os.environ.get("IRONBEAM_USERNAME")
        dp = os.environ.get("IRONBEAM_PASSWORD")
        dk = os.environ.get("IRONBEAM_API_KEY")

        if not (lu and lp and lk):
            print("[ironbeam] ❌ Missing IRONBEAM_LIVE_* creds")
            return False

        r = requests.post(
            f"{self.LIVE_BASE}/auth",
            json={"username": lu, "password": lp, "apiKey": lk},
            timeout=10
        )
        if r.status_code != 200:
            print("[ironbeam]  LIVE auth failed (%s): %s" % (r.status_code, r.text))
            return False

        self.live_token = r.json().get("token")
        self.live_account = self._fetch_account(self.LIVE_BASE, self.live_token)
        print("[ironbeam] ✅ LIVE auth (data) | account %s" % self.live_account)

        if self.is_live_exec:
            self.exec_token = self.live_token
            self.account_id = self.live_account
        else:
            if not (du and dp and dk):
                print("[ironbeam] ❌ Missing IRONBEAM_* (demo) creds")
                return False

            r = requests.post(
                f"{self.DEMO_BASE}/auth",
                json={"username": du, "password": dp, "apiKey": dk},
                timeout=10
            )
            if r.status_code != 200:
                print("[ironbeam] ❌ DEMO auth failed (%s): %s" % (r.status_code, r.text))
                return False

            self.exec_token = r.json().get("token")
            self.account_id = self._fetch_account(self.DEMO_BASE, self.exec_token)

        print("[ironbeam] ✅ EXEC auth (%s) | account %s" % (self.exec_venue, self.account_id))
        self._reconcile_state()
        return True

    def _fetch_account(self, base, token):
        if base == self.DEMO_BASE:
            env_acc = os.environ.get("IRONBEAM_ACCOUNT", "51428111")
            return str(env_acc).strip()

        try:
            r = requests.get(
                f"{base}/account/getAllAccounts",
                headers={"Authorization": "Bearer %s" % token},
                timeout=10
            )
            if r.status_code == 200:
                d = r.json()
                accs = d.get("accounts") if isinstance(d, dict) else d
                if isinstance(accs, list) and accs:
                    return str(accs[0])
            else:
                print(f"[ironbeam] ⚠️ account fetch returned HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            print("[ironbeam] ⚠️ account fetch: %s" % e)

        if not self.is_live_exec:
            return "51428111"
        return None

    def _exec_headers(self):
        return {
            "Authorization": "Bearer %s" % self.exec_token,
            "Content-Type": "application/json"
        }

    # ------------------------------------------------------------------
    # Position model (per-instrument)
    # ------------------------------------------------------------------
    def _position_map(self):
        pm = {}
        try:
            r = requests.get(
                f"{self.exec_base}/account/{self.account_id}/positions",
                headers=self._exec_headers(),
                timeout=8
            )
            if r.status_code == 200:
                for p in r.json().get("positions", []):
                    qty = int(float(p.get("quantity") or 0))
                    if qty == 0:
                        continue
                    side = str(p.get("side") or "").upper()
                    mag = abs(qty)
                    if side in ("BUY", "LONG"):
                        signed = mag
                    elif side in ("SELL", "SHORT"):
                        signed = -mag
                    else:
                        signed = qty
                    inst = self._row_instrument(p) or self.current_instrument
                    pm[inst] = pm.get(inst, 0) + signed
        except Exception as e:
            print("[ironbeam] ⚠️ position query: %s" % e)
        return pm

    def _true_position(self, inst=None):
        target = inst or self.current_instrument
        return self._position_map().get(target, 0)

    def _true_position_retry(self, attempts=3, delay=2.0, inst=None):
        for _ in range(attempts):
            pm = self._position_map()
            if pm is not None:
                return pm.get(inst or self.current_instrument, 0)
            time.sleep(delay)
        return None

    def _reconcile_state(self):
        if not self.account_id:
            return

        self.active.clear()
        self._oid_map.clear()
        active_count = 0
        ignored_count = 0
        inferred_inst = None

        for status_endpoint in ["NEW", "WORKING", "PARTIALLY_FILLED"]:
            try:
                r = requests.get(
                    f"{self.exec_base}/order/{self.account_id}/{status_endpoint}",
                    headers=self._exec_headers(),
                    timeout=10
                )
                if r.status_code == 200:
                    data = r.json()
                    orders = data.get("orders", [])
                    for o in orders:
                        oid = str(o.get("orderId"))
                        if not oid:
                            continue
                        api_status = str(o.get('status', '')).lower()
                        if any(kw in api_status for kw in self._DEAD_KEYWORDS):
                            ignored_count += 1
                            continue

                        inst = self._row_instrument(o)
                        if inst:
                            inferred_inst = inst

                        self.active[oid] = {
                            "side": o.get("side"),
                            "price": float(o.get("limitPrice") or o.get("stopPrice") or 0),
                            "size": int(o.get("quantity") or 1),
                            "our_oid": o.get("clOrdId", oid),
                            "instrument": inst or self.current_instrument
                        }
                        if o.get("clOrdId"):
                            self._oid_map[str(o.get("clOrdId"))] = oid
                        active_count += 1
            except Exception as e:
                print(f"[ironbeam] ️ Error reconciling {status_endpoint} orders: {e}")

        pm = self._position_map()
        self.position_map = pm

        position_inst = None
        for inst, net in pm.items():
            if net != 0:
                position_inst = inst
                break

        if position_inst:
            self.current_instrument = position_inst
        elif inferred_inst:
            self.current_instrument = inferred_inst

        self.data_symbol = getattr(self, "DATA_SYMBOLS", {}).get(self.current_instrument, os.environ.get("IRONBEAM_SYMBOL", "XCME:MNQ.Z26"))

        cur_net = pm.get(self.current_instrument, 0)
        if cur_net == 0 and pm:
            cur_net = next(iter(pm.values()))
        self.position = cur_net
        self.gross = sum(abs(v) for v in pm.values())

        print(f"[ironbeam] ✅ Reconciled {active_count} active orders (Ignored {ignored_count} historical)")
        print(f"[ironbeam] ✅ Reconciled position: {self.position} (gross {self.gross}, map {pm})")
        print(f"[ironbeam] 🎯 Reconciled instrument: {self.current_instrument} ({self.data_symbol})")

        self._seed_seen_fills()

    def _ref_price(self, inst, max_age=30.0):
        if inst == self.current_instrument:
            p = self.last_price
            age = time.time() - self.last_price_time
            if p and p > 0 and age < max_age:
                return float(p)
        for rec in reversed(self.fills):
            if rec.get("instrument") == inst and rec.get("price"):
                return float(rec["price"])
        try:
            for fl in self._fetch_fills_from_broker():
                if self._row_instrument(fl) == inst:
                    px = float(fl.get("fillPrice") or fl.get("avgFillPrice") or fl.get("price") or 0)
                    if px > 0:
                        return px
        except Exception:
            pass
        return None

    def _fresh_price(self, max_wait=5.0, max_age=10.0):
        deadline = time.time() + max_wait
        while time.time() < deadline:
            price = self.last_price
            age = time.time() - self.last_price_time
            if price and price > 0 and age < max_age:
                return float(price)
            time.sleep(0.5)
        return None

    # ------------------------------------------------------------------
    # Order fate resolution
    # ------------------------------------------------------------------
    def _order_fate(self, oid, timeout=12.0):
        """Resolve whether our order filled or is gone. 
        Waits for fills endpoint to catch up before declaring cancelled."""
        boid = self._oid_map.get(oid, oid)
        deadline = time.time() + timeout
        while time.time() < deadline:
            ids = self._fetch_active_ids()
            if boid in ids:
                time.sleep(1.0)
                continue
            
            # Order is gone from active list. Check fills.
            try:
                fills = self._fetch_fills_from_broker()
                for fl in fills:
                    if str(fl.get("orderId")) == boid:
                        return "filled"
            except Exception:
                pass
            
            # If not in fills yet, wait and retry. It might be a race.
            time.sleep(2.0)
            
        # If timeout reached and still not in fills, assume cancelled.
        return "cancelled"

    # ------------------------------------------------------------------
    # Flatten
    # ------------------------------------------------------------------
    def flatten_position(self, instrument=None, max_attempts=3):
        # Support both signatures: flatten_position("NNQ", 3) or flatten_position(3)
        if isinstance(instrument, int):
            max_attempts = instrument
            target_inst = None
        else:
            target_inst = instrument

        with self._flatten_guard:
            if self._flattening:
                print("[ironbeam] ⚠️ Flatten already in progress — ignoring duplicate call")
                return False
            self._flattening = True

        old_poll_paused = self._poll_paused
        self._poll_paused = True

        try:
            print("[ironbeam] 🟣 FLATTEN initiated — fill polling paused")

            remaining_after_cancel = self.cancel_all(max_sweeps=3, verbose=True)
            if remaining_after_cancel > 0:
                print("[ironbeam] ⚠️ %d orders remained after cancel sweep" % remaining_after_cancel)

            aborted = False

            for attempt in range(max_attempts):
                all_pm = {k: v for k, v in self._position_map().items() if v != 0}
                if target_inst:
                    pm = {k: v for k, v in all_pm.items() if k == target_inst}
                else:
                    pm = all_pm
                if not pm:
                    print("[ironbeam] ✅ Position already flat (map empty)")
                    break

                placed = []
                for inst, net in pm.items():
                    side = "SHORT" if net > 0 else "LONG"
                    size = abs(int(net))

                    price = self._ref_price(inst)
                    if price is None:
                        print("[ironbeam] ⚠️ No reference price for %s — cannot flatten safely" % inst)
                        continue

                    offset = 10.0 + 10.0 * attempt
                    flatten_price = price - offset if side == "SHORT" else price + offset

                    oid = "FLATTEN_%s_%d_%d" % (inst, int(time.time()), attempt)
                    boid = self.place_limit_order(oid, side, flatten_price, size, tif="IOC", instrument=inst)

                    if not boid:
                        print("[ironbeam] ️ IOC flatten rejected for %s — falling back to GTC + cancel" % inst)
                        oid = oid + "_G"
                        boid = self.place_limit_order(oid, side, flatten_price, size, tif="GTC", instrument=inst)

                    if not boid:
                        print("[ironbeam]  Flatten order failed for %s on attempt %d" % (inst, attempt))
                        continue

                    placed.append(oid)

                if not placed:
                    break

                # CRITICAL: resolve every closing order before the next attempt.
                for oid in placed:
                    fate = self._order_fate(oid, timeout=12.0)
                    if fate == "filled":
                        print("[ironbeam] ✅ Flatten order %s filled" % oid)
                        continue

                    self.cancel_order(oid)
                    fate = self._order_fate(oid, timeout=4.0)
                    if fate == "filled":
                        print("[ironbeam] ✅ Flatten order %s filled during cancel" % oid)
                        continue
                    if fate == "cancelled":
                        print("[ironbeam] ✅ Flatten order %s cancelled (unfilled)" % oid)
                        continue

                    self.last_flatten_msg = "unresolved flatten order %s — MANUAL CHECK" % oid
                    print("[ironbeam] ⛔ ABORT: flatten order %s fate unknown — MANUAL ACTION REQUIRED" % oid)
                    aborted = True

                if aborted:
                    break

            final_map = {}
            remaining = 0
            quiet = 0

            if not aborted:
                quiet_needed = 3
                for _ in range(12):
                    time.sleep(5.0)
                    self.drain_fills()
                    final_map = {k: v for k, v in self._position_map().items() if v != 0}
                    remaining = len(self._fetch_active_ids())

                    if not final_map and remaining == 0:
                        quiet += 1
                        if quiet >= quiet_needed:
                            break
                    else:
                        quiet = 0
            else:
                final_map = {k: v for k, v in self._position_map().items() if v != 0}
                remaining = len(self._fetch_active_ids())

            self.position_map = final_map
            self.gross = sum(abs(v) for v in final_map.values())
            self.position = final_map.get(self.current_instrument, 0) or (
                next(iter(final_map.values())) if final_map else 0)
            self.last_flatten_remaining = remaining

            if not aborted and not final_map and remaining == 0:
                self.last_flatten_msg = "flat"
                print("[ironbeam] ✅ Flatten complete — ALL instruments flat & swept (verified quiet x%d)" % quiet)
                return True

            self.last_flatten_msg = "INCOMPLETE map %s remaining_orders %s — MANUAL ACTION REQUIRED" % (final_map, remaining)
            print("[ironbeam] ⚠️ FLATTEN INCOMPLETE — residual %s, remaining orders %s — CLOSE MANUALLY" % (final_map, remaining))
            return False

        finally:
            self._poll_paused = old_poll_paused
            self._flattening = False

    # ------------------------------------------------------------------
    # Fill polling
    # ------------------------------------------------------------------
    def start_fill_poll(self, interval=1.5):
        if self.fill_thread and self.fill_thread.is_alive():
            return
        self._fill_running = True
        self.fill_thread = threading.Thread(target=self._fill_loop, args=(interval,), daemon=True)
        self.fill_thread.start()
        print("[ironbeam] 🔁 Fill polling started (every %.1fs)" % interval)

    def _seed_seen_fills(self):
        try:
            for fl in self._fetch_fills_from_broker():
                self._seen_fills.add(self._fill_uid(fl))
        except Exception:
            pass

    def _fill_uid(self, fl):
        return str(
            fl.get("orderUpdateId") or
            fl.get("fillId") or
            "%s-%s" % (fl.get("orderId"), fl.get("timeOrderEvent") or fl.get("timestamp"))
        )

    def _fetch_fills_from_broker(self):
        endpoints = [
            f"{self.exec_base}/account/{self.account_id}/fills",
            f"{self.exec_base}/order/{self.account_id}/FILLED",
        ]
        for url in endpoints:
            try:
                r = requests.get(url, headers=self._exec_headers(), timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    fills = data.get("fills") or data.get("orders") or (data if isinstance(data, list) else [])
                    if fills:
                        return fills
            except Exception:
                continue
        return []

    def _fill_loop(self, interval):
        while self._fill_running:
            if self._poll_paused:
                time.sleep(interval)
                continue

            try:
                fills = self._fetch_fills_from_broker()
                for fl in fills:
                    uid = self._fill_uid(fl)
                    if uid in self._seen_fills:
                        continue
                    self._seen_fills.add(uid)

                    broker_oid = str(fl.get("orderId", ""))
                    info = self.active.get(broker_oid)
                    our_oid = info["our_oid"] if info else fl.get("clOrdId", broker_oid)
                    inst = self._row_instrument(fl) or (info.get("instrument") if info else None) or self.current_instrument

                    qty = int(fl.get("fillQuantity") or fl.get("quantity") or fl.get("filledQuantity") or 0)
                    px = float(fl.get("fillPrice") or fl.get("avgFillPrice") or fl.get("price") or 0)
                    if qty <= 0 or px <= 0:
                        continue

                    side_raw = str(fl.get("side", "")).upper()
                    is_buy = side_raw in ("BUY", "LONG", "B")
                    signed = qty if is_buy else -qty

                    is_flatten_order = our_oid.startswith("FLATTEN_")

                    with self._fill_lock:
                        # FLATTEN orders are reconciled directly from broker in flatten_position()
                        # Applying delta here causes a double-decrement race condition.
                        if not is_flatten_order:
                            self.position_map[inst] = self.position_map.get(inst, 0) + signed
                            self.gross = sum(abs(v) for v in self.position_map.values())
                            if inst == self.current_instrument:
                                self.position += signed
                        self.last_price = px
                        self.last_price_time = time.time()

                    rec = {
                        "order_id": our_oid,
                        "side": "BUY" if is_buy else "SELL",
                        "price": px,
                        "size": qty,
                        "broker_oid": broker_oid,
                        "instrument": inst
                    }
                    self._fill_queue.put(rec)
                    self.fills.append(rec)

                    filled_total = int(fl.get("fillTotalQuantity") or fl.get("cumQty") or qty)
                    if info and filled_total >= info.get("size", qty):
                        self.active.pop(broker_oid, None)
                        self._oid_map.pop(our_oid, None)

                    print("[ironbeam] 💹 FILL: %s %d @ %.2f [%s] (oid=%s, broker=%s)" % (
                        "BUY" if is_buy else "SELL", qty, px, inst, our_oid, broker_oid))

            except Exception as e:
                print("[ironbeam] ⚠️ fill poll: %s" % str(e)[:120])
            time.sleep(interval)

    def drain_fills(self):
        drained = []
        while not self._fill_queue.empty():
            try:
                drained.append(self._fill_queue.get_nowait())
            except queue.Empty:
                break
        return drained

    # ------------------------------------------------------------------
    # WebSocket / bars
    # ------------------------------------------------------------------
    def start_websocket(self):
        if self.running or not self.live_token:
            return
        try:
            import websocket as wsl
            r = requests.get(
                f"{self.LIVE_BASE}/stream/create",
                headers={"Authorization": "Bearer %s" % self.live_token},
                timeout=10
            )
            if r.status_code != 200:
                print("[ironbeam] ❌ stream create: %s" % r.text)
                return

            sid = r.json()["streamId"]
            self.stream_id = sid
            print("[ironbeam] 📡 Stream: %s" % sid)

            self.ws = wsl.create_connection(
                f"{self.LIVE_WS}/{sid}?token={self.live_token}",
                timeout=10,
                sslopt={"cert_reqs": ssl.CERT_NONE}
            )
            print("[ironbeam] ✅ WebSocket connected")

            for _ in range(3):
                try:
                    self.ws.recv()
                except Exception:
                    pass
                time.sleep(0.3)

            for channel in ["quotes", "trades"]:
                sub = f"{self.LIVE_BASE}/market/{channel}/subscribe/{sid}?symbols={self.data_symbol}"
                rr = requests.get(sub, headers={"Authorization": f"Bearer {self.live_token}"}, timeout=5)
                print("[ironbeam] %s subscribe %s (%s) -> %s" % (
                    "✅" if rr.status_code == 200 else "⚠️", self.data_symbol, channel, rr.status_code))

            self.running = True
            self.ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
            self.ws_thread.start()
            print("[ironbeam] 🔁 WS listener started")
            self.start_fill_poll(interval=1.5)
        except Exception as e:
            print("[ironbeam] ❌ WS error: %s" % e)

    def stop(self):
        self.running = False
        self._fill_running = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        if self.ws_thread:
            self.ws_thread.join(timeout=2)
        if self.fill_thread:
            self.fill_thread.join(timeout=2)

    def _ws_loop(self):
        import websocket as wsl
        while self.running:
            try:
                msg = self.ws.recv()
                if not msg:
                    continue
                d = json.loads(msg)
                if not isinstance(d, dict):
                    continue
                if "p" in d and isinstance(d.get("p"), dict) and "ping" in d["p"]:
                    self.ws.send(json.dumps({"p": {"pong": "pong"}}))
                    continue

                price = None
                if "q" in d:
                    for q in (d["q"] if isinstance(d["q"], list) else [d["q"]]):
                        if isinstance(q, dict):
                            price = q.get("last") or q.get("l") or q.get("price")
                            if price:
                                break
                elif "tr" in d:
                    for t in (d["tr"] if isinstance(d["tr"], list) else [d["tr"]]):
                        if isinstance(t, dict):
                            price = t.get("price") or t.get("p")
                            if price:
                                break
                else:
                    for k in d.keys():
                        if k not in self._unknown_keys and k not in ("psa", "f", "ria", "ba", "p"):
                            self._unknown_keys.add(k)

                if price:
                    self.last_price = float(price)
                    self.last_price_time = time.time()
                    self._update_bar(self.last_price)
            except wsl.WebSocketTimeoutException:
                continue
            except Exception as e:
                if self.running:
                    print("[ironbeam] ⚠️ WS loop hiccup: %s. Reconnecting in 2s..." % str(e)[:100])
                    time.sleep(2.0)
                    try:
                        self.ws = wsl.create_connection(
                            f"{self.LIVE_WS}/{self.stream_id}?token={self.live_token}",
                            timeout=10,
                            sslopt={"cert_reqs": ssl.CERT_NONE}
                        )
                    except Exception:
                        pass
                continue

    def _update_bar(self, price):
        now = pd.Timestamp.now().floor("1min")
        with self._bar_lock:
            if self._current_bar is None or self._current_bar["ts"] != now:
                if self._current_bar is not None:
                    self.bar_queue.put(self._current_bar.copy())
                self._current_bar = {
                    "ts": now, "open": price, "high": price, "low": price, "close": price
                }
            else:
                self._current_bar["high"] = max(self._current_bar["high"], price)
                self._current_bar["low"] = min(self._current_bar["low"], price)
                self._current_bar["close"] = price

    def get_next_bar(self, timeout=1.0):
        try:
            return self.bar_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _get_exch_symbol(self, instrument=None):
        inst = instrument or self.current_instrument
        return self.EXCH_SYMBOLS.get(inst, self.EXCH_SYMBOLS.get("MNQ", "XCME:MNQ.Z26"))

    # ------------------------------------------------------------------
    # Order placement / cancellation
    # ------------------------------------------------------------------
    def place_limit_order(self, oid, side, price, size, reduce_only=False, instrument=None, tif="GTC"):
        _ = reduce_only
        return self._place(oid, side, price, size, "Limit", instrument=instrument, limit=price, tif=tif)

    def place_stop_order(self, oid, side, price, size, instrument=None, tif="GTC"):
        return self._place(oid, side, price, size, "Stop", instrument=instrument, stop=price, tif=tif)

    def _place(self, oid, side, price, size, otype, instrument=None, limit=None, stop=None, tif="GTC"):
        time.sleep(self._api_sleep)
        inst = instrument or self.current_instrument
        exch_sym = self._get_exch_symbol(inst)

        # FIX APPLIED HERE: Added "duration": "1" to satisfy Ironbeam API requirement
        payload = {
            "instrument": inst, 
            "exchSym": exch_sym,
            "side": "Buy" if side in ("LONG", "BUY") else "Sell",
            "quantity": size, 
            "orderType": otype, 
            "timeInForce": tif, 
            "clOrdId": oid,
            "duration": "1"
        }
        if limit is not None:
            payload["limitPrice"] = limit
        if stop is not None:
            payload["stopPrice"] = stop

        for attempt in range(2):
            try:
                r = requests.post(
                    f"{self.exec_base}/order/{self.account_id}/place",
                    headers=self._exec_headers(), json=payload, timeout=10
                )
                if r.status_code == 429:
                    print("[ironbeam] ⚠️ Rate limit hit placing order. Sleeping 15s...")
                    time.sleep(15)
                    continue

                if r.status_code in (200, 201):
                    data = r.json()
                    boid = str(data.get("orderId") or data.get("id") or oid)
                    self.active[boid] = {"side": side, "price": price, "size": size,
                                         "our_oid": oid, "instrument": inst}
                    self._oid_map[oid] = boid
                    print("[ironbeam] ✅ [%s] %s %s %d @ %.2f (%s oid=%s)" % (
                        self.exec_venue, inst, side, size, price, tif, boid))
                    return boid

                print("[ironbeam] ❌ Order rejected (%s): %s" % (r.status_code, r.text[:300]))
                return None
            except Exception as e:
                print("[ironbeam] ❌ Order error: %s" % e)
                return None
        return None

    def cancel_order(self, oid):
        boid = self._oid_map.get(oid, oid)
        # Proceed to cancel at broker even if local cache missed it
        if boid not in self.active and not oid.startswith("GRID_") and not oid.startswith("TP_") and not oid.startswith("SL_"):
            self._oid_map.pop(oid, None)
            return True

        time.sleep(self._api_sleep)
        for attempt in range(2):
            try:
                r = requests.delete(
                    f"{self.exec_base}/order/{self.account_id}/cancel/{boid}",
                    headers=self._exec_headers(), timeout=10
                )
                if r.status_code == 429:
                    print("[ironbeam] ⚠️ Rate limit hit cancelling order. Sleeping 10s...")
                    time.sleep(10)
                    continue
                if r.status_code in (200, 204):
                    self.active.pop(boid, None)
                    self._oid_map.pop(oid, None)
                    print("[ironbeam] ✅ Cancelled %s (broker %s)" % (oid, boid))
                    return True
                print("[ironbeam] ❌ Cancel failed (%s): %s" % (r.status_code, r.text[:200]))
                return False
            except Exception as e:
                print("[ironbeam] ️ Cancel error: %s" % e)
                return False
        return False

    def _fetch_active_ids(self):
        active_statuses = ["NEW", "WORKING", "PARTIALLY_FILLED"]
        broker_active_ids = set()
        for status in active_statuses:
            for attempt in range(2):
                try:
                    r = requests.get(
                        f"{self.exec_base}/order/{self.account_id}/{status}",
                        headers=self._exec_headers(), timeout=10
                    )
                    if r.status_code == 429:
                        print("[ironbeam] ⚠️ Rate limit fetching %s orders. Sleeping 8s..." % status)
                        time.sleep(8)
                        continue
                    if r.status_code == 200:
                        data = r.json()
                        orders = data.get("orders", [])
                        for o in orders:
                            oid = str(o.get("orderId"))
                            if not oid:
                                continue
                            api_status = str(o.get('status', '')).lower()
                            if any(kw in api_status for kw in self._DEAD_KEYWORDS):
                                continue
                            broker_active_ids.add(oid)
                    break
                except Exception as e:
                    print("[ironbeam] ⚠️ Error fetching %s orders: %s" % (status, e))
                    break
        return broker_active_ids

    def _cancel_delay(self, count):
        if count >= 150: return 0.30
        if count >= 80: return 0.26
        if count >= 30: return 0.22
        return 0.18

    def cancel_all(self, max_sweeps=3, verbose=True):
        previous_count = None
        total_real_cancelled = 0
        total_already_gone = 0

        for sweep in range(max_sweeps):
            ids = self._fetch_active_ids()
            if not ids:
                self.active.clear()
                self._oid_map.clear()
                if verbose:
                    print("[ironbeam] ✅ No active orders remain.")
                return 0

            count = len(ids)
            if verbose:
                delay = self._cancel_delay(count)
                print("[ironbeam] 🧹 Cancelling %d active orders from broker (sweep %d) — pacing %.2fs/cancel" % (count, sweep + 1, delay))

            if previous_count is not None and count >= previous_count:
                if total_already_gone >= max(1, count // 2):
                    if verbose:
                        print("[ironbeam] ️ Listing not shrinking but %d/%d cancels reported 'already gone'. Stale/phantom listing — no live exposure. Stopping sweep." % (total_already_gone, count))
                    break
                if sweep < max_sweeps - 1:
                    print("[ironbeam] ⚠️ Cancel sweep made no progress (%d -> %d). Waiting 10s for broker state to settle..." % (previous_count, count))
                    time.sleep(10)
                    continue
                print("[ironbeam] ⛔ Cancel sweep stalled (%d -> %d) and cancels are NOT reporting already-gone. Stopping to avoid API hammering. CHECK DASHBOARD." % (previous_count, count))
                break

            delay = self._cancel_delay(count)
            sweep_real = 0
            sweep_gone = 0

            for i, boid in enumerate(ids):
                time.sleep(delay)
                for attempt in range(2):
                    try:
                        r = requests.delete(
                            f"{self.exec_base}/order/{self.account_id}/cancel/{boid}",
                            headers=self._exec_headers(), timeout=10
                        )
                        if r.status_code == 429:
                            print("[ironbeam] ️ Rate limit hit during sweep. Sleeping 10s...")
                            time.sleep(10)
                            continue
                        if r.status_code in (200, 204):
                            sweep_real += 1
                            self.active.pop(boid, None)
                            for k, v in list(self._oid_map.items()):
                                if v == boid:
                                    self._oid_map.pop(k, None)
                            break
                        if r.status_code == 400:
                            sweep_gone += 1
                            self.active.pop(boid, None)
                            for k, v in list(self._oid_map.items()):
                                if v == boid:
                                    self._oid_map.pop(k, None)
                            break
                        if verbose:
                            print("[ironbeam] ⚠️ Cancel failed for %s: %s" % (boid, r.status_code))
                        break
                    except Exception as e:
                        if verbose:
                            print("[ironbeam] ⚠️ Cancel error for %s: %s" % (boid, e))
                        break

                if verbose and (i + 1) % 50 == 0:
                    print("[ironbeam] ... processed %d/%d (real %d, already-gone %d)" % (i + 1, count, sweep_real, sweep_gone))

            total_real_cancelled += sweep_real
            total_already_gone += sweep_gone
            if verbose:
                print("[ironbeam] Sweep %d done: real_cancelled=%d already_gone=%d" % (sweep + 1, sweep_real, sweep_gone))

            time.sleep(3.0)
            previous_count = count

        final_ids = self._fetch_active_ids()
        self.active.clear()
        self._oid_map.clear()
        return len(final_ids)

    def is_active(self, oid):
        return self._oid_map.get(oid, oid) in self.active

    def get_position(self):
        return self.position

    def process_bar(self, high, low, last_price, open_price=None):
        self.last_price = last_price
        self.last_price_time = time.time()

    def fetch_live_quote(self):
        """Poll live quote via REST with confirmed symbols query."""
        if not self.live_token:
            return self.last_price
        try:
            url = f"{self.LIVE_BASE}/market/quotes?symbols={self.data_symbol}"
            r = requests.get(url, headers={"Authorization": f"Bearer {self.live_token}"}, timeout=3)
            if r.status_code == 200:
                d = r.json()
                quotes = d.get("quotes", [])
                if quotes and isinstance(quotes, list):
                    q = quotes[0]
                    p = q.get("l") or q.get("last") or q.get("price")
                    if p:
                        self.last_price = float(p)
                        self.last_price_time = time.time()
                        return float(p)
        except Exception:
            pass
        return self.last_price

    def connect(self) -> bool:
        """Satisfies BaseBrokerAdapter by invoking authenticate()."""
        if hasattr(self, "authenticate"):
            return bool(self.authenticate())
        return True
