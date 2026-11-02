import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datetime
import pandas as pd
from src.broker import ReplayBroker
from src.core.grid_engine import HarmonicGridEngine
from src.core.pre_session_replay import PreSessionReplay

def generate_range_session(start_px=21050.0, low_px=21010.0, high_px=21090.0, n_bars=60):
    """Generates synthetic 1m bars oscillating inside a defined container."""
    bars = []
    base_time = datetime.datetime(2026, 11, 2, 8, 30)
    current = start_px
    direction = -1

    for i in range(n_bars):
        t = base_time + datetime.timedelta(minutes=i)
        step = 3.0 * direction
        nxt = current + step
        if nxt <= low_px:
            direction = 1
        elif nxt >= high_px:
            direction = -1

        bar_low = min(current, nxt) - 0.75
        bar_high = max(current, nxt) + 0.75
        bars.append({"dt": t, "open": current, "high": bar_high, "low": bar_low, "close": nxt})
        current = nxt

    return pd.DataFrame(bars)

def test_simulation():
    print("🚀 Running in-memory synthetic session replay...")
    df = generate_range_session()
    
    # 1. Pre-session replay
    replay = PreSessionReplay(range_low=21000.0, range_high=21100.0, resolution=16)
    out = replay.replay_bars(df)
    print(f"✅ Replay complete: Total Rotations={out['total_rotations']}, Regime={out['regime']}")

    # 2. Engine simulation
    broker = ReplayBroker()
    proposal = {"instrument": "MNQ", "range_low": 21000.0, "range_high": 21100.0}
    engine = HarmonicGridEngine(broker, proposal)
    engine.deploy(df["close"].iloc[0])

    for _, bar in df.iterrows():
        broker.process_bar(bar["high"], bar["low"], bar["close"], bar["open"])
        engine.on_fills(ts=bar["dt"].isoformat())
        engine.top_up(bar["close"])

    pnl = sum(r["pnl"] for r in engine.records)
    print(f"✅ Simulation complete: Total Fills={len(engine.records)}, Net PnL=${pnl:.2f}")

if __name__ == "__main__":
    test_simulation()
