"""Reporting an issue must never lower a crew member's score.

Both inversions these tests guard were live and silent. `flight_issue_rate`
counted rows from `T_FLIGHT_ISSUE_GENERAL_INFO` keyed on that table's `IGA` —
which is whoever RAISED the report — and weighted them `lower_is_better`, so a
crew member who noticed a broken tray and filed it scored below one who flew the
same aircraft and said nothing. `sn_involvement_rate` averaged `IS_INVOLVED`
across every polarity, counting catering failures, the crew's own filed reports,
and star-performer nominations against them; the last of those was simultaneously
scored as a positive by `sn_appreciation_count`.

Neither showed up as a bug. Both produced plausible numbers, passed the audit,
and were explained fluently on the scorecard — which is exactly why they need a
test rather than a comment.
"""

from __future__ import annotations

import pytest

from crew_perf import policy
from crew_perf.agents import dynamic, weighting
from crew_perf.agents.attributes import build_frame, discover
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def attrs(store, executor):
    return discover(store, executor)


# ─── The signals themselves ─────────────────────────────────────────────────


def test_report_volume_is_never_a_scored_signal(attrs):
    """A count of reports filed measures how much was written down, not how well
    anyone performed. It belongs in `volume`, which the weighting agent skips."""
    for name in ("flight_reports_filed", "sn_operational_exposure_rate"):
        attr = attrs.by_name(name)
        if attr is None:
            continue                      # source not onboarded in this dataset
        assert attr.family == "volume", f"{name} must not be a scored family"
        assert attr.prior == 0.0


def test_no_attribute_penalises_raising_a_report(attrs):
    """The specific shape to keep out: a `lower_is_better` signal computed from a
    report header, where the crew identifier is the reporter rather than someone
    the report names."""
    reporter_keyed = {"T_FLIGHT_ISSUE_GENERAL_INFO"}
    for attr in attrs.attributes:
        if attr.direction != "lower_is_better":
            continue
        assert not reporter_keyed & set(attr.source_tables), (
            f"{attr.name} counts against crew using a table keyed on who filed "
            f"the report — attribution must come from who the report names"
        )


def test_servicenow_negatives_are_restricted_to_crew_attributable_polarity(attrs):
    """OPERATIONAL is an upstream failure, REPORTING is the process working and
    APPRECIATION is a commendation. None of the three may reach a scored negative.

    A negative therefore has to narrow on what the report SAYS, by one of three
    routes: the polarity reading of the category, the specific service check, or
    an explicit list of sub-categories. The last is the strictest of the three —
    it names the exact rows rather than trusting a whole polarity — and is how
    the inflight-feedback concerns are cut, because the `Crew Feedback` category
    holds praise and administrative filings alongside the concerns.
    """
    from crew_perf.agents.attributes import _CROSS_SQL

    for attr in attrs.attributes:
        if attr.direction != "lower_is_better" or not attr.name.startswith("sn_"):
            continue
        sql = _CROSS_SQL.get(attr.name, "")
        assert any(guard in sql for guard in ("POLARITY", "CHECK_CODE", "SUB_CATEGORY_CODE")), (
            f"{attr.name} scores ServiceNow involvement without filtering on what "
            f"the report says about the crew"
        )


def test_being_named_on_a_service_report_is_never_scored(attrs):
    """`CREW_INVOLVED` records who ACTED. The reports name L4 for finding a broken
    fire extinguisher window on a pre-flight check, R1 for intervening in a
    physical assault, and the whole cabin crew for serving cold drinks when
    turbulence stopped the hot service. 6,674 SERVICE-polarity records are
    "Service Recovery Done". Nothing in the extract records fault, so no direction
    can be claimed — the rate is reported, never weighted."""
    attr = attrs.by_name("sn_service_response_rate")
    if attr is None:
        pytest.skip("ServiceNow not onboarded in this dataset")
    assert attr.family == "volume"
    assert attr.prior == 0.0
    assert attrs.by_name("sn_service_issue_rate") is None, (
        "the fault-framed name is back — being named on a report is not a complaint"
    )


def test_detection_quality_is_scored_and_positive(attrs):
    """The one signal that lets reporting count FOR a crew member. Substantiation
    is recorded by somebody else — a cabin defect log entry, or a human resolver
    rather than the portal auto-acknowledging — so it is not the crew grading
    their own reports."""
    from crew_perf.agents.attributes import _CROSS_SQL

    attr = attrs.by_name("sn_validated_report_rate")
    if attr is None:
        pytest.skip("ServiceNow not onboarded in this dataset")
    assert attr.direction == "higher_is_better"
    assert attr.family != "volume", "detection quality must be scoreable"

    sql = _CROSS_SQL["sn_validated_report_rate"]
    assert "CDLB_ENTRY_MADE" in sql and "IS_AUTO_RESOLVED" in sql
    # Denominator must be reports the crew member was named on. Over every report
    # on their flights it would measure how much they flew, not how well they saw.
    assert "IS_INVOLVED = TRUE" in sql


def test_no_report_is_both_evidence_for_and_against(attrs):
    """A star-performer report used to be a positive in `sn_appreciation_count`
    and a negative in `sn_involvement_rate`, from the same flag. Adding a positive
    detection signal reopens exactly that risk, so the invariant is stated once:
    no ServiceNow report may reach a scored signal in both directions."""
    scored = [a for a in attrs.attributes
              if a.name.startswith("sn_") and a.family != "volume"]
    negatives = {a.name for a in scored if a.direction == "lower_is_better"}
    positives = {a.name for a in scored if a.direction == "higher_is_better"}
    assert not (negatives & positives)
    # A scored ServiceNow negative is only allowed where the source RECORDS a
    # shortfall attributed to a named crew member, rather than recording that
    # they acted. Exactly two qualify:
    #
    #   sn_service_deviation_rate          the operating lead's own answer that
    #                                      the service standard was missed
    #   sn_inflight_feedback_concern_rate  a colleague writing up this crew
    #                                      member's conduct, grooming, late
    #                                      reporting or handover, under the one
    #                                      ServiceNow category that is about the
    #                                      crew rather than about the flight
    #
    # Anything else naming a crew member on a report records what they DID about
    # something — service recovery, finding a defect, separating fighting
    # passengers — and scoring it negative penalises the response to a problem.
    allowed = {"sn_service_deviation_rate", "sn_inflight_feedback_concern_rate"}
    assert negatives <= allowed, (
        f"unexpected ServiceNow negative(s): {negatives - allowed}"
    )


def test_inflight_feedback_splits_praise_from_concern(attrs):
    """The `Crew Feedback` category holds both — "Wow Moments Created Zone Wise"
    is praise, "Conduct Of Cabin Crew" is a concern — plus administrative
    filings ("All world Passport", "International Layover Sign In") that are
    neither. Averaged together they cancel: a crew member praised twice and
    flagged twice reads as unremarkable, when two people wrote down two strong
    and opposite things about them."""
    from crew_perf.agents.attributes import (
        INFLIGHT_FEEDBACK_CONCERN, INFLIGHT_FEEDBACK_PRAISE, _CROSS_SQL,
    )

    praise = attrs.by_name("sn_inflight_feedback_praise_rate")
    concern = attrs.by_name("sn_inflight_feedback_concern_rate")
    if praise is None or concern is None:
        pytest.skip("ServiceNow not onboarded in this dataset")

    assert praise.direction == "higher_is_better"
    assert concern.direction == "lower_is_better"
    assert not set(INFLIGHT_FEEDBACK_PRAISE) & set(INFLIGHT_FEEDBACK_CONCERN)

    # Neither may sweep up the administrative sub-categories, and both must
    # require the report to NAME the crew member.
    for name in ("sn_inflight_feedback_praise_rate", "sn_inflight_feedback_concern_rate"):
        sql = _CROSS_SQL[name]
        assert "IS_INVOLVED" in sql, f"{name} counts reports the crew were not named on"
        assert "ALL_WORLD_PASSPORT" not in sql
        assert "INTERNATIONAL_LAYOVER_SIGN_IN" not in sql


def test_no_mechanism_can_reintroduce_the_reporting_inversion():
    """Filing a report must never lower a score, and the guard is structural.

    There used to be a second guarantee here: that no declared business prior
    listed a report count. That prior is gone, so the only thing standing between
    a crew member and being penalised for noticing a defect is this set — asserted
    in code rather than left to a model reading a column description, which reads
    a rising count of issue reports as a complaint rate every time.
    """
    from crew_perf.agents.dynamic import REPORTING_OR_ENVIRONMENT

    for banned in ("flight_reports_filed", "sn_operational_exposure_rate",
                   "sn_service_response_rate"):
        assert banned in REPORTING_OR_ENVIRONMENT, (
            f"{banned} counts reporting or exposure and must never be scored"
        )


# ─── The fitted mechanisms ──────────────────────────────────────────────────


def test_business_weightset_scores_no_reporting_volume(store, executor):
    ws, _ = dynamic.design(store=store, executor=executor, use_llm=False)
    scored = {a.attribute for a in ws.attributes}
    for banned in ("flight_reports_filed", "sn_operational_exposure_rate",
                   "sn_service_response_rate",
                   # the pre-fix names, in case a rename is ever reverted
                   "flight_issue_rate", "sn_involvement_rate", "sn_service_issue_rate"):
        assert banned not in scored, f"{banned} is being scored again"


def test_semantic_pass_cannot_promote_a_reporting_count(store, executor):
    """Agent 7 reads meaning with a language model, and asked what a rising count
    of flight issue reports says about a crew member it answers that fewer is
    better — the industry reflex, and the reading that gave `flight_issue_rate`
    the largest construct weight in the mechanism. The classification is asserted
    structurally so the model is never offered the choice."""
    attrs = discover(store, executor)
    frame = build_frame(executor, attrs)
    signals = dynamic.profile(frame, attrs, store)

    # No LLM: the fallback path must honour the same assertion.
    dynamic.read_semantics(signals, store, use_llm=False)
    for s in signals:
        if s.name in dynamic.REPORTING_OR_ENVIRONMENT:
            assert s.role == "context"
            assert s.attribution == "environmental"


def test_reporting_counts_are_present_but_unweighted(store, executor):
    """Rejected, not deleted. The rows are real and worth seeing beside a score;
    what they may not do is move it."""
    attrs = discover(store, executor)
    computable = {a.name for a in attrs.attributes}
    if "flight_reports_filed" not in computable:
        pytest.skip("CrewPortal not onboarded in this dataset")

    ws, _ = dynamic.design(store=store, executor=executor, use_llm=False)
    excluded = {e["attribute"] for e in ws.excluded}
    assert "flight_reports_filed" in excluded
