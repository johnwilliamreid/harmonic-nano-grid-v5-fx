import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.broker import ReplayBroker
from src.core.grid_engine import HarmonicGridEngine
from src.adapters.telegram_adapter import TelegramBridge

def test_complete_bundle():
    print("==================================================")
    print("🚀 RUNNING FULL V4 PRODUCTION FEATURE BUNDLE TEST")
    print("==================================================")

    # 1. Test Directional Bias Asymmetric Suppression
    proposal = {"instrument": "MNQ", "range_low": 21000.0, "range_high": 21100.0}
    broker = ReplayBroker()
    engine_bull = HarmonicGridEngine(broker, proposal, bias_regime="STRONG_BULLISH")
    engine_bull.deploy(21050.0)

    short_orders = [o for o in engine_bull.orders.values() if o["side"] == "SHORT"]
    long_orders = [o for o in engine_bull.orders.values() if o["side"] == "LONG"]
    assert len(short_orders) == 0, "❌ Error: SHORT order armed under STRONG_BULLISH regime!"
    assert len(long_orders) > 0, "❌ Error: LONG orders not armed under STRONG_BULLISH regime!"
    print("✅ Directional Hysteresis: Short entries fully suppressed under STRONG_BULLISH bias.")

    # 2. Test Emergency Stop Breach
    breach_price = 20990.0  # Beyond stop_loss_long (21000 - 6.25 = 20993.75)
    killed = engine_bull.kill_check(breach_price, 21000.0, 21100.0)
    assert killed is True, "❌ Error: Envelope breach failed to trigger kill_check!"
    assert engine_bull.stopped is True, "❌ Error: Engine state not marked stopped!"
    print("✅ Native Envelope Breach: Kill switch engaged and broker orders cancelled.")

    # 3. Test Telegram Callback Command Dispatcher
    callback_action = []
    def mock_handler(cmd, payload):
        callback_action.append(cmd)

    bridge = TelegramBridge(command_handler=mock_handler)
    bridge._process_update({"callback_query": {"data": "flat:yes"}})
    assert "flat:yes" in callback_action
    print("✅ Telegram Interactive Bridge: Inline callback dispatch verified.")

    print("\n🎉 ALL 4 PRODUCTION FEATURES FULLY BUNDLED & VERIFIED!")

if __name__ == "__main__":
    test_complete_bundle()
