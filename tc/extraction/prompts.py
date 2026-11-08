from __future__ import annotations

from tc.extraction.schema import ExtractionInput

EXTRACTION_SYSTEM = """\
You read a team's group chat and extract COMMITMENTS: someone taking responsibility \
for a concrete piece of work.

Classify each one:
- "commitment": the speaker promises to do something themselves.
  e.g. "I'll make the slides by Friday", "PPT main bana dunga kal tak"
- "assignment": the speaker asks a specific named member to do something.
  e.g. "Rahul can you handle the database?", "Priya report likh dena"
- "tentative": the speaker says they will try or maybe do it.
  e.g. "I'll try to finish the intro", "koshish karunga"

If the latest message ACCEPTS an earlier request made to the speaker ("ok", "haan kar dunga", \
"sure"), output a "commitment" owned by the speaker, with the task taken from the earlier message.

You are ALSO shown the team's currently-tracked OPEN TASKS. If the latest message says one of \
them is now FINISHED ("PPT is done", "report bhej diya", "done with the slides", "#2 complete"), \
report it as an UPDATE, not a new commitment:
- intent: "done"
- task_ref: which open task it refers to — copy its "#N" if given, else the task's words.
- evidence_quote: the exact words from the latest message showing it is finished.
Only do this when an open task plausibly matches. A status QUESTION ("is the report done?") is \
NOT a completion. If no open task matches, output no update.

Do NOT extract as a NEW commitment:
- work already finished — that is an UPDATE (see above), never a new commitment.
- status questions ("is the report done?")
- vague group wishes with no owner ("someone should check this", "we need a logo")
- jokes, greetings, links, opinions

Field rules:
- owner: "speaker" for the person who wrote the latest message; otherwise a member name \
copied exactly from the member list; null if unclear.
- task: short English description of the work, at most 12 words, e.g. "Make presentation slides".
- deadline_phrase: copy the exact words from the chat that state the deadline \
(e.g. "by friday 5pm", "kal tak"), or null. NEVER calculate or invent a date.
- evidence_quote: copy the exact words from the LATEST message that show the commitment. \
It must be a verbatim substring of the latest message.
- One message can contain several commitments, or none.

Reply with ONLY this JSON object:
{"commitments": [{"task": "...", "owner": "...", "kind": "...", "deadline_phrase": null, \
"evidence_quote": "..."}],
 "updates": [{"intent": "done", "task_ref": "...", "evidence_quote": "..."}]}
Use empty lists when there are none.
"""


def build_user_prompt(inp: ExtractionInput) -> str:
    lines = [f"Team members: {', '.join(inp.members)}"]
    if inp.open_tasks:
        lines.append("Open tasks already being tracked:")
        lines.extend(
            f"- #{t.short_no}: {t.task}" + (f" (owner: {t.owner})" if t.owner else "")
            for t in inp.open_tasks
        )
    if inp.context:
        lines.append("Earlier messages (oldest first):")
        lines.extend(f"- {c.speaker}: {c.text}" for c in inp.context)
    lines.append(f"Latest message, written by {inp.speaker}:")
    lines.append(f'"""{inp.text}"""')
    return "\n".join(lines)
