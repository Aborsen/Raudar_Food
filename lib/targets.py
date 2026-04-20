"""Per-user daily calorie + macro targets.

Computed from current weight + chosen fitness goal using the spreadsheet-derived
grams-per-kg ratios in MACRO_GRAMS_PER_KG. Falls back to the static USER_PROFILE
weight and "maintain" goal when a user has no settings logged yet.
"""
from lib.config import MACRO_GRAMS_PER_KG, USER_PROFILE


def compute_targets(weight_kg: float, goal: str) -> dict:
    """Return {'calories', 'protein', 'carbs', 'fat', 'weight_kg', 'goal'}.

    All macro values are grams; calories is total kcal. Rounded to nearest int.
    """
    gpk = MACRO_GRAMS_PER_KG.get(goal) or MACRO_GRAMS_PER_KG["maintain"]
    w = float(weight_kg)
    protein_g = round(w * gpk["protein"])
    fat_g = round(w * gpk["fat"])
    carbs_g = round(w * gpk["carbs"])
    calories = round(protein_g * 4 + fat_g * 9 + carbs_g * 4)
    return {
        "calories": calories,
        "protein": protein_g,
        "carbs": carbs_g,
        "fat": fat_g,
        "weight_kg": w,
        "goal": goal if goal in MACRO_GRAMS_PER_KG else "maintain",
    }


def get_user_targets(conn, user_id: int) -> dict:
    """Fetch user's current weight + goal from the users table and compute targets.
    Missing fields fall back to USER_PROFILE defaults + 'maintain'.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT weight_kg, fitness_goal FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    weight = (row[0] if row and row[0] else USER_PROFILE["weight_kg"])
    goal = (row[1] if row and row[1] else "maintain")
    return compute_targets(float(weight), goal)
