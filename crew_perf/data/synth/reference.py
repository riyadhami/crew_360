"""Hand-authored PEP reference/master data.

This is the *encoded assessment form* (PLAN.md §3.1): templates, categories, the question
bank with its four role-dependent mark columns, the deviation matrix, and grades.
Everything here is authored to be internally consistent, because the whole
assessment chain silently breaks otherwise:

  - each template's applicable question marks sum to exactly 100
  - deviation-matrix bands tile 0..100 with no gap and no overlap
  - every question's CATEGORY_ID resolves, and every mapped category has questions

`validate_reference()` asserts all of that; the generator refuses to run if it fails.

Assumptions recorded here rather than buried in code:
  - MARKS=CA/A320, LD_MARKS=Lead/A320, ATRCA=CA/ATR, ATRLD=Lead/ATR   (confirmed
    for MARKS vs LD_MARKS; the ATR pair is inferred from naming — PLAN.md O8)
  - deviation bands A+ 98-100 / A 95-97 / B+ 92-94 / B 0-91 are SYNTHETIC —
    the real START_RANGE/END_RANGE values are unknown (PLAN.md O7)
  - B is the floor; no failing grade exists (PLAN.md O10)
"""

from __future__ import annotations

# ─── Designations & flight types ────────────────────────────────────────────

DESIGNATIONS = [
    ("CA", "Cabin Attendant", "Operating cabin crew member"),
    ("LD", "Lead", "Lead cabin crew member; supervises the cabin and junior crew"),
]

FLIGHT_TYPES = [
    (1, "A320", "Narrow-body jet operations"),
    (2, "ATR", "Turboprop regional operations"),
]

BASES = ["DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "AMD", "PNQ"]

MARK_COLUMNS = ["MARKS", "LD_MARKS", "ATRCA", "ATRLD"]

# ─── Grades & deviation bands ───────────────────────────────────────────────

# ─── Other reference lists ──────────────────────────────────────────────────

PEP_REASONS = [
    (1, "Routine PEP", 0.62),
    (2, "New Joinee", 0.12),
    (3, "Post Training", 0.10),
    (4, "Complaint Follow-up", 0.08),
    (5, "Random Check", 0.08),
]

CANCEL_REASONS = [
    "Crew roster change",
    "Flight cancelled",
    "Mentor unavailable",
    "Crew on leave",
]

REQUEST_REASONS = [
    (1, "Mentor change requested by base"),
    (2, "Flight interchange due to roster disruption"),
    (3, "PEP date deferred on crew request"),
    (4, "Reassessment requested by Performance Review Committee"),
]

CART_TYPES = ["FULL", "HALF", "NIL"]

SLA_MAIL_TEMPLATES = [
    (1, "PEP_SCHEDULED", "PEP scheduled for {crew}", "Your PEP has been scheduled on {date}.", "CREW"),
    (2, "PEP_SUBMITTED", "PEP submitted for {crew}", "The PEP assessment has been submitted.", "MENTOR"),
    (3, "PEP_SLA_BREACH", "SLA breach - PEP pending", "The PEP feedback is pending beyond SLA.", "BASE_HEAD"),
    (4, "PEP_CANCELLED", "PEP cancelled for {crew}", "The scheduled PEP has been cancelled.", "CREW"),
]

SECTORS = [
    ("DEL", "BOM"), ("DEL", "BLR"), ("BOM", "DEL"), ("BLR", "DEL"), ("DEL", "HYD"),
    ("HYD", "DEL"), ("BOM", "BLR"), ("BLR", "MAA"), ("MAA", "BLR"), ("CCU", "DEL"),
    ("DEL", "CCU"), ("AMD", "BOM"), ("BOM", "AMD"), ("PNQ", "DEL"), ("DEL", "PNQ"),
]

WORKFLOW_ACTIONS = [
    "PEP_SCHEDULED", "MENTOR_ASSIGNED", "FEEDBACK_SUBMITTED",
    "REMINDER_SENT", "ESCALATED_TO_BASE_HEAD", "PEP_CLOSED",
]


# ─── The assessment form ────────────────────────────────────────────────────
# Transcribed from the warehouse, not authored here. A hand-written stand-in
# used to sit above this and be overwritten when an extract was present; it is
# gone, because "present" was a property of one developer's filesystem. Every
# structural assumption it made was wrong in a way that mattered — categories
# that do not exist, an A+/A/B+/B scale, whole-number marks — and a fallback
# that silently substitutes those for the real form is worse than no fallback:
# the generator still runs, and every score it produces is quietly against the
# wrong form.

from crew_perf.data.synth.pep_reference import build as _form  # noqa: E402

_REAL = _form()
CATEGORIES = _REAL["categories"]
TEMPLATES = _REAL["templates"]
TEMPLATE_CATEGORIES = _REAL["template_categories"]
QUESTIONS = _REAL["questions"]
GRADES = _REAL["grades"]
DEVIATION_BANDS = _REAL["deviation_bands"]
PEP_STATUSES = _REAL["statuses"]

# True importance of each trait, deliberately misaligned from the form's own
# mark allocation. If the form's weights were already optimal, a weighting agent
# could add nothing and the two tracks could never diverge.
#
#   category            form marks (A320 CA)      true importance
#   INFLIGHTEXP                 35.45                    12%   over-weighted
#   AFTERTAKEOFF                33.60                    14%   over-weighted
#   TIMEMANAGEMENT              11.70                    22%   under-weighted
#   CLEANLINESS                  7.75                     5%
#   GROOMING                     6.40                     4%
#   PLCHECKLIST                  5.10                    18%   badly under-weighted
#   LEADSONLY                    0 for CA                10%   unobservable for CAs
#
# Generator-side ground truth: never written to the database, because the agents
# have to infer weights from data rather than be told them.
TRAIT_IMPORTANCE = {
    "customer_focus": 0.12,
    "service_delivery": 0.14,
    "punctuality": 0.22,
    "cabin_standards": 0.05,
    "demeanour": 0.04,
    "ground_duties": 0.18,
    "coaching": 0.10,
    # Invisible to PEP entirely; surfaces as leave, check-in failures and
    # acknowledgement lag in CLMS/CrewPortal.
    "reliability": 0.15,
}


# ─── Derived lookups ────────────────────────────────────────────────────────

CATEGORY_BY_CODE = {c[1]: c for c in CATEGORIES}
CATEGORY_ID_BY_CODE = {c[1]: c[0] for c in CATEGORIES}
CATEGORY_TRAIT = {c[1]: c[4] for c in CATEGORIES}
TEMPLATE_BY_ID = {t[0]: t for t in TEMPLATES}
QUESTION_BY_ID = {q[0]: q for q in QUESTIONS}

# Mark column index within a QUESTIONS row
_MARK_IDX = {"MARKS": 3, "LD_MARKS": 4, "ATRCA": 5, "ATRLD": 6}


def category_rows() -> list[tuple[int, int, str, str, str, int]]:
    """(category_id, template_id, template_code, category_code, name, position).

    One row per template x category. The delivered PEP schema has no
    `PEP_TEMP_CAT_MAPPING` table, so `PEP_CATEGORY.TEMPLATE_ID` carries the link
    directly — making it a real foreign key rather than the denormalised copy it
    would be if a mapping table existed alongside it.
    """
    out, cid = [], 1
    for tid, code, *_ in TEMPLATES:
        for pos, ccode in enumerate(TEMPLATE_CATEGORIES[tid], start=1):
            out.append((cid, tid, code, ccode, CATEGORY_BY_CODE[ccode][2], pos))
            cid += 1
    return out


def question_rows() -> list[tuple[int, int, int, str, tuple]]:
    """(question_id, category_id, template_id, category_code, source question).

    A question appears once per template that assesses it, because CATEGORY_ID is
    template-scoped. All four mark columns stay populated as the schema declares;
    which one applies is still decided by the template's role and fleet.
    """
    out, qid = [], 1
    for cid, tid, _tcode, ccode, _name, _pos in category_rows():
        mark_col = TEMPLATE_BY_ID[tid][5]
        for q in QUESTIONS:
            if q[1] == ccode and question_mark(q[0], mark_col) > 0:
                out.append((qid, cid, tid, ccode, q))
                qid += 1
    return out


def question_mark(qid: int, mark_column: str) -> float:
    """The mark a question is worth under a given role/fleet mark column."""
    return float(QUESTION_BY_ID[qid][_MARK_IDX[mark_column]])


def template_questions(template_id: int) -> list[int]:
    """Question IDs assessed by a template, in category then question order.

    Questions worth 0 marks under this template's mark column are excluded —
    that is how LED questions stay off the Cabin Attendant templates.
    """
    codes = set(TEMPLATE_CATEGORIES[template_id])
    mark_col = TEMPLATE_BY_ID[template_id][5]
    out = []
    for q in QUESTIONS:
        if q[1] in codes and question_mark(q[0], mark_col) > 0:
            out.append(q[0])
    return out


def template_total_marks(template_id: int) -> float:
    mark_col = TEMPLATE_BY_ID[template_id][5]
    return sum(question_mark(q, mark_col) for q in template_questions(template_id))


def grade_for_mark(mark: float) -> str:
    """Deterministic deviation-matrix lookup: mark -> grade.

    Matched on the lower bound, descending, rather than on a closed interval.
    Real marks are fractional — the form's marks are values like 3.20 and 2.09,
    so a total of 91.3 is ordinary — and closed integer intervals leave a gap
    between every adjacent pair of bands for such a mark to fall through.
    """
    for grade, lo, _hi in sorted(DEVIATION_BANDS, key=lambda b: -b[1]):
        if mark >= lo:
            return grade
    raise ValueError(f"mark {mark} falls below every deviation band")


def validate_reference() -> list[str]:
    """Assert internal consistency. Returns a list of problems (empty == good)."""
    problems: list[str] = []

    # Each template's marks must total exactly 100.
    for tid, code, *_ in TEMPLATES:
        total = template_total_marks(tid)
        if abs(total - 100.0) > 1e-9:
            problems.append(f"template {code}: marks total {total}, expected 100")

    # Bands are thresholds, so what matters is that they descend without
    # duplication and that the lowest one reaches 0 — a mark below every
    # threshold has no grade at all.
    lows = sorted((b[1] for b in DEVIATION_BANDS), reverse=True)
    if len(set(lows)) != len(lows):
        problems.append(f"deviation bands have duplicate lower bounds: {lows}")
    if min(lows) != 0:
        problems.append(f"lowest deviation band starts at {min(lows)}, expected 0")
    if max(b[2] for b in DEVIATION_BANDS) != 100:
        problems.append("top deviation band does not reach 100")

    # Every band grade must exist in PEP_GRADE.
    grade_values = {g[2] for g in GRADES}
    for grade, _, _ in DEVIATION_BANDS:
        if grade not in grade_values:
            problems.append(f"deviation band grade {grade!r} missing from PEP_GRADE")

    # Every question's category must exist; every mapped category must have questions.
    for q in QUESTIONS:
        if q[1] not in CATEGORY_ID_BY_CODE:
            problems.append(f"question {q[0]}: unknown category {q[1]!r}")
    for tid, codes in TEMPLATE_CATEGORIES.items():
        for code in codes:
            if code not in CATEGORY_ID_BY_CODE:
                problems.append(f"template {tid}: unknown category {code!r}")
        if not template_questions(tid):
            problems.append(f"template {tid}: no applicable questions")

    # Base pass probabilities must be probabilities.
    for q in QUESTIONS:
        if not 0.0 < q[10] < 1.0:
            problems.append(f"question {q[0]}: base_pass {q[10]} outside (0,1)")

    # Trait importance must cover every trait and sum to 1.
    # Every PEP category must have an importance. TRAIT_IMPORTANCE may also
    # carry traits PEP cannot observe (reliability, from CLMS/CrewPortal).
    traits = {c[4] for c in CATEGORIES}
    missing = traits - set(TRAIT_IMPORTANCE)
    if missing:
        problems.append(f"TRAIT_IMPORTANCE missing PEP categories: {sorted(missing)}")
    UNOBSERVABLE_IN_PEP = {"reliability"}
    extra = set(TRAIT_IMPORTANCE) - traits - UNOBSERVABLE_IN_PEP
    if extra:
        problems.append(f"TRAIT_IMPORTANCE has unknown traits: {sorted(extra)}")
    total_imp = sum(TRAIT_IMPORTANCE.values())
    if abs(total_imp - 1.0) > 1e-9:
        problems.append(f"TRAIT_IMPORTANCE sums to {total_imp}, expected 1.0")

    return problems
