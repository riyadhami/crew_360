"""Rule resolution.

The property under test is the one that makes this production-shaped: the rules
the source system records must be *read from it*, so that swapping synthetic data
for live data changes behaviour without any code or config edit.

The hazard being guarded against is concrete. `policy.py` once hardcoded grading
bands of A+ 98-100. The real scale is A/B/C; if the matrix moves a boundary, a
system reading config would keep grading by the old boundaries and report nothing
wrong — the failure would be invisible in every output.
"""

import duckdb
import pytest

from crew_perf import policy, rules
from crew_perf.data.executor import DuckDBExecutor


@pytest.fixture(scope="module")
def resolved(executor):
    return rules.resolve(executor)


def test_grading_bands_come_from_the_table_not_config(resolved):
    assert resolved.grading.from_data
    assert "PEP_DEVIATION_MATRIX" in resolved.grading.detail
    assert all("template_id" in b for b in resolved.grading.value)


def test_template_selection_comes_from_the_table(resolved):
    assert resolved.templates.from_data
    codes = {t["template_code"] for t in resolved.templates.value}
    assert {"LCA", "CA", "ATRCA", "ATRLD"} <= codes


def test_the_four_mark_columns_are_selected_by_role_and_fleet(resolved):
    assert resolved.mark_column_for("CA", 1) == "MARKS"
    assert resolved.mark_column_for("LD", 1) == "LD_MARKS"
    assert resolved.mark_column_for("CA", 2) == "ATRCA"
    assert resolved.mark_column_for("LD", 2) == "ATRLD"
    assert resolved.mark_column_for("XX", 9) is None


def test_changing_the_table_changes_the_grade(tmp_path, duckdb_snapshot):
    """The test that matters. Move a grade boundary in the database and the
    resolved rule must move with it — no code change, no config edit."""
    import shutil

    edited = tmp_path / "edited.duckdb"
    shutil.copy2(duckdb_snapshot, edited)

    con = duckdb.connect(str(edited))
    con.execute("UPDATE PEP_DEVIATION_MATRIX SET START_RANGE = 80 WHERE GRADE = 'A'")
    con.close()

    ex = DuckDBExecutor(edited, read_only=True)
    try:
        moved = rules.resolve(ex)
        assert moved.grading.from_data, "bands must still resolve from the table"
        # 85 graded C under the declared bands; after moving A's threshold down
        # to 80 in the database it must grade A, with no code or config change.
        assert moved.grade_for_mark(85, template_id=3) == "A"
        # And the declared fallback still says otherwise, proving the value was
        # genuinely read rather than coincidentally matching.
        declared = next(b for b in policy.GRADING["bands"] if b["grade"] == "A")
        assert declared["start"] == 96
    finally:
        ex.close()


def test_missing_table_falls_back_loudly(tmp_path):
    """A fallback is a reported event, never a silent substitution."""
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()

    ex = DuckDBExecutor(empty, read_only=False)
    try:
        r = rules.resolve(ex)
        assert not r.grading.from_data
        assert not r.templates.from_data
        assert len(r.warnings) == 2
        assert any("PEP_DEVIATION_MATRIX" in w for w in r.warnings)
    finally:
        ex.close()


def test_status_predicate_is_emitted_from_one_place():
    """The literal must not be written by hand in the queries that apply it, or
    the rule and the SQL drift apart without anything failing."""
    assert rules.submitted_predicate("ps") == "ps.STATUS = 2"
    assert rules.submitted_predicate("s") == "s.STATUS = 2"
    assert policy.ASSESSMENT_VALIDITY["counted_status"] == 2


def test_no_query_hardcodes_the_status_literal():
    import pathlib

    for path in ["crew_perf/agents/attributes.py", "crew_perf/agents/scoring.py"]:
        body = pathlib.Path(path).read_text()
        assert "STATUS = 2" not in body, f"{path} hardcodes the status literal"


def test_every_rule_declares_its_provenance(resolved):
    """A reader must be able to tell which values came from the data and which
    were assumed — otherwise assumptions read as facts."""
    for name, detail in resolved.provenance().items():
        assert detail.startswith(("data —", "declared —")), name


def test_judgements_stay_declared(resolved):
    """Importance and confidence thresholds cannot be derived from data, so they
    must NOT claim to come from it."""
    assert not resolved.confidence.from_data
    assert not resolved.counted_status.from_data
    # Relative importance used to live in policy.py as a declared judgement. It
    # is gone: weighting is derived from the records now, so there is no declared
    # importance left to misattribute to data. The property to hold is that it
    # did not quietly come back — a policy block claiming to say what matters
    # would be a second, invisible mechanism.
    assert not [k for k in policy.PARAMETERS if "importance" in k]
