import os
from pathlib import Path

# Auto-load repository .env file if environment variables are not exported in current shell
def _load_local_env():
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'").strip('"')
                    if k not in os.environ or not os.environ[k]:
                        os.environ[k] = v

_load_local_env()

import os
import json
import time
import threading
import requests
from typing import Optional, List, Tuple, Callable, Dict, Any

from src.config import TICK_SIZES, MULTIPLIERS, COMMISSION_PER_RT
from src.core.grid_geometry import HarmonicGridGeometry, round_to_tick
from src.core.directional_bias import DirectionalBiasCalculator

class TelegramBridge:
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

        self.wizard: Dict[str, Any] = {
            "state": "IDLE",
            "low": None,
            "high": None,
            "risk": 200.0,
            "tf": "1hr",
            "brk_low_front": None,
            "brk_low_back": None,
            "brk_high_front": None,
            "brk_high_back": None,
            "brk_dir": "UP",
            "brk_touches": 0,
            "brk_age_mins": 15.0,
            "macro_bias": "NEUTRAL",  # BEARISH, NEUTRAL, BULLISH
            "execution_mode": "BALANCED" # BALANCED, SUGGESTED, SHORTS_ONLY, LONGS_ONLY
        }

    def answer_callback(self, callback_id: str, text: Optional[str] = None):
        if not self.api_url:
            return
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            requests.post(f"{self.api_url}/answerCallbackQuery", json=payload, timeout=5)
        except Exception:
            pass

    def send(self, text: str, buttons: Optional[List[List[Tuple[str, str]]]] = None) -> Optional[int]:
        if not self.api_url or not self.chat_id:
            print(f"\n[TELEGRAM TELEMETRY]\n{text}\n")
            if buttons:
                print(f"[BUTTONS]: {buttons}\n")
            return None

        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons
                ]
            }

        try:
            resp = requests.post(f"{self.api_url}/sendMessage", json=payload, timeout=8)
            res_data = resp.json()
            if not res_data.get("ok"):
                print(f"[Telegram] ❌ API Error {resp.status_code}: {res_data.get('description')}")
                return None
            return res_data.get("result", {}).get("message_id")
        except Exception as e:
            print(f"[Telegram] ⚠️ Send request failed: {e}")
            return None

    def edit_message(self, message_id: int, text: str, buttons: Optional[List[List[Tuple[str, str]]]] = None):
        if not self.api_url or not self.chat_id:
            return
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons
                ]
            }
        try:
            requests.post(f"{self.api_url}/editMessageText", json=payload, timeout=8)
        except Exception as e:
            print(f"[Telegram] ⚠️ Edit failed: {e}")

    def prompt_step1_range(self):
        self.wizard["state"] = "AWAITING_RANGE"
        msg = (
            "📐 <b>STEP 1 OF 4: ENTER CONTAINER RANGE</b>\n"
            "───────────────────────────────────\n"
            f"Budget: <b>${self.wizard['risk']:,.2f}</b> | Timeframe: <b>{self.wizard['tf']}</b>\n\n"
            "Reply with container Low and High:\n"
            "👉 <code>27641.50 28526.00</code>"
        )
        buttons = [
            [("⏱️ 1hr", "setwiztf:1hr"), ("⏱️ 15m", "setwiztf:15m"), ("💵 $200", "setwizrisk:200")],
            [("❌ Cancel", "wizcancel")]
        ]
        self.send(msg, buttons=buttons)

    def prompt_step2_breaker_edges(self):
        self.wizard["state"] = "AWAITING_BREAKERS"
        delta_p = self.wizard["high"] - self.wizard["low"]
        msg = (
            "🔍 <b>STEP 2 OF 4: BREAKER EDGES &amp; MACRO CONTEXT</b>\n"
            "───────────────────────────────────\n"
            f"Container: <code>{self.wizard['low']:.2f} — {self.wizard['high']:.2f}</code> (Δ <code>{delta_p:.2f}</code> pts)\n"
            f"Macro Context: <b>{self.wizard['macro_bias']}</b> (Optional suggestion modifier)\n\n"
            "Reply with 4 prices for Demand and Supply edges:\n"
            "👉 <code>[demand_front] [demand_back] [supply_front] [supply_back]</code>\n\n"
            "<i>Example:</i> <code>27956.25 27641.50 28280.25 28526.00</code>"
        )
        buttons = [
            [("🐻 Macro Bearish", "setmacro:BEARISH"), ("⚖️ Macro Neutral", "setmacro:NEUTRAL"), ("🐂 Macro Bullish", "setmacro:BULLISH")],
            [("⚡ Use Geometric Breaker Lines", "use_geom_breakers")],
            [("❌ Cancel", "wizcancel")]
        ]
        self.send(msg, buttons=buttons)

    def prompt_step3_breaker_direction(self):
        self.wizard["state"] = "AWAITING_DIR"
        msg = (
            "🧭 <b>STEP 3 OF 4: RECENT BREAKER SIDE</b>\n"
            "───────────────────────────────────\n"
            "Which breaker formed most recently?"
        )
        buttons = [
            [("🟢 Demand Formed (Bullish Imbalance)", "setdir:UP")],
            [("🔴 Supply Formed (Bearish Imbalance)", "setdir:DOWN")],
            [("❌ Cancel", "wizcancel")]
        ]
        self.send(msg, buttons=buttons)

    def prompt_step4_touches_and_age(self):
        self.wizard["state"] = "AWAITING_TOUCHES"
        msg = (
            "🎯 <b>STEP 4 OF 4: BREAKER MITIGATION</b>\n"
            "───────────────────────────────────\n"
            "How many mitigation touches has the recent breaker front taken?"
        )
        buttons = [
            [("0 (Fresh / Untouched)", "settouches:0"), ("1 Touch", "settouches:1")],
            [("2 Touches", "settouches:2"), ("3+ Touches (Weakened)", "settouches:3")]
        ]
        self.send(msg, buttons=buttons)

    def prompt_user_entered_age(self):
        self.wizard["state"] = "AWAITING_AGE"
        msg = (
            "⏳ <b>BREAKER FORMATION AGE</b>\n"
            "───────────────────────────────────\n"
            "How many minutes ago did this breaker form?\n\n"
            "Reply with the number of minutes:\n"
            "👉 <code>15</code>  (or <code>30</code>, <code>45</code>, etc.)"
        )
        self.send(msg)

    def send_dual_review_card(self):
        low = self.wizard["low"]
        high = self.wizard["high"]
        risk = self.wizard["risk"]
        tf = self.wizard["tf"]
        delta_p = high - low
        p_eq = round(low + 0.5 * delta_p, 2)

        b_l_front = self.wizard["brk_low_front"] or round(low + 0.3125 * delta_p, 2)
        b_l_back = self.wizard["brk_low_back"] or round(low + 0.1875 * delta_p, 2)
        b_h_front = self.wizard["brk_high_front"] or round(low + 0.6875 * delta_p, 2)
        b_h_back = self.wizard["brk_high_back"] or round(low + 0.8125 * delta_p, 2)
        elapsed_seconds = float(self.wizard["brk_age_mins"]) * 60.0

        p_ref = getattr(self, "last_market_price", None) or p_eq

        bias_calc = DirectionalBiasCalculator(default_timeframe=tf)
        bias = bias_calc.compute_bias(
            range_low=low,
            range_high=high,
            breaker_low_front=b_l_front,
            breaker_high_front=b_h_front,
            last_breaker_dir=self.wizard["brk_dir"],
            elapsed_seconds=elapsed_seconds,
            mitigation_touches=self.wizard["brk_touches"],
            timeframe=tf,
            current_price=p_ref
        )
        
        raw_score = bias.get("s_bias", bias.get("score", 0.0))
        macro = self.wizard.get("macro_bias", "NEUTRAL")
        
        # Macro Suggestion Drag (Informational suggestion baseline)
        if macro == "BEARISH":
            macro_drag = -25.0
            macro_note = "🐻 Macro Trend: Bearish (Applies -25.0 suggestion drag)"
        elif macro == "BULLISH":
            macro_drag = +25.0
            macro_note = "🐂 Macro Trend: Bullish (Applies +25.0 suggestion boost)"
        else:
            macro_drag = 0.0
            macro_note = "⚖️ Macro Trend: Neutral"

        s_composite = max(-100.0, min(100.0, round(raw_score + macro_drag, 1)))

        geom_nnq = HarmonicGridGeometry(
            range_low=low, range_high=high, tick_size=0.50,
            multiplier=0.20, fee_per_rt=0.98, r_side_budget=risk, instrument="NNQ"
        )
        pkg_nnq = geom_nnq.generate_proposal_package(
            brk_low_front=b_l_front, brk_low_back=b_l_back,
            brk_high_front=b_h_front, brk_high_back=b_h_back
        )

        geom_mnq = HarmonicGridGeometry(
            range_low=low, range_high=high, tick_size=0.25,
            multiplier=2.0, fee_per_rt=1.94, r_side_budget=risk, instrument="MNQ"
        )
        pkg_mnq = geom_mnq.generate_proposal_package(
            brk_low_front=b_l_front, brk_low_back=b_l_back,
            brk_high_front=b_h_front, brk_high_back=b_h_back
        )

        pid = f"RANGE_{int(low)}_{int(high)}"
        self.pending_proposals[f"NNQ_{pid}"] = pkg_nnq
        self.pending_proposals[f"MNQ_{pid}"] = pkg_mnq

        opts_nnq = pkg_nnq["options"]
        opts_mnq = pkg_mnq["options"]
        diag = pkg_nnq["diagnosis"]
        d_brk = diag["demand_breaker"]
        s_brk = diag["supply_breaker"]

        touch_penalty_val = max(0.20, 1.0 - (0.25 * self.wizard["brk_touches"]))
        p_disp_label = "At Eq (Neutral)" if abs(bias.get("s_price_disp", 0.0)) < 1.0 else (
            f"+{bias.get('s_price_disp'):.1f}% in Premium" if bias.get("s_price_disp", 0.0) > 0 else f"{bias.get('s_price_disp'):.1f}% in Discount"
        )

        # -------------------------------------------------------------
        # Message 1: Decision Telemetry & Suggested Directives
        # -------------------------------------------------------------
        msg_telemetry = (
            f"📐 <b>CONTAINER &amp; DECISION TELEMETRY</b>\n"
            f"───────────────────────────────────\n"
            f"• Container: <code>{low:.2f} — {high:.2f}</code> (Δ <code>{delta_p:.2f}</code> pts)\n"
            f"• Stops: <code>L0: {geom_nnq.sl_low:.2f} | L16: {geom_nnq.sl_high:.2f}</code>\n"
            f"• Step (1/16): <code>{geom_nnq.s_step:.2f}</code> pts | Harvest (1/8): <code>{geom_nnq.s_harvest:.2f}</code> pts\n"
            f"• Side Risk Budget: <b>${risk:,.2f}</b> | TF: <b>{tf}</b>\n\n"
            f"🔍 <b>DYNAMIC BREAKER SNAP DIAGNOSIS</b>\n"
            f"• Demand Breaker: <code>{d_brk['width_pts']:.2f}</code> pts ({d_brk['width_L']:.2f} L) — <b>{d_brk['regime']}</b>\n"
            f"  └ Front: <code>{d_brk['front']:.2f}</code> | Back: <code>{d_brk['back']:.2f}</code>\n"
            f"• Supply Breaker: <code>{s_brk['width_pts']:.2f}</code> pts ({s_brk['width_L']:.2f} L) — <b>{s_brk['regime']}</b>\n"
            f"  └ Front: <code>{s_brk['front']:.2f}</code> | Back: <code>{s_brk['back']:.2f}</code>\n"
            f"• Structural Fit: <b>{pkg_nnq['options'][diag['best_fit']]['name']}</b>\n\n"
            f"🧠 <b>DECISION ENGINE STATE (SUGGESTION)</b>\n"
            f"• Composite Score: <b>{s_composite:+.1f} / 100</b> (Raw: <code>{raw_score:+.1f}</code>)\n"
            f"• Macro Context: <code>{macro_note}</code>\n"
            f"• Suggested Directive: <code>{bias.get('directive', 'TWO_SIDED_BALANCED')}</code>\n\n"
            f"📊 <b>SCORE DERIVATIVES &amp; WEIGHTS</b>\n"
            f"• <b>Spatial (60%): {bias.get('s_spatial', 0):+.1f}</b> | CCR: <code>{bias.get('ccr', 0):+.3f}</code> | Corridor: <code>{bias.get('open_corridor', 0):.2f} pts</code>\n"
            f"  └ RW Disc: <code>{bias.get('runway_disc', 0):.2f} pts</code> | RW Prem: <code>{bias.get('runway_prem', 0):.2f} pts</code> (Eq: <code>{p_eq:.2f}</code>)\n"
            f"• <b>Chrono (20%): {bias.get('s_chrono', 0):+.1f}</b> | Recent: <code>{self.wizard['brk_dir']}</code> | Touches: <code>{self.wizard['brk_touches']}</code>\n"
            f"  └ Formed: <code>{int(self.wizard['brk_age_mins'])}m ago</code> | Decay (λ): <code>{bias.get('lambda_decay', 0):.4f}</code> | Pen: <code>{touch_penalty_val:.2f}x</code>\n"
            f"• <b>Price Action (20%): {bias.get('s_price_disp', 0):+.1f}</b> | P_now: <code>{bias.get('p_now', p_eq):.2f}</code> ({p_disp_label})"
        )
        self.send(msg_telemetry)

        # -------------------------------------------------------------
        # Message 2: Profiles & Discretionary Override Controls
        # -------------------------------------------------------------
        def fmt_snapped_option_block(opt_data: Dict[str, Any], budget: float) -> str:
            risk_val = opt_data["total_catastrophic_risk"]
            fit_icon = "✅" if risk_val <= budget else "⚠️"
            fit_text = f"Under by ${budget - risk_val:.2f}" if risk_val <= budget else f"Over by ${risk_val - budget:.2f}"

            long_rules = "\n".join([f"    • {r}" for r in opt_data["level_rules_long"]])
            short_rules = "\n".join([f"    • {r}" for r in opt_data["level_rules_short"]])

            y_rot = opt_data.get('harvest_yield_per_rot', 0.0)
            cov = opt_data.get('breaker_coverage', 'N/A')
            return (
                f"  🟢 <b>Long Quotes ({len(opt_data['discount_rungs'])} rungs):</b>\n{long_rules}\n"
                f"  🔴 <b>Short Quotes ({len(opt_data['premium_rungs'])} rungs):</b>\n{short_rules}\n"
                f"  • Total Side Risk: <b>${risk_val:.2f}</b> ({fit_icon} {fit_text})\n"
                f"  • Harvest Yield: <b>+${y_rot:.2f} / rot</b> | Breaker Coverage: <b>{cov}</b>\n"
            )

        # Build dynamic profile review message for all options
        rec_key = diag.get("best_fit", "OPTION_D")
        if rec_key not in opts_nnq:
            rec_key = "OPTION_D" if "OPTION_D" in opts_nnq else "OPTION_B"

        profile_sections = []
        for opt_k, opt_v in opts_nnq.items():
            is_rec = (opt_k == rec_key)
            star = "⭐" if is_rec else "🔘"
            rec_badge = " <b>(RECOMMENDED)</b>" if is_rec else ""
            profile_sections.append(
                f"{star} <b>{opt_v['name']}</b>{rec_badge}\n"
                f"{fmt_snapped_option_block(opt_v, risk)}"
            )

        msg_profiles = (
            f"⚖️ <b>EXECUTION PROFILES — NNQ (E-nano $0.20/pt)</b>\n"
            f"───────────────────────────────────\n"
            + "\n".join(profile_sections) +
            f"───────────────────────────────────\n"
            f"<b>ARM OR OVERRIDE DIRECTIVE:</b>"
        )

        buttons = []
        # Dynamic Arm Buttons for every option in NNQ package
        for opt_k, opt_v in opts_nnq.items():
            r_cost = opt_v["total_catastrophic_risk"]
            star_prefix = "⭐ " if opt_k == rec_key else "🚀 "
            buttons.append([(f"{star_prefix}Arm {opt_v['name']} (${r_cost:.0f})", f"arm:NNQ_{pid}:{opt_k}")])

        buttons.append([
            ("🐻 Override: Shorts Only", f"override_mode:SHORTS_ONLY:{pid}"),
            ("⚖️ Force Balanced", f"override_mode:BALANCED:{pid}")
        ])
        buttons.append([("🔄 Start Over", "prompt:range"), ("💵 Change Risk", "prompt:risk")])
        buttons.append([("❌ Dismiss", f"dismiss:{pid}")])

        self.send(msg_profiles, buttons=buttons)
    def start_polling(self):
        if not self.api_url or not self.chat_id:
            return
        # Drain stale unread messages from prior runs
        try:
            flush_res = requests.get(f"{self.api_url}/getUpdates?offset=-1", timeout=5).json()
            if flush_res.get("ok") and flush_res.get("result"):
                self.last_update_id = flush_res["result"][-1]["update_id"]
                # Acknowledge up through last_update_id
                requests.get(f"{self.api_url}/getUpdates?offset={self.last_update_id + 1}&limit=1", timeout=5)
                print(f"[TelegramBridge] 🧹 Drained stale messages up to update_id {self.last_update_id}")
        except Exception as e:
            print(f"[TelegramBridge] ⚠️ Backlog flush notice: {e}")

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
                time.sleep(1)

    def _process_update(self, update: Dict[str, Any]):
        if "callback_query" in update:
            cb = update["callback_query"]
            data = cb.get("data", "")
            cb_id = cb.get("id", "")
            msg_id = cb.get("message", {}).get("message_id")
            self.answer_callback(cb_id)

            if data == "wizcancel":
                self.wizard["state"] = "IDLE"
                if msg_id:
                    self.edit_message(msg_id, "❌ <b>Setup Cancelled.</b>")
                return

            if data.startswith("setmacro:"):
                macro = data.split(":")[1]
                self.wizard["macro_bias"] = macro
                self.prompt_step2_breaker_edges()
                return

            if data.startswith("override_mode:"):
                parts = data.split(":")
                mode, pid = parts[1], parts[2]
                self.wizard["execution_mode"] = mode
                if mode == "SHORTS_ONLY":
                    self.send(f"🐻 <b>OVERRIDE APPLIED: SHORTS ONLY</b>\n• Long bids will be disarmed.\n• Only selling rallies into supply.")
                elif mode == "BALANCED":
                    self.send(f"⚖️ <b>OVERRIDE APPLIED: BALANCED TWO-SIDED</b>\n• Both Long and Short rungs active at full standard allocation.")
                return

            if data.startswith("setwiztf:"):
                tf = data.split(":")[1]
                self.wizard["tf"] = tf
                self.send(f"⏱️ Timeframe set to <b>{tf}</b>.\nNow reply with range prices:\n👉 <code>27641.50 28526.00</code>")
                return

            if data.startswith("setwizrisk:"):
                new_r = float(data.split(":")[1])
                self.wizard["risk"] = new_r
                self.send(f"💵 Side risk budget set to <b>${new_r:,.2f}</b>.\nNow reply with range prices:\n👉 <code>27641.50 28526.00</code>")
                return

            if data == "use_geom_breakers":
                delta_p = self.wizard["high"] - self.wizard["low"]
                self.wizard["brk_low_front"] = round(self.wizard["low"] + 0.3125 * delta_p, 2)
                self.wizard["brk_low_back"] = round(self.wizard["low"] + 0.1875 * delta_p, 2)
                self.wizard["brk_high_front"] = round(self.wizard["low"] + 0.6875 * delta_p, 2)
                self.wizard["brk_high_back"] = round(self.wizard["low"] + 0.8125 * delta_p, 2)
                self.prompt_step3_breaker_direction()
                return

            if data.startswith("setdir:"):
                d = data.split(":")[1]
                self.wizard["brk_dir"] = d
                self.prompt_step4_touches_and_age()
                return

            if data.startswith("settouches:"):
                t = int(data.split(":")[1])
                self.wizard["brk_touches"] = t
                self.prompt_user_entered_age()
                return

            if data == "prompt:range":
                self.prompt_step1_range()
                return

            if data == "prompt:risk":
                msg = "💵 <b>Select Side Catastrophic Risk Budget:</b>"
                btns = [
                    [("💵 $150", "setwizrisk:150"), ("💵 $200 (Default)", "setwizrisk:200"), ("💵 $300", "setwizrisk:300")]
                ]
                self.send(msg, buttons=btns)
                return

            if data.startswith("dismiss:"):
                pid = data.split(":")[1]
                self.pending_proposals.pop(f"NNQ_{pid}", None)
                self.pending_proposals.pop(f"MNQ_{pid}", None)
                if msg_id:
                    self.edit_message(msg_id, "🗑️ <b>Grid Proposal Dismissed.</b>")
                return

            if data.startswith("arm:"):
                parts = data.split(":")
                full_pid = parts[1]
                opt_key = parts[2]
                proposal = self.pending_proposals.get(full_pid)
                if proposal:
                    opt = proposal["options"][opt_key]
                    inst = proposal["instrument"]
                    mode = self.wizard.get("execution_mode", "BALANCED")
                    
                    if mode == "SHORTS_ONLY":
                        status_line = "🔴 <b>SHORT-ONLY OVERRIDE ACTIVE (Longs Disarmed)</b>"
                    else:
                        status_line = "🟢 <b>TWO-SIDED BALANCED ACTIVE</b>"

                    if msg_id:
                        self.edit_message(
                            msg_id,
                            f"🚀 <b>{inst} GRID ARMED!</b>\n\n"
                            f"• Contract: <b>{inst}</b>\n"
                            f"• Profile: <b>{opt['name']}</b>\n"
                            f"• Execution Directive: {status_line}\n"
                            f"• Total Side Catastrophic Risk: <b>${opt['total_catastrophic_risk']:.2f}</b>\n\n"
                            f"<i>In-flight control active: Use /disarmlongs, /disarmshorts, or /twosided anytime.</i>"
                        )
                if self.command_handler:
                    self.command_handler(data, proposal)
                return

            if self.command_handler:
                self.command_handler(data, None)
            return

        if "message" in update and "text" in update["message"]:
            text = update["message"]["text"].strip()
            print(f"📩 [Telegram Text]: {text}")

            if self.wizard["state"] == "AWAITING_AGE":
                try:
                    mins = float(text)
                    self.wizard["brk_age_mins"] = max(1.0, mins)
                    self.wizard["state"] = "IDLE"
                    self.send(
                        f"✅ <b>Breaker formed {int(mins)} minutes ago recorded.</b>\n"
                        f"⚡ <i>Generating Review with structural recommendations &amp; override options...</i>"
                    )
                    self.send_dual_review_card()
                    return
                except ValueError:
                    self.send("⚠️ Please reply with a number for breaker age in minutes (e.g. <code>15</code> or <code>30</code>).")
                    return

            if self.wizard["state"] == "AWAITING_BREAKERS":
                parts = text.replace(",", " ").split()
                if len(parts) >= 4:
                    try:
                        self.wizard["brk_low_front"] = float(parts[0])
                        self.wizard["brk_low_back"] = float(parts[1])
                        self.wizard["brk_high_front"] = float(parts[2])
                        self.wizard["brk_high_back"] = float(parts[3])
                        self.send("✅ Breaker coordinates recorded!")
                        self.prompt_step3_breaker_direction()
                        return
                    except ValueError:
                        self.send("⚠️ Error parsing breaker prices. Reply with 4 prices separated by spaces.")
                        return

            if text.startswith("/newrange") or text.startswith("/propose"):
                parts = text.split()
                if len(parts) >= 3:
                    try:
                        p1, p2 = float(parts[1]), float(parts[2])
                        self.wizard["low"], self.wizard["high"] = min(p1, p2), max(p1, p2)
                        if len(parts) > 3:
                            self.wizard["risk"] = float(parts[3])
                        if len(parts) > 4:
                            self.wizard["tf"] = parts[4]
                        self.prompt_step2_breaker_edges()
                        return
                    except Exception as e:
                        self.send(f"⚠️ Error parsing values: {e}")
                else:
                    self.prompt_step1_range()
                    return

            parts = text.replace(",", " ").split()
            if len(parts) == 2:
                try:
                    p1, p2 = float(parts[0]), float(parts[1])
                    self.wizard["low"], self.wizard["high"] = min(p1, p2), max(p1, p2)
                    self.prompt_step2_breaker_edges()
                    return
                except ValueError:
                    pass

            if self.command_handler:
                self.command_handler(text, None)
