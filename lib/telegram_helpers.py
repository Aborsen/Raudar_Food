"""Direct Telegram Bot API calls via httpx (no aiogram — serverless-friendly)."""
import httpx

from lib.config import TELEGRAM_BOT_TOKEN

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"


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
        ]
    }


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
