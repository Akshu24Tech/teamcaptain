# Results

Numbers are produced by the evaluation harness, not claimed by hand:

```bash
uv run python -m tc.evaluation --extractor rules
uv run python -m tc.evaluation --extractor llm      # when an LLM key is set
```

## Commitment extraction (50-example seed set, `evals/dataset.jsonl`)

| extractor | precision | recall | F1 | kind acc. | deadline acc. | false alarms |
|---|---|---|---|---|---|---|
| rule-based | 0.929 | 0.743 | 0.825 | 1.000 | 0.923 | 5.9% |

The rules were written alongside this seed set, so they flatter themselves here. The real
test is the expanded dataset and the LLM comparison (see `docs/PLAN.md`).

## Richer detection — completion of an existing task (2026-11-08)

Added plain-language completion detection ("the PPT is done" → confirm → close), a new
*update* category alongside new-commitment extraction. Owner confirms before anything closes.

- Measured impact on commitment extraction: **none.** The rule-based numbers above are
  unchanged from before the change — the new prefilter cue and prompt did not regress
  precision, recall, or deadline accuracy.
- Correctness of the new path is covered by unit tests in `tests/test_extraction.py`
  (`verify_updates`): verbatim-evidence check, match by `#N`, match by keyword, sole-open-task
  fallback, and rejection of ambiguous, someone-else's, or unmatched completions.

Next: fold completion/reassignment/reschedule examples into the dataset so the harness scores
update detection directly, and run the LLM extractor comparison once a key is available.
