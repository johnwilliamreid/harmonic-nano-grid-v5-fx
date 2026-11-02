import os
import json
import time
import threading
import requests
from typing import Optional, List, Tuple, Callable, Dict, Any

class TelegramBridge:
    """
    Bidirectional Telegram Bridge:
    - Sends structured telemetry and inline confirmation keyboards.
    - Polls updates to handle /status, /flatten, /panic, and /newrange commands.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        command_handler: Optional[Callable[[str, Any], None]] = None
    ):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.api_url = f"https://api.telegram.org/bot{self.token}" if self.token else None
        self.command_handler = command_handler
        self.last_update_id = 0
        self.polling_active = False

    def send(self, text: str, buttons: Optional[List[List[Tuple[str, str]]]] = None):
        if not self.api_url or not self.chat_id:
            print(f"\n[TELEGRAM TELEMETRY]\n{text}\n")
            if buttons:
                print(f"[BUTTONS]: {buttons}\n")
            return

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        if buttons:
            inline_keyboard = [
                [{"text": label, "callback_data": data} for label, data in row]
                for row in buttons
            ]
            payload["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})

        try:
            requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=5)
        except Exception as e:
            print(f"[Telegram] ⚠️ Send failed: {e}")

    def start_polling(self):
        if not self.api_url or not self.chat_id:
            return
        self.polling_active = True
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()

    def _poll_loop(self):
        while self.polling_active:
            try:
                url = f"{self.api_url}/getUpdates?offset={self.last_update_id + 1}&timeout=10"
                resp = requests.get(url, timeout=12).json()
                if resp.get("ok"):
                    for update in resp.get("result", []):
                        self.last_update_id = update["update_id"]
                        self._process_update(update)
            except Exception:
                time.sleep(2)

    def _process_update(self, update: Dict[str, Any]):
        if "callback_query" in update:
            data = update["callback_query"].get("data")
            if self.command_handler:
                self.command_handler(data, None)
            return

        if "message" in update and "text" in update["message"]:
            text = update["message"]["text"].strip()
            if self.command_handler:
                self.command_handler(text, None)
