"""Declared policy — the judgements that no data can settle.

Split deliberately from `crew_perf/rules.py`, which resolves the rules the source
system already records (grading bands, template selection) by reading them.

**Only the JUDGEMENTS belong here.** How much each dimension matters, and how
many assessments are enough to rank someone, are decisions a business makes;
nothing observable settles them. Grading boundaries are not in that category —
`PEP_DEVIATION_MATRIX` records them, so restating them here would mean a real
matrix could disagree with us and nothing would notice.

The blocks below marked REFERENCE ONLY are documentation of how the source system
behaves. Nothing reads them at runtime, and editing them changes no behaviour —
they are here so the assumptions are written down, and are labelled so nobody
edits them expecting an effect.
"""

from __future__ import annotations

# REFERENCE ONLY — resolved at runtime from PEP_TEMPLATE by rules.resolve().
# Used solely as the fallback when that table cannot be read.
TEMPLATE_SELECTION = {
    "rule": "designation x fleet selects the template and its mark column",
    "mapping": {
        "CA|A320": {"template_code": "CA_A320_V4", "mark_column": "MARKS"},
        "LD|A320": {"template_code": "LD_A320_V4", "mark_column": "LD_MARKS"},
        "CA|ATR": {"template_code": "CA_ATR_V4", "mark_column": "ATRCA", "assumed": True},
        "LD|ATR": {"template_code": "LD_ATR_V4", "mark_column": "ATRLD", "assumed": True},
    },
    "open_item": "the ATR pair is inferred from column naming, not confirmed",
}

# REFERENCE ONLY — documents how the source system computes a mark. The system
# reads the computed MENTOR_FEEDBACK.MARK rather than recomputing it, so nothing
# here drives behaviour.
MARK_AGGREGATION = {
    "answer_column": "PEP_QUESTION_FEEDBACK.FEEDBACK",
    "true_value": "true",
    "false_value": "false",
    "award_on_true": True,
    "partial_credit": False,
    "template_total": 100,
    "mark_column_is_text": True,
    "uncastable_marks_are_missing_not_zero": True,
}

# FALLBACK ONLY — the authority is PEP_DEVIATION_MATRIX, read by rules.resolve().
# These boundaries were chosen to reproduce the observed clustering of marks in
# the low-to-high 90s and are used only if that table cannot be read, in which
# case the fallback is reported rather than applied silently.
GRADING = {
    # The real scale is A/B/C, from the PEP_GRADE extract — not the A+/A/B+/B
    # previously assumed. C is the lowest PASSING grade, confirmed with the
    # business, so the scale still has no failing band.
    "bands": [
        {"grade": "A", "start": 96, "end": 100},
        {"grade": "B", "start": 92, "end": 95},
        {"grade": "C", "start": 0, "end": 91},
    ],
    "floor_grade": "C",
    "has_failing_grade": False,
    "source_table": "PEP_DEVIATION_MATRIX",
    "open_item": "boundaries are ASSUMED — PEP_DEVIATION_MATRIX exists in dev but "
                  "carries no usable rows, so rules.resolve() will keep reporting "
                  "grading as 'declared' until a populated matrix arrives",
}

# CONSUMED — rules.submitted_predicate() emits the SQL filter from this, so the
# status literal cannot drift between the rule and the queries that apply it.
ASSESSMENT_VALIDITY = {
    # Confirmed against the PEP_STATUS extract: 2 = Completed. The literal was
    # right; the labels below were not — 3 and 4 were guessed as Cancelled/Draft.
    "counted_status": 2,
    "status_meanings": {
        1: "Pending", 2: "Completed", 3: "Request pending with Admin",
        4: "Request approved", 5: "Rejected", 6: "Link Expired",
        7: "Request Pending For Deletion",
    },
    "reason_is_context_not_score": True,
}

# CONSUMED — scoring.py reads these thresholds on every scorecard.
CONFIDENCE = {
    "indicative_below": 2,
    "usable_from": 2,
    "sufficient_for_ranking_from": 4,
    "single_assessment_may_not_be_ranked": True,
}

# REFERENCE ONLY — the behaviour it describes is implemented directly in
# scoring.critical_findings() and enforced by the evaluator.
CRITICALITY = {
    "flags": ["SAFETY_ACTION_PARAMETER", "CRITICAL", "CRUCIAL"],
    "high_mark_does_not_cancel_critical_failure": True,
    "report_critical_failures_separately": True,
    # DORMANT against the delivered data. In the dev extract of PEP_QUESTIONS,
    # SAFETY_ACTION_PARAMETER and CRUCIAL are FALSE on all 345 rows, and CRITICAL
    # is TRUE on 4 — three inactive, one active and worth 0 marks. So the rule is
    # implemented and enforced, but nothing currently triggers it.
    #
    # Kept rather than removed: if a future extract populates these flags the
    # machinery lights up unchanged. `scoring.critical_findings()` reports the
    # dormancy rather than silently returning nothing.
    "dormant_in_delivered_data": True,
    "colour_is_weight_not_severity": "COLOUR is the only populated ordinal "
                                      "signal, and it tracks marks (GREEN~1.7, "
                                      "YELLOW~2.6, RED~3.4), not criticality",
}

# REFERENCE ONLY — the assessment form's own categories, for readers. The display
# name of each is read at runtime from PEP_CATEGORY.CATEGORY, so nothing here
# drives behaviour; it is recorded because `pass_rate_<code>` attribute names are
# built from these codes and are otherwise unreadable.
#
#   code             what the form calls it     form marks (A320 CA)
#   TIMEMANAGEMENT   Time Management                   11.70
#   GROOMING         Poise & Grace                      6.40
#   INFLIGHTEXP      Customer Focus                    35.45
#   AFTERTAKEOFF     Service Delivery                  33.60
#   PLCHECKLIST      Ground Duties                      5.10
#   CLEANLINESS      Cabin Management                   7.75
#   LEADSONLY        Inspiring Leadership          Leads only
#
# There is deliberately no declared importance here any more. Weighting is
# derived from the records by `agents/dynamic.py`, which reads no statement of
# what matters at all — see that module for why that is the only mechanism.

# REFERENCE ONLY — constraints the design honours; not read at runtime.
FAIRNESS = {
    "mentor_severity_varies": True,
    "templates_not_comparable_across_designations": True,
    "assessments_unevenly_spaced": True,
    "missing_data_reduces_coverage_never_score": True,
}

# Data the system does not have and will not get.
OUT_OF_SCOPE = {
    "duty hours": "no roster source is onboarded and none is planned",
    "flight hours": "no roster source is onboarded and none is planned",
    "sectors operated": "no roster source is onboarded and none is planned",
}

PARAMETERS = {
    "template_selection": TEMPLATE_SELECTION,
    "mark_aggregation": MARK_AGGREGATION,
    "grading": GRADING,
    "assessment_validity": ASSESSMENT_VALIDITY,
    "confidence": CONFIDENCE,
    "criticality": CRITICALITY,
    "fairness": FAIRNESS,
}

# Human-readable rule names, cited by scorecard components so a number can be
# traced to the rule that produced it.
RULE_NAMES = {
    "template_selection": "Template selection (designation x fleet)",
    "mark_aggregation": "Mark aggregation",
    "grading": "Grading bands",
    "assessment_validity": "Assessment validity",
    "confidence": "Confidence by sample size",
    "criticality": "Question criticality and safety parameters",
    "fairness": "Fairness constraints",
}


def get(name: str) -> dict:
    return PARAMETERS.get(name, {})


def rule_exists(name: str) -> bool:
    return name in PARAMETERS

