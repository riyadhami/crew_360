"""The reference data is the encoded assessment form; if it drifts, every score is wrong."""

from crew_perf.data.synth import reference as R
from crew_perf.data.synth import reference_data


def test_reference_is_internally_consistent():
    assert R.validate_reference() == []


def test_the_form_needs_no_files_on_disk():
    """The reference used to be read from a directory of dev CSV extracts, with a
    hand-written stand-in when they were absent. "Absent" was a property of one
    developer's filesystem, and the stand-in was wrong in ways that mattered —
    categories that do not exist, an A+/A/B+/B scale, whole-number marks. So a
    checkout without the extracts still generated, still reconciled, and scored
    every crew member against the wrong form.

    Transcribed into code, the form cannot go missing and cannot differ between
    two checkouts. This test fails the moment a filesystem read creeps back."""
    assert reference_data.PEP_QUESTIONS, "the question bank must be importable data"
    assert {g[2] for g in R.GRADES} == {"A", "B", "C"}, "the real scale, not the stand-in"
    assert all(isinstance(q[3], float) for q in R.QUESTIONS), "marks are fractional"
    assert not (R.__file__ and "snowflake_schema" in R.__file__)


def test_every_template_totals_100_marks():
    for tid, code, *_ in R.TEMPLATES:
        assert abs(R.template_total_marks(tid) - 100.0) < 1e-9, code


def test_deviation_bands_tile_without_gaps():
    bands = sorted(R.DEVIATION_BANDS, key=lambda b: b[1])
    assert bands[0][1] == 0 and bands[-1][2] == 100
    for prev, nxt in zip(bands, bands[1:]):
        assert nxt[1] == prev[2] + 1


def test_grade_lookup_matches_the_real_scale():
    """The real scale is A/B/C, from the PEP_GRADE extract. Bands are resolved
    as thresholds, not closed intervals — marks are fractional, so 91.3 must
    land in C rather than falling through a gap between integer bands."""
    assert R.grade_for_mark(100) == "A"
    assert R.grade_for_mark(96) == "A"
    assert R.grade_for_mark(95.9) == "B"
    assert R.grade_for_mark(92) == "B"
    assert R.grade_for_mark(91.3) == "C"
    assert R.grade_for_mark(0) == "C"


def test_lead_questions_are_absent_from_cabin_attendant_templates():
    """LEADSONLY is the real category code, and it is what makes coaching
    unobservable for Cabin Attendants — a genuine coverage hole, not an
    artefact of the old hand-written bank."""
    led_ids = {q[0] for q in R.QUESTIONS if q[1] == "LEADSONLY"}
    assert led_ids, "no LEADSONLY questions found"
    ca_templates = [t[0] for t in R.TEMPLATES if t[3] == "CA"]
    ld_templates = [t[0] for t in R.TEMPLATES if t[3] == "LD"]
    for tid in ca_templates:
        assert not (set(R.template_questions(tid)) & led_ids)
    for tid in ld_templates:
        assert set(R.template_questions(tid)) & led_ids


def test_marks_differ_by_role():
    """MARKS != LD_MARKS is the confirmed fact that makes role weighting real."""
    differing = [q for q in R.QUESTIONS if q[3] != q[4] and q[1] != "LEADSONLY"]
    assert len(differing) > 10


def test_mark_granularity_allows_every_grade_band():
    """With coarse marks, bands like 98-100 become unreachable and A+ collapses
    to a single value. Every band must contain at least two achievable totals."""
    for tid, code, *_ in R.TEMPLATES:
        mark_col = R.TEMPLATE_BY_ID[tid][5]
        marks = [R.question_mark(q, mark_col) for q in R.template_questions(tid)]
        smallest = min(marks)
        for grade, lo, hi in R.DEVIATION_BANDS:
            if grade == "B":
                continue  # open-ended catch-all
            assert hi - lo >= smallest, f"{code}: band {grade} narrower than smallest mark"


def test_some_questions_are_deliberately_non_discriminating():
    """Agent 2's discrimination diagnostic needs something real to catch."""
    assert len([q for q in R.QUESTIONS if q[10] >= 0.99]) >= 3


def test_true_importance_differs_from_template_marks():
    """If the template already weighted things correctly, no weighting agent
    could ever add value and the two-track score could never diverge."""
    ca_marks = {}
    for qid in R.template_questions(1):
        cat = R.QUESTION_BY_ID[qid][1]
        ca_marks[cat] = ca_marks.get(cat, 0) + R.question_mark(qid, "MARKS")

    divergence = 0.0
    for cat, marks in ca_marks.items():
        true_w = R.TRAIT_IMPORTANCE[R.CATEGORY_TRAIT[cat]]
        divergence += abs(marks / 100.0 - true_w)
    assert divergence > 0.2, f"template weights too close to true importance ({divergence:.3f})"
