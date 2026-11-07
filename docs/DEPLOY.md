# Deploying TeamCaptain live (always-on, ₹0)

This runs TeamCaptain 24/7 without your laptop, entirely on free tiers. In production the
Telegram bot uses a **webhook** instead of long polling, so the whole system is a single web
service.

```
Telegram ──webhook──▶ Render (FastAPI: /telegram/webhook + /api/inngest)
                          │            ▲
                          │            └── Inngest Cloud (timers, retries, cron)
                          ├──▶ Neon (Postgres)
                          └──▶ Groq (LLM)
```

**Free stack:** Render (host, no card) · Neon (Postgres, no card) · Inngest Cloud (Hobby) ·
Groq (LLM). You create the accounts; nothing here needs a credit card.

---

## 1. Postgres — Neon
1. Sign up at <https://neon.tech> and create a project (pick a region near you).
2. Copy the connection string. Convert it to the async + SSL form this app needs:
   - change the scheme `postgresql://` → `postgresql+asyncpg://`
   - drop any `sslmode=require` / `channel_binding=...` params and add `?ssl=require`

   ```
   postgresql+asyncpg://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/neondb?ssl=require
   ```
   Keep this as your `DATABASE_URL`. The tables are created automatically on first boot.

## 2. Workflows — Inngest Cloud
1. Sign up at <https://app.inngest.com> and create an environment (Production is fine).
2. Go to **Manage → Keys** and copy:
   - the **Event Key** → `INNGEST_EVENT_KEY`
   - the **Signing Key** → `INNGEST_SIGNING_KEY`
3. You'll register ("sync") the app after it's deployed — see step 6.

## 3. LLM — Groq
1. Get a free key at <https://console.groq.com> (no card) → `LLM_API_KEY`.
2. Keep `LLM_MODEL=llama-3.3-70b-versatile` (or whatever the console lists as current).

## 4. Telegram bot — BotFather
1. In Telegram, message **@BotFather** → `/newbot` → copy the token → `TELEGRAM_BOT_TOKEN`.
2. `/setprivacy` → choose your bot → **Disable** (so it can read normal group messages, not
   just `/commands`).
3. Pick any random string for `WEBHOOK_SECRET` (e.g. `openssl rand -hex 16`).

## 5. Deploy to Render
1. Push this repo to GitHub (already done).
2. At <https://dashboard.render.com> → **New → Blueprint** → select this repo. Render reads
   [`render.yaml`](../render.yaml) and creates one free web service.
3. When prompted, fill the environment variables (all the `sync: false` ones):

   | Variable | Value |
   |---|---|
   | `LLM_API_KEY` | Groq key (step 3) |
   | `TELEGRAM_BOT_TOKEN` | BotFather token (step 4) |
   | `WEBHOOK_SECRET` | your random string (step 4) |
   | `DATABASE_URL` | Neon async URL (step 1) |
   | `INNGEST_EVENT_KEY` | Inngest event key (step 2) |
   | `INNGEST_SIGNING_KEY` | Inngest signing key (step 2) |

   `INNGEST_DEV=false` and `LLM_MODEL` are already set by the blueprint.
4. Deploy. When it's live, note the URL, e.g. `https://teamcaptain.onrender.com`. Check
   `https://<your-url>/health` returns `{"status":"ok"}`.

## 6. Register the app with Inngest Cloud
1. In the Inngest dashboard → **Apps → Sync new app**, enter:
   ```
   https://<your-url>/api/inngest
   ```
2. You should see the functions appear: `process-message`, `commitment-lifecycle`,
   `daily-standup`. (Inngest needs the signing key from step 2 to be set, which it is.)

## 7. Point Telegram at the webhook
Run this once from your laptop (needs `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, and
`PUBLIC_URL=https://<your-url>` in your local `.env`):

```bash
uv run python -m tc.bot.set_webhook
```

It prints `getWebhookInfo` so you can confirm the URL and that `pending_update_count` is 0.
To undo (go back to local long polling): `uv run python -m tc.bot.set_webhook --delete`.

## 8. Test it
1. Add the bot to a Telegram group and send `/help`.
2. Type something like **"I'll send the client deck by tomorrow 6pm"**.
3. The bot replies asking you to confirm → tap **Yes**.
4. In Inngest Cloud you'll see a `commitment-lifecycle` run with its reminder/overdue timers.
5. `/report` shows the delivery report.

---

## Caveats (important for the demo)
- **Render free sleeps after ~15 min idle.** The *first* message after idle triggers a cold
  start (~30–60 s); Telegram retries the webhook, so nothing is lost. **Before a live demo,
  send one message a minute early to wake it up.**
- **Timed reminders and the daily standup still fire on time** even while asleep — Inngest
  Cloud holds the timers and wakes the service via HTTP (retrying through the cold start).
- **Groq free tier is rate-limited**; Inngest throttles extraction runs
  (`EXTRACTION_RUNS_PER_MINUTE`) so bursts queue instead of failing.
- **Inngest Hobby** has a monthly step quota — far above any demo's needs.
- Schema is created with `create_all` on boot (no Alembic yet), which is fine for a fresh DB.
  If you later change columns, reset the Neon database or add migrations first.

## Faster timers for a demo
The lifecycle delays default to 24 h. To show reminders/escalation within minutes, set on
Render (hours as decimals):
```
CONFIRMATION_TIMEOUT_HOURS=0.05
REMINDER_LEAD_HOURS=0.03
ESCALATION_DELAY_HOURS=0.05
```
