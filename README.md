# Crohn's-Friendly Calorie Tracker (Telegram Bot)

A Python Telegram bot deployed on **Vercel** (serverless) that tracks daily food intake via GPT-4o photo analysis, tailored for a user with Crohn's disease and specific food allergies. Sends a personalized end-of-day summary each night.

- 📸 Photo analysis → calories, macros, allergen + Crohn's warnings
- 📊 `/today` progress bars · `/history` 7-day summary · `/suggest_meal` AI recipe
- 🌙 Automatic nightly summary via Vercel Cron
- 💾 Neon Postgres (via Vercel Marketplace) — fully integrated, auto-injected env vars

---

## Prerequisites

1. **Telegram bot** — create one with [@BotFather](https://t.me/BotFather), get the token.
2. **OpenAI API key** — from [platform.openai.com/api-keys](https://platform.openai.com/api-keys). GPT-4o vision access.
3. **Vercel account** — free tier at [vercel.com](https://vercel.com). Database (Neon) is installed from the Marketplace inside Vercel — no separate signup.
4. **GitHub account** — to host the source and auto-deploy via Vercel's GitHub integration.
5. **Python 3.11+** locally (for running the webhook-registration script).

---

## Setup

### 1. Clone & install local deps

```bash
git clone <your-fork-url> Food
cd Food
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your credentials (see steps below).

### 2. Provision Neon Postgres from the Vercel Marketplace

You do this **after** the first Vercel deploy (step 5), from the Vercel dashboard:

1. Go to your Vercel project → **Storage** tab → **Create Database** → **Neon**.
2. Accept the Marketplace terms. Pick the free plan and default region.
3. Vercel auto-injects `DATABASE_URL` (and several aliases like `POSTGRES_URL`, `PGHOST`, etc.) into all environments. Nothing else to configure.

For local `.env`, copy the `DATABASE_URL` value from **Settings → Environment Variables**.

No manual migrations needed — the bot runs `CREATE TABLE IF NOT EXISTS …` on every request.

### 3. Generate secrets

Pick any random strings for the two remaining secrets:

```bash
python -c "import secrets; print('WEBHOOK_SECRET=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('CRON_SECRET='    + secrets.token_urlsafe(32))"
```

Add both to `.env`.

### 4. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<you>/Food.git
git push -u origin main
```

### 5. Deploy to Vercel

- Go to [vercel.com/new](https://vercel.com/new) → import the GitHub `Food` repo.
- Framework preset: **Other**. Vercel will detect `vercel.json` and use the `@vercel/python` builder.
- After the first deploy, note the URL (e.g. `food-abc123.vercel.app`).
- In **Project → Settings → Environment Variables**, add all 7 variables (from your `.env`):
  - `TELEGRAM_BOT_TOKEN`
  - `OPENAI_API_KEY`
  - `WEBHOOK_SECRET`
  - `VERCEL_URL` — the deployed domain (no `https://`), e.g. `food-abc123.vercel.app`
  - `CRON_SECRET`
  - (`DATABASE_URL` is auto-injected by the Neon Marketplace integration — don't set manually.)
- Redeploy to pick up the env vars (Deployments → … → Redeploy).

### 6. Register the webhook with Telegram

Once deployed and env vars are set, from your local machine:

```bash
python scripts/set_webhook.py
```

You should see `{"ok": true, "result": true, "description": "Webhook was set"}`.

### 7. Try it out

In Telegram:

1. Send `/start` to your bot.
2. Send a photo of a meal.
3. Tap one of `Breakfast / Lunch / Dinner / Snack`.
4. Wait ~10–20 seconds — receive the full analysis.
5. Send `/today` to see progress bars.
6. Send `/suggest_meal` to get an allergen-safe recipe for what remains today.

The nightly summary runs automatically at **22:00 UTC** via Vercel Cron.

---

## Architecture

```
Telegram → POST /api/webhook  ── httpx ──► Telegram API
                │
                ├─► lib/database.py  ── psycopg ──► Neon Postgres
                ├─► lib/openai_vision.py  ── GPT-4o (vision) ──► analysis JSON
                └─► lib/openai_nutrition.py  ── GPT-4o ──► summary / recipe

Vercel Cron → GET /api/cron_daily_summary   (22:00 UTC)
Vercel Cron → GET /api/cron_midnight_reset  (00:00 UTC)
```

- **No long-polling, no FSM** — Vercel functions are stateless. Pending photos (between `sendPhoto` and the user tapping a meal-type button) live in the `pending_photos` DB table with a 10-minute expiry.
- **Webhook auth:** verified via the `X-Telegram-Bot-Api-Secret-Token` header.
- **Cron auth:** Vercel sends `Authorization: Bearer $CRON_SECRET` — we verify before running.
- **Always returns HTTP 200** to Telegram (even on errors) to avoid retry loops. Errors are logged via `print(..., flush=True)` and surface in Vercel logs.

---

## Local development

Vercel functions use a plain `BaseHTTPRequestHandler` pattern, so you can't run the webhook locally without either:

- Using [`vercel dev`](https://vercel.com/docs/cli/dev), which emulates the serverless runtime locally.
- Exposing a local endpoint via [ngrok](https://ngrok.com/) and temporarily pointing Telegram at it:
  ```bash
  vercel dev --listen 3000
  ngrok http 3000
  # Then edit scripts/set_webhook.py to point VERCEL_URL at the ngrok domain.
  ```

For most tweaks it's simpler to push to a preview branch and test against the Vercel preview URL.

---

## Files

```
Food/
├── vercel.json              # routes + cron schedule
├── requirements.txt
├── api/
│   ├── webhook.py                 # POST — Telegram updates
│   ├── cron_daily_summary.py      # GET  — 22:00 UTC summary
│   └── cron_midnight_reset.py     # GET  — 00:00 UTC cleanup
├── lib/
│   ├── config.py                  # USER_PROFILE + env + prompts
│   ├── database.py                # Turso connection + schema + CRUD
│   ├── telegram_helpers.py        # sendMessage / getFile / keyboards
│   ├── openai_vision.py           # GPT-4o vision → analysis JSON
│   ├── openai_nutrition.py        # summaries + recipes
│   └── formatters.py              # progress bars, HTML templates
└── scripts/
    └── set_webhook.py             # one-time webhook registration
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Webhook registered but no replies | Check `VERCEL_URL` has no `https://`, redeploy, re-run `set_webhook.py`. Check Vercel → Logs. |
| `403` from webhook | `WEBHOOK_SECRET` in Vercel env doesn't match the one used by `set_webhook.py`. |
| `psycopg` not installed / DB connection error | Confirm `requirements.txt` is in the repo root and `DATABASE_URL` is present in the Vercel env (it's auto-injected by the Neon integration). |
| Photo analysis times out | Hobby plan has a 60s function timeout; GPT-4o usually returns in 10-20s. Try a smaller photo. |
| Cron didn't fire | Crons require a Production deployment. Promote your deploy, or GET the cron URL manually with `Authorization: Bearer $CRON_SECRET`. |
| `/history_detail` returns empty | It needs the `YYYY-MM-DD` UTC date. Use `/today` first to confirm meals are logged. |

---

## Deployment checklist

- [ ] GitHub repo pushed
- [ ] Vercel project imported + first deploy succeeded
- [ ] Neon database provisioned via Vercel → Storage (auto-injects `DATABASE_URL`)
- [ ] App env vars set in Vercel: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `WEBHOOK_SECRET`, `VERCEL_URL`, `CRON_SECRET`
- [ ] `.env` filled in locally (for `set_webhook.py`)
- [ ] Deployment promoted to production (so crons run)
- [ ] `python scripts/set_webhook.py` succeeded
- [ ] `/start` in Telegram replies with welcome message
- [ ] Test photo → meal-type buttons → analysis arrives
- [ ] (Optional) Manually GET `/api/cron_daily_summary` with the bearer header to confirm the summary flow

---

## v1 scope (what this does NOT do)

- Multi-user profile management (profile is hardcoded in `lib/config.py`)
- Web dashboard
- Payments / subscriptions
- Photo storage (only Telegram `file_id` is kept)
- Timezone selection (everything is UTC)
- Manual calorie entry / meal editing
