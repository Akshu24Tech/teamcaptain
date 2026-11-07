import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tc.extraction.extractor import LLMExtractor, RuleBasedExtractor, run_extraction
from tc.extraction.owners import resolve_owner
from tc.extraction.prefilter import should_extract
from tc.extraction.schema import ChatLine, ExtractionInput, RawCandidate
from tc.extraction.verify import verify_candidates
from tc.llm import LLMError

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
MEMBERS = ["Akshu", "Rahul Sharma", "Priya", "Aman"]


def inp(text: str, speaker: str = "Akshu", context: list[ChatLine] | None = None):
    return ExtractionInput(speaker=speaker, text=text, sent_at=NOW, members=MEMBERS,
                           context=context or [])


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
