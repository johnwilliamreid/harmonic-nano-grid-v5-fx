import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.adapters.telegram_adapter import TelegramBridge

received_events = []

def handle_command(cmd, payload):
    print(f"\n📩 RECEIVED TELEGRAM EVENT: {cmd}")
    received_events.append(cmd)

bridge = TelegramBridge(command_handler=handle_command)

if not bridge.token or not bridge.chat_id:
    print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured in .env")
    sys.exit(1)

print("🚀 Dispatching interactive telemetry probe to Telegram...")
bridge.send(
    "🧪 <b>Harmonic Nano-Grid v4 Pre-Flight Probe</b>\n"
    "• System: Local Test Harness\n"
    "• Ironbeam Account: <code>51428111</code> (Paper)\n\n"
    "Tap a button below or reply with <code>/status</code> to verify round-trip telemetry:",
    buttons=[
        [("🟣 TEST FLATTEN", "test:flatten"), ("✅ TEST STATUS", "test:status")]
    ]
)

print("📡 Polling for incoming Telegram events (press a button in chat, 20s timeout)...")
bridge.start_polling()

start = time.time()
while time.time() - start < 20:
    if received_events:
        print(f"🎉 Round-trip verified! Event logged: {received_events[-1]}")
        break
    time.sleep(1)

bridge.polling_active = False
print("Telegram probe complete.")
