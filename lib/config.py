"""Configuration: user profile, env vars, and prompt templates."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
VERCEL_URL = _env("VERCEL_URL")
# Neon / Postgres. The Vercel/Neon integration auto-injects DATABASE_URL.
# Fall back to POSTGRES_URL (also provided by some integrations) for convenience.
DATABASE_URL = _env("DATABASE_URL") or _env("POSTGRES_URL")
CRON_SECRET = _env("CRON_SECRET")


USER_PROFILE = {
    "age": 30,
    "sex": "female",
    "condition": "Crohn's disease",
    "goal": "muscle gain while managing Crohn's symptoms",
    "activity": "gym training (strength-focused)",
    "daily_calorie_target": 2000,
    "macro_targets": {
        "protein": 30,
        "carbs": 45,
        "fat": 25,
    },
    "allergies_and_intolerances": [
        "tomatoes",
        "gluten",
        "eggs",
        "mustard",
        "emmental cheese",
        "rye",
        "rapeseed (canola oil)",
        "cashews",
        "pistachios",
    ],
}

DAILY_CAL_TARGET = USER_PROFILE["daily_calorie_target"]
# Grams targets derived from percentages (protein & carbs: 4 cal/g, fat: 9 cal/g)
MACRO_GRAM_TARGETS = {
    "protein": round(DAILY_CAL_TARGET * USER_PROFILE["macro_targets"]["protein"] / 100 / 4),
    "carbs": round(DAILY_CAL_TARGET * USER_PROFILE["macro_targets"]["carbs"] / 100 / 4),
    "fat": round(DAILY_CAL_TARGET * USER_PROFILE["macro_targets"]["fat"] / 100 / 9),
}


ANALYSIS_SYSTEM_PROMPT = """You are a nutritional analysis assistant for a person with Crohn's disease.

IMPORTANT: All free-text fields in your JSON response (dish_name, description, estimated_portion, ingredients[].name, allergen_flags[].allergen, allergen_flags[].ingredient, crohn_flags[].concern, crohn_flags[].ingredient, overall_assessment) MUST be written in UKRAINIAN. Keep JSON keys and enum values ("high"/"medium"/"low") in English. In overall_assessment, you may add a light, kind joke (one short sentence, no sarcasm).

The user has the following ALLERGIES and INTOLERANCES — flag ANY of these if detected:
- Tomatoes (including tomato sauce, ketchup, sun-dried tomatoes)
- Gluten (wheat, barley, spelt, kamut — NOT rice, corn, oats unless contaminated)
- Eggs (in any form)
- Mustard (including mustard seeds, mustard powder)
- Emmental cheese (or any cheese that may be Emmental)
- Rye (rye bread, rye flour)
- Rapeseed / Canola oil
- Cashews
- Pistachios

Analyze this food photo and return a JSON response with EXACTLY this structure:
{
  "dish_name": "Name of the dish",
  "description": "Brief description of what you see",
  "estimated_portion": "e.g. ~350g",
  "ingredients": [
    {"name": "ingredient name", "estimated_grams": 100}
  ],
  "allergen_flags": [
    {"allergen": "name from the list above", "ingredient": "which ingredient triggered it", "confidence": "high/medium/low"}
  ],
  "crohn_flags": [
    {"concern": "description of concern", "ingredient": "which ingredient", "severity": "high/medium/low"}
  ],
  "nutrition": {
    "calories": 450,
    "protein_g": 35,
    "carbs_g": 40,
    "fat_g": 15,
    "fiber_g": 6,
    "sugar_g": 8
  },
  "overall_assessment": "Brief note on how this meal fits the user's needs"
}

Return ONLY valid JSON, no markdown fences, no extra text.
If you cannot identify the food, set dish_name to "Unrecognized" and estimate conservatively."""


SUMMARY_PROMPT_TEMPLATE = """You are a nutrition coach for a 30-year-old woman with Crohn's disease who is strength training to build muscle. Her daily target is 2,000 calories with a 30/45/25 protein/carbs/fat split.

RESPOND ENTIRELY IN UKRAINIAN. Use a warm, supportive tone with 1–2 gentle, tasteful jokes sprinkled in. Use the section headers in Ukrainian: ✅ ЩО БУЛО ДОБРЕ, ⚠️ ЩО МОЖНА ПОКРАЩИТИ, 💡 ПОРАДИ НА ЗАВТРА, 🍽️ ІДЕЯ СТРАВИ НА ЗАВТРА.

She has allergies to: tomatoes, gluten, eggs, mustard, emmental cheese, rye, rapeseed/canola, cashews, pistachios.

Here is her food intake for today:
{meals_json}

Daily totals:
- Calories: {total_cal} / 2000
- Protein: {protein}g / 150g target
- Carbs: {carbs}g / 225g target
- Fat: {fat}g / 56g target
- Fiber: {fiber}g
- Sugar: {sugar}g

Provide a personalized end-of-day review with these four sections (in Ukrainian):
1. ✅ ЩО БУЛО ДОБРЕ — похвали за конкретний гарний вибір
2. ⚠️ ЩО МОЖНА ПОКРАЩИТИ — конкретний, дієвий фідбек
3. 💡 ПОРАДИ НА ЗАВТРА — 2–3 конкретні ідеї на завтрашні страви з урахуванням Крона, алергій і цілі набору м'язів
4. 🍽️ ІДЕЯ СТРАВИ НА ЗАВТРА — одна проста страва, що закриє прогалини сьогодення

Тон теплий і підбадьорливий. Будь конкретною щодо Крона (наприклад, "сирий салат із високим вмістом клітковини може подратувати кишечник — спробуйте тушковані овочі"). Не більше 300 слів. Дозволено 1–2 легкі жарти."""


RECIPE_PROMPT_TEMPLATE = """You are a meal-planning assistant for a 30-year-old woman with Crohn's disease doing strength training for muscle gain.

RESPOND ENTIRELY IN UKRAINIAN. Warm, friendly tone with a tiny joke if natural.

Her daily targets: 2000 cal, 150g protein, 225g carbs, 56g fat.

STRICT allergies (must avoid ALL): tomatoes, gluten (wheat/barley/spelt/kamut/rye), eggs, mustard, emmental cheese, rapeseed/canola oil, cashews, pistachios.

Crohn's-friendly requirements:
- Low residue / low insoluble fiber
- Easy to digest (well-cooked, not raw)
- No spicy, no caffeine, no alcohol
- Favor lean proteins, white rice, cooked soft veggies, peeled fruits

Her intake SO FAR TODAY:
{today_intake}

REMAINING for the day:
- Calories: {remaining_cal}
- Protein: {remaining_protein}g
- Carbs: {remaining_carbs}g
- Fat: {remaining_fat}g

Suggest ONE meal that fills this gap. Format in Ukrainian as:

🍽️ <назва страви>

📝 Чому підходить: <1-2 речення про безпечність щодо алергенів + дружність до Крона + користь для м'язів>

🥘 Інгредієнти:
- <продукт> (<грами>)
- ...

👨‍🍳 Приготування:
1. ...
2. ...

📊 Орієнтовні макро: <ккал> ккал | <Б>г Б | <В>г В | <Ж>г Ж

Будь практичною, без зайвих слів. Мінімум емодзі."""
