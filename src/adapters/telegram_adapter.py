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
    - Handles interactive multi-option grid proposal cards and arming callbacks.
    - Polls updates to handle /status, /flatten, /propose, and /risk commands.
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
        self.pending_proposals: Dict[str, Dict[str, Any]] = {}

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

    def edit_message(self, message_id: int, text: str, buttons: Optional[List[List[Tuple[str, str]]]] = None):
        if not self.api_url or not self.chat_id:
            return
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
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
            requests.post(f"{self.api_url}/editMessageText", json=payload, timeout=5)
        except Exception as e:
            print(f"[Telegram] ⚠️ Edit failed: {e}")

    def send_proposal_card(self, proposal: Dict[str, Any], proposal_id: Optional[str] = None):
        """Dispatches an interactive Telegram proposal card with Best Fit recommendation."""
        inst = proposal["instrument"]
        low = proposal["range_low"]
        high = proposal["range_high"]
        pid = proposal_id or f"{inst}_{int(low)}_{int(high)}"
        self.pending_proposals[pid] = proposal

        diag = proposal["diagnosis"]
        opt_a = proposal["options"]["OPTION_A"]
        opt_b = proposal["options"]["OPTION_B"]
        opt_c = proposal["options"]["OPTION_C"]
        best_fit = diag["best_fit"]

        msg = (
            f"📐 <b>GRID PROPOSAL — {inst}</b>\n"
            f"───────────────────────────────────\n"
            f"• Container: <code>{low:.2f} — {high:.2f}</code> (Δ <code>{proposal['delta_p']:.2f}</code> pts)\n"
            f"• Step (S<sub>1/16</sub>): <code>{proposal['s_step']:.2f}</code> pts | Harvest (S<sub>1/8</sub>): <code>{proposal['s_harvest']:.2f}</code> pts\n"
            f"• Boundary: <code>L0: {proposal['sl_low']:.2f} | L16: {proposal['sl_high']:.2f}</code>\n"
            f"• Side Budget: <b>${proposal['r_side_budget']:,.2f}</b> | Fee: <b>${proposal['fee_per_rt']:.2f}</b> RT\n\n"
            f"🔍 <b>STRUCTURE DIAGNOSIS</b>\n"
            f"• Detected Breaker: <code>{diag['width_pts']:.2f}</code> pts ({diag['width_L']:.2f} L)\n"
            f"• Regime: <b>{diag['regime']}</b>\n"
            f"• Recommended: <b>{proposal['options'][best_fit]['name']}</b>\n"
            f"───────────────────────────────────\n"
            f"{'⭐' if best_fit == 'OPTION_A' else '🔘'} <b>OPTION A: {opt_a['name']}</b> {'(RECOMMENDED)' if best_fit == 'OPTION_A' else ''}\n"
            f"• Active Rungs: <code>{', '.join(opt_a['rung_labels'])}</code>\n"
            f"• Sizing: <code>{opt_a['ladder_qty']}</code> cts ({opt_a['total_contracts']} total)\n"
            f"• EWR Target: <code>{opt_a['ewr_target_pct']}%</code> (${opt_a['r_target']:.2f}/rung) | Risk: <b>${opt_a['peak_risk']:.2f}</b>\n\n"
            f"{'⭐' if best_fit == 'OPTION_B' else '🔘'} <b>OPTION B: {opt_b['name']}</b> {'(RECOMMENDED)' if best_fit == 'OPTION_B' else ''}\n"
            f"• Active Rungs: <code>{', '.join(opt_b['rung_labels'])}</code>\n"
            f"• Sizing: <code>{opt_b['ladder_qty']}</code> cts ({opt_b['total_contracts']} total)\n"
            f"• EWR Target: <code>{opt_b['ewr_target_pct']}%</code> (${opt_b['r_target']:.2f}/rung) | Risk: <b>${opt_b['peak_risk']:.2f}</b>\n\n"
            f"{'⭐' if best_fit == 'OPTION_C' else '🔘'} <b>OPTION C: {opt_c['name']}</b> {'(RECOMMENDED)' if best_fit == 'OPTION_C' else ''}\n"
            f"• Active Rungs: <code>{', '.join(opt_c['rung_labels'])}</code>\n"
            f"• Sizing: <code>{opt_c['ladder_qty']}</code> cts ({opt_c['total_contracts']} total)\n"
            f"• EWR Target: <code>{opt_c['ewr_target_pct']}%</code> (${opt_c['r_target']:.2f}/rung) | Risk: <b>${opt_c['peak_risk']:.2f}</b>\n"
            f"───────────────────────────────────\n"
            f"Tap an execution profile below to arm orders:"
        )

        buttons = [
            [(f"✅ Arm Best Fit ({proposal['options'][best_fit]['name'].split()[0]})", f"arm:{pid}:{best_fit}")],
            [("🔘 Option B (1/8 Backbone)", f"arm:{pid}:OPTION_B"), ("🔘 Option C (Compact Core)", f"arm:{pid}:OPTION_C")],
            [("❌ Dismiss Proposal", f"dismiss:{pid}")]
        ]

        self.send(msg, buttons=buttons)

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
            cb = update["callback_query"]
            data = cb.get("data", "")
            msg_id = cb.get("message", {}).get("message_id")

            if data.startswith("dismiss:"):
                pid = data.split(":")[1]
                self.pending_proposals.pop(pid, None)
                if msg_id:
                    self.edit_message(msg_id, "🗑️ <b>Grid Proposal Dismissed.</b>")
                return

            if data.startswith("arm:"):
                parts = data.split(":")
                pid = parts[1]
                opt_key = parts[2]
                proposal = self.pending_proposals.get(pid)
                if proposal:
                    opt = proposal["options"][opt_key]
                    if msg_id:
                        self.edit_message(
                            msg_id,
                            f"🚀 <b>GRID ARMED &amp; QUOTING ACTIVE!</b>\n\n"
                            f"• Profile: <b>{opt['name']}</b>\n"
                            f"• Active Rungs: <code>{', '.join(opt['rung_labels'])}</code>\n"
                            f"• Ladder Sizing: <code>{opt['ladder_qty']}</code> cts\n"
                            f"• Side Risk Allocated: <b>${opt['peak_risk']:.2f}</b>"
                        )
                if self.command_handler:
                    self.command_handler(data, proposal)
                return

            if self.command_handler:
                self.command_handler(data, None)
            return

        if "message" in update and "text" in update["message"]:
            text = update["message"]["text"].strip()
            if self.command_handler:
                self.command_handler(text, None)
