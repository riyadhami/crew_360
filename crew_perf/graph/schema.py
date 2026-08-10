"""Vertex/edge vocabulary for the crew-performance knowledge graph.

Defined once, here, so the builder, the SQL validator, the weighting agent and
the evaluator all agree on what a "measure" column is or what `BELONGS_TO` means.
The donor repo left this implicit in prompt strings, which is why its column
semantics drifted between agents.
"""

from __future__ import annotations

from enum import StrEnum


class VertexLabel(StrEnum):
    """Vertex types. Only these four ever exist in the graph.

    Scoring rules are NOT vertices. They live in `crew_perf/policy.py` as plain
    configuration, are read there by the agents that apply them, and are cited by
    name on a scorecard component. A rule that also existed as a graph vertex
    could disagree with the one the code reads, and nothing would notice.
    """

    TABLE = "Table"          # a physical table in a source system
    CONCEPT = "Concept"      # a semantic grouping of columns across tables
    WEIGHTSET = "WeightSet"  # a versioned scoring weight vector (Agent 7 output)
    IDENTITY = "CrewIdentity"  # one crew member, with all cross-system aliases
    FEEDBACK = "Feedback"    # human/agent note; also carries evaluator gap reports


class EdgeLabel(StrEnum):
    """Edge types."""

    BELONGS_TO = "BELONGS_TO"      # Table   -> Concept
    JOINS_TO = "JOINS_TO"          # Table   -> Table   (carries confidence/evidence)
    SCORES = "SCORES"              # WeightSet -> Concept | Table
    IDENTIFIES = "IDENTIFIES"      # CrewIdentity -> Table (where this crew appears)
    ABOUT = "ABOUT"                # Feedback -> any vertex
    RELATES_TO = "RELATES_TO"      # generic semantic link


class ColumnRole(StrEnum):
    """Per-column classification. Drives SQL validation and attribute discovery.

    The two that carry real behaviour:
      TEMPORAL_CONTROL -> forces a `P_IS_CURRENT = TRUE` predicate (G7)
      MEASURE          -> the only role eligible to become a scoring attribute
    """

    IDENTITY = "identity"                  # join keys: IGA, CrewID, EMPLOYEE_ID
    MEASURE = "measure"                    # numeric/boolean facts worth scoring
    DIMENSION = "dimension"                # categorical slicers: BASE, DESIGNATION
    TEMPORAL = "temporal"                  # business dates: FLIGHT_DATE, PEP_DATE
    TEMPORAL_CONTROL = "temporal_control"  # SCD-2 control: P_IS_CURRENT, ROW_HASH
    AUDIT = "audit"                        # P_CREATED_BY, LOAD_DATE — never scored
    FREE_TEXT = "free_text"                # STRENGHTH, REMARKS — qualitative only


class JoinSource(StrEnum):
    """Where a JOINS_TO edge came from. Asserted beats inferred."""

    INFERRED = "inferred"    # name/type/cardinality evidence        -> confidence < 1.0
    ASSERTED = "asserted"    # declared in the registry by a human
    DECLARED = "declared"    # stated by the curated relationship export


# ─── Column-role heuristics ─────────────────────────────────────────────────
# Deterministic pre-classification applied before the LLM sees a table. Audit and
# SCD-2 columns are ~40% of every PEP table and are mechanically identifiable, so
# spending LLM calls (and risking drift) on them is waste.

SCD2_CONTROL_COLUMNS = {"P_IS_CURRENT", "ROW_HASH"}

AUDIT_COLUMNS = {
    "LOAD_DATE", "P_CREATED_BY", "P_CREATED_DT", "P_MODIFIED_BY", "P_MODIFIED_DT",
    "CREATED_BY", "CREATED_ON", "CREATED_DATE", "MODIFIED_BY", "MODIFIED_ON",
    "MODIFIED_DATE", "UPDATED_ON",
}

IDENTITY_COLUMN_HINTS = {
    "IGA", "CREWID", "CREW_ID", "EMPLOYEE_ID", "EMPLOYEEID", "PEP_ID",
    "PEP_SCHDULER_ID", "PEP_SCHEDULER_ID", "MENTOR_LOGIN",
}

# ─── Personal data ──────────────────────────────────────────────────────────
#
# The warehouse hashes personal data behind the crew identifier, so IGA (and
# CREW_ID, which reaches IGA through the bridge) is the only thing that means
# "this crew member". Everything else a person could be recognised by — their
# name, email, phone, address, passport, date of birth, gender — is not a
# performance signal, is not a join key, and must not reach a query, a prompt,
# an answer or a cached scorecard.
#
# Enforced by dropping these columns where the exports are parsed, so nothing
# downstream can ask for what it never sees: the graph has no such column, the
# validator refuses it as unknown, the digest never spends tokens on it and
# attribute discovery cannot build a signal from it. A deny-list at the far end
# would leave the columns visible in every prompt that lists a table.
#
# **Matched exactly, never by pattern.** `COMPLIANCE_NAME`, `CATEGORY_NAME`,
# `STATION_NAME` and `AIRPORT_NAME` all contain "NAME" and are reference labels
# a question legitimately needs; a regex for personal data removes the decode
# tables and leaves the answer full of raw codes.
PII_COLUMNS = {
    # who they are
    "EMPLOYEE_NAME", "CREW_NAME", "MENTOR_NAME", "FIRST_NAME", "MIDDLE_NAME",
    "LAST_NAME", "CHECK_LEAD_NAME", "CORE_LEAD_NAME",
    # how to reach them
    "EMAIL", "EMAIL_ID", "USER_EMAIL_ID", "CREW_EMAIL_ID", "PHONE", "CONTACT_NO",
    "MOBILE", "MOBILE_NO", "ADDRESS_1", "ADDRESS_2",
    # protected characteristics and documents
    "DOB", "DATE_OF_BIRTH", "GENDER", "MARITAL_STATUS", "PASSPORT_NO",
}


def is_pii_column(name: str) -> bool:
    """True when a column identifies a person rather than describing performance."""
    return (name or "").strip().upper() in PII_COLUMNS

FREE_TEXT_HINTS = {
    "STRENGHTH", "STRENGTH", "IMPROVEMENT_AREAS", "IMPROVEMENT_LAST_FLIGHT",
    "REMARKS", "COMMENT", "INNOVATIVE_INITIATIVES", "CANCEL_REMARK",
    "MAIL_BODY", "DESCRIPTION", "REQUEST_REASON",
}

# Confirmed semantics that the (name, type) pair alone cannot reveal. These are
# facts established from the source system, not guesses — so they are asserted
# here rather than left for an LLM to re-derive (differently) on every run.
#
# Both entries below are load-bearing:
#   MENTOR_FEEDBACK.MARK is declared TEXT but holds the assessment score out of
#   100 — the label every correlation is computed against (PLAN.md G4). Left as
#   a dimension it would never enter the candidate attribute pool at all.
#
#   PEP_QUESTION_FEEDBACK.FEEDBACK is the boolean answer to a question, not prose.
#   It is the highest-variance signal in the whole schema (PLAN.md G8) and the
#   primary thing Agent 2 fits against.
CONFIRMED_ROLE_OVERRIDES: dict[tuple[str, str], "ColumnRole"] = {}


def classify_column(name: str, data_type: str, table: str | None = None) -> ColumnRole:
    """Best-effort deterministic role for a column.

    Returns a *provisional* role; the builder may upgrade DIMENSION -> MEASURE
    based on table context, but never downgrades AUDIT or TEMPORAL_CONTROL —
    those classifications are structural, not semantic.
    """
    upper = name.upper().strip()
    dtype = (data_type or "").upper()

    if table:
        override = CONFIRMED_ROLE_OVERRIDES.get((table.upper(), upper))
        if override is not None:
            return override

    if upper in SCD2_CONTROL_COLUMNS:
        return ColumnRole.TEMPORAL_CONTROL
    if upper in AUDIT_COLUMNS:
        return ColumnRole.AUDIT
    if upper in IDENTITY_COLUMN_HINTS or upper.endswith("_ID") or upper == "ID":
        return ColumnRole.IDENTITY
    if upper in FREE_TEXT_HINTS:
        return ColumnRole.FREE_TEXT
    if "TIMESTAMP" in dtype or dtype == "DATE":
        return ColumnRole.TEMPORAL
    if dtype in {"NUMBER", "FLOAT", "INT", "INTEGER", "DECIMAL"}:
        return ColumnRole.MEASURE
    if dtype == "BOOLEAN":
        return ColumnRole.MEASURE
    return ColumnRole.DIMENSION


CONFIRMED_ROLE_OVERRIDES.update({
    ("MENTOR_FEEDBACK", "MARK"): ColumnRole.MEASURE,
    ("MENTOR_FEEDBACK", "GRADE"): ColumnRole.MEASURE,      # ordinal label A+/A/B+/B
    ("PEP_QUESTION_FEEDBACK", "FEEDBACK"): ColumnRole.MEASURE,
})

# Semantics an LLM will otherwise infer wrongly from the declared type. Surfaced
# to every prompt that describes a column, so the graph does not end up asserting
# that the primary boolean signal is prose.
CONFIRMED_COLUMN_NOTES: dict[tuple[str, str], str] = {
    ("PEP_QUESTION_FEEDBACK", "FEEDBACK"): (
        "BOOLEAN answer ('true'/'false') to the question, NOT narrative text. "
        "The question's marks are awarded when true, zero when false. This is the "
        "per-question outcome and the highest-variance signal in the schema."
    ),
    ("PEP_QUESTION_FEEDBACK", "REMARKS"): (
        "Optional narrative note, usually present only when the answer was false."
    ),
    ("MENTOR_FEEDBACK", "MARK"): (
        "Assessment score out of 100, stored as TEXT. Values cluster 92-100 and "
        "some rows are uncastable; always TRY_CAST."
    ),
    ("MENTOR_FEEDBACK", "GRADE"): (
        "Ordinal band derived from MARK via PEP_DEVIATION_MATRIX: A+, A, B+, B. "
        "B is the floor; there is no failing grade."
    ),
    ("PEP_QUESTIONS", "MARKS"): (
        "Marks awarded for this question under the Cabin Attendant / A320 template. "
        "0 means the question is not on that template."
    ),
    ("PEP_QUESTIONS", "LD_MARKS"): (
        "Marks for the same question under the Lead template — deliberately a "
        "different value from MARKS. Role selects which column applies."
    ),
    ("PEP_QUESTIONS", "ATRCA"): "Marks under the Cabin Attendant / ATR template.",
    ("PEP_QUESTIONS", "ATRLD"): "Marks under the Lead / ATR template.",
    # Onboarding ServiceNow put a second column called something-BASE in front of
    # the retrieval agent, and it started reporting that the schema has no way to
    # tell which base a crew member belongs to — while EMPLOYEE_INFO.BASE sat
    # right there. Two columns with the same word in the name and different
    # grains is precisely the case a schema listing cannot resolve on its own.
    ("EMPLOYEE_INFO", "BASE"): (
        "DISAMBIGUATION: the crew member's own home base, one value per person. "
        "This is what 'crew at BOM' means — filter here. Not to be confused with "
        "SN_FLIGHT_REPORT.LEAD_BASE, which is per flight."
    ),
    ("SN_FLIGHT_REPORT", "LEAD_BASE"): (
        "DISAMBIGUATION: the operating lead's base on THIS flight, one value per "
        "report. It is not the base of the crew on board. For 'crew based at X' "
        "use EMPLOYEE_INFO.BASE."
    ),
    ("SN_REPORT_CREW", "IS_INVOLVED"): (
        "BOOLEAN. TRUE only when the report names this seat position under 'Crew "
        "Involved'. Every other row is a crew member who was on the flight but was "
        "not named — being rostered is not being implicated, and counting all rows "
        "as involvement attributes somebody else's failure to them."
    ),
    ("SN_SERVICE_CHECK", "IS_PASS"): (
        "BOOLEAN, nullable. NULL means the lead did not answer the check — about "
        "half the reports. Treating NULL as a pass credits crew for a question "
        "nobody answered. DEVIATION is inverted: 'No' is the passing answer."
    ),
    ("PEP_CATEGORY", "TEMPLATE_ID"): (
        "The template this category belongs to. The delivered schema has no "
        "template-to-category mapping table, so this is the real relationship, "
        "not a denormalised copy — category rows are template-scoped."
    ),
}


def column_note(table: str | None, name: str) -> str:
    if not table:
        return ""
    return CONFIRMED_COLUMN_NOTES.get((table.upper(), name.upper().strip()), "")


def is_scd2_table(columns: list[str]) -> bool:
    """True when a table carries SCD-2 versioning and needs a currency filter."""
    upper = {c.upper() for c in columns}
    return bool(SCD2_CONTROL_COLUMNS & upper)
