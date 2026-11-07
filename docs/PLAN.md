# Build plan, report outline and demo script

## Status
✅ Done in the scaffold: extraction pipeline (prefilter, LLM + rule-based extractors, verifier,
deadline resolver), durable workflows, Telegram bot with confirm/done/reschedule, delivery
report, evaluation harness, 50-example seed dataset, 63 tests.

⬜ Remaining work is below. Each week ends with something demoable.

## Week 1 — make it run for real
- [ ] Get a Groq key and a Telegram bot token; run the full stack locally.
- [ ] Test in a group with 2–3 friends. Note every wrong extraction in `docs/ERRORS.md`.
- [ ] Run `--extractor llm` on the seed set; record numbers in `docs/RESULTS.md`.
- [ ] Write an integration test that runs `commitment-lifecycle` against the Inngest dev
      server with short timeouts (e.g. 10-second deadlines) and a fake Telegram.

## Week 2 — data and evaluation (this is what makes it final-year level)
- [ ] Expand `evals/dataset.jsonl` to ~200 examples. Source: your own project group chats
      (with permission, names replaced). Keep ~40% negatives.
- [ ] Get a second person to label 50 of them; report inter-annotator agreement.
- [ ] Compare: rule-based vs Groq Llama 70B vs local Qwen 7B (precision, recall, F1,
      deadline accuracy, latency). Ablations: without prefilter, without verifier.
- [ ] Error analysis: group failures into categories (missed acceptance, wrong owner,
      vague deadline…) and fix the top one.

## Week 3 — polish and deploy on a real team
- [ ] `/deadline` and check-ins for tasks with no deadline (already wired; test them).
- [ ] A small web dashboard (FastAPI page) showing tasks, timeline and the report.
- [ ] Run it on your actual project group for 2+ weeks. Collect: tasks tracked, confirmed vs
      rejected proposals, reminders sent, on-time rate. Ask the group for feedback.
- [ ] Record a backup demo video.

## Report outline (maps directly to the code)
1. **Introduction** — commitments get lost in chats; teams don't use project tools.
2. **Literature / existing tools** — Trello, Jira, Notion, Slack reminders (all manual);
   NLP work on commitment/action-item detection in email and meetings.
3. **System design** — architecture diagram, event flow, data model, lifecycle state machine
   (proposed → confirmed → done / overdue → escalated; rejected; expired; cancelled).
4. **Trustworthy extraction** — prefilter, LLM prompt, verifier rules, deterministic dates.
5. **Durable workflows** — why Inngest, step memoisation, crash recovery, versioned reschedules.
6. **Evaluation** — dataset, metrics, model comparison, ablations, error analysis.
7. **Deployment study** — real-team results and feedback.
8. **Ethics** — consent, confirmation before tracking, report measures reliability not effort,
   data minimisation.
9. **Limitations & future work** — WhatsApp, voice notes, workload-aware scoring.

## 10-minute demo script
1. **Problem (30 s):** show a messy real group chat where a task got lost.
2. **Detect (2 min):** in the live group type "I'll make the PPT by friday 5pm" → bot proposes
   with owner, resolved date and the quote it relied on. Tap ✅.
3. **Trust (1 min):** have someone else try to confirm Rahul's task → refused. Show a
   hallucination the verifier rejected (from the eval failures file).
4. **Durability (2 min):** create a task due in 2 minutes. Stop the server. Restart it.
   Open the Inngest dashboard: the run resumed and the reminder still arrives on time.
5. **Accountability (1 min):** `/done`, then `/report`.
6. **Evidence (2 min):** results table: baseline vs free LLMs, and one error-analysis insight.
7. **Close (30 s):** "Every promise in the chat is now confirmed, tracked and accounted for,
   at zero cost."

Tip for step 4 (demo mode): in `.env` set `REMINDER_LEAD_HOURS=0.02` (≈1 minute) and
`ESCALATION_DELAY_HOURS=0.03`, then promise something "in 3 minutes". The reminder, overdue
notice and escalation all happen within ~5 minutes, live.
