"""Message formatting helpers for Telegram replies (Ukrainian, з гумором)."""
from datetime import datetime

from lib.config import DAILY_CAL_TARGET, MACRO_GRAM_TARGETS


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


def welcome_message(first_name: str | None = None) -> str:
    name = _name_or_default(first_name)
    return (
        f"Привіт, <b>{name}</b>! 🥗\n\n"
        f"Я твій особистий трекер їжі — дружній до Крона й суворий до алергенів. "
        f"Пришли мені фото страви — і я:\n"
        f"• порахую калорії та БЖВ 🔥\n"
        f"• помахаю червоним прапорцем над помідорами, глютеном та іншими «ні-ні» 🚩\n"
        f"• підкажу, чи сподобається це твоєму кишечнику 🫀\n"
        f"• ввечері підсумую день і дам поради на завтра 🌙\n\n"
        f"Жарт дня: я бачив твою каструлю ще в попередньому житті — не переживай, "
        f"сьогодні вона смачніша. 😉\n\n"
        f"Тисни кнопку <b>«Меню»</b> зліва знизу — там усі команди."
    )


def help_message() -> str:
    return (
        "🤖 <b>Команди</b>\n"
        "/start — привітання та меню\n"
        "/today — прогрес за сьогодні\n"
        "/history — останні 7 днів\n"
        "/history_detail YYYY-MM-DD — страви за певний день\n"
        "/suggest_meal — ідея страви, яка закриє день\n"
        "/help — показати цей список\n\n"
        "📸 Просто надішли фото страви — я спитаю, який це прийом їжі, і зроблю аналіз. "
        "Спойлер: рентген я не вмикаю, але око в мене натреноване. 😎"
    )


def format_today_progress(log: dict, first_name: str | None = None) -> str:
    date_display = _ua_date_long(datetime.utcnow())
    cal = log.get("calories", 0)
    p = log.get("protein", 0)
    c = log.get("carbs", 0)
    f = log.get("fat", 0)
    fib = log.get("fiber", 0)
    sug = log.get("sugar", 0)
    meals = log.get("meal_count", 0)
    remaining = max(0, DAILY_CAL_TARGET - cal)
    name = _name_or_default(first_name)

    # A tiny, mood-aware quip
    if meals == 0:
        quip = "Поки порожньо, як у холодильнику студента перед стипендією. 😅"
    elif cal < DAILY_CAL_TARGET * 0.5:
        quip = "Ще є місце для маневрів (і для курки з рисом). 🍚"
    elif cal < DAILY_CAL_TARGET * 0.9:
        quip = "Цілковита гармонія — продовжуй у тому ж дусі. 💪"
    elif cal <= DAILY_CAL_TARGET * 1.05:
        quip = "Ідеально в ціль, як снайпер по котлеті. 🎯"
    else:
        quip = "Сьогодні ми святкували. Завтра — легше. 😉"

    return (
        f"📊 <b>Прогрес на сьогодні ({date_display})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {name}\n"
        f"🔥 Калорії:  {round(cal)} / {DAILY_CAL_TARGET} ({_pct(cal, DAILY_CAL_TARGET)}%)\n"
        f"   {_bar(cal, DAILY_CAL_TARGET)}\n"
        f"🥩 Білки:    {round(p)}г / {MACRO_GRAM_TARGETS['protein']}г ({_pct(p, MACRO_GRAM_TARGETS['protein'])}%)\n"
        f"   {_bar(p, MACRO_GRAM_TARGETS['protein'])}\n"
        f"🍚 Вуглеводи:{round(c)}г / {MACRO_GRAM_TARGETS['carbs']}г ({_pct(c, MACRO_GRAM_TARGETS['carbs'])}%)\n"
        f"   {_bar(c, MACRO_GRAM_TARGETS['carbs'])}\n"
        f"🧈 Жири:     {round(f)}г / {MACRO_GRAM_TARGETS['fat']}г ({_pct(f, MACRO_GRAM_TARGETS['fat'])}%)\n"
        f"   {_bar(f, MACRO_GRAM_TARGETS['fat'])}\n"
        f"📈 Клітковина: {round(fib)}г\n"
        f"🍬 Цукор:      {round(sug)}г\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Прийомів їжі: {meals}\n"
        f"Залишилось: ~{round(remaining)} ккал\n\n"
        f"<i>{quip}</i>"
    )


_CONFIDENCE_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
_SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}

_MEAL_TYPE_UA = {
    "breakfast": "Сніданок",
    "lunch": "Обід",
    "dinner": "Вечеря",
    "snack": "Перекус",
}


def format_meal_logged(
    meal_type: str,
    analysis: dict,
    today_log: dict,
    first_name: str | None = None,
) -> str:
    nutrition = analysis.get("nutrition", {}) or {}
    dish = analysis.get("dish_name") or "Страва"
    date_display = _ua_date_long(datetime.utcnow())
    meal_ua = _MEAL_TYPE_UA.get(meal_type.lower(), meal_type.capitalize())

    allergen_flags = analysis.get("allergen_flags") or []
    crohn_flags = analysis.get("crohn_flags") or []

    lines = [
        f"✅ <b>Записав: {dish}</b>",
        f"🕐 {meal_ua} — {date_display}",
        "",
        (
            f"🔥 {round(nutrition.get('calories', 0))} ккал | "
            f"🥩 {round(nutrition.get('protein_g', 0))}г Б | "
            f"🍚 {round(nutrition.get('carbs_g', 0))}г В | "
            f"🧈 {round(nutrition.get('fat_g', 0))}г Ж"
        ),
    ]

    if allergen_flags:
        lines.append("")
        lines.append("⚠️ <b>УВАГА, АЛЕРГЕН:</b>")
        for a in allergen_flags:
            icon = _CONFIDENCE_ICON.get((a.get("confidence") or "").lower(), "⚠️")
            lines.append(
                f"  {icon} {a.get('allergen', '?').capitalize()} "
                f"(впевненість: {a.get('confidence', '?')}) — у складі: {a.get('ingredient', 'цієї страви')}"
            )
        lines.append("<i>Жарт на тему: помідор — овоч лише в салаті. В нас у списку — це злочинець. 🙃</i>")

    if crohn_flags:
        lines.append("")
        lines.append("⚠️ <b>Примітка щодо Крона:</b>")
        for c in crohn_flags:
            icon = _SEVERITY_ICON.get((c.get("severity") or "").lower(), "🟡")
            lines.append(
                f"  {icon} {c.get('concern', 'питання')} "
                f"({c.get('ingredient', '?')})"
            )

    assessment = analysis.get("overall_assessment")
    if assessment:
        lines.append("")
        lines.append(f"💬 {assessment}")

    lines.append("")
    lines.append(
        f"📊 Разом за день: {round(today_log.get('calories', 0))} / {DAILY_CAL_TARGET} ккал"
    )

    # Tiny personal nudge
    if first_name:
        lines.append(f"<i>Тримайся, {first_name}! 💪</i>")

    return "\n".join(lines)


def format_history(rows: list[dict]) -> str:
    if not rows:
        return (
            "📅 Історії ще немає.\n"
            "Надішли перше фото — і ми почнемо писати цю кулінарну сагу. 📖🍳"
        )

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
        elif cal > DAILY_CAL_TARGET * 1.05:
            marker = "⚠️ перебір"
        elif cal < DAILY_CAL_TARGET * 0.80:
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
ANALYZING_WAIT = "🔍 Аналізую страву, хвильку…"
PHOTO_DOWNLOAD_FAILED = "Вибач, не вдалося завантажити фото. Спробуй ще раз. 📷"
PHOTO_ANALYSIS_FAILED = (
    "Не зміг розпізнати страву. Спробуй зробити фото чіткішим — "
    "я ж не кіт, у темряві не бачу. 🐈‍⬛"
)
PENDING_EXPIRED = (
    "⏰ Минуло більше 10 хвилин, і я вже забув, що було на фото (у мене "
    "серверна пам’ять — коротка). Надішли ще раз, будь ласка."
)
UNKNOWN_COMMAND = "Не знаю такої команди. Глянь /help — там усе розписано. 🤓"
SUGGEST_THINKING = "🧠 Думаю над ідеєю, яка закриє твій день…"
SUGGEST_FAILED = "Ідея тимчасово застрягла в моделі. Спробуй за хвилину. 🤖💤"
HISTORY_USAGE = "Використай так: /history_detail РРРР-ММ-ДД (наприклад, /history_detail 2026-04-12)"
