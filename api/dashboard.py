"""Telegram Mini App: read-only per-user dashboard.

Served at /api/dashboard. Opened from the bot's reply-keyboard "Dashboard" button
(which sets `web_app.url` to this endpoint). Telegram passes signed `initData`
which this handler verifies via HMAC-SHA256 using the bot token as the secret
seed — the canonical Telegram Web App auth flow.

Shows: today's progress bars, today's meals, last 7 days history. Read-only.
"""
import hashlib
import hmac
import html
import json
import os
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.config import (
    ALLOWED_USER_IDS,
    DAILY_CAL_TARGET,
    MACRO_GRAM_TARGETS,
    TELEGRAM_BOT_TOKEN,
)
from lib.database import (
    get_conn,
    init_db,
    get_today_log,
    get_meals_for_day,
    get_history,
)


# 24-hour max age for initData, per Telegram's recommendation
INIT_DATA_MAX_AGE = 24 * 60 * 60

_SECURITY_HEADERS = [
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    # Miniapps are loaded in Telegram's webview, so frame-ancestors must allow it.
    # Note: we intentionally DO NOT set X-Frame-Options here — Telegram must iframe us.
    (
        "Content-Security-Policy",
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' https://telegram.org; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors https://web.telegram.org https://t.me",
    ),
]


def _verify_init_data(init_data: str) -> dict | None:
    """Validate Telegram WebApp initData. Returns parsed user dict on success.

    Implements https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    Uses constant-time comparison for the HMAC check.
    """
    if not init_data or not TELEGRAM_BOT_TOKEN:
        return None

    # Parse as query string (keys are URL-encoded)
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    params = dict(pairs)

    received_hash = params.pop("hash", None)
    if not received_hash:
        return None

    # Check auth_date freshness — reject stale tokens
    try:
        auth_date = int(params.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date <= 0 or time.time() - auth_date > INIT_DATA_MAX_AGE:
        return None

    # Build data_check_string: sorted lines of "key=value", \n-joined
    data_check_string = "\n".join(
        f"{k}={params[k]}" for k in sorted(params.keys())
    )

    # secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        TELEGRAM_BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    # Parse the user JSON
    user_json = params.get("user", "")
    if not user_json:
        return None
    try:
        user = json.loads(user_json)
    except Exception:
        return None

    user_id = user.get("id")
    if not isinstance(user_id, int):
        return None

    # Additional whitelist check (defense-in-depth; normally enforced by the bot anyway)
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return None

    return user


class handler(BaseHTTPRequestHandler):
    def _apply_security_headers(self):
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)

    def _send_html(self, code: int, body: str):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        init_data_values = qs.get("initData") or []
        init_data = init_data_values[0] if init_data_values else ""

        # First load: no initData yet. Serve a bootstrap page whose JS grabs
        # window.Telegram.WebApp.initData and re-navigates with it as a query
        # parameter. Second load: server renders the dashboard.
        if not init_data:
            self._send_html(200, _BOOTSTRAP_HTML)
            return

        user = _verify_init_data(init_data)
        if user is None:
            self._send_html(401, _unauthorized_html())
            return

        try:
            body = _render_dashboard(user)
        except Exception:
            print("dashboard render error:", traceback.format_exc(), flush=True)
            body = "<pre>Dashboard error (see logs)</pre>"
        self._send_html(200, body)


# ------------------------------------------------------------------ HTML --

_BOOTSTRAP_HTML = """<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Food Tracker</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  body { margin: 0; padding: 40px 20px; font-family: -apple-system, system-ui, sans-serif;
         background: #0f0f1a; color: #e0e0e0; text-align: center; }
  .spinner { width: 32px; height: 32px; margin: 20px auto; border: 3px solid #16213e;
             border-top-color: #e94560; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="spinner"></div>
<p>Завантаження dashboard…</p>
<script>
(function(){
  var tg = window.Telegram && window.Telegram.WebApp;
  if (!tg || !tg.initData) {
    document.body.innerHTML = '<p>Помилка: цю сторінку треба відкривати з Telegram.</p>';
    return;
  }
  tg.ready();
  tg.expand && tg.expand();
  // Re-navigate with initData as a query string so the server can verify it.
  var url = new URL(window.location.href);
  url.searchParams.set('initData', tg.initData);
  window.location.replace(url.toString());
})();
</script>
</body>
</html>"""


def _unauthorized_html() -> str:
    return """<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Food Tracker</title>
<style>
  body { margin: 0; padding: 40px 20px; font-family: -apple-system, system-ui, sans-serif;
         background: #0f0f1a; color: #e0e0e0; text-align: center; }
  h1 { color: #e94560; }
</style></head>
<body>
<h1>🔒 Доступ заборонено</h1>
<p>Не вдалося підтвердити ідентичність у Telegram. Спробуй закрити і відкрити знову з бота.</p>
</body></html>"""


def _esc(s) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def _bar(value: float, target: float, width: int = 20) -> str:
    if target <= 0:
        return "░" * width
    ratio = max(0.0, min(1.0, value / target))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


_MEAL_TYPE_UA = {
    "breakfast": "🍳 Сніданок",
    "lunch": "🥗 Обід",
    "dinner": "🍽️ Вечеря",
    "snack": "🍎 Перекус",
}


def _aggregate(rows: list[dict]) -> dict:
    """Sum + average totals over history rows (for week/month views)."""
    if not rows:
        return {"days": 0, "avg_cal": 0, "avg_p": 0, "avg_c": 0, "avg_f": 0,
                "total_cal": 0, "total_p": 0, "total_c": 0, "total_f": 0}
    n = len(rows)
    tc = sum(r.get("calories", 0) for r in rows)
    tp = sum(r.get("protein", 0) for r in rows)
    tca = sum(r.get("carbs", 0) for r in rows)
    tf = sum(r.get("fat", 0) for r in rows)
    return {
        "days": n,
        "avg_cal": round(tc / n), "avg_p": round(tp / n),
        "avg_c": round(tca / n), "avg_f": round(tf / n),
        "total_cal": round(tc), "total_p": round(tp),
        "total_c": round(tca), "total_f": round(tf),
    }


def _render_history_table(rows: list[dict]) -> str:
    if not rows:
        return "<tr><td colspan='5' class='empty'>Історії ще немає.</td></tr>"
    out = ""
    for h in rows:
        out += (
            f"<tr>"
            f"<td>{_esc(h.get('date',''))}</td>"
            f"<td class='num'>{round(h.get('calories',0))}</td>"
            f"<td class='num'>{round(h.get('protein',0))}</td>"
            f"<td class='num'>{round(h.get('carbs',0))}</td>"
            f"<td class='num'>{round(h.get('fat',0))}</td>"
            f"</tr>"
        )
    return out


def _render_dashboard(user: dict) -> str:
    user_id = user["id"]
    first_name = user.get("first_name") or "друже"

    conn = get_conn()
    try:
        init_db(conn)
        log = get_today_log(conn, user_id)
        today_meals = get_meals_for_day(conn, user_id, log["date"])
        week = get_history(conn, user_id, days=7)
        month = get_history(conn, user_id, days=30)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    cal = log.get("calories") or 0
    p = log.get("protein") or 0
    c = log.get("carbs") or 0
    f = log.get("fat") or 0
    meal_count = log.get("meal_count") or 0
    week_agg = _aggregate(week)
    month_agg = _aggregate(month)

    # Today's meals list — each meal tagged with data attributes for client-side filtering
    meals_rows = ""
    if today_meals:
        for m in today_meals:
            mt = m.get("meal_type", "") or ""
            desc = (m.get("description") or "")
            allergen_count = len(m.get("allergen_warnings") or [])
            crohn_count = len(m.get("crohn_warnings") or [])
            badges = ""
            if allergen_count:
                badges += f"<span class='badge badge-allergen' title='Алергени'>⚠️ {allergen_count}</span>"
            if crohn_count:
                badges += f"<span class='badge badge-crohn' title='Крон'>🩺 {crohn_count}</span>"
            meals_rows += (
                f"<div class='meal' data-type='{_esc(mt)}' "
                f"data-desc='{_esc(desc.lower())}' "
                f"data-has-allergen='{1 if allergen_count else 0}' "
                f"data-has-crohn='{1 if crohn_count else 0}'>"
                f"<div class='meal-head'>{_MEAL_TYPE_UA.get(mt, mt)}"
                f" · <span class='kcal'>{round(m.get('calories',0))} ккал</span> {badges}</div>"
                f"<div class='meal-desc'>{_esc(desc[:160])}</div>"
                f"<div class='meal-macros'>Б {round(m.get('protein_g',0))}г · "
                f"В {round(m.get('carbs_g',0))}г · "
                f"Ж {round(m.get('fat_g',0))}г</div>"
                f"</div>"
            )
    else:
        meals_rows = "<p class='empty'>Сьогодні ще нічого не записано.</p>"

    week_rows = _render_history_table(week)
    month_rows = _render_history_table(month)

    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Food Tracker</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 16px; font-family: -apple-system, system-ui, 'Segoe UI', sans-serif;
         background: #0f0f1a; color: #e0e0e0; font-size: 15px; line-height: 1.45; }}
  h1 {{ margin: 0 0 4px; color: #e94560; font-size: 1.4em; }}
  .sub {{ color: #888; font-size: 0.9em; margin-bottom: 18px; }}

  .tabs {{ display: flex; gap: 6px; background: #16213e; padding: 4px; border-radius: 10px;
          margin-bottom: 14px; }}
  .tab {{ flex: 1; padding: 8px 10px; border: none; background: transparent; color: #bdbdd0;
         font-size: 0.95em; border-radius: 7px; cursor: pointer; font-family: inherit; }}
  .tab.active {{ background: #e94560; color: #fff; font-weight: 600; }}
  .tab:not(.active):hover {{ background: #1e2e52; }}

  .view {{ display: none; }}
  .view.active {{ display: block; }}

  .card {{ background: #16213e; border-radius: 12px; padding: 14px 16px; margin-bottom: 14px; }}
  .card h2 {{ margin: 0 0 10px; font-size: 1.05em; color: #e0e0e0; }}

  .macro {{ margin: 8px 0; }}
  .macro-label {{ display: flex; justify-content: space-between; font-size: 0.9em;
                 color: #bdbdd0; margin-bottom: 3px; }}
  .macro-label b {{ color: #e0e0e0; }}
  .bar {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.85em;
         color: #e94560; letter-spacing: -1px; }}

  .meal {{ padding: 10px 0; border-bottom: 1px solid #1e1e3a; }}
  .meal:last-child {{ border-bottom: none; }}
  .meal-head {{ font-weight: 600; margin-bottom: 2px; }}
  .meal-head .kcal {{ color: #888; font-weight: 400; font-size: 0.9em; }}
  .meal-desc {{ color: #bdbdd0; font-size: 0.9em; margin-bottom: 2px; }}
  .meal-macros {{ color: #888; font-size: 0.8em; }}

  .badge {{ display: inline-block; font-size: 0.7em; padding: 1px 6px; border-radius: 6px;
           margin-left: 4px; vertical-align: middle; font-weight: 500; }}
  .badge-allergen {{ background: #4a1e2a; color: #ff6b7f; }}
  .badge-crohn {{ background: #4a3a1e; color: #ffbb5b; }}

  .filter-bar {{ margin-bottom: 12px; }}
  .filter-bar input[type="search"] {{
    width: 100%; background: #0f0f1a; color: #e0e0e0; border: 1px solid #2a2a4a;
    padding: 8px 12px; border-radius: 8px; font-size: 0.9em; margin-bottom: 8px;
    font-family: inherit;
  }}
  .filter-bar input[type="search"]::placeholder {{ color: #666; }}
  .filter-bar input[type="search"]:focus {{ outline: none; border-color: #e94560; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chip {{ padding: 4px 10px; border-radius: 14px; background: #0f0f1a; color: #bdbdd0;
         font-size: 0.8em; cursor: pointer; border: 1px solid #2a2a4a;
         font-family: inherit; user-select: none; }}
  .chip.active {{ background: #e94560; color: #fff; border-color: #e94560; }}
  .chip.toggle.active {{ background: #4a1e2a; color: #ff6b7f; border-color: #ff6b7f; }}
  .chip.toggle-crohn.active {{ background: #4a3a1e; color: #ffbb5b; border-color: #ffbb5b; }}

  .filter-count {{ font-size: 0.8em; color: #666; margin-top: 6px; }}

  .stats {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 10px; }}
  .stat {{ background: #0f0f1a; padding: 10px; border-radius: 8px; text-align: center; }}
  .stat .v {{ font-size: 1.3em; font-weight: 600; color: #e94560; }}
  .stat .l {{ font-size: 0.75em; color: #888; margin-top: 2px; text-transform: uppercase; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
  th, td {{ padding: 6px 8px; text-align: left; border-bottom: 1px solid #1e1e3a; }}
  th {{ color: #888; font-weight: 500; font-size: 0.8em; text-transform: uppercase; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}

  .empty {{ color: #666; text-align: center; padding: 12px; }}
</style>
</head>
<body>

<h1>👋 Привіт, {_esc(first_name)}!</h1>
<p class="sub">Твій прогрес · тільки для перегляду</p>

<div class="tabs" role="tablist">
  <button class="tab active" data-view="day" role="tab">День</button>
  <button class="tab" data-view="week" role="tab">7 днів</button>
  <button class="tab" data-view="month" role="tab">30 днів</button>
</div>

<!-- ============ DAY VIEW ============ -->
<div class="view active" data-view="day">
  <div class="card">
    <h2>📊 Сьогодні ({_esc(log.get('date',''))})</h2>
    <div class="macro">
      <div class="macro-label"><span>Калорії</span><b>{round(cal)} / {DAILY_CAL_TARGET} ккал</b></div>
      <div class="bar">{_bar(cal, DAILY_CAL_TARGET)}</div>
    </div>
    <div class="macro">
      <div class="macro-label"><span>Білки</span><b>{round(p)} / {MACRO_GRAM_TARGETS['protein']} г</b></div>
      <div class="bar">{_bar(p, MACRO_GRAM_TARGETS['protein'])}</div>
    </div>
    <div class="macro">
      <div class="macro-label"><span>Вуглеводи</span><b>{round(c)} / {MACRO_GRAM_TARGETS['carbs']} г</b></div>
      <div class="bar">{_bar(c, MACRO_GRAM_TARGETS['carbs'])}</div>
    </div>
    <div class="macro">
      <div class="macro-label"><span>Жири</span><b>{round(f)} / {MACRO_GRAM_TARGETS['fat']} г</b></div>
      <div class="bar">{_bar(f, MACRO_GRAM_TARGETS['fat'])}</div>
    </div>
    <p class="sub" style="margin-top:10px">Страв записано: {meal_count}</p>
  </div>

  <div class="card">
    <h2>🍽️ Страви сьогодні</h2>
    <div class="filter-bar">
      <input type="search" id="mealSearch" placeholder="🔍 Пошук по опису…">
      <div class="chips" id="mealTypeChips">
        <button class="chip active" data-type="">Всі</button>
        <button class="chip" data-type="breakfast">🍳 Сніданок</button>
        <button class="chip" data-type="lunch">🥗 Обід</button>
        <button class="chip" data-type="dinner">🍽️ Вечеря</button>
        <button class="chip" data-type="snack">🍎 Перекус</button>
      </div>
      <div class="chips" style="margin-top: 6px;">
        <button class="chip toggle" data-flag="allergen">⚠️ Тільки з алергенами</button>
        <button class="chip toggle-crohn" data-flag="crohn">🩺 Тільки з Крон-прапорами</button>
      </div>
      <div class="filter-count" id="mealFilterCount"></div>
    </div>
    <div id="mealsList">{meals_rows}</div>
  </div>
</div>

<!-- ============ WEEK VIEW ============ -->
<div class="view" data-view="week">
  <div class="card">
    <h2>📅 Останні 7 днів</h2>
    <div class="stats">
      <div class="stat"><div class="v">{week_agg['days']}</div><div class="l">днів з даними</div></div>
      <div class="stat"><div class="v">{week_agg['avg_cal']}</div><div class="l">сер. ккал/день</div></div>
      <div class="stat"><div class="v">{week_agg['avg_p']}</div><div class="l">сер. білків (г)</div></div>
      <div class="stat"><div class="v">{week_agg['avg_c']}</div><div class="l">сер. вуглеводів (г)</div></div>
    </div>
    <table>
      <thead><tr>
        <th>Дата</th>
        <th class="num">ккал</th>
        <th class="num">Б</th>
        <th class="num">В</th>
        <th class="num">Ж</th>
      </tr></thead>
      <tbody>{week_rows}</tbody>
    </table>
  </div>
</div>

<!-- ============ MONTH VIEW ============ -->
<div class="view" data-view="month">
  <div class="card">
    <h2>📅 Останні 30 днів</h2>
    <div class="stats">
      <div class="stat"><div class="v">{month_agg['days']}</div><div class="l">днів з даними</div></div>
      <div class="stat"><div class="v">{month_agg['avg_cal']}</div><div class="l">сер. ккал/день</div></div>
      <div class="stat"><div class="v">{month_agg['avg_p']}</div><div class="l">сер. білків (г)</div></div>
      <div class="stat"><div class="v">{month_agg['avg_c']}</div><div class="l">сер. вуглеводів (г)</div></div>
    </div>
    <table>
      <thead><tr>
        <th>Дата</th>
        <th class="num">ккал</th>
        <th class="num">Б</th>
        <th class="num">В</th>
        <th class="num">Ж</th>
      </tr></thead>
      <tbody>{month_rows}</tbody>
    </table>
  </div>
</div>

<script>
  if (window.Telegram && window.Telegram.WebApp) {{
    window.Telegram.WebApp.ready();
    window.Telegram.WebApp.expand();
  }}
  document.querySelectorAll('.tab').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var target = btn.dataset.view;
      document.querySelectorAll('.tab').forEach(function(b) {{ b.classList.toggle('active', b.dataset.view === target); }});
      document.querySelectorAll('.view').forEach(function(v) {{ v.classList.toggle('active', v.dataset.view === target); }});
    }});
  }});

  /* --- Day view meal filters --- */
  (function() {{
    var activeType = '';
    var onlyAllergen = false;
    var onlyCrohn = false;
    var searchEl = document.getElementById('mealSearch');
    var countEl = document.getElementById('mealFilterCount');
    var meals = Array.from(document.querySelectorAll('#mealsList .meal'));
    if (meals.length === 0) return;

    function apply() {{
      var q = (searchEl.value || '').toLowerCase().trim();
      var visible = 0;
      meals.forEach(function(m) {{
        var show = true;
        if (activeType && m.dataset.type !== activeType) show = false;
        if (onlyAllergen && m.dataset.hasAllergen !== '1') show = false;
        if (onlyCrohn && m.dataset.hasCrohn !== '1') show = false;
        if (q && m.dataset.desc.indexOf(q) === -1) show = false;
        m.style.display = show ? '' : 'none';
        if (show) visible++;
      }});
      countEl.textContent = 'Показано ' + visible + ' / ' + meals.length;
    }}

    document.querySelectorAll('#mealTypeChips .chip').forEach(function(chip) {{
      chip.addEventListener('click', function() {{
        activeType = chip.dataset.type;
        document.querySelectorAll('#mealTypeChips .chip').forEach(function(c) {{
          c.classList.toggle('active', c === chip);
        }});
        apply();
      }});
    }});
    document.querySelectorAll('.chip.toggle, .chip.toggle-crohn').forEach(function(chip) {{
      chip.addEventListener('click', function() {{
        var isActive = chip.classList.toggle('active');
        if (chip.dataset.flag === 'allergen') onlyAllergen = isActive;
        if (chip.dataset.flag === 'crohn') onlyCrohn = isActive;
        apply();
      }});
    }});
    searchEl.addEventListener('input', apply);
    apply();
  }})();
</script>
</body></html>"""
