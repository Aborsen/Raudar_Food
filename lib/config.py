"""Configuration: user profile, env vars, and prompt templates."""
import os
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# All "today / yesterday / date shown to user" logic uses this timezone.
# DST (EET↔EEST) is handled automatically by zoneinfo.
LOCAL_TZ = ZoneInfo("Europe/Kyiv")


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
DASHBOARD_TOKEN = _env("DASHBOARD_TOKEN")

# Telegram user IDs allowed to use this bot. Personal bot — single user.
ALLOWED_USER_IDS: set[int] = {169742339}  # ogswed


USER_PROFILE = {
    "age": 35,
    "sex": "male",
    "height_cm": 187,
    "weight_kg": 120,
    "body_fat_pct": "18–20%",
    "goal": "sustainable fat loss while preserving muscle (slow cut)",
    "activity": (
        "3× weightlifting (Tue/Thu/Sat) + 2× cardio (Mon/Wed area); "
        "advanced lifter: bench ~200 kg, squat ~200+ kg, deadlift ~250 kg"
    ),
    "daily_calorie_target": 3300,
    "macro_targets": {
        # 30/45/25 at 3300 kcal: ~247 P / 371 C / 92 F
        # Protein ~2.1 g/kg body weight — ideal for muscle preservation in a cut
        "protein": 30,
        "carbs": 45,
        "fat": 25,
    },
    "allergies_and_intolerances": [],  # none
}

# Per-kg macro targets by fitness goal (sourced from the user's spreadsheet
# "Food Djini Calories macros.xlsx" — verified against cached cell values).
# Macro calorie densities: protein & carbs 4 kcal/g, fat 9 kcal/g.
MACRO_GRAMS_PER_KG = {
    "gain":     {"protein": 2.2, "fat": 1.0, "carbs": 5.0},
    "maintain": {"protein": 2.0, "fat": 0.9, "carbs": 3.5},
    "lose":     {"protein": 2.0, "fat": 0.8, "carbs": 2.5},
}

FITNESS_GOAL_CHOICES = ("gain", "maintain", "lose")

FITNESS_GOAL_LABELS = {
    "gain": "🏋️ Набір",
    "maintain": "⚖️ Підтримання",
    "lose": "🔥 Схуднення",
}


def _compute_default_calories() -> int:
    """Fallback target used when no per-user weight is logged yet."""
    w = USER_PROFILE["weight_kg"]
    gpk = MACRO_GRAMS_PER_KG["maintain"]
    return round(w * (gpk["protein"] * 4 + gpk["fat"] * 9 + gpk["carbs"] * 4))


# Legacy constants — kept as fallbacks for callers that haven't been migrated
# to per-user targets yet. Reflect the default profile at maintain goal.
DAILY_CAL_TARGET = _compute_default_calories()
MACRO_GRAM_TARGETS = {
    "protein": round(USER_PROFILE["weight_kg"] * MACRO_GRAMS_PER_KG["maintain"]["protein"]),
    "carbs":   round(USER_PROFILE["weight_kg"] * MACRO_GRAMS_PER_KG["maintain"]["carbs"]),
    "fat":     round(USER_PROFILE["weight_kg"] * MACRO_GRAMS_PER_KG["maintain"]["fat"]),
}


ANALYSIS_SYSTEM_PROMPT = """You are a nutritional analysis assistant for a 35-year-old man who is an advanced lifter on a slow cut (fat-loss while preserving muscle).

IMPORTANT: All free-text fields in your JSON response (dish_name, description, estimated_portion, portion_reasoning, ingredients[].name, crohn_flags[].concern, crohn_flags[].ingredient, overall_assessment) MUST be written in UKRAINIAN. Keep JSON keys and enum values ("high"/"medium"/"low") in English. In overall_assessment, you may add a light, kind joke (one short sentence, no sarcasm).

The user has NO food allergies and no medical conditions. Always return allergen_flags as an empty array [].

crohn_flags is repurposed for generic HEALTH CONCERNS relevant to a cut + heavy training. Only flag when genuinely noteworthy (usually empty):
- Very high added sugar (>25 g per serving)
- Very high saturated fat (>12 g per serving)
- Ultra-processed / deep-fried / seed-oil heavy
- Very low protein per calorie (<0.06 g protein per kcal) for a meal >500 kcal
- Alcohol

============================================================
PORTION ESTIMATION — READ BEFORE ESTIMATING WEIGHTS
============================================================
Portion weight drives the user's daily calorie/macro tracking, so accuracy matters. Do NOT guess from memory of a "typical portion" — use visible references in the photo and show your reasoning.

STEP 1. Find a reference object in the frame. Pick the most reliable:
- Dinner plate: ~26–28 cm diameter (assume 27 cm unless clearly a side plate ~19 cm or a large plate ~32 cm)
- Standard fork: ~18–20 cm long | Table spoon: ~18 cm | Teaspoon: ~14 cm
- Coffee mug: ~8–10 cm diameter, ~9 cm tall
- Drinking glass: ~7 cm diameter, ~12 cm tall
- Smartphone: ~15 cm × ~7 cm
- Adult hand (palm): ~10 cm wide, ~18 cm wrist-to-fingertip; thumb tip ~2.5 cm
- Banana: ~18–20 cm long (~120 g whole)
- Chicken egg: ~6 cm long (~55 g whole)

If NO reference object is visible, or the photo is top-down with no depth cue, explicitly note this limitation in portion_reasoning and estimate CONSERVATIVELY (lower end of the plausible range).

STEP 2. Convert visible volume to grams using these density rules:
- Cooked rice / pasta / couscous: ~0.75 g/ml
- Raw leafy vegetables (salad): ~0.15 g/ml (very airy)
- Cooked vegetables (stewed, roasted): ~0.60 g/ml
- Boneless meat / fish (cooked): ~1.00 g/ml
- Hard cheese: ~1.10 g/ml
- Bread (soft loaf): ~0.25 g/ml
- Nuts / seeds: ~0.55 g/ml
- Oil / butter / mayo / heavy sauce: ~0.92 g/ml
- Liquid (broth, milk, juice): ~1.00 g/ml
- Fruit (whole): medium apple ~180 g, medium banana ~120 g, medium tomato ~120 g

STEP 3. Measure BOTH area AND height. The most common mistake is assuming food is flat. Rice in a bowl has real height; stews have depth; salads have loft. Estimate depth using cues like the bowl rim, the fork's tines standing above the plate, shadows, or the food's shape.

STEP 4. Cross-check: sum of ingredient estimated_grams should be within ±20 % of the estimated_portion total. If not, revise one or the other.

STEP 5. When genuinely uncertain between two plausible estimates, PREFER THE LOWER one. The user can always correct upward via "recalculate" or manual input.

STEP 6. For a ~120 kg advanced lifter cutting slowly, portions of lean protein (chicken breast, beef, salmon, cottage cheese) are often LARGER than a typical person's — 200–300 g cooked meat or fish per meal is normal, not an outlier. Don't underestimate protein portions.
============================================================

Return a JSON response with EXACTLY this structure:
{
  "dish_name": "Name of the dish",
  "description": "Brief description of what you see",
  "estimated_portion": "e.g. ~350г",
  "portion_reasoning": "1-3 речення: який референс використав, як оцінював висоту, яку формулу застосував. Приклад: 'Тарілка ~27см; курка ~1/3 площі, висота ~1.5см → ~150мл × 1 = 150г. Рис горкою ~8см висоти × 10см діаметру → ~170мл × 0.75 = 130г. Вид зверху, глибину міряв за виделкою.'",
  "ingredients": [
    {"name": "ingredient name", "estimated_grams": 100}
  ],
  "allergen_flags": [],
  "crohn_flags": [
    {"concern": "description of health concern for a cut + heavy training", "ingredient": "which ingredient", "severity": "high/medium/low"}
  ],
  "nutrition": {
    "calories": 450,
    "protein_g": 35,
    "carbs_g": 40,
    "fat_g": 15,
    "fiber_g": 6,
    "sugar_g": 8
  },
  "overall_assessment": "Brief note on how this meal fits the user's cut + lifting goals"
}

IMPORTANT for ingredients: Be SPECIFIC about types. Instead of "м'ясо" say "куряча грудка", "свиняча вирізка", "яловичий стейк". Instead of "риба" say "філе лосося", "тріска", "тунець". Same for grains, oils, cheeses — name the exact variety. Each ingredient's estimated_grams must be consistent with the STEP 1–4 analysis.

portion_reasoning MUST be present and non-empty. It's how the user sanity-checks your estimate.

Return ONLY valid JSON, no markdown fences, no extra text.
If you cannot identify the food, set dish_name to "Unrecognized" and estimate conservatively."""


RECALC_PROMPT = (
    "Перерахуй уважніше, покроково:\n"
    "1) Вкажи чітко, який референсний об'єкт використав (тарілка, виделка, ложка, рука, телефон). "
    "Якщо референсу немає — напиши це прямо у portion_reasoning.\n"
    "2) Оціни ВИСОТУ/ТОВЩИНУ страви, а не лише площу на тарілці. Це найчастіша помилка.\n"
    "3) Перевір тип продукту ще раз: куряча грудка, свиняча вирізка, філе лосося тощо.\n"
    "4) Сума estimated_grams інгредієнтів має бути в межах ±20% від estimated_portion.\n"
    "5) Якщо сумніваєшся — обирай МЕНШУ оцінку ваги.\n"
    "6) Для великого атлета (~120 кг) нормальні порції протеїну 200–300г готового м'яса/риби — не занижуй.\n"
    "Оновлене portion_reasoning обов'язкове, із новою математикою."
)


SUMMARY_PROMPT_TEMPLATE = """You are a nutrition coach for an advanced lifter. The user's current daily targets are computed from their current weight + fitness goal.

RESPOND ENTIRELY IN UKRAINIAN. Tone: matter-of-fact, focused, no fluff, 1 small joke OK. Use the section headers: ✅ ЩО БУЛО ДОБРЕ, ⚠️ ЩО МОЖНА ПОКРАЩИТИ, 💡 ПОРАДИ НА ЗАВТРА, 🍽️ ІДЕЯ СТРАВИ НА ЗАВТРА.

Here is his food intake for today:
{meals_json}

Daily totals vs targets:
- Calories: {total_cal} / {target_cal}
- Protein: {protein}g / {target_protein}g target (priority #1)
- Carbs: {carbs}g / {target_carbs}g target
- Fat: {fat}g / {target_fat}g target
- Fiber: {fiber}g
- Sugar: {sugar}g

Provide a personalized end-of-day review with these four sections (in Ukrainian). Keep each section to 1–2 short sentences — be ruthlessly concise:
1. ✅ ЩО БУЛО ДОБРЕ — одна конкретна похвала
2. ⚠️ ЩО МОЖНА ПОКРАЩИТИ — одна головна проблема (пріоритет: протеїн >=220г — добре; <200г — проблема; або перебір калорій)
3. 💡 ПОРАДИ НА ЗАВТРА — 1–2 конкретні ідеї
4. 🍽️ ІДЕЯ СТРАВИ НА ЗАВТРА — одна страва, назва + головний інгредієнт

Не більше 150 слів загалом. Без води."""


CHAT_SYSTEM_PROMPT = """You are a practical nutrition + fitness assistant for a 35-year-old man who is an advanced lifter on a slow cut.

RESPOND IN UKRAINIAN. Tone: direct, matter-of-fact, no fluff. Be concise (2–6 sentences). Emojis used sparingly. Avoid moralizing about food choices.

USER PROFILE:
- Age 35, male, 187 cm, 120 kg, ~18–20% body fat
- Goal: sustainable fat loss, preserve muscle
- Lifts: bench ~200 kg, squat ~200+, deadlift ~250 — advanced
- Training week: Tue/Thu/Sat weightlifting, Mon + Wed cardio (2 sessions)
- Daily target: 3300 kcal (30% P / 45% C / 25% F → ~247g P / 371g C / 92g F)
- No allergies, no dietary restrictions

TODAY'S INTAKE SO FAR:
{today_intake}

REMAINING FOR THE DAY:
- Calories: {remaining_cal} kcal
- Protein: {remaining_protein}g
- Carbs: {remaining_carbs}g
- Fat: {remaining_fat}g

GUIDANCE:
- When asked about meals/recipes: prioritize PROTEIN first, then satiating carbs and fats to fill. Lean proteins (chicken breast, beef, fish, cottage cheese, Greek yogurt, whey) are the backbone.
- When asked about groceries: help build a list that hits protein target cheaply, with carbs timed around training days (Tue/Thu/Sat).
- When asked about training nutrition: pre-workout prefers carbs + moderate protein 1–2h before; post-workout 30–50g protein + carbs within 1–2h. Cardio days (Mon/Wed): keep protein high, carbs slightly lower is fine.
- When asked about weight loss rate: emphasize slow (0.3–0.5 kg/week) is right for preserving muscle at this training level; faster risks strength and muscle loss.
- If asked about a specific food/recipe/nutrition question — answer directly with numbers when possible.
- Unrelated questions: answer briefly, note you specialize in food + training nutrition."""


RECIPE_PROMPT_TEMPLATE = """You are a meal-planning assistant for an advanced lifter.

RESPOND ENTIRELY IN UKRAINIAN. Matter-of-fact tone, a tiny joke only if natural. No fluff.
No allergies, no dietary restrictions.

Training week: Tue/Thu/Sat weightlifting, Mon/Wed cardio.

His intake SO FAR TODAY:
{today_intake}

REMAINING for the day:
- Calories: {remaining_cal}
- Protein: {remaining_protein}g
- Carbs: {remaining_carbs}g
- Fat: {remaining_fat}g

Suggest ONE meal that fills the gap. Priorities in order:
1. Close the PROTEIN gap (this is the #1 lever on a cut)
2. Stay within remaining calories
3. Use simple, quick-to-cook ingredients

Format in Ukrainian as:

🍽️ <назва страви>

📝 Чому підходить: <1-2 речення — скільки білка закриває, чи вкладається в калорії, чи швидко готується>

🥘 Інгредієнти:
- <продукт> (<грами>)
- ...

👨‍🍳 Приготування:
1. ...
2. ...

📊 Орієнтовні макро: <ккал> ккал | <Б>г Б | <В>г В | <Ж>г Ж

Без зайвих слів. Мінімум емодзі."""
