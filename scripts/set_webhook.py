"""One-time setup: register the Telegram webhook and the bot's command menu.

Run locally after deploying to Vercel:
    python scripts/set_webhook.py

Requires these in .env:
    TELEGRAM_BOT_TOKEN
    WEBHOOK_SECRET
    VERCEL_URL          (e.g. your-app.vercel.app — no https://, no trailing slash)
"""
import os
import sys

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Native command menu shown under the blue "Menu" button inside Telegram.
# Ukrainian descriptions (max 256 chars each, must be lowercase-friendly).
COMMANDS_UA = [
    {"command": "today", "description": "📊 Прогрес за сьогодні"},
    {"command": "history", "description": "📅 Останні 7 днів"},
    {"command": "history_detail", "description": "🔎 Страви за конкретний день (YYYY-MM-DD)"},
    {"command": "suggest_meal", "description": "🍽️ Ідея страви під залишок дня"},
    {"command": "help", "description": "❓ Список команд"},
]

# English fallback shown to users whose Telegram language is not uk.
COMMANDS_EN = [
    {"command": "today", "description": "Today's progress"},
    {"command": "history", "description": "Last 7 days"},
    {"command": "history_detail", "description": "Meals on a specific day (YYYY-MM-DD)"},
    {"command": "suggest_meal", "description": "AI meal suggestion"},
    {"command": "help", "description": "Show commands"},
]


def _post(token: str, method: str, payload: dict) -> dict:
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload,
        timeout=15,
    )
    return resp.json()


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    secret = os.getenv("WEBHOOK_SECRET")
    vercel_url = os.getenv("VERCEL_URL")

    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": token,
        "WEBHOOK_SECRET": secret,
        "VERCEL_URL": vercel_url,
    }.items() if not v]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 1

    vercel_url = vercel_url.replace("https://", "").replace("http://", "").rstrip("/")
    webhook_url = f"https://{vercel_url}/api/webhook"

    # 1. Register webhook
    wh = _post(token, "setWebhook", {
        "url": webhook_url,
        "secret_token": secret,
        "allowed_updates": ["message", "callback_query"],
    })
    print(f"→ setWebhook to {webhook_url}")
    print(" ", wh)
    if not wh.get("ok"):
        return 2

    # 2. Register command menus (Ukrainian + English fallback)
    cm_ua = _post(token, "setMyCommands", {"commands": COMMANDS_UA, "language_code": "uk"})
    print("→ setMyCommands (uk)")
    print(" ", cm_ua)

    cm_default = _post(token, "setMyCommands", {"commands": COMMANDS_EN})
    print("→ setMyCommands (default / English fallback)")
    print(" ", cm_default)

    if not cm_ua.get("ok") or not cm_default.get("ok"):
        return 3

    print("\n✅ Готово! У Telegram натисни кнопку «Меню» зліва знизу — команди відобразяться.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
