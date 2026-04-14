"""Admin stats dashboard — view in browser with bearer token auth."""
import json
import os
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.config import CRON_SECRET
from lib.database import get_conn, init_db


def _authorized(headers) -> bool:
    if not CRON_SECRET:
        return True
    auth = headers.get("Authorization", "")
    return auth == f"Bearer {CRON_SECRET}"


def _token_from_query(path: str) -> str:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(path or "").query)
    return (qs.get("token") or [""])[0]


def _esc(s) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        authed = _authorized(self.headers)
        if not authed:
            token = _token_from_query(self.path)
            if CRON_SECRET and token == CRON_SECRET:
                authed = True
        if not authed:
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized. Add ?token=YOUR_CRON_SECRET or Authorization: Bearer header.")
            return

        try:
            html = build_html()
        except Exception:
            print("admin_stats error:", traceback.format_exc(), flush=True)
            html = f"<pre>Error: {traceback.format_exc()}</pre>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


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
            f"<tr><td>{uid}</td><td>{_esc(uname)}</td>"
            f"<td>{_esc((joined or '')[:19])}</td>"
            f"<td>{meals}</td><td>{_esc((last or '—')[:19])}</td></tr>\n"
        )

    # Meals table — all history
    meals_tbody = ""
    for r in meal_rows:
        mid, uid, uname, date, mt, desc, cal, p, c, f, fib, sug, ts = r
        meals_tbody += (
            f"<tr>"
            f"<td>{_esc((ts or '')[:16])}</td>"
            f"<td>{_esc(uname)} <span class='uid'>({uid})</span></td>"
            f"<td>{_esc(date)}</td>"
            f"<td>{_esc(mt)}</td>"
            f"<td>{_esc((desc or '')[:80])}</td>"
            f"<td class='num'>{round(cal or 0)}</td>"
            f"<td class='num'>{round(p or 0)}</td>"
            f"<td class='num'>{round(c or 0)}</td>"
            f"<td class='num'>{round(f or 0)}</td>"
            f"<td class='num'>{round(fib or 0)}</td>"
            f"<td class='num'>{round(sug or 0)}</td>"
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
</script>

</body>
</html>"""
