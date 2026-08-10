"""The conversational layer — Agent 8's deterministic half.

The agent's prose needs an LLM and is exercised by hand; what is pinned here is
everything that decides *what the model is shown*: which turns are treated as
follow-ups, what a turn records, and what a thread carries forward. Those are
where a conversation goes wrong silently — a follow-up routed as a fresh
question still returns an answer, just not to the question that was asked.
"""

from __future__ import annotations

import pytest

from crew_perf.agents import orchestrator
from crew_perf.agents.conversation import (
    MAX_HISTORY_TURNS, Conversation, Turn, is_followup,
)
from crew_perf.api.sessions import SessionStore


def scored(iga="IGA60406", score=4.2) -> Turn:
    return Turn(question=f"How is {iga} performing?", kind="scoring",
                answer="A short answer.",
                facts={"igas": [iga], "mechanism": "business",
                       "scorecards": [{"iga": iga, "composite_score": score}]})


@pytest.fixture
def thread():
    c = Conversation(id="t")
    c.add(scored())
    return c


# ─── routing ────────────────────────────────────────────────────────────────


def test_nothing_is_a_followup_on_a_cold_start():
    """"Why?" with no history has no referent. Sending it to Agent 8 would
    produce an answer about an empty transcript, which is worse than the
    retrieval agent honestly finding nothing."""
    assert not is_followup("why?", None)
    assert not is_followup("why is that low?", Conversation(id="t"))
    assert orchestrator.classify("why?", None).kind != "followup"


@pytest.mark.parametrize("question", [
    "why is that low?",
    "explain the leave component",
    "how does she compare to the rest of DEL?",
    "and the leads?",
    "what about grooming",
    "are you sure?",
    "how many duty hours did the first one fly last month?",
    "which of those has the worst leave record?",
])
def test_wording_that_only_means_something_in_context_is_a_followup(thread, question):
    assert orchestrator.classify(question, thread).kind == "followup"


@pytest.mark.parametrize("question,kind", [
    ("Score IGA60024", "scoring"),
    ("How is IGA60024 performing?", "scoring"),
    ("rank the weakest 3 crew at DEL", "scoring"),
    ("what is the average assessment mark at BOM?", "retrieval"),
    ("how many crew are based at DEL?", "retrieval"),
])
def test_a_self_contained_question_still_routes_on_its_own_content(thread, question, kind):
    """Position in a conversation must not override what a sentence says. Agent 8
    is forbidden from computing a composite, so a scoring request swallowed as a
    follow-up would come back as a refusal instead of a scorecard."""
    assert orchestrator.classify(question, thread).kind == kind


# ─── what a thread carries ──────────────────────────────────────────────────


def test_the_thread_remembers_who_it_has_discussed(thread):
    """A follow-up naming nobody is about the crew already on screen. Losing
    that is how "why is their leave low?" becomes a fleet-wide question."""
    thread.add(scored("IGA60024"))
    assert thread.igas() == ["IGA60024", "IGA60406"], "most recent must come first"


def test_the_brief_carries_computed_numbers_not_prose(thread):
    """The model may cite a figure exactly or not at all. A transcript holding
    only the rendered sentence would force it to re-derive the number from
    English, which is how a score acquires a second, wrong value."""
    brief = thread.brief()
    assert "IGA60406" in brief and "4.2" in brief
    assert "ROUTED TO: scoring" in brief


def test_old_turns_are_dropped_rather_than_summarised():
    """A summary of a scorecard is a paraphrase of numbers, and a paraphrased
    number is the one thing this system must not carry forward. Dropping is
    honest — the agent can always re-score."""
    c = Conversation(id="t")
    for i in range(MAX_HISTORY_TURNS + 4):
        c.add(scored(f"IGA6{i:04d}", score=float(i)))
    assert len(c.turns) == MAX_HISTORY_TURNS
    assert c.turns[-1].facts["igas"] == [f"IGA6{MAX_HISTORY_TURNS + 3:04d}"]


def test_a_recorded_scoring_turn_keeps_the_numbers_it_reported():
    """A follow-up reasons over what was already answered, so the turn has to
    carry the computed figures. Re-deriving them from rendered prose is how a
    follow-up ends up contradicting the answer above it."""
    c = Conversation(id="t")
    route = orchestrator.Route("scoring", ["IGA60406"], "test")
    result = orchestrator.PipelineResult(question="q", route=route)
    orchestrator._record(c, result)
    assert c.last.facts["igas"] == []
    assert "scorecards" in c.last.facts


def test_recording_a_retrieval_turn_bounds_the_rows_it_keeps():
    """The transcript is re-sent on every later turn, so an unbounded answer
    would cost its full width for the rest of the conversation."""
    from crew_perf.agents.retrieval import RetrievalResult

    c = Conversation(id="t")
    r = RetrievalResult(question="q", sql="SELECT 1 LIMIT 500", columns=["a"],
                        rows=[(i,) for i in range(500)])
    result = orchestrator.PipelineResult(
        question="q", route=orchestrator.Route("retrieval", [], "test"), retrieval=r)
    orchestrator._record(c, result)
    assert len(c.last.facts["rows"]) == 30
    assert c.last.facts["row_count"] == 500, "the real count must survive the trim"


def test_a_pipeline_run_without_a_conversation_records_nothing():
    """The CLI and the eval harnesses ask one question and want it forgotten.
    Threading them by default would make routing depend on run order."""
    result = orchestrator.PipelineResult(
        question="q", route=orchestrator.Route("retrieval", [], "test"))
    orchestrator._record(None, result)       # must not raise


# ─── session store ──────────────────────────────────────────────────────────


def test_an_unknown_id_yields_a_thread_under_that_same_id():
    """The client has already put the id in flight. Minting a different one on a
    miss would strand the thread the user is looking at after one eviction."""
    store = SessionStore()
    assert store.get("abc123").id == "abc123"
    assert store.get(None).id != "abc123"


def test_the_same_id_returns_the_same_thread():
    store = SessionStore()
    first = store.get(None)
    first.add(scored())
    assert store.get(first.id).turns


def test_sessions_are_bounded_and_droppable():
    store = SessionStore(max_sessions=3)
    ids = [store.get(None).id for _ in range(6)]
    assert len(store) == 3
    assert store.drop(ids[-1]) and not store.drop(ids[-1])


def test_an_expired_session_is_gone_rather_than_stale():
    store = SessionStore(max_age=-1)
    old = store.get(None)
    old.add(scored())
    assert not store.get(old.id).turns, "an expired thread must not answer as itself"
