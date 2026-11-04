import sqlite3
import threading
import queue
import time
import os
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("DataStore")

class DataStore:
    def __init__(self, timeframe: str = "15m", db_dir: str = "data"):
        self.timeframe = timeframe
        self.db_dir = db_dir
        os.makedirs(self.db_dir, exist_ok=True)
        self.db_path = os.path.join(self.db_dir, f"nanogrid_{self.timeframe}.db")

        self._queue: queue.Queue = queue.Queue()
        self._running = True

        # In-memory fast tracking structures for 17 rungs and active cycles
        # Dict[rung_index -> dict]
        self._levels: Dict[int, Dict[str, Any]] = {}
        # Dict[cycle_id -> dict]
        self._active_cycles: Dict[str, Dict[str, Any]] = {}

        self._init_db()

        # Background worker thread for SQLite writes
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        logger.info(f"DataStore initialized with SQLite WAL partition: {self.db_path}")

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        # 1. Universal 17-Rung Level Touches Auditor
        cur.execute("""
            CREATE TABLE IF NOT EXISTS level_touches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timeframe TEXT,
                rung_index INTEGER,
                rung_price REAL,
                role TEXT,
                side TEXT,
                is_armed INTEGER,
                first_touch_ts REAL,
                last_touch_ts REAL,
                touch_count INTEGER,
                max_bounce_pts REAL,
                max_penetration_pts REAL,
                status TEXT
            );
        """)

        # 2. Armed Trade Excursions (MAE / MFE / Runner)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trade_excursions (
                cycle_id TEXT PRIMARY KEY,
                instrument TEXT,
                timeframe TEXT,
                rung_index INTEGER,
                side TEXT,
                entry_price REAL,
                entry_ts REAL,
                target_tp REAL,
                stop_loss REAL,
                contracts INTEGER,
                mae_pts REAL,
                mfe_pts REAL,
                post_harvest_runner_pts REAL,
                harvest_ts REAL,
                exit_ts REAL,
                exit_price REAL,
                status TEXT
            );
        """)

        # 3. Order Audit Log
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders_log (
                order_id TEXT PRIMARY KEY,
                timestamp REAL,
                instrument TEXT,
                side TEXT,
                price REAL,
                qty INTEGER,
                order_type TEXT,
                status TEXT,
                notes TEXT
            );
        """)

        conn.commit()
        conn.close()

    def _worker_loop(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        batch = []
        last_commit = time.time()

        while self._running or not self._queue.empty():
            try:
                try:
                    task = self._queue.get(timeout=0.25)
                    batch.append(task)
                except queue.Empty:
                    pass

                now = time.time()
                if (len(batch) >= 20 or (now - last_commit >= 1.0)) and batch:
                    for sql, params in batch:
                        try:
                            cur.execute(sql, params)
                        except Exception as e:
                            logger.error(f"SQL execute error: {e}")
                    conn.commit()
                    batch.clear()
                    last_commit = now

            except Exception as e:
                logger.error(f"DataStore worker loop exception: {e}")

        if batch:
            for sql, params in batch:
                try:
                    cur.execute(sql, params)
                except Exception:
                    pass
            conn.commit()
        conn.close()

    # ---------------------------------------------------------
    # Public Non-blocking APIs (Called from live_runner loop)
    # ---------------------------------------------------------

    def register_grid_levels(self, grid: Dict[int, Dict[str, Any]], session_id: str = "LIVE"):
        """Called when a container is armed or updated. Tracks all 17 levels."""
        self._levels.clear()
        for idx in range(17):
            r = grid.get(idx, {})
            price = r.get("price", 0.0)
            role = r.get("role", f"L{idx}")
            side = r.get("side", "LONG" if idx < 8 else ("SHORT" if idx > 8 else "MID"))
            is_armed = 1 if r.get("contracts", 0) > 0 else 0

            self._levels[idx] = {
                "session_id": session_id,
                "timeframe": self.timeframe,
                "rung_index": idx,
                "rung_price": price,
                "role": role,
                "side": side,
                "is_armed": is_armed,
                "first_touch_ts": None,
                "last_touch_ts": None,
                "touch_count": 0,
                "max_bounce_pts": 0.0,
                "max_penetration_pts": 0.0,
                "state": "UNTOUCHED"
            }

    def evaluate_level_touches(self, current_price: float, high: float, low: float):
        """Called on every price tick. Evaluates penetration and bounce on all 17 rungs."""
        if not self._levels:
            return

        now = time.time()
        for idx, lvl in self._levels.items():
            px = lvl["rung_price"]
            if px <= 0:
                continue

            # Check if this tick touches the level
            touched = (low <= px <= high)

            if touched:
                lvl["touch_count"] += 1
                lvl["last_touch_ts"] = now
                if lvl["first_touch_ts"] is None:
                    lvl["first_touch_ts"] = now
                    lvl["state"] = "TOUCHED"

            # If level has been touched at least once, track penetration vs bounce
            if lvl["first_touch_ts"] is not None:
                if lvl["side"] == "LONG":
                    # For longs, bounce is move above px; penetration is adverse move below px
                    penetration = max(0.0, px - low)
                    bounce = max(0.0, high - px)
                elif lvl["side"] == "SHORT":
                    # For shorts, bounce is move below px; penetration is adverse move above px
                    penetration = max(0.0, high - px)
                    bounce = max(0.0, px - low)
                else:
                    penetration = max(0.0, abs(current_price - px))
                    bounce = 0.0

                if bounce > lvl["max_bounce_pts"]:
                    lvl["max_bounce_pts"] = bounce
                if penetration > lvl["max_penetration_pts"]:
                    lvl["max_penetration_pts"] = penetration

    def update_price_excursion(self, current_price: float):
        """Called on every tick to update MAE/MFE for active armed trade cycles."""
        for cycle_id, c in list(self._active_cycles.items()):
            entry = c["entry_price"]
            side = c["side"]

            if c["status"] == "OPEN":
                if side == "LONG":
                    favorable = current_price - entry
                    adverse = entry - current_price
                else:
                    favorable = entry - current_price
                    adverse = current_price - entry

                if adverse > c["mae_pts"]:
                    c["mae_pts"] = adverse
                if favorable > c["mfe_pts"]:
                    c["mfe_pts"] = favorable

            elif c["status"] == "POST_HARVEST_RUNNER":
                # Tracking runner beyond 1/8 TP
                if side == "LONG":
                    runner = current_price - c["target_tp"]
                else:
                    runner = c["target_tp"] - current_price
                if runner > c["post_harvest_runner_pts"]:
                    c["post_harvest_runner_pts"] = runner

    def start_trade_cycle(self, cycle_id: str, instrument: str, rung_index: int, side: str,
                          entry_price: float, target_tp: float, stop_loss: float, contracts: int):
        """Logs when a rung is filled and initiates an active excursion tracking cycle."""
        c = {
            "cycle_id": cycle_id,
            "instrument": instrument,
            "timeframe": self.timeframe,
            "rung_index": rung_index,
            "side": side,
            "entry_price": entry_price,
            "entry_ts": time.time(),
            "target_tp": target_tp,
            "stop_loss": stop_loss,
            "contracts": contracts,
            "mae_pts": 0.0,
            "mfe_pts": 0.0,
            "post_harvest_runner_pts": 0.0,
            "harvest_ts": None,
            "exit_ts": None,
            "exit_price": None,
            "status": "OPEN"
        }
        self._active_cycles[cycle_id] = c

        sql = """
            INSERT OR REPLACE INTO trade_excursions (
                cycle_id, instrument, timeframe, rung_index, side,
                entry_price, entry_ts, target_tp, stop_loss, contracts,
                mae_pts, mfe_pts, post_harvest_runner_pts, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            cycle_id, instrument, self.timeframe, rung_index, side,
            entry_price, c["entry_ts"], target_tp, stop_loss, contracts,
            0.0, 0.0, 0.0, "OPEN"
        )
        self._queue.put((sql, params))

    def record_harvest(self, cycle_id: str, harvest_price: float):
        """Called when a 1/8 TP harvest order fills. Continues tracking excursion for post-harvest runner."""
        c = self._active_cycles.get(cycle_id)
        if c:
            c["harvest_ts"] = time.time()
            c["status"] = "POST_HARVEST_RUNNER"
            sql = """
                UPDATE trade_excursions
                SET harvest_ts = ?, status = 'POST_HARVEST_RUNNER', mae_pts = ?, mfe_pts = ?
                WHERE cycle_id = ?
            """
            self._queue.put((sql, (c["harvest_ts"], c["mae_pts"], c["mfe_pts"], cycle_id)))

    def close_trade_cycle(self, cycle_id: str, exit_price: float, final_status: str = "CLOSED"):
        """Called on final fill, stop-out, or manual close."""
        c = self._active_cycles.pop(cycle_id, None)
        if c:
            now = time.time()
            sql = """
                UPDATE trade_excursions
                SET exit_ts = ?, exit_price = ?, mae_pts = ?, mfe_pts = ?,
                    post_harvest_runner_pts = ?, status = ?
                WHERE cycle_id = ?
            """
            self._queue.put((sql, (now, exit_price, c["mae_pts"], c["mfe_pts"], c["post_harvest_runner_pts"], final_status, cycle_id)))

    def log_order(self, order_id: str, instrument: str, side: str, price: float, qty: int,
                  order_type: str = "LIMIT", status: str = "PLACED", notes: str = ""):
        """Asynchronously writes order lifecycle states."""
        sql = """
            INSERT OR REPLACE INTO orders_log (
                order_id, timestamp, instrument, side, price, qty, order_type, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        self._queue.put((sql, (order_id, time.time(), instrument, side, price, qty, order_type, status, notes)))

    def flush_level_touches_to_db(self):
        """Flushes in-memory 17-rung stats into SQLite."""
        for idx, lvl in self._levels.items():
            if lvl["touch_count"] > 0 or lvl["is_armed"]:
                sql = """
                    INSERT INTO level_touches (
                        session_id, timeframe, rung_index, rung_price, role, side,
                        is_armed, first_touch_ts, last_touch_ts, touch_count,
                        max_bounce_pts, max_penetration_pts, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                params = (
                    lvl["session_id"], lvl["timeframe"], lvl["rung_index"], lvl["rung_price"],
                    lvl["role"], lvl["side"], lvl["is_armed"], lvl["first_touch_ts"],
                    lvl["last_touch_ts"], lvl["touch_count"], round(lvl["max_bounce_pts"], 2),
                    round(lvl["max_penetration_pts"], 2), lvl["state"]
                )
                self._queue.put((sql, params))

    def close(self):
        """Flushes remaining records and closes background thread."""
        self.flush_level_touches_to_db()
        self._running = False
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
