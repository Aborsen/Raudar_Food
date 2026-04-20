"""Message formatting helpers for Telegram replies (Ukrainian, з гумором)."""
import random
from datetime import datetime

from lib.config import DAILY_CAL_TARGET, LOCAL_TZ, MACRO_GRAM_TARGETS


def _bar(used: float, target: float, width: int = 10) -> str:
    if target <= 0:
        return "─" * width
    pct = max(0.0, min(1.0, used / target))
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled)


def _pct(used: float, target: float) -> int:
    if target <= 0:
        return 0
    return round(100 * used / target)


# --- Ukrainian month names for pretty dates ---
_UA_MONTHS_FULL = [
    "", "січня", "лютого", "березня", "квітня", "травня", "червня",
    "липня", "серпня", "вересня", "жовтня", "листопада", "грудня",
]
_UA_MONTHS_SHORT = [
    "", "січ", "лют", "бер", "кві", "тра", "чер",
    "лип", "сер", "вер", "жов", "лис", "гру",
]


def _ua_date_long(dt: datetime) -> str:
    return f"{dt.day} {_UA_MONTHS_FULL[dt.month]}"


def _ua_date_short(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{dt.day} {_UA_MONTHS_SHORT[dt.month]}"
    except Exception:
        return date_str


def _name_or_default(first_name: str | None) -> str:
    return first_name.strip() if (first_name and first_name.strip()) else "друже"


_CONFIDENCE_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
_SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}

_MEAL_TYPE_UA = {
    "breakfast": "Сніданок",
    "lunch": "Обід",
    "dinner": "Вечеря",
    "snack": "Перекус",
}


# --- Shared helpers ---

def _format_ingredients(analysis: dict) -> list[str]:
    """Build ingredient list lines from analysis.ingredients."""
    ingredients = analysis.get("ingredients") or []
    if not ingredients:
        return []
    lines = ["", "📋 <b>Інгредієнти:</b>"]
    for ing in ingredients:
        name = ing.get("name", "?")
        grams = ing.get("estimated_grams")
        if grams:
            lines.append(f"  • {name} — ~{round(grams)}г")
        else:
            lines.append(f"  • {name}")
    return lines


def _format_warnings(analysis: dict) -> list[str]:
    """Build allergen + Crohn warning lines."""
    lines = []
    allergen_flags = analysis.get("allergen_flags") or []
    crohn_flags = analysis.get("crohn_flags") or []

    if allergen_flags:
        lines.append("")
        lines.append("⚠️ <b>УВАГА, АЛЕРГЕН:</b>")
        for a in allergen_flags:
            icon = _CONFIDENCE_ICON.get((a.get("confidence") or "").lower(), "⚠️")
            lines.append(
                f"  {icon} {a.get('allergen', '?').capitalize()} "
                f"(впевненість: {a.get('confidence', '?')}) — у складі: {a.get('ingredient', 'цієї страви')}"
            )

    if crohn_flags:
        lines.append("")
        lines.append("💡 <b>Нотатки щодо здоров'я (для кату):</b>")
        for c in crohn_flags:
            icon = _SEVERITY_ICON.get((c.get("severity") or "").lower(), "🟡")
            lines.append(
                f"  {icon} {c.get('concern', 'питання')} "
                f"({c.get('ingredient', '?')})"
            )

    return lines


def _format_nutrition_line(nutrition: dict) -> str:
    return (
        f"🔥 {round(nutrition.get('calories', 0))} ккал | "
        f"🥩 {round(nutrition.get('protein_g', 0))}г Б | "
        f"🍚 {round(nutrition.get('carbs_g', 0))}г В | "
        f"🧈 {round(nutrition.get('fat_g', 0))}г Ж"
    )


# --- Preview (before user accepts) ---

def format_meal_preview(meal_type: str, analysis: dict) -> str:
    """Preview message shown after AI analysis, before user taps Accept."""
    dish = analysis.get("dish_name") or "Страва"
    meal_ua = _MEAL_TYPE_UA.get(meal_type.lower(), meal_type.capitalize())
    nutrition = analysis.get("nutrition", {}) or {}

    lines = [
        f"🔍 <b>Попередній перегляд: {dish}</b>",
        f"🕐 {meal_ua}",
    ]

    lines.extend(_format_ingredients(analysis))
    lines.append("")
    lines.append(_format_nutrition_line(nutrition))
    lines.extend(_format_warnings(analysis))

    # Show the AI's portion reasoning so the user can sanity-check the grams
    portion_reasoning = (analysis.get("portion_reasoning") or "").strip()
    if portion_reasoning:
        lines.append("")
        lines.append(f"📏 <i>{portion_reasoning}</i>")

    assessment = analysis.get("overall_assessment")
    if assessment:
        lines.append("")
        lines.append(f"💬 {assessment}")

    lines.append("")
    lines.append("👇 <b>Підтвердити або виправити:</b>")
    return "\n".join(lines)


# --- Final confirmation (after Accept) ---

def format_meal_logged(
    meal_type: str,
    analysis: dict,
    today_log: dict,
    first_name: str | None = None,
    targets: dict | None = None,
) -> str:
    t_cal = _resolve_targets(targets)["calories"]
    nutrition = analysis.get("nutrition", {}) or {}
    dish = analysis.get("dish_name") or "Страва"
    date_display = _ua_date_long(datetime.now(LOCAL_TZ))
    meal_ua = _MEAL_TYPE_UA.get(meal_type.lower(), meal_type.capitalize())

    lines = [
        f"✅ <b>Записав: {dish}</b>",
        f"🕐 {meal_ua} — {date_display}",
    ]

    lines.extend(_format_ingredients(analysis))
    lines.append("")
    lines.append(_format_nutrition_line(nutrition))
    lines.extend(_format_warnings(analysis))

    assessment = analysis.get("overall_assessment")
    if assessment:
        lines.append("")
        lines.append(f"💬 {assessment}")

    lines.append("")
    lines.append(
        f"📊 Разом за день: {round(today_log.get('calories', 0))} / {t_cal} ккал"
    )

    if first_name:
        lines.append(f"<i>Тримайся, {first_name}! 💪</i>")

    return "\n".join(lines)


# --- Meal management list ---

def format_meals_list(meals: list[dict]) -> str:
    """List today's meals with IDs for edit/delete."""
    if not meals:
        return (
            "📋 <b>Сьогодні ще нічого не записано.</b>\n"
            "Надішли фото або напиши, що їв/їла. 📸"
        )

    lines = ["📋 <b>Страви за сьогодні:</b>", ""]
    for i, m in enumerate(meals, 1):
        mt = _MEAL_TYPE_UA.get((m.get("meal_type") or "").lower(), "")
        desc = m.get("description", "")[:50]
        cal = round(m.get("calories", 0))
        p = round(m.get("protein_g", 0))
        c = round(m.get("carbs_g", 0))
        f = round(m.get("fat_g", 0))
        lines.append(f"{i}. <b>{mt}</b> — {desc}")
        lines.append(f"   🔥 {cal} ккал | 🥩 {p}г Б | 🍚 {c}г В | 🧈 {f}г Ж")
        lines.append("")

    lines.append("👇 Обери дію під кожною стравою:")
    return "\n".join(lines)


# --- Today progress ---

_WELCOME_VARIANTS = [
    # Short, different joke each time. Pool so /start feels fresh.
    "Йо, <b>{name}</b>. 120 кг живого м'яса самі себе не годують. 📸 фото або 📝 текст — я рахую. 💪",
    "Привіт, <b>{name}</b>. Твоя штанга вже важить за тебе. Я рахуватиму калорії. Чесна робота. 🏋️",
    "<b>{name}</b>, вітаю. На катті білок — це релігія, а я твій жрець-бухгалтер. 📸 / 📝 — давай страву.",
    "Йо, <b>{name}</b>. Я рахую калорії швидше, ніж ти тиснеш 200 кг. Ну майже. Надсилай їжу.",
    "Привіт, <b>{name}</b>. Худнути повільно — щоб штанга не ображалась. 📸 страви, /ask для порад.",
    "<b>{name}</b>, салют. Я як тренер, але їсти не забороняю — просто рахую. Фото чи текст?",
    "Йо, <b>{name}</b>. Мета дня: 3300 ккал, 248г білка, і не пропустити кардіо. Я допоможу перше. Інше — сам.",
    "Привіт, <b>{name}</b>. Два варіанти: 📸 фото або 📝 текст. А потім головне питання: «де білок?»",
    "<b>{name}</b>, здоров. 120 кг і дефіцит — це мистецтво. Я — твій калькулятор. Надсилай страву.",
    "Йо, <b>{name}</b>. Дедлайн 250 кг любить повний холодильник і точні калорії. Давай їжу, я рахую.",
    "Привіт, <b>{name}</b>. Я бот, ти — маса. Разом ми будемо чуть менша маса, але сильніша. 📸 / 📝 старт.",
    "<b>{name}</b>, вітаю. Правило просте: бачу страву — рахую. Не бачу — нагадаю. Надсилай.",
]


def welcome_message(first_name: str | None = None) -> str:
    name = _name_or_default(first_name)
    return random.choice(_WELCOME_VARIANTS).format(name=name)


def help_message() -> str:
    return (
        "🤖 <b>Команди</b>\n"
        "/start — привітання та меню\n"
        "/ask — 💬 запитати AI про їжу, рецепти, покупки (пам'ятає контекст 1 год)\n"
        "/today — прогрес за сьогодні\n"
        "/meals — список страв за сьогодні (видалити / змінити)\n"
        "/history — останні 7 днів\n"
        "/history_detail YYYY-MM-DD — страви за певний день\n"
        "/suggest_meal — ідея страви, яка закриє день\n"
        "/help — показати цей список\n\n"
        "📸 Надішли фото страви — я спитаю, який це прийом їжі, і покажу аналіз на перевірку.\n"
        "📝 Або напиши текстом (наприклад: «курка 200г, рис 150г, броколі 100г»).\n"
        "🎙 Голосове — скажи, що ти з'їв/з'їла, я розшифрую і запишу. "
        "(А якщо надіслати голосове у відповідь на «Що запитаєш?» — стане питанням до AI.)\n"
        "Після аналізу: ✅ Прийняти / 🔄 Перерахувати / ✏️ Ввести вручну."
    )


def _resolve_targets(targets: dict | None) -> dict:
    """Default to module-level constants if no per-user targets are passed."""
    if targets:
        return targets
    return {
        "calories": DAILY_CAL_TARGET,
        "protein": MACRO_GRAM_TARGETS["protein"],
        "carbs": MACRO_GRAM_TARGETS["carbs"],
        "fat": MACRO_GRAM_TARGETS["fat"],
    }


def format_today_progress(log: dict, first_name: str | None = None, targets: dict | None = None) -> str:
    t = _resolve_targets(targets)
    t_cal, t_p, t_c, t_f = t["calories"], t["protein"], t["carbs"], t["fat"]
    date_display = _ua_date_long(datetime.now(LOCAL_TZ))
    cal = log.get("calories", 0)
    p = log.get("protein", 0)
    c = log.get("carbs", 0)
    f = log.get("fat", 0)
    fib = log.get("fiber", 0)
    sug = log.get("sugar", 0)
    meals = log.get("meal_count", 0)
    remaining = max(0, t_cal - cal)
    name = _name_or_default(first_name)

    if meals == 0:
        quip = "Поки порожньо, як у холодильнику студента перед стипендією. 😅"
    elif cal < t_cal * 0.5:
        quip = "Ще є місце для маневрів (і для курки з рисом). 🍚"
    elif cal < t_cal * 0.9:
        quip = "Цілковита гармонія — продовжуй у тому ж дусі. 💪"
    elif cal <= t_cal * 1.05:
        quip = "Ідеально в ціль, як снайпер по котлеті. 🎯"
    else:
        quip = "Сьогодні ми святкували. Завтра — легше. 😉"

    return (
        f"📊 <b>Прогрес на сьогодні ({date_display})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {name}\n"
        f"🔥 Калорії:  {round(cal)} / {t_cal} ({_pct(cal, t_cal)}%)\n"
        f"   {_bar(cal, t_cal)}\n"
        f"🥩 Білки:    {round(p)}г / {t_p}г ({_pct(p, t_p)}%)\n"
        f"   {_bar(p, t_p)}\n"
        f"🍚 Вуглеводи:{round(c)}г / {t_c}г ({_pct(c, t_c)}%)\n"
        f"   {_bar(c, t_c)}\n"
        f"🧈 Жири:     {round(f)}г / {t_f}г ({_pct(f, t_f)}%)\n"
        f"   {_bar(f, t_f)}\n"
        f"📈 Клітковина: {round(fib)}г\n"
        f"🍬 Цукор:      {round(sug)}г\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Прийомів їжі: {meals}\n"
        f"Залишилось: ~{round(remaining)} ккал\n\n"
        f"<i>{quip}</i>"
    )


def format_yesterday(log: dict, meals: list[dict], first_name: str | None = None, targets: dict | None = None) -> str:
    """Yesterday's progress + meal list in one message."""
    t = _resolve_targets(targets)
    t_cal, t_p, t_c, t_f = t["calories"], t["protein"], t["carbs"], t["fat"]
    date_str = log.get("date", "")
    try:
        date_display = _ua_date_long(datetime.strptime(date_str, "%Y-%m-%d"))
    except Exception:
        date_display = date_str

    cal = log.get("calories", 0)
    p = log.get("protein", 0)
    c = log.get("carbs", 0)
    f = log.get("fat", 0)
    fib = log.get("fiber", 0)
    sug = log.get("sugar", 0)
    meal_count = log.get("meal_count", 0)
    name = _name_or_default(first_name)

    if meal_count == 0:
        return (
            f"📆 <b>Вчора ({date_display})</b>\n"
            f"Нічого не було записано. Тиша в холодильнику. 🤫"
        )

    meal_lines = []
    for m in meals:
        mt_raw = (m.get("meal_type") or "").lower()
        mt = _MEAL_TYPE_UA.get(mt_raw, mt_raw.capitalize() or "—")
        desc = (m.get("description") or "")[:60]
        meal_lines.append(f"• {mt}: {desc} ({round(m.get('calories', 0))} ккал)")
    meal_section = "\n".join(meal_lines)

    return (
        f"📆 <b>Вчора ({date_display})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {name}\n"
        f"🔥 Калорії:  {round(cal)} / {t_cal} ({_pct(cal, t_cal)}%)\n"
        f"   {_bar(cal, t_cal)}\n"
        f"🥩 Білки:    {round(p)}г / {t_p}г\n"
        f"   {_bar(p, t_p)}\n"
        f"🍚 Вуглеводи:{round(c)}г / {t_c}г\n"
        f"   {_bar(c, t_c)}\n"
        f"🧈 Жири:     {round(f)}г / {t_f}г\n"
        f"   {_bar(f, t_f)}\n"
        f"📈 Клітковина: {round(fib)}г\n"
        f"🍬 Цукор:      {round(sug)}г\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Страви ({meal_count}):</b>\n"
        f"{meal_section}"
    )


def format_history(rows: list[dict], targets: dict | None = None) -> str:
    if not rows:
        return (
            "📅 Історії ще немає.\n"
            "Надішли перше фото — і ми почнемо писати цю кулінарну сагу. 📖🍳"
        )

    t_cal = _resolve_targets(targets)["calories"]
    lines = ["📅 <b>Останні 7 днів</b>"]
    for r in rows:
        cal = r.get("calories", 0)
        p = r.get("protein", 0)
        c = r.get("carbs", 0)
        f = r.get("fat", 0)
        total_macro_cal = p * 4 + c * 4 + f * 9
        if total_macro_cal > 0:
            p_pct = round(100 * p * 4 / total_macro_cal)
            c_pct = round(100 * c * 4 / total_macro_cal)
            f_pct = round(100 * f * 9 / total_macro_cal)
        else:
            p_pct = c_pct = f_pct = 0

        if cal == 0:
            marker = ""
        elif cal > t_cal * 1.05:
            marker = "⚠️ перебір"
        elif cal < t_cal * 0.80:
            marker = "⚠️ замало"
        else:
            marker = "✅"

        lines.append(
            f"{_ua_date_short(r.get('date', ''))}: {round(cal)} ккал — Б:{p_pct}% В:{c_pct}% Ж:{f_pct}% {marker}"
        )
    lines.append("")
    lines.append("<i>Нагадаю: консистенція важливіша за перфекціонізм. 🌱</i>")
    return "\n".join(lines)


def format_day_detail(date: str, meals: list[dict]) -> str:
    if not meals:
        return f"📅 На {_ua_date_short(date)} нічого не записано. Тиша в холодильнику. 🤫"

    lines = [f"📅 <b>Страви за {_ua_date_short(date)}</b>", ""]
    total_cal = 0
    for m in meals:
        total_cal += m.get("calories", 0)
        mt = _MEAL_TYPE_UA.get((m.get("meal_type") or "").lower(), (m.get("meal_type") or "").capitalize())
        lines.append(f"🕐 <b>{mt}</b> — {m.get('description', '')}")
        lines.append(
            f"   🔥 {round(m.get('calories', 0))} ккал | "
            f"🥩 {round(m.get('protein_g', 0))}г Б | "
            f"🍚 {round(m.get('carbs_g', 0))}г В | "
            f"🧈 {round(m.get('fat_g', 0))}г Ж"
        )
        if m.get("allergen_warnings"):
            names = ", ".join(a.get("allergen", "?") for a in m["allergen_warnings"])
            lines.append(f"   ⚠️ Алергени: {names}")
        lines.append("")

    lines.append(f"<b>Разом: {round(total_cal)} ккал</b>")
    return "\n".join(lines)


# --- Short texts used by webhook.py ---

PHOTO_PROMPT_MEAL_TYPE = "📸 Отримав! Що це за прийом їжі?"
TEXT_PROMPT_MEAL_TYPE = "📝 Записав твій опис! Що це за прийом їжі?"
ANALYZING_WAIT = "🔍 Аналізую страву, хвильку…"
RECALC_WAIT = "🔄 Перераховую уважніше…"
PHOTO_DOWNLOAD_FAILED = "Вибач, не вдалося завантажити фото. Спробуй ще раз. 📷"
PHOTO_ANALYSIS_FAILED = (
    "Не зміг розпізнати страву. Спробуй зробити фото чіткішим — "
    "я ж не кіт, у темряві не бачу. 🐈‍⬛"
)
TEXT_ANALYSIS_FAILED = (
    "Не зміг нормально розпарсити опис. Спробуй написати простіше — "
    "наприклад: «курка 200г, рис 150г, броколі 100г». 🙂"
)
PENDING_EXPIRED = (
    "⏰ Минуло більше 10 хвилин, і я вже забув, що було на фото (у мене "
    "серверна пам'ять — коротка). Надішли ще раз, будь ласка."
)
MANUAL_INPUT_PROMPT = "✏️ Напиши, що ти їв/їла (наприклад: курка 200г, рис 150г, броколі 100г):"
MEAL_DELETED = "🗑 Видалено: <b>{dish}</b> ({cal} ккал). Денний підрахунок оновлено."
MEAL_EDIT_PROMPT = "✏️ Напиши новий опис страви (замість «{dish}»):"
MEAL_NOT_FOUND = "Не знайшов цю страву. Можливо, вже видалена."
NO_MEALS_TO_MANAGE = "Сьогодні ще нічого не записано. Надішли фото або текст. 📸"
MEAL_CANCELLED = "Скасовано. Надішли фото або текст, коли будеш готовий. 👌"
UNKNOWN_COMMAND = "Не знаю такої команди. Глянь /help — там усе розписано. 🤓"
SUGGEST_THINKING = "🧠 Думаю над ідеєю, яка закриє твій день…"
SUGGEST_FAILED = "Ідея тимчасово застрягла в моделі. Спробуй за хвилину. 🤖💤"
HISTORY_USAGE = "Використай так: /history_detail РРРР-ММ-ДД (наприклад, /history_detail 2026-04-12)"

# --- Chat mode (/ask) ---
ASK_PROMPT = "💬 Що ти хочеш запитати? Напиши у відповіді — і я врахую твою історію харчування на сьогодні."
ASK_THINKING = "🧠 Думаю над відповіддю…"
ASK_ERROR = "Щось пішло не так з відповіддю. Спробуй ще раз за хвилину. 🤖"

# --- Reply-keyboard button labels (must match the strings used in main_menu_keyboard) ---
# When a user taps one of these buttons, Telegram sends its label as a message.
# webhook.py intercepts these labels and routes them to the corresponding command.
BTN_ASK = "💬 Запитати AI"
BTN_TODAY = "📊 Сьогодні"
BTN_YESTERDAY = "📆 Вчора"
BTN_MEALS = "📋 Мої страви"
BTN_HISTORY = "📅 Історія"
BTN_SUGGEST = "🍽️ Ідея страви"
BTN_FAV = "⭐ Улюблені"
BTN_WATER = "💧 +250мл"
BTN_PROFILE = "⚙️ Профіль"
# Kept as a defensive intercept in webhook.py for any cached keyboards that
# still have this label; removed from the active reply keyboard layout.
BTN_DASHBOARD = "📱 Dashboard"

MENU_BUTTON_LABELS = {
    BTN_ASK, BTN_TODAY, BTN_YESTERDAY, BTN_MEALS, BTN_HISTORY, BTN_SUGGEST,
    BTN_FAV, BTN_WATER, BTN_PROFILE,
}


def format_water(total_ml: int, target_ml: int) -> str:
    total_l = total_ml / 1000
    target_l = target_ml / 1000
    pct = (total_ml / target_ml) if target_ml else 0
    filled = max(0, min(10, round(pct * 10)))
    bar = "▰" * filled + "░" * (10 - filled)
    line = f"💧 Вода: {total_l:.2f} / {target_l:.1f} л"
    if pct > 1.0:
        line += f" ({round(pct * 100)}%)"
    return f"{line}\n{bar}"
