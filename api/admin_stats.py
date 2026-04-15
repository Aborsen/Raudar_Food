"""Admin stats dashboard — view in browser with HTTP Basic Auth."""
import base64
import hmac
import html
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.config import CRON_SECRET
from lib.database import get_conn, init_db, delete_meal, recalc_daily_log


# Security headers applied to every response.
# CSP allows 'unsafe-inline' because the dashboard uses inline styles/scripts.
_SECURITY_HEADERS = [
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'none'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'",
    ),
]


def _authorized(headers) -> bool:
    """Authenticate via either Bearer token (curl) or HTTP Basic Auth (browser).

    Fails closed when CRON_SECRET is not configured — refuses to serve
    rather than exposing the dashboard on an unconfigured deployment.
    Uses constant-time comparison to resist timing attacks.
    """
    if not CRON_SECRET:
        return False

    auth = headers.get("Authorization", "")
    if not auth:
        return False

    expected_bearer = f"Bearer {CRON_SECRET}"
    if hmac.compare_digest(auth, expected_bearer):
        return True

    # Basic auth: any username, password must match CRON_SECRET.
    # Accepted so browsers can prompt the user and cache credentials without
    # putting the token in the URL (which would leak via history/logs/referer).
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8", errors="replace")
        except Exception:
            return False
        _, _, password = decoded.partition(":")
        if password and hmac.compare_digest(password, CRON_SECRET):
            return True

    return False


def _same_origin(headers) -> bool:
    """CSRF check: require Origin header to match the request host.

    Browsers always send Origin on cross-origin POSTs and on same-origin
    POSTs with fetch/XHR, so an absent Origin on state-changing requests
    is suspicious and rejected.
    """
    origin = headers.get("Origin", "")
    host = headers.get("Host", "")
    if not origin or not host:
        return False
    # Expected origin is https://<host> (Vercel enforces HTTPS).
    return origin == f"https://{host}"


class handler(BaseHTTPRequestHandler):
    def _apply_security_headers(self):
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)

    def _send_unauthorized(self, body: bytes = b"Unauthorized"):
        self.send_response(401)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("WWW-Authenticate", 'Basic realm="Food Admin", charset="UTF-8"')
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not _authorized(self.headers):
            self._send_unauthorized(
                b"Unauthorized. Authenticate via HTTP Basic Auth (any username, "
                b"password = CRON_SECRET) or Authorization: Bearer <CRON_SECRET>."
            )
            return

        try:
            body = build_html()
        except Exception:
            print("admin_stats error:", traceback.format_exc(), flush=True)
            body = "<pre>Error rendering dashboard (see logs)</pre>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        """Handle admin actions (delete meal) via AJAX."""
        if not _authorized(self.headers):
            self._send_unauthorized(b'{"ok": false, "error": "unauthorized"}')
            return

        # CSRF: require same-origin Origin header on state-changing POSTs.
        if not _same_origin(self.headers):
            self._json_response(403, {"ok": False, "error": "origin mismatch"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        # Admin actions send tiny JSON payloads — cap at 8 KB.
        if length > 8 * 1024:
            self._json_response(413, {"ok": False, "error": "payload too large"})
            return

        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            self._json_response(400, {"ok": False, "error": "bad json"})
            return

        action = body.get("action")
        if action == "delete_meal":
            meal_id = body.get("meal_id")
            user_id = body.get("user_id")
            if not meal_id or not user_id:
                self._json_response(400, {"ok": False, "error": "meal_id and user_id required"})
                return
            conn = get_conn()
            try:
                init_db(conn)
                deleted = delete_meal(conn, int(meal_id), int(user_id))
                if not deleted:
                    self._json_response(404, {"ok": False, "error": "meal not found"})
                    return
                recalc_daily_log(conn, int(user_id), deleted["date"])
                self._json_response(200, {"ok": True, "deleted": deleted["description"][:60]})
            except Exception as e:
                print("admin delete error:", traceback.format_exc(), flush=True)
                self._json_response(500, {"ok": False, "error": str(e)})
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        else:
            self._json_response(400, {"ok": False, "error": f"unknown action: {action}"})

    def _json_response(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._apply_security_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def _esc(s) -> str:
    """HTML-escape including quotes — safe for attribute interpolation."""
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def build_html() -> str:
    conn = get_conn()
    try:
        init_db(conn)
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM users")
        n_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM meals")
        n_meals = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM daily_logs")
        n_days = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM daily_recommendations")
        n_recs = cur.fetchone()[0]

        cur.execute("""
            SELECT u.user_id, COALESCE(u.username, ''), u.created_at,
                   COUNT(m.id) AS meals, MAX(m.created_at) AS last_meal
            FROM users u LEFT JOIN meals m ON m.user_id = u.user_id
            GROUP BY u.user_id, u.username, u.created_at
            ORDER BY meals DESC, u.created_at DESC
        """)
        user_rows = cur.fetchall()

        # ALL meals, newest first
        cur.execute("""
            SELECT m.id, m.user_id, COALESCE(u.username, ''), m.date, m.meal_type,
                   m.description, m.calories, m.protein_g, m.carbs_g, m.fat_g,
                   m.fiber_g, m.sugar_g, m.created_at
            FROM meals m LEFT JOIN users u ON u.user_id = m.user_id
            ORDER BY m.id DESC
        """)
        meal_rows = cur.fetchall()

        cur.close()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Users table
    user_tbody = ""
    for r in user_rows:
        uid, uname, joined, meals, last = r
        user_tbody += (
            f"<tr><td>{_esc(uid)}</td><td>{_esc(uname)}</td>"
            f"<td>{_esc((joined or '')[:19])}</td>"
            f"<td>{_esc(meals)}</td><td>{_esc((last or '—')[:19])}</td></tr>\n"
        )

    # Meals table — all history
    meals_tbody = ""
    for r in meal_rows:
        mid, uid, uname, date, mt, desc, cal, p, c, f, fib, sug, ts = r
        meals_tbody += (
            f'<tr data-mid="{_esc(mid)}" data-uid="{_esc(uid)}">'
            f"<td>{_esc((ts or '')[:16])}</td>"
            f"<td>{_esc(uname)} <span class='uid'>({_esc(uid)})</span></td>"
            f"<td>{_esc(date)}</td>"
            f"<td>{_esc(mt)}</td>"
            f"<td>{_esc((desc or '')[:80])}</td>"
            f"<td class='num'>{round(cal or 0)}</td>"
            f"<td class='num'>{round(p or 0)}</td>"
            f"<td class='num'>{round(c or 0)}</td>"
            f"<td class='num'>{round(f or 0)}</td>"
            f"<td class='num'>{round(fib or 0)}</td>"
            f"<td class='num'>{round(sug or 0)}</td>"
            f"<td><button class='btn-del' onclick='deleteMeal(this)' title='Видалити'>🗑</button></td>"
            f"</tr>\n"
        )

    return f"""<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Food Tracker — Admin</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, 'Segoe UI', sans-serif; background: #0f0f1a; color: #e0e0e0; margin: 0; padding: 20px; }}
  h1 {{ color: #e94560; margin-bottom: 4px; }}
  h2 {{ background: #16213e; padding: 10px 16px; border-radius: 8px; color: #e0e0e0; margin-top: 30px; }}
  .subtitle {{ color: #888; margin-top: 0; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }}
  .card {{ background: #16213e; border-radius: 12px; padding: 20px 28px; min-width: 140px; text-align: center; }}
  .card .num {{ font-size: 2.2em; font-weight: bold; color: #e94560; }}
  .card .label {{ color: #a0a0a0; margin-top: 4px; font-size: 0.9em; }}

  /* Filter bar */
  .filter-bar {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin: 12px 0 8px; }}
  .filter-bar input, .filter-bar select {{
    background: #16213e; color: #e0e0e0; border: 1px solid #2a2a4a;
    padding: 8px 12px; border-radius: 6px; font-size: 0.95em;
  }}
  .filter-bar input::placeholder {{ color: #666; }}
  .filter-bar input:focus, .filter-bar select:focus {{ outline: none; border-color: #e94560; }}
  .filter-bar .count {{ color: #888; font-size: 0.85em; margin-left: auto; }}

  /* Tables */
  table {{ border-collapse: collapse; width: 100%; margin: 0 0 30px; }}
  th, td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid #1e1e3a; white-space: nowrap; }}
  td {{ font-size: 0.9em; }}
  th {{ background: #0f3460; color: #fff; cursor: pointer; user-select: none; position: sticky; top: 0; }}
  th:hover {{ background: #1a4a80; }}
  th .arrow {{ font-size: 0.7em; margin-left: 4px; opacity: 0.5; }}
  th.sorted .arrow {{ opacity: 1; color: #e94560; }}
  tr:hover {{ background: #1a1a30; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .uid {{ color: #666; font-size: 0.8em; }}

  /* Scrollable table wrapper */
  .table-wrap {{ max-height: 70vh; overflow: auto; border: 1px solid #1e1e3a; border-radius: 8px; }}
  .table-wrap table {{ margin: 0; }}

  .btn-del {{
    background: transparent; border: 1px solid #e94560; color: #e94560;
    border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 1em;
    transition: all 0.15s;
  }}
  .btn-del:hover {{ background: #e94560; color: #fff; }}
  .btn-del:disabled {{ opacity: 0.3; cursor: default; }}
  .btn-del.done {{ border-color: #4caf50; color: #4caf50; }}

  @media (max-width: 700px) {{
    .cards {{ flex-direction: column; }}
    th, td {{ padding: 5px 6px; font-size: 0.8em; }}
  }}
</style>
</head>
<body>

<h1>🥗 Food Tracker — Admin</h1>
<p class="subtitle">Повна історія всіх страв з фільтрами та сортуванням</p>

<div class="cards">
  <div class="card"><div class="num">{n_users}</div><div class="label">Користувачів</div></div>
  <div class="card"><div class="num">{n_meals}</div><div class="label">Страв записано</div></div>
  <div class="card"><div class="num">{n_days}</div><div class="label">Днів з даними</div></div>
  <div class="card"><div class="num">{n_recs}</div><div class="label">Нічних підсумків</div></div>
</div>

<h2>👥 Користувачі</h2>
<table id="tblUsers">
<thead><tr>
  <th data-col="0" data-type="num">user_id <span class="arrow">▲</span></th>
  <th data-col="1" data-type="str">Username <span class="arrow">▲</span></th>
  <th data-col="2" data-type="str">Приєднався <span class="arrow">▲</span></th>
  <th data-col="3" data-type="num">Страв <span class="arrow">▲</span></th>
  <th data-col="4" data-type="str">Остання страва <span class="arrow">▲</span></th>
</tr></thead>
<tbody>{user_tbody}</tbody>
</table>

<h2>🍽️ Вся історія страв ({n_meals})</h2>

<div class="filter-bar">
  <input type="text" id="searchMeals" placeholder="🔍 Пошук (назва, тип, користувач…)" style="min-width:260px;">
  <select id="filterUser"><option value="">Всі користувачі</option></select>
  <select id="filterType">
    <option value="">Всі типи</option>
    <option value="breakfast">Сніданок</option>
    <option value="lunch">Обід</option>
    <option value="dinner">Вечеря</option>
    <option value="snack">Перекус</option>
  </select>
  <input type="date" id="filterDateFrom" title="Від дати">
  <input type="date" id="filterDateTo" title="До дати">
  <span class="count" id="mealsCount"></span>
</div>

<div class="table-wrap">
<table id="tblMeals">
<thead><tr>
  <th data-col="0" data-type="str">Час <span class="arrow">▲</span></th>
  <th data-col="1" data-type="str">Користувач <span class="arrow">▲</span></th>
  <th data-col="2" data-type="str">Дата <span class="arrow">▲</span></th>
  <th data-col="3" data-type="str">Тип <span class="arrow">▲</span></th>
  <th data-col="4" data-type="str">Опис <span class="arrow">▲</span></th>
  <th data-col="5" data-type="num">ккал <span class="arrow">▲</span></th>
  <th data-col="6" data-type="num">Б <span class="arrow">▲</span></th>
  <th data-col="7" data-type="num">В <span class="arrow">▲</span></th>
  <th data-col="8" data-type="num">Ж <span class="arrow">▲</span></th>
  <th data-col="9" data-type="num">Кліт <span class="arrow">▲</span></th>
  <th data-col="10" data-type="num">Цук <span class="arrow">▲</span></th>
  <th>Дія</th>
</tr></thead>
<tbody>{meals_tbody}</tbody>
</table>
</div>

<script>
/* --- Sortable tables --- */
document.querySelectorAll('table').forEach(table => {{
  const headers = table.querySelectorAll('th[data-col]');
  let curCol = -1, asc = true;

  headers.forEach(th => {{
    th.addEventListener('click', () => {{
      const col = +th.dataset.col;
      const type = th.dataset.type;
      if (curCol === col) asc = !asc; else {{ curCol = col; asc = true; }}

      headers.forEach(h => h.classList.remove('sorted'));
      th.classList.add('sorted');
      th.querySelector('.arrow').textContent = asc ? '▲' : '▼';

      const tbody = table.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {{
        let va = a.cells[col]?.textContent.trim() || '';
        let vb = b.cells[col]?.textContent.trim() || '';
        if (type === 'num') {{
          va = parseFloat(va.replace(/[^\\d.-]/g, '')) || 0;
          vb = parseFloat(vb.replace(/[^\\d.-]/g, '')) || 0;
          return asc ? va - vb : vb - va;
        }}
        return asc ? va.localeCompare(vb) : vb.localeCompare(va);
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
}});

/* --- Meals filtering --- */
const mealsTable = document.getElementById('tblMeals');
const mealsRows = Array.from(mealsTable.querySelectorAll('tbody tr'));
const searchInput = document.getElementById('searchMeals');
const filterUser = document.getElementById('filterUser');
const filterType = document.getElementById('filterType');
const filterDateFrom = document.getElementById('filterDateFrom');
const filterDateTo = document.getElementById('filterDateTo');
const mealsCount = document.getElementById('mealsCount');

// Populate user filter dropdown from data
const users = new Map();
mealsRows.forEach(r => {{
  const cell = r.cells[1]?.textContent.trim() || '';
  if (cell && !users.has(cell)) users.set(cell, cell);
}});
Array.from(users.keys()).sort().forEach(u => {{
  const opt = document.createElement('option');
  opt.value = u; opt.textContent = u;
  filterUser.appendChild(opt);
}});

function applyFilters() {{
  const q = searchInput.value.toLowerCase();
  const user = filterUser.value.toLowerCase();
  const type = filterType.value.toLowerCase();
  const dateFrom = filterDateFrom.value;
  const dateTo = filterDateTo.value;
  let visible = 0;

  mealsRows.forEach(row => {{
    const text = row.textContent.toLowerCase();
    const rowUser = (row.cells[1]?.textContent || '').toLowerCase();
    const rowType = (row.cells[3]?.textContent || '').toLowerCase();
    const rowDate = (row.cells[2]?.textContent || '').trim();

    let show = true;
    if (q && !text.includes(q)) show = false;
    if (user && !rowUser.includes(user)) show = false;
    if (type && !rowType.includes(type)) show = false;
    if (dateFrom && rowDate < dateFrom) show = false;
    if (dateTo && rowDate > dateTo) show = false;

    row.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  mealsCount.textContent = `Показано: ${{visible}} / ${{mealsRows.length}}`;
}}

searchInput.addEventListener('input', applyFilters);
filterUser.addEventListener('change', applyFilters);
filterType.addEventListener('change', applyFilters);
filterDateFrom.addEventListener('change', applyFilters);
filterDateTo.addEventListener('change', applyFilters);
applyFilters();

/* --- Delete meal from admin --- */
async function deleteMeal(btn) {{
  const row = btn.closest('tr');
  const mid = row.dataset.mid;
  const uid = row.dataset.uid;
  const desc = row.cells[4]?.textContent.trim() || '';
  if (!confirm(`Видалити страву "${{desc}}"?`)) return;

  btn.disabled = true;
  btn.textContent = '...';
  try {{
    // Same-origin POST — browser auto-includes Basic Auth credentials.
    const resp = await fetch(window.location.pathname, {{
      method: 'POST',
      credentials: 'same-origin',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ action: 'delete_meal', meal_id: +mid, user_id: +uid }})
    }});
    const data = await resp.json();
    if (data.ok) {{
      row.style.transition = 'opacity 0.3s';
      row.style.opacity = '0';
      setTimeout(() => row.remove(), 300);
      // Update total counter
      const totalCard = document.querySelector('.card:nth-child(2) .num');
      if (totalCard) totalCard.textContent = Math.max(0, parseInt(totalCard.textContent) - 1);
      applyFilters();
    }} else {{
      alert('Помилка: ' + (data.error || 'невідома'));
      btn.disabled = false;
      btn.textContent = '🗑';
    }}
  }} catch(e) {{
    alert('Мережева помилка: ' + e.message);
    btn.disabled = false;
    btn.textContent = '🗑';
  }}
}}
</script>

</body>
</html>"""
