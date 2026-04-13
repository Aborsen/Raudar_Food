"""Postgres (Neon) database layer: connection, schema migration, and CRUD helpers."""
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg

from lib.config import DATABASE_URL


def get_conn():
    """Return a fresh psycopg3 connection. Call per invocation (serverless)."""
    return psycopg.connect(DATABASE_URL, autocommit=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
        # Migration: text-entry support. Add optional text_description column.
        cur.execute("""
            ALTER TABLE pending_photos
            ADD COLUMN IF NOT EXISTS text_description TEXT
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
    """Return (photo_file_id, text_description) for the most recent pending entry, then delete all.

    Exactly one of the two fields will be non-null.
    Returns None if nothing is pending.
    """
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


# Backwards-compat alias (not used after the text-entry refactor).
def pop_pending_photo(conn, user_id: int) -> Optional[str]:
    result = pop_pending_entry(conn, user_id)
    if result is None:
        return None
    return result[0]


def cleanup_stale_pending(conn, minutes: int = 10) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM pending_photos WHERE created_at < %s", (cutoff,))
    conn.commit()


# ---------- Meals ----------

def save_meal(
    conn,
    user_id: int,
    meal_type: str,
    analysis: dict,
    photo_file_id: str,
    raw_response: str,
) -> None:
    nutrition = analysis.get("nutrition", {}) or {}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO meals (
                user_id, date, meal_type, description, ingredients,
                allergen_warnings, crohn_warnings,
                calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g,
                photo_file_id, ai_raw_response, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                user_id,
                _today_str(),
                meal_type,
                analysis.get("description") or analysis.get("dish_name", ""),
                json.dumps(analysis.get("ingredients", [])),
                json.dumps(analysis.get("allergen_flags", [])),
                json.dumps(analysis.get("crohn_flags", [])),
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
    conn.commit()


def get_meals_for_day(conn, user_id: int, date: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT meal_type, description, ingredients, allergen_warnings, crohn_warnings,
                      calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g, created_at
               FROM meals WHERE user_id = %s AND date = %s ORDER BY id ASC""",
            (user_id, date),
        )
        rows = cur.fetchall()
    return [
        {
            "meal_type": r[0],
            "description": r[1],
            "ingredients": json.loads(r[2] or "[]"),
            "allergen_warnings": json.loads(r[3] or "[]"),
            "crohn_warnings": json.loads(r[4] or "[]"),
            "calories": r[5] or 0,
            "protein_g": r[6] or 0,
            "carbs_g": r[7] or 0,
            "fat_g": r[8] or 0,
            "fiber_g": r[9] or 0,
            "sugar_g": r[10] or 0,
            "created_at": r[11],
        }
        for r in rows
    ]


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


def get_today_log(conn, user_id: int) -> dict:
    today = _today_str()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT total_calories, total_protein_g, total_carbs_g, total_fat_g,
                      total_fiber_g, total_sugar_g
               FROM daily_logs WHERE user_id = %s AND date = %s""",
            (user_id, today),
        )
        row = cur.fetchone()
        cur.execute(
            "SELECT COUNT(*) FROM meals WHERE user_id = %s AND date = %s",
            (user_id, today),
        )
        meal_count = (cur.fetchone() or (0,))[0]
    if not row:
        return {
            "date": today, "calories": 0, "protein": 0, "carbs": 0,
            "fat": 0, "fiber": 0, "sugar": 0, "meal_count": meal_count,
        }
    return {
        "date": today,
        "calories": row[0] or 0,
        "protein": row[1] or 0,
        "carbs": row[2] or 0,
        "fat": row[3] or 0,
        "fiber": row[4] or 0,
        "sugar": row[5] or 0,
        "meal_count": meal_count,
    }


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
    """Users with meals today and no summary yet. Returns [(user_id, date)]."""
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
