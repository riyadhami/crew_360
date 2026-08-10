"""Routing. A misroute is cheap to detect and expensive to hide: a scoring
question answered by the retrieval agent returns rows with no audit, no
confidence and no evidence trail — which still looks like an answer."""

import pytest

from crew_perf.agents import orchestrator
from crew_perf.agents.orchestrator import classify


@pytest.mark.parametrize("q", [
    "How is IGA60406 performing?",
    "Score IGA60042 for June",
    "Rank the weakest 5 crew at DEL",
    "Who are the strongest cabin attendants?",
    "give me the recent performance of IGA60130",
])
def test_scoring_questions_route_to_scoring(q):
    assert classify(q).kind == "scoring"


@pytest.mark.parametrize("q", [
    "What is the average assessment mark at DEL?",
    "How many assessments were cancelled?",
    "Which questions fail most often?",
    "Show me the grade distribution",
])
def test_data_questions_route_to_retrieval(q):
    assert classify(q).kind == "retrieval"


def test_iga_is_extracted_in_several_forms():
    assert classify("how is IGA60406 doing").igas == ["IGA60406"]
    assert classify("score IGA 60406").igas == ["IGA60406"]
    assert "IGA60406" in classify("compare IGA60406 and IGA60002").igas


def test_aggregate_wording_wins_over_ranking_words():
    """'How many' wants rows even though 'best' appears — the question is a
    count, not a scorecard."""
    r = classify("How many crew scored best last month?")
    assert r.kind == "retrieval"


def test_route_explains_itself():
    r = classify("How is IGA60406 performing?")
    assert r.reason and "IGA60406" in r.reason


@pytest.mark.parametrize("q,expected", [
    ("Rank the weakest 3 crew at DEL", 3),
    ("Who are the strongest 3 Leads?", 3),
    ("show me the top 10 crew", 10),
    ("bottom 5 at BOM", 5),
    ("rank the weakest crew", None),          # no count named
    ("How is IGA60406 performing?", None),    # one person, not a ranking
    ("the worst 500 crew", None),             # out of range, fall back to the default
])
def test_the_question_sets_how_many_crew_come_back(q, expected):
    """Answering "the weakest 3" with five scorecards is not a rounding error.
    The two extra people read as findings the user never asked for, about
    colleagues they did not name."""
    assert classify(q).count == expected


@pytest.mark.parametrize("q,expected", [
    ("Who is the worst performing crew in terms of leave and service", ["leave", "service"]),
    ("weakest crew on safety", ["safety"]),
    ("rank crew by compliance and check-in reliability", ["compliance", "reliability"]),
    ("rank crew by time management", ["punctuality"]),
    ("who is the worst performing crew", []),          # nothing named
    ("how is IGA60406 performing?", []),
])
def test_the_question_decides_which_metrics_the_ranking_counts(q, expected):
    """The question used to control who was ranked, how many came back and which
    end — never what counted. "Worst on leave and service" put 25% of the weight
    on leave and service and 57% on safety and communication, which ranks people
    on something the asker did not ask about."""
    from crew_perf.agents import weighting

    assert weighting.detect_focus(q).aspects == expected


@pytest.mark.parametrize("q,native", [
    ("who has the lowest assessment marks", True),
    ("worst crew by grade", True),
    ("worst crew in terms of leave and service", False),
    ("who is the worst performing crew", False),
    # NOT native any more. "Feedback" is not a PEP word: the mentor's mark is one
    # of at least three places feedback is written down, alongside the inflight
    # reports colleagues file naming a crew member and the appreciations on their
    # leave record. Ranking it on the mark alone answered a narrower question than
    # the one asked, using two thirds fewer sources than the airline holds.
    ("rank crew by worst mentor feedback", False),
    ("top performing crew in terms of feedback", False),
])
def test_a_question_about_the_assessment_ranks_on_the_assessment(q, native):
    """A question naming the recorded MARK is a request for that number, not for
    a weighted view of the attributes behind it. It cannot be answered by
    weighting `mean_mark` into the standing composite either: the weights are
    fitted AGAINST that mark as their label, so feeding it back in there is
    circular — it correlates 1.0 with itself and the composite becomes the native
    mark with extra steps."""
    from crew_perf.agents.orchestrator import wants_native_track

    assert wants_native_track(q) is native


@pytest.mark.parametrize("q,expected", [
    # A named aspect wins even when assessment wording is also present.
    ("rank crew by grooming marks", "focus:grooming"),
    # Safety has no scoreable attribute in the delivered data (the flags are
    # FALSE on all 345 questions), so the aspect is detected and reported as
    # unanswerable rather than silently falling through to a native ranking.
    ("worst crew by safety grade", "unscoreable:safety"),
    ("worst crew on leave and service", "focus:leave"),
    # Feedback spans sources, so it is an aspect and not the native track — see
    # `test_feedback_is_answered_from_every_source_that_holds_it`.
    ("rank crew by worst mentor feedback", "focus:feedback"),
    # Nothing more specific than the recorded mark itself.
    ("who has the lowest assessment marks", "native"),
    ("who is the worst performing crew", "standard"),
])
def test_a_named_aspect_beats_the_assessment_wording(q, expected, ws_for_exclusion):
    """"Rank crew by grooming marks" is a question about grooming that happens
    to contain the word "marks". Letting the native track win there would rank
    on the overall assessment — an answer to a question nobody asked."""
    from crew_perf.agents import weighting
    from crew_perf.agents.orchestrator import wants_native_track

    focus = weighting.detect_focus(q, ws_for_exclusion)
    native = wants_native_track(q) and not focus

    if expected == "native":
        assert native and not focus
    elif expected == "standard":
        assert not native and not focus
    elif expected.startswith("unscoreable:"):
        aspect = expected.split(":")[1]
        assert aspect in focus.aspects
        assert aspect in focus.unscoreable
        assert not focus.attributes, "nothing should be scoreable for this aspect"
    else:
        assert not native, "a named aspect must not fall through to the native track"
        assert expected.split(":")[1] in focus.aspects


def test_the_mentor_mark_stays_out_of_the_weighting(ws_for_exclusion):
    """If this ever starts being weighted, the composite has silently become a
    restatement of the label and the second track is measuring nothing."""
    excluded = {e["attribute"]: e["reason"] for e in ws_for_exclusion.excluded}
    assert "circular" in excluded.get("mean_mark", "")
    assert "mean_mark" not in {a.attribute for a in ws_for_exclusion.attributes}
    # Excluded from the STANDING weighting is not the same as discarded: it is
    # held on `reportable` at zero weight so a question that names the assessment
    # can still be answered on it. A mark that vanished entirely would make
    # "which crew get the best feedback" unanswerable from the best-attributed
    # feedback the airline holds.
    assert "mean_mark" in {a.attribute for a in ws_for_exclusion.reportable}
    assert all(a.weight == 0.0 for a in ws_for_exclusion.reportable)


@pytest.fixture(scope="module")
def ws_for_exclusion(weights):
    return weights


def test_crew_with_no_mark_are_not_ranked_as_the_worst(executor):
    """Having no submitted assessment is not the same as having a bad one, and
    the weakest-N list is exactly where that confusion does damage."""
    import numpy as np
    import pandas as pd

    from crew_perf.agents.orchestrator import _rank_native

    pop = pd.DataFrame({"mean_mark": [95.0, np.nan, 80.0]}, index=["A", "B", "C"])
    ranked = _rank_native(pop)
    assert list(ranked.index) == ["C", "A"]
    assert "B" not in ranked.index


@pytest.mark.parametrize("q", [
    "Top performing crew in terms of feedback",
    "rank crew by worst mentor feedback",
    "which crew get the best feedback",
])
def test_feedback_is_answered_from_every_source_that_holds_it(q, ws_for_exclusion):
    """"Top performing crew in terms of feedback" matched no aspect at all and
    fell through to the standing weighting — a ranking of overall performance
    handed back under the word "feedback", naming people who may have had no
    feedback recorded about them whatsoever.

    Feedback is not one system's word. It is the mentor's assessment (PEP), the
    inflight reports colleagues file naming this crew member and the
    star-performer and customer-experience nominations (ServiceNow), and the
    appreciations on their record (CLMS). A ranking that reads only one of them
    is a wrong answer, not a partial one.
    """
    from crew_perf.agents import weighting

    focus = weighting.detect_focus(q, ws_for_exclusion)
    assert "feedback" in focus.aspects
    assert focus.attributes, "feedback must be rankable, not reported unscoreable"

    # The mentor's own mark has to be in there. It is excluded from the standing
    # weighting as circular and promoted back for exactly this question.
    assert "mean_mark" in focus.attributes

    # And it must not be answerable from PEP alone.
    non_pep = [a for a in focus.attributes if not a.startswith(("pass_rate_", "mean_mark",
                                                               "top_grade_share"))]
    assert non_pep, f"feedback ranked on PEP alone: {focus.attributes}"
    assert len(focus.sources) > 1, f"only one system contributed: {focus.sources}"


def test_the_mentor_mark_is_promoted_only_when_the_question_asks_for_it(ws_for_exclusion):
    """The promotion path exists so a feedback question can count the mentor's
    mark. If it leaked into an unrelated ranking the composite would quietly
    become a restatement of the label, which is what excluding it prevents."""
    from crew_perf.agents import weighting

    assert "mean_mark" in {a.attribute for a in ws_for_exclusion.reportable}

    grooming = weighting.refocus(
        ws_for_exclusion, weighting.detect_focus("worst crew on grooming", ws_for_exclusion))
    assert grooming.weights().get("mean_mark", 0.0) == 0.0

    feedback = weighting.refocus(
        ws_for_exclusion,
        weighting.detect_focus("best crew on feedback", ws_for_exclusion))
    assert feedback.weights()["mean_mark"] > 0.0
    # ...and it must not swallow the ranking. The declared share is just over
    # half; renormalising it against the other priors on their own scale gave it
    # 91%, which is the native mark with three rounding errors attached.
    assert feedback.weights()["mean_mark"] < 0.75


# ─── ranking a quantity is not judging a person ─────────────────────────────


@pytest.mark.parametrize("question", [
    "top 5 crew which has taken lowest leaves",
    "which crew took the most leave",
    "crew with the least time off",
    "who had the highest absence",
])
def test_a_question_about_a_recorded_quantity_goes_to_sql(question):
    """The bug this catches returned the exact opposite of what was asked.

    "lowest leaves" set `wanted_worst`, so the pipeline returned the *weakest*
    five on a leave-scoped composite. Leave is lower-is-better, so those are the
    crew who took the MOST leave. Nothing errored, the prose was fluent, and the
    reasons quoted were signals carrying zero weight in that ranking.

    Ordering a column the warehouse already holds is a query, not a judgement.
    """
    assert orchestrator.asks_for_a_quantity(question)
    assert orchestrator.classify(question).kind == "retrieval"


@pytest.mark.parametrize("question", [
    "worst crew in terms of leave and service",
    "best crew on feedback",
    "rank the weakest 3 crew at DEL",
    "top performing crew in terms of feedback",
])
def test_scoping_a_judgement_by_an_aspect_still_scores(question):
    """The mirror image, and the reason this cannot be "any question mentioning
    leave goes to SQL". "Worst CREW in terms of leave" ranks people and merely
    scopes what counts; the superlative attaches to the crew, not the quantity."""
    assert not orchestrator.asks_for_a_quantity(question)
    assert orchestrator.classify(question).kind == "scoring"


def test_the_plural_the_question_actually_uses_is_matched():
    """`ASPECTS` says "leave"; people write "leaves". Matching only the singular
    left the one phrasing this exists to catch routed to scoring."""
    assert orchestrator.asks_for_a_quantity("who has the lowest leaves")
    assert orchestrator.asks_for_a_quantity("who has the lowest leave")
