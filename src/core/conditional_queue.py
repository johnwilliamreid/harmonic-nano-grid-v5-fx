import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional, Tuple

TF_SECONDS = {"1m": 60, "4m": 240, "15m": 900, "1H": 3600, "4H": 14400}

def get_correlation_group(symbol: str) -> str:
    s = symbol.upper()
    if s in ("XAUUSD", "XAGUSD"):
        return "METALS"
    if s in ("USTEC", "US500"):
        return "US_INDEX"
    if "JPY" in s:
        return "JPY_CROSS"
    if "USD" in s:
        return "USD_MAJOR"
    return f"CROSS_{s[:3]}"


@dataclass
class ConditionalRangeTrigger:
    trigger_id: str
    symbol: str
    tf: str
    low: float
    high: float
    brk_low_front: float
    brk_low_back: float
    brk_high_front: float
    brk_high_back: float
    profile_key: str = "AUTO_V5"
    posture: str = "TWO_SIDED_BALANCED"
    risk_budget: float = 100.0
    status: str = "STAGED_WATCHING"   # STAGED_WATCHING | PRIMED_IN_ZONE | PAUSED_SLOT_BUSY | DEPLOYED | INVALIDATED
    primed_side: Optional[str] = None
    demand_mit: str = "FRESH"
    supply_mit: str = "FRESH"
    last_price: float = 0.0
    last_bar_close: float = 0.0
    current_bar_id: int = 0
    bar_open: float = 0.0
    bar_high: float = 0.0
    bar_low: float = 0.0
    bar_close: float = 0.0
    created_at: float = 0.0
    expires_at: float = 0.0
    correlation_group: str = ""
    status_note: str = "Waiting for price to test outer reaction shelf"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        span = max(1e-9, self.high - self.low)
        d["span"] = round(span, 5)
        d["step"] = round(span / 16.0, 5)
        d["d_mt"] = round(self.brk_low_back + 0.5 * (self.brk_low_front - self.brk_low_back), 5)
        d["s_mt"] = round(self.brk_high_front + 0.5 * (self.brk_high_back - self.brk_high_front), 5)
        return d


class ConditionalWatchlistRegistry:
    def __init__(self, db_path: str = "data/conditional_watches.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self.watches: Dict[str, ConditionalRangeTrigger] = {}
        self._init_db()
        self._load_active()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conditional_watches (
                trigger_id TEXT PRIMARY KEY,
                symbol TEXT,
                tf TEXT,
                status TEXT,
                payload_json TEXT,
                updated_at REAL
            )
        """)
        conn.commit()
        conn.close()

    def _save_item(self, item: ConditionalRangeTrigger):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO conditional_watches (trigger_id, symbol, tf, status, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (item.trigger_id, item.symbol, item.tf, item.status, json.dumps(asdict(item)), time.time()),
        )
        conn.commit()
        conn.close()

    def _load_active(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT payload_json FROM conditional_watches WHERE status IN ('STAGED_WATCHING', 'PRIMED_IN_ZONE', 'PAUSED_SLOT_BUSY')"
        ).fetchall()
        conn.close()
        now = time.time()
        for (raw,) in rows:
            try:
                data = json.loads(raw)
                item = ConditionalRangeTrigger(**data)
                if item.expires_at and now > item.expires_at:
                    item.status = "INVALIDATED"
                    item.status_note = "Expired while offline"
                    self._save_item(item)
                else:
                    self.watches[item.trigger_id] = item
            except Exception:
                pass

    def stage_watch(self, payload: Dict[str, Any]) -> ConditionalRangeTrigger:
        sym = str(payload.get("symbol") or payload.get("instrument") or "EURUSD").upper().strip()
        tf = str(payload.get("tf") or "15m").strip()
        low = float(payload["low"])
        high = float(payload["high"])
        span = high - low
        df = float(payload.get("brk_low_front") or (low + 0.3125 * span))
        db = float(payload.get("brk_low_back") or low)
        sf = float(payload.get("brk_high_front") or (high - 0.3125 * span))
        sb = float(payload.get("brk_high_back") or high)
        now = time.time()
        tf_sec = TF_SECONDS.get(tf, 900)
        ttl = float(payload.get("ttl_seconds") or (tf_sec * 24))  # 6h on 15m, 24h on 1H
        tid = f"{sym}_{tf}_{int(now)}"
        item = ConditionalRangeTrigger(
            trigger_id=tid,
            symbol=sym,
            tf=tf,
            low=low,
            high=high,
            brk_low_front=df,
            brk_low_back=db,
            brk_high_front=sf,
            brk_high_back=sb,
            profile_key=str(payload.get("profile_key") or "AUTO_V5").upper(),
            posture=str(payload.get("posture") or "TWO_SIDED_BALANCED").upper(),
            risk_budget=float(payload.get("risk_budget") or 100.0),
            created_at=now,
            expires_at=now + ttl,
            correlation_group=get_correlation_group(sym),
            status_note=f"Watching {sym} ({tf}) for entry into [{db:.5f}..{df:.5f}] or [{sf:.5f}..{sb:.5f}]",
        )
        with self._lock:
            self.watches[tid] = item
            self._save_item(item)
        return item

    def cancel_watch(self, trigger_id: str) -> bool:
        with self._lock:
            item = self.watches.pop(trigger_id, None)
            if item:
                item.status = "INVALIDATED"
                item.status_note = "Canceled by operator"
                self._save_item(item)
                return True
        return False

    def update_profile(self, trigger_id: str, profile_key: str) -> bool:
        with self._lock:
            item = self.watches.get(trigger_id)
            if not item:
                return False
            item.profile_key = str(profile_key or "AUTO_V5").upper()
            self._save_item(item)
            return True

    def get_active_symbols(self) -> List[str]:
        with self._lock:
            return sorted(list({w.symbol for w in self.watches.values() if w.status in ("STAGED_WATCHING", "PRIMED_IN_ZONE")}))

    def list_watches(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [w.to_dict() for w in self.watches.values() if w.status in ("STAGED_WATCHING", "PRIMED_IN_ZONE", "PAUSED_SLOT_BUSY")]

    def on_slot_occupied(self, active_symbol: str, active_group: str):
        """First-Trigger-Wins: pause unrelated watches while 1 grid is active; cancel correlated siblings."""
        with self._lock:
            for tid, w in list(self.watches.items()):
                if w.status not in ("STAGED_WATCHING", "PRIMED_IN_ZONE"):
                    continue
                if w.correlation_group == active_group:
                    w.status = "INVALIDATED"
                    w.status_note = f"Auto-canceled: correlated {active_symbol} ({active_group}) deployed first"
                    self._save_item(w)
                    del self.watches[tid]
                else:
                    w.status = "PAUSED_SLOT_BUSY"
                    w.status_note = f"Paused while {active_symbol} occupies active grid slot"
                    self._save_item(w)

    def on_slot_freed(self):
        with self._lock:
            for w in self.watches.values():
                if w.status == "PAUSED_SLOT_BUSY":
                    w.status = "STAGED_WATCHING"
                    w.primed_side = None
                    w.status_note = "Resumed watching (active grid slot freed)"
                    self._save_item(w)

    def on_tick(self, prices_by_symbol: Dict[str, float], slot_busy: bool = False) -> Tuple[Optional[ConditionalRangeTrigger], List[str]]:
        """
        Process live ticks across all watched symbols.
        Returns (triggered_item_if_any, list_of_event_messages).
        """
        events: List[str] = []
        triggered: Optional[ConditionalRangeTrigger] = None
        now = time.time()

        with self._lock:
            for tid, w in list(self.watches.items()):
                if w.expires_at and now > w.expires_at:
                    w.status = "INVALIDATED"
                    w.status_note = "Expired (TTL reached)"
                    self._save_item(w)
                    del self.watches[tid]
                    events.append(f"⌛ EXPIRED: {w.symbol} {w.tf} conditional watch timed out.")
                    continue

                if slot_busy:
                    if w.status in ("STAGED_WATCHING", "PRIMED_IN_ZONE"):
                        w.status = "PAUSED_SLOT_BUSY"
                        w.status_note = "Paused (active grid slot occupied)"
                        self._save_item(w)
                    continue
                elif w.status == "PAUSED_SLOT_BUSY":
                    w.status = "STAGED_WATCHING"
                    w.status_note = "Resumed watching"

                px = prices_by_symbol.get(w.symbol)
                if not px or px <= 0:
                    continue
                w.last_price = px

                tf_sec = TF_SECONDS.get(w.tf, 900)
                bar_id = int(now // tf_sec)

                # Check if previous bar just closed
                if w.current_bar_id != 0 and bar_id > w.current_bar_id:
                    closed_px = w.bar_close
                    w.last_bar_close = closed_px
                    d_mt = w.brk_low_back + 0.5 * (w.brk_low_front - w.brk_low_back)
                    s_mt = w.brk_high_front + 0.5 * (w.brk_high_back - w.brk_high_front)

                    # 1. Check Invalidation (body closed beyond MT or outer L0/L16)
                    if closed_px < d_mt or closed_px < w.low or closed_px > s_mt or closed_px > w.high:
                        w.status = "INVALIDATED"
                        w.status_note = f"Invalidated: {w.tf} body closed @ {closed_px:.5f} beyond MT/boundary"
                        self._save_item(w)
                        del self.watches[tid]
                        events.append(f"🛑 CANCELED: {w.symbol} {w.tf} closed @ {closed_px:.5f} beyond MT/boundary.")
                        continue

                    # 2. Check Trigger Confirmation (must have primed in outer shelf, then closed back inside corridor)
                    if w.status == "PRIMED_IN_ZONE" and triggered is None:
                        if w.primed_side == "DEMAND" and closed_px > w.brk_low_front:
                            w.status = "DEPLOYED"
                            w.status_note = f"Armed on {w.tf} close @ {closed_px:.5f} (> {w.brk_low_front:.5f})"
                            self._save_item(w)
                            del self.watches[tid]
                            triggered = w
                            events.append(f"🚀 ARMED: {w.symbol} {w.tf} closed @ {closed_px:.5f} inside corridor (> {w.brk_low_front:.5f})!")
                            continue
                        elif w.primed_side == "SUPPLY" and closed_px < w.brk_high_front:
                            w.status = "DEPLOYED"
                            w.status_note = f"Armed on {w.tf} close @ {closed_px:.5f} (< {w.brk_high_front:.5f})"
                            self._save_item(w)
                            del self.watches[tid]
                            triggered = w
                            events.append(f"🚀 ARMED: {w.symbol} {w.tf} closed @ {closed_px:.5f} inside corridor (< {w.brk_high_front:.5f})!")
                            continue

                    # Reset bar OHLC for new bar
                    w.current_bar_id = bar_id
                    w.bar_open = w.bar_high = w.bar_low = w.bar_close = px
                else:
                    if w.current_bar_id == 0:
                        w.current_bar_id = bar_id
                        w.bar_open = w.bar_high = w.bar_low = w.bar_close = px
                    else:
                        w.bar_high = max(w.bar_high, px)
                        w.bar_low = min(w.bar_low, px)
                        w.bar_close = px

                # Live intra-bar Priming & MT wick tracking
                d_mt = w.brk_low_back + 0.5 * (w.brk_low_front - w.brk_low_back)
                s_mt = w.brk_high_front + 0.5 * (w.brk_high_back - w.brk_high_front)

                if px <= w.brk_low_front:
                    if w.status != "PRIMED_IN_ZONE" or w.primed_side != "DEMAND":
                        w.status = "PRIMED_IN_ZONE"
                        w.primed_side = "DEMAND"
                        events.append(f"🟠 PRIMED: {w.symbol} {w.tf} entered Demand shelf @ {px:.5f}. Waiting close > {w.brk_low_front:.5f}")
                    if px <= d_mt and w.demand_mit != "MT_MITIGATED":
                        w.demand_mit = "MT_MITIGATED"
                        events.append(f"⚠️ MT WICKED: {w.symbol} Demand MT ({d_mt:.5f}) tagged intra-bar.")
                    w.status_note = f"PRIMED DEMAND @ {px:.5f} -> Need {w.tf} close > {w.brk_low_front:.5f} ({w.demand_mit})"
                    self._save_item(w)
                elif px >= w.brk_high_front:
                    if w.status != "PRIMED_IN_ZONE" or w.primed_side != "SUPPLY":
                        w.status = "PRIMED_IN_ZONE"
                        w.primed_side = "SUPPLY"
                        events.append(f"🟠 PRIMED: {w.symbol} {w.tf} entered Supply shelf @ {px:.5f}. Waiting close < {w.brk_high_front:.5f}")
                    if px >= s_mt and w.supply_mit != "MT_MITIGATED":
                        w.supply_mit = "MT_MITIGATED"
                        events.append(f"⚠️ MT WICKED: {w.symbol} Supply MT ({s_mt:.5f}) tagged intra-bar.")
                    w.status_note = f"PRIMED SUPPLY @ {px:.5f} -> Need {w.tf} close < {w.brk_high_front:.5f} ({w.supply_mit})"
                    self._save_item(w)

        return triggered, events
