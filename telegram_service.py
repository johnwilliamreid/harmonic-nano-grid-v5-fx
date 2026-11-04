import time
from dotenv import load_dotenv
load_dotenv()

from src.adapters.telegram_adapter import TelegramBridge

def handle_incoming(cmd: str, payload: any = None):
    print(f"⚡ [TELEGRAM ACTION]: {cmd}")
    if cmd.startswith("arm:"):
        print(f"🎯 Execution triggered: {cmd}")

if __name__ == "__main__":
    print("=" * 60)
    print("🤖 NANO-GRID V4 INTERACTIVE TELEGRAM SERVICE")
    print("Ready to review MNQ & NNQ containers and listen for commands.")
    print("=" * 60)

    bridge = TelegramBridge(command_handler=handle_incoming)
    bridge.start_polling()
    bridge.send(
        "🟢 <b>Harmonic Grid Bot Online</b>\n"
        "• Send <code>/newrange</code> or type <code>[Low] [High]</code>\n"
        "• Bot will analyze and compare <b>MNQ ($2.00)</b> and <b>NNQ ($0.20)</b> automatically."
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Telegram Service...")
        bridge.polling_active = False
