"""The business Q&A pairs must stay true of the data.

They are the artifact people quote from, which makes a stale number here worse
than a missing one — it reads exactly like a fresh one. The SQL-verified pairs
run against the test snapshot on every `pytest`; the mechanism-verified ones need
both weightsets on disk and are exercised by `crewperf business-qa`.
"""

from __future__ import annotations

import sys

import pytest

from crew_perf import config

sys.path.insert(0, str(config.EVAL_DIR))

from business_qa_runner import DIFFICULTIES, load_pairs, run_pair  # noqa: E402


@pytest.fixture(scope="module")
def pairs():
    return load_pairs()


def test_every_difficulty_is_represented(pairs):
    levels = {p.get("difficulty") for p in pairs}
    assert levels == set(DIFFICULTIES)
    for level in DIFFICULTIES:
        assert sum(1 for p in pairs if p["difficulty"] == level) >= 3


def test_every_pair_is_verifiable(pairs):
    """An answer with no computation behind it is a claim, not a result."""
    for p in pairs:
        assert p.get("answer", "").strip(), f"{p['id']} has no answer"
        verify = p.get("verify") or {}
        assert verify.get("expect"), f"{p['id']} declares nothing to check"
        if verify.get("kind") != "mechanism":
            assert "sql" in verify, f"{p['id']} has no query to reproduce it"


def test_every_question_is_about_crew_performance(pairs):
    """These pairs are the crew-performance deliverable. A question about how many
    rows a source holds is a retrieval test — it belongs in the golden set, and
    here it quietly turns the artifact into a data inventory."""
    subject = (
        "perform", "score", "scoring", "scorecard", "assessment", "assessed",
        "mark", "grade", "rank", "weakest", "strongest", "crew who", "crew are",
        "crew have", "crew members", "leads", "cabin attendant", "mechanism",
    )
    for p in pairs:
        question = p["question"].lower()
        assert any(term in question for term in subject), \
            f"{p['id']} does not ask about crew performance: {p['question']!r}"


def test_ids_are_unique(pairs):
    ids = [p["id"] for p in pairs]
    assert len(ids) == len(set(ids))


def test_hard_pairs_state_their_caveat(pairs):
    """A cross-source or cross-mechanism answer without a caveat about coverage,
    attribution or which mechanism produced it is misleading even when the number
    is right."""
    hedges = ("coverage", "18%", "partial", "attribut", "mechanism", "synthetic",
              "not comparable", "caveat", "names a seat")
    for p in pairs:
        if p["difficulty"] != "hard":
            continue
        answer = p["answer"].lower()
        assert any(h in answer for h in hedges), f"{p['id']} states no caveat"


@pytest.mark.parametrize("pair", [p for p in load_pairs()
                                  if (p.get("verify") or {}).get("kind") != "mechanism"],
                         ids=lambda p: p["id"])
def test_sql_verified_answers_still_hold(pair, executor):
    result = run_pair(pair, executor=executor)
    assert result.passed, "; ".join(result.failures)
