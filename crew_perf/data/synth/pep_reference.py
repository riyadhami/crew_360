"""The PEP assessment form, assembled from the transcribed reference data.

Replaces a hand-authored question bank whose every structural assumption turned
out to be wrong in a way that mattered:

    assumed                          actually
    ───────────────────────────────  ──────────────────────────────────────────
    categories SAF/COM/SVC/CAB/…     TIMEMANAGEMENT/GROOMING/INFLIGHTEXP/
                                     AFTERTAKEOFF/PLCHECKLIST/CLEANLINESS/
                                     LEADSONLY — zero overlap, and neither a
                                     Safety nor a Communication category exists
    grades A+/A/B+/B                 A/B/C
    integer marks                    fractional (3.20, 2.65, 2.09, 1.10)
    safety/critical flags in use     FALSE on all 345 rows; CRITICAL true on 4,
                                     of which 3 inactive and 1 worth 0 marks

The mark-column rule, by contrast, is now *confirmed*: each role and fleet reads
its own column, and that column totals exactly 100.00 across the live questions.
Leads answer the shared body plus a LEADSONLY add-on — 84.40 + 15.60 for A320.

What still has to be invented is generator-side only and never reaches the
database: which latent trait a category expresses, and how likely an average
crew member is to pass each question. Everything a query can see is real.
"""

from __future__ import annotations

import hashlib

from crew_perf.data.synth import reference_data

# ─── Category -> latent trait ───────────────────────────────────────────────
# Generator-side only. Names describe what the category actually assesses, not
# the trait vocabulary of the old hand-written bank.

CATEGORY_TRAITS = {
    "TIMEMANAGEMENT": "punctuality",
    "GROOMING": "demeanour",
    "INFLIGHTEXP": "customer_focus",
    "AFTERTAKEOFF": "service_delivery",
    "PLCHECKLIST": "ground_duties",
    "CLEANLINESS": "cabin_standards",
    "LEADSONLY": "coaching",
}

# The four assessment variants, using real template ids. A320 shares one form
# (LCA, id 3) across both roles, differing only by mark column; Leads
# additionally answer the LEADSONLY categories carried on template 1.
#   (template_id, code, name, designation, flight_type_id, mark_column)
TEMPLATE_VARIANTS = [
    (3,  "LCA",   "A320 — Cabin Attendant", "CA", 1, "MARKS"),
    (1,  "CA",    "A320 — Lead",            "LD", 1, "LD_MARKS"),
    (48, "ATRCA", "ATR — Cabin Attendant",  "CA", 2, "ATRCA"),
    (49, "ATRLD", "ATR — Lead",             "LD", 2, "ATRLD"),
]

# ASSUMED. `PEP_DEVIATION_MATRIX` exists in dev but carries no usable rows, so
# these boundaries are chosen to reproduce the observed clustering of marks in
# the mid-to-high 90s. `rules.resolve()` will keep reporting grading as
# "declared" until a populated matrix arrives, which is the correct signal.
DEVIATION_BANDS = [("A", 96, 100), ("B", 92, 95), ("C", 0, 91)]


def _base_pass(question_id: int, marks: float) -> float:
    """Pass probability for an average crew member, derived deterministically.

    Not in the extract and not inferable from it, but it cannot be uniform: the
    discrimination diagnostic exists to catch questions everyone passes, so some
    must genuinely be undiscriminating. Seeded off the question id so the value
    is stable across regenerations.
    """
    h = int(hashlib.sha256(str(question_id).encode()).hexdigest()[:8], 16)
    r = (h % 10_000) / 10_000.0
    if r < 0.15:
        return 0.990 + 0.009 * r / 0.15          # ~15% carry almost no information
    # Heavier questions are marginally harder, so marks and difficulty are not
    # independent — a flat relationship would make the weighting problem easier
    # than the real one.
    return 0.935 + 0.050 * r - 0.004 * min(marks, 4.0)


def build() -> dict:
    """Assemble the reference tables the generator writes.

    Everything here except `pass_probability` is transcribed from the warehouse
    (see `reference_data`). The pass probability is derived, not stored, because
    it is generator-side only: it decides how the synthetic answers come out and
    is never a fact about the real form.
    """
    questions = [
        (*q, round(_base_pass(q[0], max(q[3], q[4], q[5], q[6])), 4))
        for q in reference_data.PEP_QUESTIONS
    ]
    return {
        "categories": reference_data.PEP_CATEGORIES,
        "templates": TEMPLATE_VARIANTS,
        "template_categories": reference_data.PEP_TEMPLATE_CATEGORIES,
        "questions": questions,
        "grades": reference_data.PEP_GRADES,
        "deviation_bands": DEVIATION_BANDS,
        "statuses": reference_data.PEP_STATUSES,
    }
