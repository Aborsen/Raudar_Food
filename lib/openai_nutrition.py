"""OpenAI calls for end-of-day summaries and meal suggestions."""
import json

from openai import OpenAI

from lib.config import (
    OPENAI_API_KEY,
    SUMMARY_PROMPT_TEMPLATE,
    RECIPE_PROMPT_TEMPLATE,
    DAILY_CAL_TARGET,
    MACRO_GRAM_TARGETS,
)

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def generate_daily_summary(meals: list[dict], totals: dict, targets: dict | None = None) -> str:
    t = targets or {
        "calories": DAILY_CAL_TARGET,
        "protein": MACRO_GRAM_TARGETS["protein"],
        "carbs": MACRO_GRAM_TARGETS["carbs"],
        "fat": MACRO_GRAM_TARGETS["fat"],
    }
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        meals_json=json.dumps(meals, indent=2, default=str),
        total_cal=round(totals.get("calories", 0)),
        protein=round(totals.get("protein", 0)),
        carbs=round(totals.get("carbs", 0)),
        fat=round(totals.get("fat", 0)),
        fiber=round(totals.get("fiber", 0)),
        sugar=round(totals.get("sugar", 0)),
        target_cal=t["calories"],
        target_protein=t["protein"],
        target_carbs=t["carbs"],
        target_fat=t["fat"],
    )
    resp = _get_client().chat.completions.create(
        model="gpt-4o",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return (resp.choices[0].message.content or "").strip()


def suggest_meal(today_log: dict, today_meals: list[dict], targets: dict | None = None) -> str:
    t = targets or {
        "calories": DAILY_CAL_TARGET,
        "protein": MACRO_GRAM_TARGETS["protein"],
        "carbs": MACRO_GRAM_TARGETS["carbs"],
        "fat": MACRO_GRAM_TARGETS["fat"],
    }
    remaining_cal = max(0, t["calories"] - today_log.get("calories", 0))
    remaining_p = max(0, t["protein"] - today_log.get("protein", 0))
    remaining_c = max(0, t["carbs"] - today_log.get("carbs", 0))
    remaining_f = max(0, t["fat"] - today_log.get("fat", 0))

    intake_lines = []
    for m in today_meals:
        intake_lines.append(
            f"- {m.get('meal_type', 'meal').capitalize()}: {m.get('description', '')} "
            f"({round(m.get('calories', 0))} cal, "
            f"{round(m.get('protein_g', 0))}g P, "
            f"{round(m.get('carbs_g', 0))}g C, "
            f"{round(m.get('fat_g', 0))}g F)"
        )
    today_intake = "\n".join(intake_lines) if intake_lines else "(nothing logged yet)"

    prompt = RECIPE_PROMPT_TEMPLATE.format(
        today_intake=today_intake,
        remaining_cal=round(remaining_cal),
        remaining_protein=round(remaining_p),
        remaining_carbs=round(remaining_c),
        remaining_fat=round(remaining_f),
    )
    resp = _get_client().chat.completions.create(
        model="gpt-4o",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return (resp.choices[0].message.content or "").strip()
