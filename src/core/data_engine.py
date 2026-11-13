from typing import List, Dict, Any, Optional
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
        self.fee_per_rt: float = 1.86

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

            # Safe schema migration for Pre-Launch Structural Advisory metadata
            for col_def in [
                "advisory_score REAL",
                "advisory_tier TEXT",
                "s_brk_fit REAL",
                "s_symmetry REAL",
                "s_mt REAL",
                "demand_mit TEXT",
                "supply_mit TEXT",
            ]:
                try:
                    cur.execute(f"ALTER TABLE analytics_sessions ADD COLUMN {col_def};")
                except Exception:
                    pass

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
                model_type TEXT DEFAULT 'MEAN_REVERSION',
                target_milestone REAL,
                structural_dist_pts REAL,
                milestone_progress REAL DEFAULT 0.0,
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
            # Migrate level_cycles for Trend Breaker if needed
            try:
                cur = conn.cursor()
                cols = [r[1] for r in cur.execute("PRAGMA table_info(level_cycles)").fetchall()]
                if "model_type" not in cols:
                    cur.execute("ALTER TABLE level_cycles ADD COLUMN model_type TEXT DEFAULT 'MEAN_REVERSION'")
                if "target_milestone" not in cols:
                    cur.execute("ALTER TABLE level_cycles ADD COLUMN target_milestone REAL")
                if "structural_dist_pts" not in cols:
                    cur.execute("ALTER TABLE level_cycles ADD COLUMN structural_dist_pts REAL")
                if "milestone_progress" not in cols:
                    cur.execute("ALTER TABLE level_cycles ADD COLUMN milestone_progress REAL DEFAULT 0.0")
                conn.commit()
            except Exception:
                pass

    def start_session(self, session_uid: str, geom: Any, instrument: str, posture: str, profile_name: str, risk: float, timeframe: str = "15m", advisory: Optional[Dict[str, Any]] = None):
        """Initializes a new tracking session, logs pre-launch advisory geometry, and builds the counterfactual Option A shadow grid."""
        self.session_uid = session_uid
        self.geom = geom
        self.instrument = instrument
        self.multiplier = getattr(geom, "multiplier", 2.0)
        self.fee_per_rt = getattr(geom, "fee_per_rt", 1.86)

        adv = advisory or {}
        adv_score = adv.get("score")
        adv_tier = adv.get("tier")
        s_brk_fit = adv.get("s_brk_fit")
        s_symmetry = adv.get("s_symmetry")
        s_mt = adv.get("s_mt")
        d_mit = adv.get("demand_mit")
        s_mit = adv.get("supply_mit")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
            INSERT OR REPLACE INTO analytics_sessions 
            (session_uid, instrument, timeframe, range_low, range_high, p_eq, s_step, s_harvest, posture, profile_name, risk_budget, created_at,
             advisory_score, advisory_tier, s_brk_fit, s_symmetry, s_mt, demand_mit, supply_mit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_uid, instrument, timeframe,
                geom.range_low, geom.range_high,
                (geom.range_low + geom.range_high) / 2.0,
                geom.s_step, geom.s_harvest,
                posture, profile_name, risk, time.time(),
                adv_score, adv_tier, s_brk_fit, s_symmetry, s_mt, d_mit, s_mit
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

    def record_cycle_entry(self, cycle_id: str, rung_idx: int, side: str, fill_px: float, tp_px: float, sl_px: float, s_harvest: float, model_type: str = "MEAN_REVERSION", target_milestone: Optional[float] = None):
        """Registers a live entry fill and starts active MFE/MAE tracking."""
        t_dist = abs(tp_px - fill_px)
        self.active_cycles[cycle_id] = {
            "session_uid": self.session_uid,
            "rung_idx": rung_idx,
            "side": side,
            "entry_price": fill_px,
            "tp_price": tp_px,
            "sl_price": sl_px,
            "s_harvest": s_harvest if s_harvest > 0 else (self.geom.s_harvest if self.geom else 1.0),
            "model_type": model_type,
            "target_milestone": target_milestone,
            "structural_dist_pts": t_dist,
            "milestone_progress": 0.0,
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

            if c.get("model_type") == "TREND_BREAKER":
                dist = c.get("structural_dist_pts", 0.0)
                c["milestone_progress"] = round(c["mfe_pts"] / dist, 3) if dist > 0 else 0.0

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

    def get_trend_breaker_scorecard(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT 
                    rung_idx,
                    side,
                    target_milestone,
                    COUNT(*) as fills,
                    SUM(CASE WHEN exit_type LIKE '%TP%' OR exit_type LIKE '%HARVEST%' THEN 1 ELSE 0 END) as harvests,
                    SUM(CASE WHEN exit_type LIKE '%STOP%' OR exit_type LIKE '%SL%' THEN 1 ELSE 0 END) as stops,
                    ROUND(AVG(milestone_progress) * 100.0, 1) as avg_progress_pct,
                    ROUND(AVG(mae_pts), 2) as avg_drawdown_pts,
                    ROUND(SUM(net_pnl), 2) as net_pnl_usd
                FROM level_cycles
                WHERE model_type = 'TREND_BREAKER' AND exit_type IS NOT NULL
                GROUP BY rung_idx, side, target_milestone
                ORDER BY rung_idx ASC
            """).fetchall()
            return [dict(r) for r in rows]

    # ==========================================================================
    # READ-ONLY ADVISORY: 17-RUNG MFE/MAE & TP RECOMMENDATIONS (NO ORDER CHANGES)
    # ==========================================================================
    def _ensure_shadow_excursion_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_rung_excursions (
                touch_id TEXT PRIMARY KEY,
                session_uid TEXT,
                instrument TEXT,
                rung_idx INTEGER,
                side TEXT,
                touch_price REAL,
                s_harvest REAL,
                touch_time REAL,
                mfe_pts REAL DEFAULT 0.0,
                mae_pts REAL DEFAULT 0.0,
                mfe_ratio REAL DEFAULT 0.0,
                hit_0_5x INTEGER DEFAULT 0,
                hit_1_0x INTEGER DEFAULT 0,
                hit_1_5x INTEGER DEFAULT 0,
                resolved INTEGER DEFAULT 0
            );
            """)
            conn.commit()

    def track_virtual_rung_touches(self, live_px: float, now: float, geom: Any, instrument: str = "MNQ"):
        """Passive observer: logs virtual 15-min excursions across all 17 rungs."""
        if not geom or live_px <= 0:
            return
        if not hasattr(self, "_virtual_touches"):
            self._ensure_shadow_excursion_table()
            self._virtual_touches = {}
            self._last_touch_ts = {}

        s_step = getattr(geom, "s_step", 0.0)
        s_harvest = getattr(geom, "s_harvest", 2.0 * s_step)
        r_low = getattr(geom, "range_low", 0.0)
        if s_step <= 0:
            return

        for idx in range(1, 16):
            if idx == 8:
                continue
            rung_px = r_low + idx * s_step
            side = "LONG" if idx < 8 else "SHORT"
            if abs(live_px - rung_px) <= (0.15 * s_step):
                if (now - self._last_touch_ts.get(idx, 0.0)) >= 900.0:
                    tid = f"VIRT_{instrument}_L{idx}_{int(now)}"
                    self._last_touch_ts[idx] = now
                    self._virtual_touches[tid] = {
                        "session_uid": getattr(self, "session_uid", "UNKNOWN"),
                        "instrument": instrument,
                        "rung_idx": idx,
                        "side": side,
                        "touch_price": rung_px,
                        "s_harvest": s_harvest,
                        "touch_time": now,
                        "mfe_pts": 0.0,
                        "mae_pts": 0.0,
                    }

        completed = []
        for tid, vt in list(self._virtual_touches.items()):
            fav = (live_px - vt["touch_price"]) if vt["side"] == "LONG" else (vt["touch_price"] - live_px)
            adv = (vt["touch_price"] - live_px) if vt["side"] == "LONG" else (live_px - vt["touch_price"])
            vt["mfe_pts"] = max(vt["mfe_pts"], fav)
            vt["mae_pts"] = max(vt["mae_pts"], adv)
            if (now - vt["touch_time"]) >= 900.0:
                completed.append(tid)

        if completed:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    for tid in completed:
                        vt = self._virtual_touches.pop(tid)
                        sh = vt["s_harvest"] if vt["s_harvest"] > 0 else 1.0
                        conn.execute("""
                        INSERT OR REPLACE INTO shadow_rung_excursions
                        (touch_id, session_uid, instrument, rung_idx, side, touch_price, s_harvest,
                         touch_time, mfe_pts, mae_pts, mfe_ratio, hit_0_5x, hit_1_0x, hit_1_5x, resolved)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """, (
                            tid, vt["session_uid"], vt["instrument"], vt["rung_idx"], vt["side"],
                            vt["touch_price"], sh, vt["touch_time"], vt["mfe_pts"], vt["mae_pts"],
                            round(vt["mfe_pts"] / sh, 3),
                            1 if vt["mfe_pts"] >= 0.5 * sh else 0,
                            1 if vt["mfe_pts"] >= 1.0 * sh else 0,
                            1 if vt["mfe_pts"] >= 1.5 * sh else 0,
                        ))
                    conn.commit()
            except Exception:
                pass

    def get_tp_recommendations(self, min_samples: int = 10) -> List[Dict[str, Any]]:
        """
        Read-only advisory report: evaluates live fills + virtual shadow touches
        and recommends optimal TP multipliers per rung without changing live orders.
        """
        self._ensure_shadow_excursion_table()
        recs = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(level_cycles)").fetchall()]
                where_model = "AND (model_type IS NULL OR model_type != 'TREND_BREAKER')" if "model_type" in cols else ""

                live_rows = conn.execute(f"""
                    SELECT rung_idx, COUNT(*) as n, AVG(mfe_ratio) as avg_mfe, AVG(mae_pts) as avg_mae,
                           AVG(hit_0_5x_tp) as p05, AVG(hit_1_0x_tp) as p10, AVG(hit_1_5x_tp) as p15
                    FROM level_cycles
                    WHERE exit_type IS NOT NULL {where_model}
                    GROUP BY rung_idx
                """).fetchall()

                virt_rows = conn.execute("""
                    SELECT rung_idx, COUNT(*) as n, AVG(mfe_ratio) as avg_mfe, AVG(mae_pts) as avg_mae,
                           AVG(hit_0_5x) as p05, AVG(hit_1_0x) as p10, AVG(hit_1_5x) as p15
                    FROM shadow_rung_excursions
                    WHERE resolved = 1
                    GROUP BY rung_idx
                """).fetchall()

                by_rung = {}
                for r in live_rows:
                    by_rung[r["rung_idx"]] = {
                        "live_n": r["n"], "virt_n": 0, "w_n": r["n"] * 2,
                        "mfe": (r["avg_mfe"] or 1.0) * r["n"] * 2,
                        "mae": (r["avg_mae"] or 0.0) * r["n"] * 2,
                        "p05": (r["p05"] or 0.0) * r["n"] * 2,
                        "p10": (r["p10"] or 0.0) * r["n"] * 2,
                        "p15": (r["p15"] or 0.0) * r["n"] * 2,
                    }
                for r in virt_rows:
                    idx = r["rung_idx"]
                    cur = by_rung.setdefault(idx, {"live_n": 0, "virt_n": 0, "w_n": 0, "mfe": 0.0, "mae": 0.0, "p05": 0.0, "p10": 0.0, "p15": 0.0})
                    cur["virt_n"] += r["n"]
                    cur["w_n"] += r["n"]
                    cur["mfe"] += (r["avg_mfe"] or 1.0) * r["n"]
                    cur["mae"] += (r["avg_mae"] or 0.0) * r["n"]
                    cur["p05"] += (r["p05"] or 0.0) * r["n"]
                    cur["p10"] += (r["p10"] or 0.0) * r["n"]
                    cur["p15"] += (r["p15"] or 0.0) * r["n"]

                for idx in sorted(by_rung.keys()):
                    st = by_rung[idx]
                    total_obs = st["live_n"] + st["virt_n"]
                    avg_mfe = st["mfe"] / st["w_n"]
                    avg_mae = st["mae"] / st["w_n"]
                    p05 = st["p05"] / st["w_n"]
                    p10 = st["p10"] / st["w_n"]
                    p15 = st["p15"] / st["w_n"]

                    direction = 1 if idx < 8 else -1
                    if total_obs < min_samples:
                        rung_delta = 2
                        tgt_rung = idx + (direction * rung_delta)
                        rec_mult = f"+2 Rungs (->L{tgt_rung:02d})"
                        reason = f"Gathering samples ({total_obs}/{min_samples})"
                    elif p05 >= 0.75 and (p10 < 0.45 or avg_mfe < 0.85):
                        rung_delta = 1
                        tgt_rung = idx + (direction * rung_delta)
                        rec_mult = f"+1 Rung  (->L{tgt_rung:02d})"
                        reason = "Stalls before +2 rungs — quick 1-rung scalp"
                    elif p15 >= 0.65 and avg_mfe >= 1.85:
                        rung_delta = 4
                        tgt_rung = idx + (direction * rung_delta)
                        rec_mult = f"+4 Rungs (->L{tgt_rung:02d})"
                        reason = "Full quartile runway extension"
                    elif p15 >= 0.55 and avg_mfe >= 1.40:
                        rung_delta = 3
                        tgt_rung = idx + (direction * rung_delta)
                        rec_mult = f"+3 Rungs (->L{tgt_rung:02d})"
                        reason = "Strong sweep bounce — hold for +3 rungs"
                    else:
                        rung_delta = 2
                        tgt_rung = idx + (direction * rung_delta)
                        rec_mult = f"+2 Rungs (->L{tgt_rung:02d})"
                        reason = "Standard 2-rung (1/8th) reversion is optimal"

                    recs.append({
                        "rung_idx": idx,
                        "side": "LONG" if idx < 8 else "SHORT",
                        "live_fills": st["live_n"],
                        "shadow_touches": st["virt_n"],
                        "avg_mfe_ratio": round(avg_mfe, 2),
                        "avg_mae_pts": round(avg_mae, 2),
                        "hit_1_0x_pct": round(p10 * 100, 1),
                        "hit_1_5x_pct": round(p15 * 100, 1),
                        "recommendation": rec_mult,
                        "rationale": reason,
                    })
        except Exception:
            pass
        return recs

    def get_advisory_score_accuracy(self):
        """Aggregates realized cycle performance & regime alpha by Pre-Launch Structural Confidence Tier."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT
                    COALESCE(s.advisory_tier, 'UNSCORED') AS tier,
                    COUNT(DISTINCT s.session_uid) AS sessions,
                    ROUND(AVG(s.advisory_score), 1) AS avg_score,
                    ROUND(AVG(s.s_brk_fit), 0) AS avg_brk_fit,
                    ROUND(AVG(s.s_symmetry), 0) AS avg_sym,
                    ROUND(AVG(s.s_mt), 0) AS avg_mt,
                    COUNT(c.cycle_id) AS cycles,
                    ROUND(COALESCE(SUM(CASE WHEN c.net_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(c.cycle_id), 0), 0.0), 1) AS win_pct,
                    ROUND(COALESCE(SUM(CASE WHEN c.exit_type = 'SL_STOP' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(c.cycle_id), 0), 0.0), 1) AS stop_pct,
                    ROUND(COALESCE(SUM(c.net_pnl), 0.0), 2) AS net_pnl
                FROM analytics_sessions s
                LEFT JOIN level_cycles c ON c.session_uid = s.session_uid AND c.exit_type IS NOT NULL
                GROUP BY COALESCE(s.advisory_tier, 'UNSCORED')
                ORDER BY
                    CASE COALESCE(s.advisory_tier, 'UNSCORED')
                        WHEN 'HIGH CONFIDENCE' THEN 1
                        WHEN 'MODERATE' THEN 2
                        WHEN 'LOW / DEGRADED' THEN 3
                        WHEN 'WEAK / BREACHED' THEN 4
                        ELSE 5
                    END ASC
            """).fetchall()
            return [dict(r) for r in rows]
