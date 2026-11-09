import os
import time
import sqlite3
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("GridDataEngine")

class GridDataEngine:
    """
    Asynchronous, decoupled analytics engine for harmonic grid execution.
    Computes real-time Regime Attribution against a counterfactual flat-balanced
    shadow benchmark and records microstructure level excursions (MFE/MAE).
    Zero broker API calls, zero network overhead, non-blocking SQLite WAL persistence.
    """
    def __init__(self, db_path: str = "data/nano_analytics.db"):
        self.db_path = db_path
        self._init_db()
        self.session_uid: Optional[str] = None
        self.geom: Any = None
        self.instrument: str = "MNQ"
        self.multiplier: float = 2.0
        self.fee_per_rt: float = 1.94

        # Active tracking states (in-memory for sub-microsecond updates)
        self.active_cycles: Dict[str, Dict[str, Any]] = {}
        self.shadow_grid: Dict[int, Dict[str, Any]] = {}
        self.shadow_stats: Dict[str, float] = {
            "realized_pnl": 0.0,
            "total_fees": 0.0,
            "harvests": 0,
            "stops": 0
        }
        self.actual_stats: Dict[str, float] = {
            "realized_pnl": 0.0,
            "total_fees": 0.0,
            "harvests": 0,
            "stops": 0
        }

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            cur = conn.cursor()

            # 1. Sessions Ledger
            cur.execute("""
            CREATE TABLE IF NOT EXISTS analytics_sessions (
                session_uid TEXT PRIMARY KEY,
                instrument TEXT,
                timeframe TEXT,
                range_low REAL,
                range_high REAL,
                p_eq REAL,
                s_step REAL,
                s_harvest REAL,
                posture TEXT,
                profile_name TEXT,
                risk_budget REAL,
                created_at REAL,
                ended_at REAL
            );
            """)

            # 2. Microstructure Level Cycles (MFE / MAE Excursion Tracking)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS level_cycles (
                cycle_id TEXT PRIMARY KEY,
                session_uid TEXT,
                rung_idx INTEGER,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                tp_price REAL,
                sl_price REAL,
                s_harvest REAL,
                entry_time REAL,
                exit_time REAL,
                duration_sec REAL,
                mae_pts REAL,
                mae_dlr REAL,
                mfe_pts REAL,
                mfe_dlr REAL,
                mfe_ratio REAL,
                hit_0_5x_tp INTEGER DEFAULT 0,
                hit_1_0x_tp INTEGER DEFAULT 0,
                hit_1_5x_tp INTEGER DEFAULT 0,
                hit_2_0x_tp INTEGER DEFAULT 0,
                exit_type TEXT,
                net_pnl REAL,
                fee REAL
            );
            """)

            # 3. Comparative Regime Attribution (Actual vs Counterfactual Shadow)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS comparative_attribution (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                session_uid TEXT,
                instrument TEXT,
                live_price REAL,
                actual_net_pnl REAL,
                actual_fees REAL,
                shadow_net_pnl REAL,
                shadow_fees REAL,
                regime_alpha_usd REAL
            );
            """)

            # 4. Directional Bias Validation Ledger
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bias_evaluations (
                eval_id TEXT PRIMARY KEY,
                session_uid TEXT,
                timestamp REAL,
                regime TEXT,
                directive TEXT,
                score_bias REAL,
                entry_ref_price REAL,
                px_5m REAL,
                px_15m REAL,
                avoided_stop_dlr REAL DEFAULT 0.0,
                missed_harvest_dlr REAL DEFAULT 0.0,
                net_bias_alpha REAL DEFAULT 0.0
            );
            """)
            conn.commit()

    def start_session(self, session_uid: str, geom: Any, instrument: str, posture: str, profile_name: str, risk: float, timeframe: str = "15m"):
        """Initializes a new tracking session and builds the counterfactual Option A shadow grid."""
        self.session_uid = session_uid
        self.geom = geom
        self.instrument = instrument
        self.multiplier = getattr(geom, "multiplier", 2.0)
        self.fee_per_rt = getattr(geom, "fee_per_rt", 1.94)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
            INSERT OR REPLACE INTO analytics_sessions 
            (session_uid, instrument, timeframe, range_low, range_high, p_eq, s_step, s_harvest, posture, profile_name, risk_budget, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_uid, instrument, timeframe,
                geom.range_low, geom.range_high,
                (geom.range_low + geom.range_high) / 2.0,
                geom.s_step, geom.s_harvest,
                posture, profile_name, risk, time.time()
            ))
            conn.commit()

        # Build Counterfactual Option A: 6-Level Balanced Symmetrical Grid
        # (L2-L7 Long, L9-L14 Short, equal contract allocation, zero bias suppression)
        self.shadow_grid.clear()
        base_contracts = 1
        for i in range(2, 8): # Discount rungs
            px = geom.range_low + i * geom.s_step
            self.shadow_grid[i] = {
                "side": "LONG", "price": px, "tp": px + geom.s_harvest, "sl": geom.range_low,
                "contracts": base_contracts, "state": "IDLE"
            }
        for i in range(9, 15): # Premium rungs
            px = geom.range_low + i * geom.s_step
            self.shadow_grid[i] = {
                "side": "SHORT", "price": px, "tp": px - geom.s_harvest, "sl": geom.range_high,
                "contracts": base_contracts, "state": "IDLE"
            }
        logger.info(f"Initialized GridDataEngine session {session_uid} with {len(self.shadow_grid)} shadow rungs.")

    def record_cycle_entry(self, cycle_id: str, rung_idx: int, side: str, fill_px: float, tp_px: float, sl_px: float, s_harvest: float):
        """Registers a live entry fill and starts active MFE/MAE tracking."""
        self.active_cycles[cycle_id] = {
            "session_uid": self.session_uid,
            "rung_idx": rung_idx,
            "side": side,
            "entry_price": fill_px,
            "tp_price": tp_px,
            "sl_price": sl_px,
            "s_harvest": s_harvest if s_harvest > 0 else (self.geom.s_harvest if self.geom else 1.0),
            "entry_time": time.time(),
            "mae_pts": 0.0,
            "mfe_pts": 0.0,
            "hit_0_5x": False,
            "hit_1_0x": False,
            "hit_1_5x": False,
            "hit_2_0x": False
        }

    def on_market_tick(self, live_px: float, now: float):
        """Consumes live market quote to update excursions and simulate the shadow benchmark."""
        # 1. Update in-flight active cycle excursions
        for cid, c in list(self.active_cycles.items()):
            side = c["side"]
            entry = c["entry_price"]
            s_h = c["s_harvest"]

            if side == "LONG":
                fav = max(0.0, live_px - entry)
                adv = max(0.0, entry - live_px)
            else:
                fav = max(0.0, entry - live_px)
                adv = max(0.0, live_px - entry)

            if fav > c["mfe_pts"]:
                c["mfe_pts"] = fav
            if adv > c["mae_pts"]:
                c["mae_pts"] = adv

            # Evaluate milestone expansions
            if fav >= 0.5 * s_h: c["hit_0_5x"] = True
            if fav >= 1.0 * s_h: c["hit_1_0x"] = True
            if fav >= 1.5 * s_h: c["hit_1_5x"] = True
            if fav >= 2.0 * s_h: c["hit_2_0x"] = True

        # 2. Simulate Counterfactual Shadow Option A Benchmark
        for idx, r in self.shadow_grid.items():
            st = r["state"]
            side = r["side"]
            px = r["price"]
            tp = r["tp"]
            sl = r["sl"]
            cts = r["contracts"]

            if st == "IDLE":
                # Check for virtual fill
                if (side == "LONG" and live_px <= px) or (side == "SHORT" and live_px >= px):
                    r["state"] = "IN_POSITION"
                    r["entry_fill"] = px
            elif st == "IN_POSITION":
                # Check for TP harvest
                if (side == "LONG" and live_px >= tp) or (side == "SHORT" and live_px <= tp):
                    r["state"] = "IDLE"
                    gross_pnl = abs(tp - px) * self.multiplier * cts
                    net_pnl = gross_pnl - (self.fee_per_rt * cts)
                    self.shadow_stats["realized_pnl"] += net_pnl
                    self.shadow_stats["total_fees"] += (self.fee_per_rt * cts)
                    self.shadow_stats["harvests"] += 1
                # Check for catastrophic SL stop
                elif (side == "LONG" and live_px <= sl) or (side == "SHORT" and live_px >= sl):
                    r["state"] = "STOPPED"
                    loss = abs(px - sl) * self.multiplier * cts
                    net_loss = -loss - (self.fee_per_rt * cts)
                    self.shadow_stats["realized_pnl"] += net_loss
                    self.shadow_stats["total_fees"] += (self.fee_per_rt * cts)
                    self.shadow_stats["stops"] += 1

    def record_cycle_exit(self, cycle_id: str, exit_px: float, exit_type: str, net_pnl: float, fee: float):
        """Finalizes cycle metrics, locks MFE/MAE ratios, and persists to SQLite WAL."""
        c = self.active_cycles.pop(cycle_id, None)
        now = time.time()
        if not c:
            return

        duration = max(0.1, now - c["entry_time"])
        s_h = c["s_harvest"]
        mfe_pts = c["mfe_pts"]
        mae_pts = c["mae_pts"]
        mfe_ratio = round(mfe_pts / s_h, 3) if s_h > 0 else 0.0

        # Update actual stats
        self.actual_stats["realized_pnl"] += net_pnl
        self.actual_stats["total_fees"] += fee
        if "TP" in exit_type.upper() or "HARVEST" in exit_type.upper():
            self.actual_stats["harvests"] += 1
        else:
            self.actual_stats["stops"] += 1

        regime_alpha = self.actual_stats["realized_pnl"] - self.shadow_stats["realized_pnl"]

        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("""
                INSERT OR REPLACE INTO level_cycles
                (cycle_id, session_uid, rung_idx, side, entry_price, exit_price, tp_price, sl_price, s_harvest,
                 entry_time, exit_time, duration_sec, mae_pts, mae_dlr, mfe_pts, mfe_dlr, mfe_ratio,
                 hit_0_5x_tp, hit_1_0x_tp, hit_1_5x_tp, hit_2_0x_tp, exit_type, net_pnl, fee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cycle_id, c["session_uid"], c["rung_idx"], c["side"],
                    c["entry_price"], exit_px, c["tp_price"], c["sl_price"], s_h,
                    c["entry_time"], now, duration,
                    mae_pts, mae_pts * self.multiplier,
                    mfe_pts, mfe_pts * self.multiplier,
                    mfe_ratio,
                    1 if c["hit_0_5x"] else 0,
                    1 if c["hit_1_0x"] else 0,
                    1 if c["hit_1_5x"] else 0,
                    1 if c["hit_2_0x"] else 0,
                    exit_type, net_pnl, fee
                ))

                # Log periodic comparative attribution record
                cur.execute("""
                INSERT INTO comparative_attribution
                (timestamp, session_uid, instrument, live_price, actual_net_pnl, actual_fees, shadow_net_pnl, shadow_fees, regime_alpha_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, c["session_uid"], self.instrument, exit_px,
                    round(self.actual_stats["realized_pnl"], 2),
                    round(self.actual_stats["total_fees"], 2),
                    round(self.shadow_stats["realized_pnl"], 2),
                    round(self.shadow_stats["total_fees"], 2),
                    round(regime_alpha, 2)
                ))
                conn.commit()
        except Exception as e:
            logger.warning(f"Error persisting cycle {cycle_id} to data_engine: {e}")
