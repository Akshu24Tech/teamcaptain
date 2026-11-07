# TeamCaptain — guide for Claude Code

An autonomous AI agent that turns group-chat conversations into verified, self-managing
commitments. Final-year B.Tech project. Must run at ₹0: free LLM tiers or local models only.

## Architecture (one line each)
- `tc/bot/` — Telegram long-polling bot. Stores messages, handles buttons and /commands,
  emits events. Never calls the LLM, never sleeps.
- `tc/workflows.py` — Inngest durable functions: `process-message` (extract → propose),
  `commitment-lifecycle` (confirm → remind → overdue → escalate), `daily-standup` (cron).
- `tc/extraction/` — prefilter → extractor (LLM or rule-based) → verifier.
  `dates.py` turns deadline phrases into datetimes deterministically.
- `tc/repo.py` — all DB access. `tc/db.py` — SQLAlchemy models (Postgres; SQLite in tests).
- `tc/views.py` — every message the bot sends. `tc/scoring.py` — delivery report.
- `tc/evaluation.py` + `evals/dataset.jsonl` — precision/recall harness.

## Non-negotiable rules
1. **The LLM never decides facts.** It points at evidence; code verifies. Evidence quotes must
   be verbatim substrings, owners must resolve to real members, dates are computed by
   `dates.py`. Any new LLM output field needs a matching check in `verify.py`.
2. **Human-in-the-loop.** Nothing is tracked until the accountable person taps "Yes".
   Unconfirmed extractions never appear in reports.
3. **Workflow determinism.** In Inngest functions, side effects and `now()` happen only inside
   `ctx.step.*`. Step IDs must be stable across replays (include `v{version}` in tracking steps).
4. **DB is the source of truth.** Every notification step re-checks status and
   `schedule_version` before sending. Waits are an optimisation, not a guarantee.
5. **Free only.** No paid APIs, no paid SDK features. Providers are swapped via `.env`
   (any OpenAI-compatible endpoint).
6. **Measure, don't claim.** Changes to extraction must be followed by
   `python -m tc.evaluation --extractor rules` (and the LLM run when a key is available);
   put the numbers in `docs/RESULTS.md`.

## Commands
```bash
uv sync                                   # install
docker compose up -d                      # Postgres :5434, Inngest dashboard :8288
uv run uvicorn tc.main:app --port 8000    # workflows + API
uv run python -m tc.bot.poller            # Telegram bot
uv run pytest -q                          # tests (SQLite, fake Telegram, fake LLM)
uv run ruff check tc tests
uv run python -m tc.evaluation --extractor rules
uv run python -m tc.evaluation --extractor llm
```

## Known limitations / next work (see docs/PLAN.md)
- "before monday" said on a Monday resolves to today (humans mean next week) — ex050.
- "give me 3 days" isn't a recognised relative phrase — ex007.
- Dataset is a 50-example seed written by hand; expand to ~200 from real (anonymised) chats.
- Workflows are not yet covered by an automated test against a live Inngest dev server.
- Schema is created with `create_all`; move to Alembic before changing columns.
