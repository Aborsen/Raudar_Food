"""Vercel Cron endpoint — sends a weekly weigh-in prompt to every allowed user.

Scheduled Sunday 15:00 UTC (≈ 18:00 Kyiv). Each recipient gets a short message
with two inline buttons: "✏️ Ввести вагу" (starts the awaiting_weight FSM) and
"⏭ Пропустити". Logging a weight that matches the last log is a no-op for
targets — see handle_weight_input in api/webhook.py.
"""
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.config import ALLOWED_USER_IDS, CRON_SECRET
from lib.telegram_helpers import send_message


WEIGH_IN_TEXT = (
    "⚖️ <b>Тижнева перевірка ваги</b>\n"
    "Скільки ти зараз? Введи вагу в кг або пропусти — цілі не зміняться."
)


def _weigh_in_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✏️ Ввести вагу", "callback_data": "weigh_in:log"},
                {"text": "⏭ Пропустити", "callback_data": "weigh_in:skip"},
            ]
        ]
    }


def _authorized(headers) -> bool:
    if not CRON_SECRET:
        return False
    return headers.get("Authorization", "") == f"Bearer {CRON_SECRET}"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _authorized(self.headers):
            self.send_response(401)
            self.end_headers()
            return

        result = {"ok": True, "sent": 0, "errors": []}
        try:
            result = run_weekly_weigh_in()
        except Exception:
            print("cron_weekly_weigh_in error:", traceback.format_exc(), flush=True)
            result = {"ok": False, "error": "internal"}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


def run_weekly_weigh_in() -> dict:
    sent = 0
    errors = []
    for user_id in ALLOWED_USER_IDS:
        try:
            send_message(user_id, WEIGH_IN_TEXT, reply_markup=_weigh_in_keyboard())
            sent += 1
        except Exception as e:
            errors.append({"user_id": user_id, "error": str(e)})
            print(f"weigh-in error for {user_id}:", traceback.format_exc(), flush=True)
    return {
        "ok": True,
        "sent": sent,
        "errors": errors,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
