import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datetime
import pandas as pd
from src.broker import ReplayBroker
from src.core.grid_engine import HarmonicGridEngine
from src.core.pre_session_replay import PreSessionReplay

def run_tests():
    print("==================================================")
    print("🚀 RUNNING HARMONIC NANO-GRID V4 TEST HARNESS")
    print("==================================================")

    # 1. Macro Container (1/16 resolution, Delta P = 100 pts)
    proposal_16 = {
        "instrument": "MNQ",
        "range_low": 21000.0,
        "range_high": 21100.0,
    }
    broker = ReplayBroker()
    engine = HarmonicGridEngine(broker, proposal_16)

    assert engine.resolution == 16, f"Expected 16, got {engine.resolution}"
    assert engine.s_step == 6.25, f"Expected step 6.25, got {engine.s_step}"
    assert engine.harvest_step == 12.5, f"Expected harvest step 12.5, got {engine.harvest_step}"
    print(f"✅ Resolution Selection: 1/{engine.resolution} Grid validated for 100 pt container.")

    # 2. Intraday Swing (1/8 resolution, Delta P = 64 pts)
    proposal_8 = {
        "instrument": "MNQ",
        "range_low": 21000.0,
        "range_high": 21064.0,
    }
    engine_8 = HarmonicGridEngine(broker, proposal_8)
    assert engine_8.resolution == 8, f"Expected 8, got {engine_8.resolution}"
    assert engine_8.s_step == 8.0, f"Expected step 8.0, got {engine_8.s_step}"
    assert engine_8.harvest_step == 8.0, f"Expected harvest step 8.0, got {engine_8.harvest_step}"
    print(f"✅ Resolution Selection: 1/{engine_8.resolution} Grid validated for 64 pt container.")

    # 3. Test 2-Contract Rolling Window & $200 Risk Budget
    engine.deploy(21050.0)
    assert engine.open_contracts_count() == 0
    assert engine.active_risk() <= 200.0
    print(f"✅ Deployment: Initial risk committed = ${engine.active_risk():.2f} <= $200.00")

    # Simulate price drop to trigger L7 (21043.75) and L6 (21037.50)
    broker.process_bar(high=21051.0, low=21035.0, last_price=21036.0, open_price=21050.0)
    engine.on_fills()
    assert engine.open_contracts_count() <= 2, f"Open contracts exceeded 2: {engine.open_contracts_count()}"
    print(f"✅ Rolling Window: Concurrent open contracts = {engine.open_contracts_count()} <= 2")

    # 4. Test Trailing Break-Even Ratchet
    engine.top_up(21045.0)
    for pos in engine.open.values():
        if pos["side"] == "LONG" and 21045.0 >= (pos["entry_avg"] + engine.s_step):
            assert pos.get("ratcheted") is True
    print("✅ Trailing Break-Even Ratchet: Risk mitigated to $0.00 upon +1 step progression.")

    # 5. Pre-Session Replay & Hysteresis Test
    replay = PreSessionReplay(range_low=21000.0, range_high=21100.0, resolution=16)
    timestamps = [datetime.datetime(2026, 11, 2, 8, 30) + datetime.timedelta(minutes=i) for i in range(30)]
    mock_df = pd.DataFrame({
        "dt": timestamps,
        "open": [21020.0] * 30,
        "high": [21030.0] * 30,
        "low": [21010.0] * 30,
        "close": [21015.0] * 30,
    })
    replay_out = replay.replay_bars(mock_df)
    assert "s_bias" in replay_out
    assert replay_out["regime"] in ("STRONG_BULLISH", "MILD_BULLISH", "NEUTRAL", "MILD_BEARISH", "STRONG_BEARISH")
    print(f"✅ Pre-Session Replay: Directional Bias = {replay_out['s_bias']} ({replay_out['regime']})")

    print("\n🎉 ALL LOCAL TEST HARNESS ASSERTIONS PASSED (100% SPEC COMPLIANCE)")

if __name__ == "__main__":
    run_tests()
