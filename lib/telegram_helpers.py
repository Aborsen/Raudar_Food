"""Direct Telegram Bot API calls via httpx (no aiogram — serverless-friendly)."""
import httpx

from lib.config import TELEGRAM_BOT_TOKEN, VERCEL_URL

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"


def _dashboard_url() -> str:
    """Absolute HTTPS URL for the miniapp dashboard. Requires VERCEL_URL env var."""
    host = (VERCEL_URL or "").replace("https://", "").replace("http://", "").rstrip("/")
    return f"https://{host}/api/dashboard"


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = httpx.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def answer_callback_query(callback_query_id: str, text: str | None = None) -> dict:
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        resp = httpx.post(f"{BASE_URL}/answerCallbackQuery", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def edit_message_text(chat_id: int, message_id: int, text: str) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        resp = httpx.post(f"{BASE_URL}/editMessageText", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_file_bytes(file_id: str) -> bytes:
    """Fetch the binary contents of a Telegram-hosted file."""
    meta = httpx.get(f"{BASE_URL}/getFile", params={"file_id": file_id}, timeout=10).json()
    if not meta.get("ok"):
        raise RuntimeError(f"getFile failed: {meta}")
    file_path = meta["result"]["file_path"]
    resp = httpx.get(f"{FILE_URL}/{file_path}", timeout=30)
    resp.raise_for_status()
    return resp.content


def meal_type_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🍳 Сніданок", "callback_data": "meal_type:breakfast"},
                {"text": "🥗 Обід", "callback_data": "meal_type:lunch"},
            ],
            [
                {"text": "🍽️ Вечеря", "callback_data": "meal_type:dinner"},
                {"text": "🍎 Перекус", "callback_data": "meal_type:snack"},
            ],
            [
                {"text": "❌ Скасувати", "callback_data": "meal_type:cancel"},
            ],
        ]
    }


def moderation_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Прийняти", "callback_data": "mod:accept"},
                {"text": "🔄 Перерахувати", "callback_data": "mod:recalc"},
            ],
            [
                {"text": "✏️ Ввести вручну", "callback_data": "mod:manual"},
            ],
        ]
    }


def meals_list_keyboard(meals: list[dict]) -> dict:
    """Build inline keyboard with Delete/Edit buttons for each meal."""
    rows = []
    for i, m in enumerate(meals, 1):
        meal_id = m["id"]
        rows.append([
            {"text": f"🗑 Видалити {i}", "callback_data": f"meal_del:{meal_id}"},
            {"text": f"✏️ Змінити {i}", "callback_data": f"meal_edit:{meal_id}"},
        ])
    return {"inline_keyboard": rows}


def main_menu_keyboard() -> dict:
    """Persistent reply keyboard shown below the input field.

    All buttons are plain-text — tapping sends the label as a message and
    webhook.py routes it. The Dashboard button intentionally does NOT use
    KeyboardButton.web_app because that mode does not provide signed initData
    (Telegram API limitation). Instead, tapping Dashboard makes the bot reply
    with an inline keyboard whose web_app button gives full initData.
    """
    from lib.formatters import (
        BTN_ASK, BTN_TODAY, BTN_YESTERDAY, BTN_MEALS, BTN_HISTORY, BTN_SUGGEST,
    )
    return {
        "keyboard": [
            [{"text": BTN_ASK}, {"text": BTN_YESTERDAY}],
            [{"text": BTN_TODAY}, {"text": BTN_MEALS}],
            [{"text": BTN_HISTORY}, {"text": BTN_SUGGEST}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def dashboard_inline_keyboard() -> dict:
    """Inline keyboard with a single web_app button — this launch mode DOES
    provide signed initData, unlike the KeyboardButton.web_app mode.
    """
    return {
        "inline_keyboard": [[
            {"text": "📱 Відкрити Dashboard", "web_app": {"url": _dashboard_url()}}
        ]]
    }


def set_chat_menu_button(chat_id: int | None = None) -> dict:
    """Register a persistent Mini App button as the bot's chat menu button
    (the icon left of the input area). When chat_id is given, applies to that
    specific chat (the global-default setChatMenuButton call sometimes doesn't
    take effect in Telegram, so we call this per-user on /start).
    """
    payload: dict = {
        "menu_button": {
            "type": "web_app",
            "text": "📱 Dashboard",
            "web_app": {"url": _dashboard_url()},
        }
    }
    if chat_id is not None:
        payload["chat_id"] = chat_id
    try:
        resp = httpx.post(f"{BASE_URL}/setChatMenuButton", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_my_commands(commands: list[dict], language_code: str | None = None) -> dict:
    """Register the bot's native command menu (the blue 'Menu' button)."""
    payload: dict = {"commands": commands}
    if language_code:
        payload["language_code"] = language_code
    try:
        resp = httpx.post(f"{BASE_URL}/setMyCommands", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}
