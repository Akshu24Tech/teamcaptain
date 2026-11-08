import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tc.extraction.extractor import LLMExtractor, RuleBasedExtractor, run_extraction
from tc.extraction.owners import resolve_owner
from tc.extraction.prefilter import should_extract
from tc.extraction.schema import ChatLine, ExtractionInput, OpenTask, RawCandidate, RawUpdate
from tc.extraction.verify import verify_candidates, verify_updates
from tc.llm import LLMError

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
MEMBERS = ["Akshu", "Rahul Sharma", "Priya", "Aman"]
OPEN = [
    OpenTask(short_no=1, task="Make the PPT", owner="Akshu"),
    OpenTask(short_no=2, task="Set up the database", owner="Rahul Sharma"),
]


def inp(text: str, speaker: str = "Akshu", context: list[ChatLine] | None = None,
        open_tasks: list[OpenTask] | None = None):
    return ExtractionInput(speaker=speaker, text=text, sent_at=NOW, members=MEMBERS,
                           context=context or [], open_tasks=open_tasks or [])


class FakeModel:
    """Returns scripted replies, so tests never call a real LLM."""

    name = "fake"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, system: str, user: str) -> tuple[str, int, int]:
        self.calls += 1
        return self.replies.pop(0), 10, 10


def reply(*items: dict) -> str:
    return json.dumps({"commitments": list(items)})


# --- prefilter -----------------------------------------------------------------

@pytest.mark.parametrize("text", ["I'll do it by friday", "PPT bana dunga", "Rahul can you check",
                                  "koshish karungi", "kal tak bhej dena"])
def test_prefilter_passes_cues(text: str) -> None:
    assert should_extract(text, has_context=False)[0]


@pytest.mark.parametrize("text", ["lol", "good morning", "/tasks", "", "nice meme"])
def test_prefilter_skips_chatter(text: str) -> None:
    assert not should_extract(text, has_context=False)[0]


def test_prefilter_bare_ok_needs_context() -> None:
    assert not should_extract("ok", has_context=False)[0]
    assert should_extract("ok", has_context=True)[0]


# --- owner resolution ------------------------------------------------------------

def test_owner_resolution() -> None:
    assert resolve_owner("speaker", "Akshu", MEMBERS) == "Akshu"
    assert resolve_owner("Rahul", "Akshu", MEMBERS) == "Rahul Sharma"
    assert resolve_owner("@aman_k", "Akshu", MEMBERS, {"@aman_k": "Aman"}) == "Aman"
    assert resolve_owner("Vikram", "Akshu", MEMBERS) is None  # invented person
    assert resolve_owner(None, "Akshu", MEMBERS) is None


# --- verifier (the trust layer) -----------------------------------------------------

def test_verifier_rejects_hallucinated_evidence() -> None:
    raw = [RawCandidate(task="Make slides", owner="speaker", kind="commitment",
                        evidence_quote="I will make the slides tonight")]
    ok, rejected = verify_candidates(raw, inp("I'll handle the slides"), TZ)
    assert ok == [] and rejected[0].reason == "evidence_not_in_message"


def test_verifier_drops_invented_deadline_phrase() -> None:
    raw = [RawCandidate(task="Make slides", owner="speaker", kind="commitment",
                        deadline_phrase="by friday", evidence_quote="I'll make the slides")]
    ok, rejected = verify_candidates(raw, inp("I'll make the slides"), TZ)
    assert ok[0].deadline_at is None
    assert rejected[0].reason == "deadline_phrase_not_in_chat"


def test_verifier_computes_date_itself() -> None:
    raw = [RawCandidate(task="Make slides", owner="speaker", kind="commitment",
                        deadline_phrase="kal tak", evidence_quote="slides kal tak bana dunga")]
    ok, _ = verify_candidates(raw, inp("slides kal tak bana dunga"), TZ)
    assert ok[0].deadline_at.date().isoformat() == "2026-11-10"
    assert ok[0].deadline_rule == "tomorrow"


def test_verifier_fixes_inconsistent_kind() -> None:
    raw = [RawCandidate(task="Set up database", owner="Rahul", kind="commitment",
                        evidence_quote="Rahul can you set up the database")]
    ok, _ = verify_candidates(raw, inp("Rahul can you set up the database"), TZ)
    assert ok[0].kind == "assignment" and ok[0].owner == "Rahul Sharma"


def test_verifier_unknown_owner_stays_unassigned_with_lower_confidence() -> None:
    raw = [RawCandidate(task="Design poster", owner="Vikram", kind="assignment",
                        evidence_quote="Vikram please design the poster")]
    ok, _ = verify_candidates(raw, inp("Vikram please design the poster"), TZ)
    assert ok[0].owner is None and ok[0].confidence < 0.9


def test_deadline_phrase_may_come_from_context() -> None:
    ctx = [ChatLine(speaker="Akshu", text="Rahul can you make the ER diagram by wednesday?")]
    raw = [RawCandidate(task="Make ER diagram", owner="speaker", kind="commitment",
                        deadline_phrase="by wednesday", evidence_quote="ok")]
    ok, _ = verify_candidates(raw, inp("ok", speaker="Rahul Sharma", context=ctx), TZ)
    assert ok[0].owner == "Rahul Sharma"
    assert ok[0].deadline_at.date().isoformat() == "2026-11-11"


# --- LLM extractor with a fake model ---------------------------------------------------

async def test_llm_extractor_end_to_end() -> None:
    model = FakeModel(reply({"task": "Make PPT", "owner": "speaker", "kind": "commitment",
                             "deadline_phrase": "by friday", "evidence_quote": "PPT by friday"}))
    result = await run_extraction(LLMExtractor(model), inp("I'll make the PPT by friday"), TZ)
    assert len(result.candidates) == 1
    assert result.candidates[0].deadline_at.date().isoformat() == "2026-11-13"


async def test_llm_invalid_json_is_repaired_once() -> None:
    model = FakeModel("not json", reply())
    result = await run_extraction(LLMExtractor(model), inp("I'll make the PPT"), TZ)
    assert model.calls == 2 and result.candidates == []


async def test_llm_gives_up_after_repairs() -> None:
    model = FakeModel("nope", "still nope")
    with pytest.raises(LLMError):
        await run_extraction(LLMExtractor(model), inp("I'll make the PPT"), TZ)


async def test_prefilter_skips_llm_call() -> None:
    model = FakeModel()
    result = await run_extraction(LLMExtractor(model), inp("lol"), TZ)
    assert result.skipped_by_prefilter and model.calls == 0


# --- rule-based baseline -------------------------------------------------------------

async def test_baseline_ignores_finished_work() -> None:
    result = await run_extraction(RuleBasedExtractor(), inp("I finished the PPT"), TZ)
    assert result.candidates == []


async def test_baseline_assignment() -> None:
    result = await run_extraction(RuleBasedExtractor(), inp("Priya please write the intro"), TZ)
    assert result.candidates[0].owner == "Priya"
    assert result.candidates[0].kind == "assignment"


# --- richer detection: completion of an existing task --------------------------------

def test_prefilter_completion_needs_open_tasks() -> None:
    assert not should_extract("the PPT is done", has_context=False, has_open_tasks=False)[0]
    assert should_extract("the PPT is done", has_context=False, has_open_tasks=True)[0]


def test_prefilter_completion_ignores_status_question() -> None:
    assert not should_extract("is the PPT done?", has_context=False, has_open_tasks=True)[0]


def test_update_matches_by_explicit_number() -> None:
    raw = [RawUpdate(intent="done", task_ref="#1", evidence_quote="#1 done")]
    ok, _ = verify_updates(raw, inp("#1 done", open_tasks=OPEN))
    assert ok[0].short_no == 1 and ok[0].confidence == 1.0


def test_update_matches_by_keyword() -> None:
    raw = [RawUpdate(intent="done", task_ref="the ppt", evidence_quote="ppt is done")]
    ok, _ = verify_updates(raw, inp("the ppt is done", open_tasks=OPEN))
    assert ok[0].short_no == 1 and ok[0].confidence == 0.8


def test_update_rejects_evidence_not_in_message() -> None:
    raw = [RawUpdate(intent="done", task_ref="the ppt", evidence_quote="all finished now")]
    ok, rejected = verify_updates(raw, inp("ppt is done", open_tasks=OPEN))
    assert ok == [] and rejected[0].reason == "evidence_not_in_message"


def test_update_rejects_when_nothing_matches() -> None:
    raw = [RawUpdate(intent="done", task_ref="the logo", evidence_quote="logo is done")]
    ok, rejected = verify_updates(raw, inp("logo is done", speaker="Aman", open_tasks=OPEN))
    assert ok == [] and rejected[0].reason == "no_matching_open_task"


def test_update_rejects_someone_elses_task() -> None:
    raw = [RawUpdate(intent="done", task_ref="#2", evidence_quote="#2 done")]
    ok, rejected = verify_updates(raw, inp("#2 done", speaker="Akshu", open_tasks=OPEN))
    assert ok == [] and rejected[0].reason == "not_speakers_task"


def test_update_falls_back_to_sole_open_task() -> None:
    one = [OpenTask(short_no=5, task="Write the report", owner="Akshu")]
    raw = [RawUpdate(intent="done", task_ref="it", evidence_quote="all done")]
    ok, _ = verify_updates(raw, inp("all done", open_tasks=one))
    assert ok[0].short_no == 5 and ok[0].confidence == 0.6


async def test_baseline_detects_completion_as_update() -> None:
    result = await run_extraction(RuleBasedExtractor(), inp("ppt done", open_tasks=OPEN), TZ)
    assert result.candidates == []
    assert result.updates[0].short_no == 1


async def test_llm_detects_completion_update() -> None:
    model = FakeModel(json.dumps({"commitments": [], "updates": [
        {"intent": "done", "task_ref": "the ppt", "evidence_quote": "ppt is done"}]}))
    result = await run_extraction(LLMExtractor(model), inp("the ppt is done", open_tasks=OPEN), TZ)
    assert result.candidates == [] and result.updates[0].short_no == 1
