import os
import queue
import threading
import pandas as pd

TARGET_PARQUET = "/Users/johnreid/consolidation_engine/data/rolling_1m_nq.parquet"
_bar_q: queue.SimpleQueue = queue.SimpleQueue()
_started = False
_start_lock = threading.Lock()


def _writer_loop():
    while True:
        bar = _bar_q.get()
        batch = [bar]
        while True:
            try:
                batch.append(_bar_q.get_nowait())
            except queue.Empty:
                break

        try:
            rows = []
            for b in batch:
                ts = pd.Timestamp(b["ts"])
                if ts.tzinfo is None:
                    ts_utc = ts.tz_localize("America/New_York").tz_convert("UTC")
                else:
                    ts_utc = ts.tz_convert("UTC")
                rows.append({
                    "ts": ts_utc,
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b.get("volume", 1.0)),
                })

            df_new = pd.DataFrame(rows).set_index("ts")
            if os.path.exists(TARGET_PARQUET):
                df_old = pd.read_parquet(TARGET_PARQUET)
                df_new.index.name = df_old.index.name
                merged = pd.concat([df_old, df_new])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            else:
                merged = df_new.sort_index()

            tmp_path = f"{TARGET_PARQUET}.tmp.{os.getpid()}"
            merged.to_parquet(tmp_path)
            os.replace(tmp_path, TARGET_PARQUET)
        except Exception as exc:
            print(f"[regime_bridge] Parquet append warning: {exc}")


def push_closed_bar(bar: dict):
    """Non-blocking (<1us) enqueue of a closed 1m Ironbeam bar to consolidation_engine."""
    global _started
    if not _started:
        with _start_lock:
            if not _started:
                t = threading.Thread(target=_writer_loop, name="regime-parquet-bridge", daemon=True)
                t.start()
                _started = True
    _bar_q.put(bar.copy())
