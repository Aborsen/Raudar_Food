"""Postgres (Neon) database layer: connection, schema migration, and CRUD helpers."""
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg

from lib.config import DATABASE_URL, LOCAL_TZ


def get_conn():
    """Return a fresh psycopg3 connection. Call per invocation (serverless)."""
    return psycopg.connect(DATABASE_URL, autocommit=False)


def _now_iso() -> str:
    # Timestamps stay in UTC — absolute time, unambiguous in the DB.
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    # "Today" is a calendar concept — compute from Kyiv local so meals logged
    # between midnight Kyiv and midnight UTC don't fall on the wrong day.
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def init_db(conn=None) -> None:
    """Create tables if they don't exist. Idempotent — safe to call every request."""
    close_after = False
    if conn is None:
        conn = get_conn()
        close_after = True

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_logs (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                date TEXT,
                total_calories DOUBLE PRECISION DEFAULT 0,
                total_protein_g DOUBLE PRECISION DEFAULT 0,
                total_carbs_g DOUBLE PRECISION DEFAULT 0,
                total_fat_g DOUBLE PRECISION DEFAULT 0,
                total_fiber_g DOUBLE PRECISION DEFAULT 0,
                total_sugar_g DOUBLE PRECISION DEFAULT 0,
                summary_sent INTEGER DEFAULT 0,
                created_at TEXT,
                UNIQUE(user_id, date)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS meals (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                date TEXT,
                meal_type TEXT,
                description TEXT,
                ingredients TEXT,
                allergen_warnings TEXT,
                crohn_warnings TEXT,
                calories DOUBLE PRECISION,
                protein_g DOUBLE PRECISION,
                carbs_g DOUBLE PRECISION,
                fat_g DOUBLE PRECISION,
                fiber_g DOUBLE PRECISION,
                sugar_g DOUBLE PRECISION,
                photo_file_id TEXT,
                ai_raw_response TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_recommendations (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                date TEXT,
                recommendation TEXT,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_photos (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                photo_file_id TEXT,
                created_at TEXT
            )
        """)
        cur.execute("ALTER TABLE pending_photos ADD COLUMN IF NOT EXISTS text_description TEXT")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_analyses (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                meal_type TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                photo_file_id TEXT,
                text_description TEXT,
                raw_response TEXT,
                awaiting_manual INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_ts "
            "ON chat_sessions (user_id, created_at)"
        )
        cur.execute("ALTER TABLE meals ADD COLUMN IF NOT EXISTS is_favorite INTEGER DEFAULT 0")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS water_logs (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount_ml INTEGER NOT NULL,
                logged_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_water_user_logged "
            "ON water_logs(user_id, logged_at DESC)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS water_prefs (
                user_id BIGINT PRIMARY KEY,
                target_ml INTEGER NOT NULL DEFAULT 2500,
                updated_at TEXT
            )
        """)
    conn.commit()
    if close_after:
        try:
            conn.close()
        except Exception:
            pass


# ---------- Users ----------

def upsert_user(conn, user_id: int, username: Optional[str]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (user_id, username, created_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id) DO NOTHING",
            (user_id, username or "", _now_iso()),
        )
    conn.commit()


# ---------- Pending photos ----------

def save_pending_photo(conn, user_id: int, photo_file_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_photos WHERE user_id = %s", (user_id,))
        cur.execute(
            "INSERT INTO pending_photos (user_id, photo_file_id, text_description, created_at) "
            "VALUES (%s, %s, NULL, %s)",
            (user_id, photo_file_id, _now_iso()),
        )
    conn.commit()


def save_pending_text(conn, user_id: int, text_description: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_photos WHERE user_id = %s", (user_id,))
        cur.execute(
            "INSERT INTO pending_photos (user_id, photo_file_id, text_description, created_at) "
            "VALUES (%s, NULL, %s, %s)",
            (user_id, text_description, _now_iso()),
        )
    conn.commit()


def pop_pending_entry(conn, user_id: int) -> Optional[tuple[Optional[str], Optional[str]]]:
    """Return (photo_file_id, text_description) then delete all pending for user."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT photo_file_id, text_description FROM pending_photos "
            "WHERE user_id = %s ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        file_id, text = row[0], row[1]
        cur.execute("DELETE FROM pending_photos WHERE user_id = %s", (user_id,))
    conn.commit()
    return (file_id, text)


def cleanup_stale_pending(conn, minutes: int = 10) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_photos WHERE created_at < %s", (cutoff,))
    conn.commit()


# ---------- Pending analyses (moderation step) ----------

def save_pending_analysis(
    conn,
    user_id: int,
    meal_type: str,
    analysis: dict,
    photo_file_id: Optional[str],
    text_description: Optional[str],
    raw_response: str,
) -> None:
    """Store an AI analysis for user review. One row per user (replaces previous)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_analyses WHERE user_id = %s", (user_id,))
        cur.execute(
            """INSERT INTO pending_analyses
               (user_id, meal_type, analysis_json, photo_file_id, text_description,
                raw_response, awaiting_manual, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, 0, %s)""",
            (
                user_id,
                meal_type,
                json.dumps(analysis, ensure_ascii=False),
                photo_file_id,
                text_description,
                raw_response,
                _now_iso(),
            ),
        )
    conn.commit()


def get_pending_analysis(conn, user_id: int) -> Optional[dict]:
    """Non-destructive read of the user's pending analysis."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, meal_type, analysis_json, photo_file_id, text_description,
                      raw_response, awaiting_manual, created_at
               FROM pending_analyses WHERE user_id = %s ORDER BY id DESC LIMIT 1""",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "meal_type": row[1],
        "analysis": json.loads(row[2]),
        "photo_file_id": row[3],
        "text_description": row[4],
        "raw_response": row[5],
        "awaiting_manual": bool(row[6]),
        "created_at": row[7],
    }


def pop_pending_analysis(conn, user_id: int) -> Optional[dict]:
    """Read + delete the user's pending analysis."""
    entry = get_pending_analysis(conn, user_id)
    if entry is None:
        return None
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_analyses WHERE user_id = %s", (user_id,))
    conn.commit()
    return entry


def set_awaiting_manual(conn, user_id: int, meal_type: Optional[str] = None) -> None:
    """Flag the user's pending analysis as awaiting manual text input."""
    with conn.cursor() as cur:
        if meal_type:
            cur.execute(
                "UPDATE pending_analyses SET awaiting_manual = 1, meal_type = %s WHERE user_id = %s",
                (meal_type, user_id),
            )
        else:
            cur.execute(
                "UPDATE pending_analyses SET awaiting_manual = 1 WHERE user_id = %s",
                (user_id,),
            )
    conn.commit()


def cleanup_stale_analyses(conn, minutes: int = 10) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_analyses WHERE created_at < %s", (cutoff,))
    conn.commit()


# ---------- Chat sessions (multi-turn /ask history) ----------

def get_chat_history(conn, user_id: int, limit: int = 10, minutes: int = 60) -> list[dict]:
    """Return the user's recent chat messages (within `minutes`), oldest first.

    Shape matches what OpenAI expects: [{"role": "user"|"assistant", "content": "..."}].
    Rows older than `minutes` are treated as a new conversation.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT role, content FROM chat_sessions
               WHERE user_id = %s AND created_at >= %s
               ORDER BY id DESC LIMIT %s""",
            (user_id, cutoff, limit),
        )
        rows = cur.fetchall()
    # Fetched newest-first for the LIMIT; flip to chronological order for the LLM.
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def append_chat_message(conn, user_id: int, role: str, content: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_sessions (user_id, role, content, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, role, content, _now_iso()),
        )
    conn.commit()


def cleanup_stale_chat(conn, minutes: int = 60) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chat_sessions WHERE created_at < %s", (cutoff,))
    conn.commit()


# ---------- Meals ----------

def save_meal(
    conn,
    user_id: int,
    meal_type: str,
    analysis: dict,
    photo_file_id: str,
    raw_response: str,
) -> int:
    nutrition = analysis.get("nutrition", {}) or {}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO meals (
                user_id, date, meal_type, description, ingredients,
                allergen_warnings, crohn_warnings,
                calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g,
                photo_file_id, ai_raw_response, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id""",
            (
                user_id,
                _today_str(),
                meal_type,
                analysis.get("description") or analysis.get("dish_name", ""),
                json.dumps(analysis.get("ingredients", []), ensure_ascii=False),
                json.dumps(analysis.get("allergen_flags", []), ensure_ascii=False),
                json.dumps(analysis.get("crohn_flags", []), ensure_ascii=False),
                float(nutrition.get("calories", 0) or 0),
                float(nutrition.get("protein_g", 0) or 0),
                float(nutrition.get("carbs_g", 0) or 0),
                float(nutrition.get("fat_g", 0) or 0),
                float(nutrition.get("fiber_g", 0) or 0),
                float(nutrition.get("sugar_g", 0) or 0),
                photo_file_id,
                raw_response,
                _now_iso(),
            ),
        )
        meal_id = cur.fetchone()[0]
    conn.commit()
    return int(meal_id)


def get_meals_for_day(conn, user_id: int, date: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, meal_type, description, ingredients, allergen_warnings, crohn_warnings,
                      calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, created_at
               FROM meals WHERE user_id = %s AND date = %s ORDER BY id ASC""",
            (user_id, date),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "meal_type": r[1],
            "description": r[2],
            "ingredients": json.loads(r[3] or "[]"),
            "allergen_warnings": json.loads(r[4] or "[]"),
            "crohn_warnings": json.loads(r[5] or "[]"),
            "calories": r[6] or 0,
            "protein_g": r[7] or 0,
            "carbs_g": r[8] or 0,
            "fat_g": r[9] or 0,
            "fiber_g": r[10] or 0,
            "sugar_g": r[11] or 0,
            "created_at": r[12],
        }
        for r in rows
    ]


def delete_meal(conn, meal_id: int, user_id: int) -> Optional[dict]:
    """Delete a meal by ID (must belong to user). Returns its data for confirmation, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT meal_type, description, date, calories FROM meals WHERE id = %s AND user_id = %s",
            (meal_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        data = {"meal_type": row[0], "description": row[1], "date": row[2], "calories": row[3] or 0}
        cur.execute("DELETE FROM meals WHERE id = %s AND user_id = %s", (meal_id, user_id))
    conn.commit()
    return data


def recalc_daily_log(conn, user_id: int, date: str) -> None:
    """Recompute daily_logs totals from SUM of remaining meals. Delete row if no meals left."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(calories),0), COALESCE(SUM(protein_g),0),
                      COALESCE(SUM(carbs_g),0), COALESCE(SUM(fat_g),0),
                      COALESCE(SUM(fiber_g),0), COALESCE(SUM(sugar_g),0), COUNT(*)
               FROM meals WHERE user_id = %s AND date = %s""",
            (user_id, date),
        )
        row = cur.fetchone()
        if not row or row[6] == 0:
            cur.execute(
                "DELETE FROM daily_logs WHERE user_id = %s AND date = %s",
                (user_id, date),
            )
        else:
            cur.execute(
                """UPDATE daily_logs
                   SET total_calories = %s, total_protein_g = %s, total_carbs_g = %s,
                       total_fat_g = %s, total_fiber_g = %s, total_sugar_g = %s
                   WHERE user_id = %s AND date = %s""",
                (row[0], row[1], row[2], row[3], row[4], row[5], user_id, date),
            )
    conn.commit()


# ---------- Daily logs ----------

def upsert_daily_log_from_meal(conn, user_id: int, analysis: dict) -> None:
    """Insert today's row if needed, then increment totals from this meal."""
    today = _today_str()
    nutrition = analysis.get("nutrition", {}) or {}
    cal = float(nutrition.get("calories", 0) or 0)
    p = float(nutrition.get("protein_g", 0) or 0)
    c = float(nutrition.get("carbs_g", 0) or 0)
    f = float(nutrition.get("fat_g", 0) or 0)
    fib = float(nutrition.get("fiber_g", 0) or 0)
    sug = float(nutrition.get("sugar_g", 0) or 0)

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO daily_logs (user_id, date, total_calories, total_protein_g,
                                       total_carbs_g, total_fat_g, total_fiber_g, total_sugar_g,
                                       summary_sent, created_at)
               VALUES (%s, %s, 0, 0, 0, 0, 0, 0, 0, %s)
               ON CONFLICT (user_id, date) DO NOTHING""",
            (user_id, today, _now_iso()),
        )
        cur.execute(
            """UPDATE daily_logs
               SET total_calories = total_calories + %s,
                   total_protein_g = total_protein_g + %s,
                   total_carbs_g = total_carbs_g + %s,
                   total_fat_g = total_fat_g + %s,
                   total_fiber_g = total_fiber_g + %s,
                   total_sugar_g = total_sugar_g + %s
               WHERE user_id = %s AND date = %s""",
            (cal, p, c, f, fib, sug, user_id, today),
        )
    conn.commit()


def get_log_for_date(conn, user_id: int, date: str) -> dict:
    """Return the daily log for any date. Same shape as get_today_log."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT total_calories, total_protein_g, total_carbs_g, total_fat_g,
                      total_fiber_g, total_sugar_g
               FROM daily_logs WHERE user_id = %s AND date = %s""",
            (user_id, date),
        )
        row = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM meals WHERE user_id = %s AND date = %s",
            (user_id, date),
        )
        meal_count = (cur.fetchone() or (0,))[0]
    if not row:
        return {
            "date": date, "calories": 0, "protein": 0, "carbs": 0,
            "fat": 0, "fiber": 0, "sugar": 0, "meal_count": meal_count,
        }
    return {
        "date": date,
        "calories": row[0] or 0,
        "protein": row[1] or 0,
        "carbs": row[2] or 0,
        "fat": row[3] or 0,
        "fiber": row[4] or 0,
        "sugar": row[5] or 0,
        "meal_count": meal_count,
    }


def get_today_log(conn, user_id: int) -> dict:
    return get_log_for_date(conn, user_id, _today_str())


def get_history(conn, user_id: int, days: int = 7) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT date, total_calories, total_protein_g, total_carbs_g, total_fat_g
               FROM daily_logs WHERE user_id = %s
               ORDER BY date DESC LIMIT %s""",
            (user_id, days),
        )
        rows = cur.fetchall()
    return [
        {
            "date": r[0],
            "calories": r[1] or 0,
            "protein": r[2] or 0,
            "carbs": r[3] or 0,
            "fat": r[4] or 0,
        }
        for r in rows
    ]


# ---------- Summaries / recommendations ----------

def get_users_needing_summary(conn) -> list[tuple[int, str]]:
    today = _today_str()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT dl.user_id, dl.date
               FROM daily_logs dl
               WHERE dl.date = %s AND dl.summary_sent = 0
                 AND EXISTS (SELECT 1 FROM meals m WHERE m.user_id = dl.user_id AND m.date = dl.date)""",
            (today,),
        )
        rows = cur.fetchall()
    return [(r[0], r[1]) for r in rows]


def save_recommendation(conn, user_id: int, date: str, text: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO daily_recommendations (user_id, date, recommendation, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (user_id, date, text, _now_iso()),
        )
    conn.commit()


def get_latest_recommendation(conn, user_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, recommendation FROM daily_recommendations "
            "WHERE user_id = %s ORDER BY date DESC LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"date": row[0], "text": row[1]}


def get_recommendation_for_date(conn, user_id: int, date: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, recommendation FROM daily_recommendations "
            "WHERE user_id = %s AND date = %s ORDER BY id DESC LIMIT 1",
            (user_id, date),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"date": row[0], "text": row[1]}


def mark_summary_sent(conn, user_id: int, date: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE daily_logs SET summary_sent = 1 WHERE user_id = %s AND date = %s",
            (user_id, date),
        )
    conn.commit()


def mark_all_previous_summaries_sent(conn) -> None:
    today = _today_str()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE daily_logs SET summary_sent = 1 WHERE date < %s AND summary_sent = 0",
            (today,),
        )
    conn.commit()


# ---------- Favorites + Recent ----------

def toggle_favorite(conn, meal_id: int, user_id: int) -> Optional[bool]:
    """Flip is_favorite for a meal. Returns new bool state, or None if not owned."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_favorite FROM meals WHERE id = %s AND user_id = %s",
            (meal_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        new_val = 0 if row[0] else 1
        cur.execute(
            "UPDATE meals SET is_favorite = %s WHERE id = %s AND user_id = %s",
            (new_val, meal_id, user_id),
        )
    conn.commit()
    return bool(new_val)


def set_favorite(conn, meal_id: int, user_id: int, is_fav: bool) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE meals SET is_favorite = %s WHERE id = %s AND user_id = %s",
            (1 if is_fav else 0, meal_id, user_id),
        )
        ok = cur.rowcount > 0
    conn.commit()
    return ok


def get_meal_by_id(conn, meal_id: int, user_id: int) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, meal_type, description, ingredients, allergen_warnings, crohn_warnings,
                      calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g,
                      photo_file_id, ai_raw_response, is_favorite, date, created_at
               FROM meals WHERE id = %s AND user_id = %s""",
            (meal_id, user_id),
        )
        r = cur.fetchone()
    if not r:
        return None
    return {
        "id": r[0], "meal_type": r[1], "description": r[2],
        "ingredients": json.loads(r[3] or "[]"),
        "allergen_warnings": json.loads(r[4] or "[]"),
        "crohn_warnings": json.loads(r[5] or "[]"),
        "calories": r[6] or 0, "protein_g": r[7] or 0, "carbs_g": r[8] or 0,
        "fat_g": r[9] or 0, "fiber_g": r[10] or 0, "sugar_g": r[11] or 0,
        "photo_file_id": r[12], "ai_raw_response": r[13],
        "is_favorite": bool(r[14]), "date": r[15], "created_at": r[16],
    }


def get_recent_meals(conn, user_id: int, limit: int = 10) -> list[dict]:
    """Up to `limit` most recent meals, deduplicated by lowercased description."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (LOWER(COALESCE(description, '')))
                      id, meal_type, description, calories, protein_g, carbs_g, fat_g,
                      is_favorite, created_at
               FROM meals
               WHERE user_id = %s AND description IS NOT NULL AND description != ''
               ORDER BY LOWER(COALESCE(description, '')), created_at DESC""",
            (user_id,),
        )
        rows = cur.fetchall()
    results = [
        {
            "id": r[0], "meal_type": r[1], "description": r[2],
            "calories": r[3] or 0, "protein_g": r[4] or 0,
            "carbs_g": r[5] or 0, "fat_g": r[6] or 0,
            "is_favorite": bool(r[7]), "created_at": r[8],
        }
        for r in rows
    ]
    results.sort(key=lambda m: m["created_at"] or "", reverse=True)
    return results[:limit]


def get_favorites(conn, user_id: int, limit: int = 20) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (LOWER(COALESCE(description, '')))
                      id, meal_type, description, calories, protein_g, carbs_g, fat_g,
                      is_favorite, created_at
               FROM meals
               WHERE user_id = %s AND is_favorite = 1
                 AND description IS NOT NULL AND description != ''
               ORDER BY LOWER(COALESCE(description, '')), created_at DESC""",
            (user_id,),
        )
        rows = cur.fetchall()
    results = [
        {
            "id": r[0], "meal_type": r[1], "description": r[2],
            "calories": r[3] or 0, "protein_g": r[4] or 0,
            "carbs_g": r[5] or 0, "fat_g": r[6] or 0,
            "is_favorite": True, "created_at": r[8],
        }
        for r in rows
    ]
    results.sort(key=lambda m: m["created_at"] or "", reverse=True)
    return results[:limit]


def clone_meal_for_today(conn, meal_id: int, user_id: int, meal_type: str) -> Optional[int]:
    """Copy an existing meal into today with a new meal_type. Returns new id."""
    src = get_meal_by_id(conn, meal_id, user_id)
    if not src:
        return None
    today = _today_str()
    now = _now_iso()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO meals (
                user_id, date, meal_type, description, ingredients,
                allergen_warnings, crohn_warnings,
                calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g,
                photo_file_id, ai_raw_response, is_favorite, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id""",
            (
                user_id, today, meal_type, src["description"],
                json.dumps(src["ingredients"], ensure_ascii=False),
                json.dumps(src["allergen_warnings"], ensure_ascii=False),
                json.dumps(src["crohn_warnings"], ensure_ascii=False),
                src["calories"], src["protein_g"], src["carbs_g"],
                src["fat_g"], src["fiber_g"], src["sugar_g"],
                src["photo_file_id"], src["ai_raw_response"],
                0, now,
            ),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    recalc_daily_log(conn, user_id, today)
    return new_id


# ---------- Water tracking ----------

WATER_PRESETS = (200, 250, 300, 500, 750)
WATER_TARGET_DEFAULT = 2500
WATER_TARGET_MIN = 1500
WATER_TARGET_MAX = 4000


def _clamp_water_target(ml: int) -> int:
    return max(WATER_TARGET_MIN, min(WATER_TARGET_MAX, int(ml)))


def get_water_target(conn, user_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT target_ml FROM water_prefs WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    return int(row[0]) if row else WATER_TARGET_DEFAULT


def set_water_target(conn, user_id: int, target_ml: int) -> int:
    clamped = _clamp_water_target(target_ml)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO water_prefs (user_id, target_ml, updated_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (user_id) DO UPDATE SET
                   target_ml = EXCLUDED.target_ml,
                   updated_at = EXCLUDED.updated_at""",
            (user_id, clamped, _now_iso()),
        )
    conn.commit()
    return clamped


def add_water(conn, user_id: int, amount_ml: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO water_logs (user_id, amount_ml) VALUES (%s, %s)",
            (user_id, int(amount_ml)),
        )
    conn.commit()
    return get_water_today(conn, user_id)


def remove_last_water_today(conn, user_id: int) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM water_logs
               WHERE id = (
                 SELECT id FROM water_logs
                 WHERE user_id = %s
                   AND (logged_at AT TIME ZONE 'Europe/Kyiv')::date
                       = (now() AT TIME ZONE 'Europe/Kyiv')::date
                 ORDER BY logged_at DESC LIMIT 1
               )""",
            (user_id,),
        )
        deleted = cur.rowcount > 0
    conn.commit()
    if not deleted:
        return None
    return get_water_today(conn, user_id)


def get_water_today(conn, user_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(amount_ml), 0) FROM water_logs
               WHERE user_id = %s
                 AND (logged_at AT TIME ZONE 'Europe/Kyiv')::date
                     = (now() AT TIME ZONE 'Europe/Kyiv')::date""",
            (user_id,),
        )
        row = cur.fetchone()
    return int(row[0] or 0)


def get_water_for_date(conn, user_id: int, date_str: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(SUM(amount_ml), 0) FROM water_logs
               WHERE user_id = %s
                 AND (logged_at AT TIME ZONE 'Europe/Kyiv')::date = %s::date""",
            (user_id, date_str),
        )
        row = cur.fetchone()
    return int(row[0] or 0)
