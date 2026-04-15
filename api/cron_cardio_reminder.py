"""Vercel Cron endpoint — sends a cardio reminder to the user.

Schedule (vercel.json): `30 4 * * 1-6` = 07:30 Kyiv local (UTC+3 in summer)
every Monday through Saturday.

The user trains weights Tue/Thu/Sat and wants 2 cardio sessions/week fit in
on the other days. The daily reminder nudges — they choose which 2 days
actually hit. Sundays are rest (no reminder).
"""
import json
import os
import random
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


# Weekday → training context (0=Mon … 6=Sun)
_WEEKDAY_CONTEXT = {
    0: ("Понеділок", "lift_tomorrow"),   # Mon — lift tomorrow, good cardio day
    1: ("Вівторок", "lift_today"),       # Tue — lift day (reminder fires but message adapts)
    2: ("Середа", "rest_middle"),        # Wed — mid-week cardio slot
    3: ("Четвер", "lift_today"),         # Thu — lift day
    4: ("П'ятниця", "rest_friday"),      # Fri — cardio-eligible
    5: ("Субота", "lift_today"),         # Sat — lift day
    6: ("Неділя", "rest_sunday"),        # Sun — not triggered by cron (1-6), left for completeness
}


_REMINDERS_CARDIO_DAY = [
    "🏃 Ранок, {day}. Добрий день для кардіо — 30–45 хв, ЧСС 120–140. Закриваєш одну з 2 сесій цього тижня?",
    "🚴 {day}, кардіо-день. Легка сесія 30 хв зараз — і будні закриті. Плюс бонус для дефіциту.",
    "💓 {day}. Нагадую про кардіо: можна будь-що — біг, велик, швидка ходьба 45 хв. 2 сесії/тиждень = ціль.",
    "🏃 {day} — ідеальний день для cardio. Ресурс: зараз свіжий, до силового завтра ще встигне відновитися.",
]

_REMINDERS_LIFT_DAY = [
    "💪 {day} — силова сьогодні. Якщо встигнеш коротку кардіо-сесію (15–20 хв) після, ще один бонус для дефіциту.",
    "🏋️ {day} — гиря-день. Зосередься на силовій. Кардіо можна пропустити — відновлення важливіше.",
]

_REMINDER_SUNDAY = "😌 Неділя — відпочинок. Нагадаю про кардіо завтра."


def _authorized(headers) -> bool:
    """Verify Vercel Cron bearer token. Fails closed if CRON_SECRET is not set."""
    if not CRON_SECRET:
        return False
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {CRON_SECRET}"


def _pick_reminder() -> str:
    """Pick a reminder text appropriate for today's weekday (Kyiv local)."""
    now_utc = datetime.now(timezone.utc)
    # Kyiv is UTC+2 (winter) or UTC+3 (summer). April is EEST (UTC+3).
    # Cron fires at 04:30 UTC = 07:30 Kyiv, so the local weekday matches
    # the UTC weekday for this cron time.
    weekday = now_utc.weekday()  # 0=Mon … 6=Sun
    day_name, context = _WEEKDAY_CONTEXT.get(weekday, ("Сьогодні", "rest_middle"))

    if context in ("rest_middle", "rest_friday", "lift_tomorrow"):
        pool = _REMINDERS_CARDIO_DAY
    elif context == "lift_today":
        pool = _REMINDERS_LIFT_DAY
    else:
        return _REMINDER_SUNDAY

    return random.choice(pool).format(day=day_name)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _authorized(self.headers):
            self.send_response(401)
            self.end_headers()
            return

        result = {"ok": True, "sent": 0}
        try:
            text = _pick_reminder()
            sent = 0
            for user_id in ALLOWED_USER_IDS:
                try:
                    send_message(user_id, text)
                    sent += 1
                except Exception as e:
                    print(f"cardio reminder send error for {user_id}:", e, flush=True)
            result = {
                "ok": True,
                "sent": sent,
                "ran_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception:
            print("cron_cardio_reminder error:", traceback.format_exc(), flush=True)
            result = {"ok": False, "error": "internal"}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())
