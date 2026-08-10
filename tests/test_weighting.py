"""The weighting vocabulary, and question-scoped emphasis.

The mechanism that produces weights is Agent 7 (`agents/dynamic.py`) and is
tested in `test_dynamic.py`. What is held here is what surrounds it: a weightset
normalises, round-trips, refuses to score its own label, and — the part that
earns the most tests — narrows onto the aspects a question actually named.

There used to be a second mechanism starting from a declared business prior.
It is gone, so the tests that held *it* to its claims are gone with it; the ones
below are the ones that were never about which mechanism produced the weights.
"""

import pytest

from crew_perf.agents import weighting
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def ws(weights):
    return weights


def test_weights_are_normalised(ws):
    assert abs(sum(a.weight for a in ws.attributes) - 1.0) < 1e-5  # 6dp rounding across many weights accumulates ~5e-6


def test_every_weight_says_why_it_is_that_size(ws):
    """A weight nobody can account for is a number the reader has to take on
    faith. There is no declared prior any more — the share comes from the data —
    so the rationale is the only place the reasoning survives."""
    for a in ws.attributes:
        assert a.rationale, f"{a.attribute} carries a weight with no stated reason"
        assert 0 <= a.coverage <= 1
        assert a.weight >= 0


def test_volume_attributes_are_never_scored(ws):
    """Assessment count governs confidence, not performance (the confidence rule)."""
    scored = {a.attribute for a in ws.attributes}
    assert "assessment_count" not in scored
    assert "mentor_count" not in scored


def test_the_label_is_not_an_input_to_itself(ws):
    scored = {a.attribute for a in ws.attributes}
    assert "mean_mark" not in scored
    assert "top_grade_share" not in scored


def test_lead_only_category_is_kept_but_marked_partial(ws):
    """Coaching is unobservable for ~75% of crew. Dropping it silently lost a real
    signal for the Leads who DO have it; keeping it unmarked would imply every
    composite was built from the same attributes. It is kept and flagged, and the
    flag is what the scorecard reports."""
    partial = [a for a in ws.attributes if a.partial_population]
    assert partial, "a signal covering part of the fleet must be kept and flagged"
    for a in partial:
        assert a.coverage < weighting.MIN_COVERAGE
        assert a.rationale, "a partial signal must say so where a reader will see it"


def test_the_mechanism_is_deterministic(store, executor):
    """Two runs over the same data must weight it the same way. The semantic pass
    only names what the numbers decided, so it is skipped here — a mechanism that
    moved when an LLM was reachable would make every score unrepeatable."""
    from crew_perf.agents import dynamic

    a, _ = dynamic.design(store=store, executor=executor, use_llm=False)
    b, _ = dynamic.design(store=store, executor=executor, use_llm=False)
    assert a.weights() == b.weights()


def test_weightset_round_trips(ws, tmp_path):
    path = tmp_path / "ws.json"
    path.write_text(__import__("json").dumps(ws.to_dict(), default=str))
    loaded = weighting.load(path)
    assert loaded.weights() == ws.weights()
    assert loaded.id == ws.id


# ─── Question-scoped emphasis ────────────────────────────────────────────────


def test_a_question_that_names_aspects_reweights_onto_them(ws):
    """"Worst crew in terms of leave and service" was answered with the standard
    weights: 25% of them on leave and service, 57% on safety and communication.
    That is a ranking of overall performance wearing the question's label, and it
    names different people than the question asked about."""
    q = "Who is the worst performing crew in terms of leave and service in flights"
    focus = weighting.detect_focus(q, ws)
    assert focus.aspects == ["leave", "service"]

    fw = weighting.refocus(ws, focus)
    counted = {a.attribute: a.weight for a in fw.attributes if a.weight}
    assert set(counted) == {"leave_days_taken", "pass_rate_aftertakeoff"}
    assert abs(sum(counted.values()) - 1.0) < 1e-6
    assert not any(a.weight for a in fw.attributes
                   if a.attribute.startswith("pass_rate_inflightexp"))


def test_a_volume_signal_is_promoted_when_the_question_asks_for_it(ws):
    """`leave_days_taken` is held out of the standing weighting because exposure is
    not quality — but it is the *semantic* pass that reads it as volume, and the
    fixture above is built with `use_llm=False`, which admits it as an ordinary
    signal. So every test here passed while the shipped build answered "rank the
    crew best in terms of leave" with the standing weighting: a ranking of overall
    performance wearing the question's label, with no leave signal anywhere in the
    breakdown a reader could check.

    The weightset is reshaped into what the semantic pass actually produces rather
    than mocked, so the numbers stay the mechanism's own.
    """
    from dataclasses import replace

    held = next(a for a in ws.attributes if a.attribute == "leave_days_taken")
    as_read = replace(
        ws, attributes=[a for a in ws.attributes if a.attribute != "leave_days_taken"])
    as_read.reportable = [*ws.reportable, replace(held, weight=0.0, prior=0.0, held_for="volume")]
    as_read.excluded = [*ws.excluded, {
        "attribute": "leave_days_taken", "kind": "by_design",
        "reason": "measures exposure or volume, not quality — kept for confidence"}]

    focus = weighting.detect_focus("rank the crew best in terms of leave", as_read)
    assert focus.aspects == ["leave"]
    assert not focus.unscoreable, "held back for a general ranking is not unavailable"

    fw = weighting.refocus(as_read, focus)
    counted = {a.attribute: round(a.weight, 6) for a in fw.attributes if a.weight}
    assert counted == {"leave_days_taken": 1.0}, (
        "a question naming only leave must be ranked on leave alone")

    # and it must not leak back into a ranking that did not ask for it
    assert not any(a.attribute == "leave_days_taken" and a.weight
                   for a in as_read.attributes)


def test_a_promoted_volume_signal_shares_with_what_else_was_named(ws):
    """Promotion must not become a takeover. "Leave and service" is two aspects,
    and the volume signal has no reserved share to claim — unlike the recorded
    verdict, which is capped precisely because it would otherwise swamp the rest.
    """
    from dataclasses import replace

    held = next(a for a in ws.attributes if a.attribute == "leave_days_taken")
    as_read = replace(
        ws, attributes=[a for a in ws.attributes if a.attribute != "leave_days_taken"])
    as_read.reportable = [*ws.reportable, replace(held, weight=0.0, prior=0.0, held_for="volume")]

    focus = weighting.detect_focus("worst crew in terms of leave and service", as_read)
    fw = weighting.refocus(as_read, focus)
    counted = {a.attribute: a.weight for a in fw.attributes if a.weight}

    assert set(counted) == {"leave_days_taken", "pass_rate_aftertakeoff"}
    assert abs(sum(counted.values()) - 1.0) < 1e-6
    assert all(0.05 < w < 0.95 for w in counted.values()), (
        f"neither aspect should take the whole ranking: {counted}")


def test_focus_preserves_the_declared_ordering_within_it(ws):
    """The question chooses which attributes count. It must not choose how they
    rank against each other — that stays a declared business judgement."""
    focus = weighting.detect_focus("rank on service and cabin cleanliness", ws)
    fw = weighting.refocus(ws, focus)
    got = {a.attribute: (a.prior, a.weight) for a in fw.attributes if a.weight}
    ordering = sorted(got, key=lambda k: -got[k][0])
    assert ordering == sorted(got, key=lambda k: -got[k][1])


def test_an_unfocused_attribute_is_reported_at_zero_not_deleted(ws):
    """A scorecard has to show what the ranking chose not to count, or a reader
    cannot tell a narrow ranking from a complete one."""
    focus = weighting.detect_focus("worst on leave", ws)
    fw = weighting.refocus(ws, focus)
    assert len(fw.attributes) == len(ws.attributes)
    zeros = [a for a in fw.attributes if not a.weight]
    assert zeros and all("not in focus" in a.rationale for a in zeros)


def test_a_question_naming_nothing_keeps_the_standard_weighting(ws):
    """Degrading to the declared weighting is the safe direction: a missed
    keyword gives a normal ranking, never a silently arbitrary one."""
    focus = weighting.detect_focus("who is the worst performing crew", ws)
    assert not focus
    assert weighting.refocus(ws, focus) is ws


def test_focus_does_not_refit_the_evidence(ws):
    """Coverage, dispersion and correlation are properties of the data, not of
    the question. Re-running them per question would let a phrasing change alter
    what counts as a reliable signal."""
    fw = weighting.refocus(ws, weighting.detect_focus("leave and service", ws))
    before = {a.attribute: (a.n, a.coverage, a.rho_label) for a in ws.attributes}
    after = {a.attribute: (a.n, a.coverage, a.rho_label) for a in fw.attributes}
    assert before == after


def test_an_aspect_with_no_usable_data_is_named_as_unanswered(ws):
    """Safety questions are unflagged in the delivered extract, so nothing scores
    them. Asking about safety must say that part is unanswered, not quietly rank
    everyone on the remaining aspect."""
    focus = weighting.detect_focus("worst crew on safety and service", ws)
    assert "safety" in focus.aspects
    assert "safety" in focus.unscoreable
    assert "safety_failure_rate" not in focus.attributes
    fw = weighting.refocus(ws, focus)
    assert any("unanswered" in n for n in fw.notes)


def test_the_focus_is_recorded_on_the_weightset(ws):
    """An unlabelled focused score is not comparable with a standard one, and
    nobody can tell from the number which they are looking at."""
    fw = weighting.refocus(ws, weighting.detect_focus("leave", ws))
    assert fw.focus and fw.focus.aspects == ["leave"]
    assert "focus" in fw.id
    assert fw.to_dict()["focus"]["aspects"] == ["leave"]


def test_a_focused_weighting_still_sums_to_one(ws):
    """The bug this catches is silent, and it produced a plausible answer.

    `refocus` decides whether to renormalise on `prior` or on the derived weight.
    Reading that off the whole in-focus pool meant one promoted signal carrying a
    prior flipped every OTHER attribute onto `prior` — where the derived
    mechanism puts 0.0 by design. Total weight came out at 0.03, the ranking
    turned on the single promoted signal, and five crew tied at 7.95 because they
    all had full marks. Nothing errored; the number was just built from one
    signal instead of four.
    """
    for question in ("best in terms of feedback and assessment",
                     "worst crew in terms of leave and service",
                     "rank crew by grooming"):
        focus = weighting.detect_focus(question, ws)
        if not focus:
            continue
        focused = weighting.refocus(ws, focus)
        total = sum(focused.weights().values())
        assert abs(total - 1.0) < 1e-4, f"{question!r} weights sum to {total}"


def test_a_feedback_ranking_is_not_carried_by_the_mark_alone(ws):
    """Feedback spans three systems. If the mentor's mark is the only thing with
    weight, the answer is the recorded mark wearing a broader label — and every
    crew member on full marks ties."""
    focus = weighting.detect_focus("best crew on feedback", ws)
    focused = weighting.refocus(ws, focus)
    counted = {a: w for a, w in focused.weights().items() if w > 0.0005}

    assert "mean_mark" in counted, "the mark must be counted when asked for"
    assert len(counted) > 1, f"only the mark carried weight: {counted}"
    assert counted["mean_mark"] <= 0.75, (
        f"the mark took {counted['mean_mark']:.0%} — it must not swallow the ranking"
    )


def test_the_recorded_verdict_share_goes_to_the_mark_not_its_own_derivative(ws):
    """`top_grade_share` is how often the same mark landed in the top band — a
    coarser reading of `mean_mark`, not a second opinion. Splitting the reserved
    share between them by measured reliability gave the band 0.47 and the mark
    0.03, because a near-binary indicator disperses less than a mark out of 100."""
    priors = {a.attribute: a.prior for a in ws.reportable}
    assert priors.get("mean_mark", 0) > priors.get("top_grade_share", 0)
    assert all(a.weight == 0.0 for a in ws.reportable), (
        "the verdict must weigh nothing until a question asks for it"
    )


def test_an_aspect_that_matches_nothing_cannot_hand_the_ranking_to_the_other(ws):
    """"Top 5 crews based on their achievements and service" came back as Service
    Delivery at 100%.

    `achievement` was in no aspect's terms, and an aspect that matches nothing is
    not reported as unanswerable — it simply never appears. So `service` was the
    only match and took the entire ranking: half the question, answered under the
    whole question's label, with no recognition signal anywhere in the breakdown.
    """
    focus = weighting.detect_focus(
        "Top 5 crews based on their achievements and service", ws)
    assert set(focus.aspects) == {"service", "recognition"}

    fw = weighting.refocus(ws, focus)
    counted = {a.attribute: a.weight for a in fw.attributes if a.weight}
    assert "pass_rate_aftertakeoff" in counted, "service dropped out"
    assert counted["pass_rate_aftertakeoff"] < 0.9, (
        f"service is still taking the ranking: {counted}")
    assert any(a.startswith(("appreciation", "cac_", "sn_appreciation", "sn_cx"))
               for a in counted), f"nothing recognition-shaped counted: {counted}"


def test_achievements_are_read_from_every_system_that_records_one(ws):
    """An achievement is recorded wherever somebody happened to record it — a
    letter through the Appreciation Centre, an appreciation keyed into CLMS, a
    star-performer or CX nomination in ServiceNow. Ranking on one of them ranks
    on which system the crew member's manager happens to use."""
    focus = weighting.detect_focus("who has the most achievements", ws)
    assert focus.aspects == ["recognition"]
    assert {"appreciation_count", "cac_appreciation_count",
            "sn_appreciation_count"} <= set(focus.attributes)
    # and the answer must be able to say which records it read
    assert len(focus.sources) >= 2, focus.sources
