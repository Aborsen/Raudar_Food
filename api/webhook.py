"""Vercel serverless handler for Telegram webhook updates."""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.config import WEBHOOK_SECRET, RECALC_PROMPT, ALLOWED_USER_IDS
from lib.database import (
    get_conn,
    init_db,
    upsert_user,
    save_pending_photo,
    save_pending_text,
    pop_pending_entry,
    cleanup_stale_pending,
    cleanup_stale_analyses,
    save_pending_analysis,
    get_pending_analysis,
    pop_pending_analysis,
    set_awaiting_manual,
    save_meal,
    upsert_daily_log_from_meal,
    get_today_log,
    get_history,
    get_meals_for_day,
    delete_meal,
    recalc_daily_log,
)
from lib.telegram_helpers import (
    send_message,
    answer_callback_query,
    get_file_bytes,
    meal_type_keyboard,
    moderation_keyboard,
    meals_list_keyboard,
)
from lib.openai_vision import analyze_photo, analyze_text
from lib.openai_nutrition import suggest_meal
from lib.formatters import (
    welcome_message,
    help_message,
    format_today_progress,
    format_history,
    format_day_detail,
    format_meal_logged,
    format_meal_preview,
    format_meals_list,
    PHOTO_PROMPT_MEAL_TYPE,
    TEXT_PROMPT_MEAL_TYPE,
    ANALYZING_WAIT,
    RECALC_WAIT,
    PHOTO_DOWNLOAD_FAILED,
    PHOTO_ANALYSIS_FAILED,
    TEXT_ANALYSIS_FAILED,
    PENDING_EXPIRED,
    MANUAL_INPUT_PROMPT,
    MEAL_DELETED,
    MEAL_EDIT_PROMPT,
    MEAL_NOT_FOUND,
    NO_MEALS_TO_MANAGE,
    UNKNOWN_COMMAND,
    SUGGEST_THINKING,
    SUGGEST_FAILED,
    HISTORY_USAGE,
)


NOT_AUTHORIZED = "🔒 Вибач, цей бот працює тільки для авторизованих користувачів. З питань співпраці писати @ogswed"


def _is_allowed(user_id: int | None) -> bool:
    if not ALLOWED_USER_IDS:
        return True  # empty set = allow everyone
    return user_id in ALLOWED_USER_IDS


def _reject_user(conn, cb: dict) -> None:
    """Send rejection for unauthorized callback_query."""
    message = cb.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    if chat_id:
        send_message(chat_id, NOT_AUTHORIZED)
    answer_callback_query(cb["id"], "🔒 Не авторизовано")


# Max webhook payload size (Telegram updates are small; photos are file_id refs)
MAX_WEBHOOK_BYTES = 512 * 1024  # 512 KB


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Fail closed: if WEBHOOK_SECRET is not configured OR the header does
        # not match exactly, reject the request. Prevents an unconfigured
        # deployment from being wide open.
        secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
            self.send_response(403)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > MAX_WEBHOOK_BYTES:
            self.send_response(413)
            self.end_headers()
            return

        try:
            raw = self.rfile.read(length) if length else b"{}"
            update = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._respond_ok()
            return

        try:
            process_update(update)
        except Exception:
            print("webhook error:", traceback.format_exc(), flush=True)

        self._respond_ok()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "service": "webhook"}).encode())

    def _respond_ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())


def process_update(update: dict) -> None:
    conn = get_conn()
    try:
        init_db(conn)
        cleanup_stale_pending(conn, minutes=10)
        cleanup_stale_analyses(conn, minutes=10)

        # Extract user_id from either callback_query or message
        if "callback_query" in update:
            cb_user_id = update["callback_query"].get("from", {}).get("id")
            if not _is_allowed(cb_user_id):
                _reject_user(conn, update["callback_query"])
                return
            handle_callback(conn, update["callback_query"])
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        user = message.get("from", {})
        user_id = user.get("id")
        username = user.get("username") or user.get("first_name")
        first_name = user.get("first_name")

        if not _is_allowed(user_id):
            chat_id = message.get("chat", {}).get("id")
            if chat_id:
                send_message(chat_id, NOT_AUTHORIZED)
            return

        if user_id:
            upsert_user(conn, user_id, username)

        if message.get("photo"):
            handle_photo(conn, message)
            return

        text = (message.get("text") or "").strip()
        if not text:
            return

        if text.startswith("/"):
            handle_command(conn, message, text, first_name)
            return

        # Check if user is awaiting manual input for moderation
        if user_id:
            pending = get_pending_analysis(conn, user_id)
            if pending and pending["awaiting_manual"]:
                handle_manual_text_input(conn, message, text, pending)
                return

        # Otherwise: new meal via free text
        handle_text_entry(conn, message, text)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------- Photo / text entry ----------

def handle_photo(conn, message: dict) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    photos = message["photo"]
    file_id = photos[-1]["file_id"]
    save_pending_photo(conn, user_id, file_id)
    send_message(chat_id, PHOTO_PROMPT_MEAL_TYPE, reply_markup=meal_type_keyboard())


def handle_text_entry(conn, message: dict, text: str) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    save_pending_text(conn, user_id, text)
    send_message(chat_id, TEXT_PROMPT_MEAL_TYPE, reply_markup=meal_type_keyboard())


# ---------- Callback router ----------

def handle_callback(conn, cb: dict) -> None:
    data = cb.get("data", "")
    if data.startswith("meal_type:"):
        handle_meal_type_callback(conn, cb)
    elif data.startswith("mod:"):
        handle_moderation_callback(conn, cb)
    elif data.startswith("meal_del:") or data.startswith("meal_edit:"):
        handle_meal_manage_callback(conn, cb)
    else:
        answer_callback_query(cb["id"], "Невідома дія")


# ---------- Meal type selection → analyze → show preview ----------

def handle_meal_type_callback(conn, cb: dict) -> None:
    cb_id = cb["id"]
    data = cb["data"]
    user_id = cb["from"]["id"]
    first_name = cb["from"].get("first_name")
    message = cb.get("message", {})
    chat_id = message.get("chat", {}).get("id", user_id)

    meal_type = data.split(":", 1)[1]
    meal_ua_map = {"breakfast": "сніданок", "lunch": "обід", "dinner": "вечерю", "snack": "перекус"}
    answer_callback_query(cb_id, f"Аналізую твій {meal_ua_map.get(meal_type, meal_type)}…")

    entry = pop_pending_entry(conn, user_id)
    if entry is None:
        send_message(chat_id, PENDING_EXPIRED)
        return
    file_id, text_description = entry

    send_message(chat_id, ANALYZING_WAIT)

    analysis, raw = None, ""
    try:
        if file_id:
            try:
                image_bytes = get_file_bytes(file_id)
            except Exception as e:
                print("getFile error:", e, flush=True)
                send_message(chat_id, PHOTO_DOWNLOAD_FAILED)
                return
            analysis, raw = analyze_photo(image_bytes)
        elif text_description:
            analysis, raw = analyze_text(text_description)
        else:
            send_message(chat_id, PENDING_EXPIRED)
            return
    except Exception as e:
        print("analysis error:", e, flush=True)
        send_message(chat_id, TEXT_ANALYSIS_FAILED if text_description else PHOTO_ANALYSIS_FAILED)
        return

    # Save analysis for moderation (NOT to meals yet)
    save_pending_analysis(conn, user_id, meal_type, analysis, file_id, text_description, raw)

    # Show preview with ingredients + moderation buttons
    send_message(chat_id, format_meal_preview(meal_type, analysis), reply_markup=moderation_keyboard())


# ---------- Moderation: Accept / Recalculate / Manual ----------

def handle_moderation_callback(conn, cb: dict) -> None:
    cb_id = cb["id"]
    action = cb["data"].split(":", 1)[1]  # "accept", "recalc", "manual"
    user_id = cb["from"]["id"]
    first_name = cb["from"].get("first_name")
    message = cb.get("message", {})
    chat_id = message.get("chat", {}).get("id", user_id)

    if action == "accept":
        answer_callback_query(cb_id, "✅ Записую!")
        pending = pop_pending_analysis(conn, user_id)
        if not pending:
            send_message(chat_id, PENDING_EXPIRED)
            return
        analysis = pending["analysis"]
        save_meal(conn, user_id, pending["meal_type"], analysis, pending["photo_file_id"] or "", pending["raw_response"])
        upsert_daily_log_from_meal(conn, user_id, analysis)
        today_log = get_today_log(conn, user_id)
        send_message(chat_id, format_meal_logged(pending["meal_type"], analysis, today_log, first_name))

    elif action == "recalc":
        answer_callback_query(cb_id, "🔄 Перераховую…")
        pending = get_pending_analysis(conn, user_id)
        if not pending:
            send_message(chat_id, PENDING_EXPIRED)
            return
        send_message(chat_id, RECALC_WAIT)

        try:
            if pending["photo_file_id"]:
                image_bytes = get_file_bytes(pending["photo_file_id"])
                analysis, raw = analyze_photo(image_bytes, retry_prompt=RECALC_PROMPT)
            elif pending["text_description"]:
                analysis, raw = analyze_text(pending["text_description"], retry_prompt=RECALC_PROMPT)
            else:
                send_message(chat_id, PENDING_EXPIRED)
                return
        except Exception as e:
            print("recalc error:", e, flush=True)
            send_message(chat_id, PHOTO_ANALYSIS_FAILED)
            return

        save_pending_analysis(conn, user_id, pending["meal_type"], analysis, pending["photo_file_id"], pending["text_description"], raw)
        send_message(chat_id, format_meal_preview(pending["meal_type"], analysis), reply_markup=moderation_keyboard())

    elif action == "manual":
        answer_callback_query(cb_id, "✏️ Чекаю на текст")
        pending = get_pending_analysis(conn, user_id)
        if not pending:
            # Create a minimal pending_analyses row so the state machine works
            send_message(chat_id, PENDING_EXPIRED)
            return
        set_awaiting_manual(conn, user_id)
        send_message(chat_id, MANUAL_INPUT_PROMPT)


def handle_manual_text_input(conn, message: dict, text: str, pending: dict) -> None:
    """User typed free text while in awaiting_manual state."""
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    send_message(chat_id, ANALYZING_WAIT)

    try:
        analysis, raw = analyze_text(text)
    except Exception as e:
        print("manual text analysis error:", e, flush=True)
        send_message(chat_id, TEXT_ANALYSIS_FAILED)
        return

    # Update pending analysis with new result; clear awaiting_manual
    save_pending_analysis(conn, user_id, pending["meal_type"], analysis, pending["photo_file_id"], text, raw)
    send_message(chat_id, format_meal_preview(pending["meal_type"], analysis), reply_markup=moderation_keyboard())


# ---------- Meal management: Delete / Edit ----------

def handle_meal_manage_callback(conn, cb: dict) -> None:
    cb_id = cb["id"]
    data = cb["data"]
    user_id = cb["from"]["id"]
    message = cb.get("message", {})
    chat_id = message.get("chat", {}).get("id", user_id)

    if data.startswith("meal_del:"):
        meal_id = int(data.split(":", 1)[1])
        answer_callback_query(cb_id, "🗑 Видаляю…")
        deleted = delete_meal(conn, meal_id, user_id)
        if not deleted:
            send_message(chat_id, MEAL_NOT_FOUND)
            return
        recalc_daily_log(conn, user_id, deleted["date"])
        send_message(
            chat_id,
            MEAL_DELETED.format(dish=deleted["description"][:40], cal=round(deleted["calories"])),
        )

    elif data.startswith("meal_edit:"):
        meal_id = int(data.split(":", 1)[1])
        answer_callback_query(cb_id, "✏️ Готуюсь до заміни…")
        deleted = delete_meal(conn, meal_id, user_id)
        if not deleted:
            send_message(chat_id, MEAL_NOT_FOUND)
            return
        recalc_daily_log(conn, user_id, deleted["date"])
        # Create a pending_analyses row in awaiting_manual mode so next text triggers re-analysis
        save_pending_analysis(conn, user_id, deleted["meal_type"], {}, None, None, "")
        set_awaiting_manual(conn, user_id, meal_type=deleted["meal_type"])
        send_message(
            chat_id,
            MEAL_EDIT_PROMPT.format(dish=deleted["description"][:40]),
        )


# ---------- Commands ----------

def handle_command(conn, message: dict, text: str, first_name: str | None) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    parts = text.split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd == "/start":
        send_message(chat_id, welcome_message(first_name))
        return

    if cmd == "/help":
        send_message(chat_id, help_message())
        return

    if cmd == "/today":
        log = get_today_log(conn, user_id)
        send_message(chat_id, format_today_progress(log, first_name))
        return

    if cmd == "/meals":
        log = get_today_log(conn, user_id)
        meals = get_meals_for_day(conn, user_id, log["date"])
        if not meals:
            send_message(chat_id, NO_MEALS_TO_MANAGE)
            return
        send_message(chat_id, format_meals_list(meals), reply_markup=meals_list_keyboard(meals))
        return

    if cmd == "/history":
        rows = get_history(conn, user_id, days=7)
        send_message(chat_id, format_history(rows))
        return

    if cmd == "/history_detail":
        if not args:
            send_message(chat_id, HISTORY_USAGE)
            return
        date = args[0]
        meals = get_meals_for_day(conn, user_id, date)
        send_message(chat_id, format_day_detail(date, meals))
        return

    if cmd == "/suggest_meal":
        log = get_today_log(conn, user_id)
        meals = get_meals_for_day(conn, user_id, log["date"])
        send_message(chat_id, SUGGEST_THINKING)
        try:
            recipe = suggest_meal(log, meals)
        except Exception as e:
            print("suggest error:", e, flush=True)
            send_message(chat_id, SUGGEST_FAILED)
            return
        send_message(chat_id, recipe)
        return

    send_message(chat_id, UNKNOWN_COMMAND)
