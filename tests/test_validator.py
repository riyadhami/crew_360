"""SQL validation. This is the gate between an LLM and the database.

The SCD-2 rule is the one that earns its keep: a query missing it returns a
plausible number that is silently wrong, so it cannot be left to a prompt.
"""

import pytest

from crew_perf.data.validator import validate
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


def ok(sql, store, **kw):
    return validate(sql, store, **kw)


# ── SCD-2 currency (G7) ────────────────────────────────────────────────────


def test_missing_scd2_filter_is_rejected(store):
    r = ok("SELECT IGA, MARK FROM MENTOR_FEEDBACK LIMIT 10", store)
    assert not r.ok
    assert any("P_IS_CURRENT" in e for e in r.errors)


def test_scd2_filter_accepted_via_alias(store):
    r = ok("SELECT mf.IGA FROM MENTOR_FEEDBACK mf WHERE mf.P_IS_CURRENT = TRUE LIMIT 10", store)
    assert r.ok, r.errors


def test_scd2_filter_required_on_every_joined_table(store):
    """Filtering one side and not the other still double-counts."""
    sql = """SELECT qf.ID FROM PEP_QUESTION_FEEDBACK qf
             JOIN PEP_QUESTIONS q ON qf.QUESTION_ID = q.QUESTION_ID
             WHERE qf.P_IS_CURRENT = TRUE LIMIT 10"""
    r = ok(sql, store)
    assert not r.ok
    assert any("PEP_QUESTIONS" in e and "P_IS_CURRENT" in e for e in r.errors)


def test_is_true_form_is_accepted(store):
    r = ok("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT IS TRUE LIMIT 5", store)
    assert r.ok, r.errors


# ── Schema grounding ───────────────────────────────────────────────────────


def test_unknown_table_is_rejected(store):
    r = ok("SELECT * FROM PEPCard WHERE P_IS_CURRENT = TRUE LIMIT 5", store)
    assert not r.ok
    assert any("unknown table" in e for e in r.errors)


def test_unknown_column_is_rejected_with_alternatives(store):
    r = ok("SELECT mf.BONUS FROM MENTOR_FEEDBACK mf WHERE mf.P_IS_CURRENT=TRUE LIMIT 5", store)
    assert not r.ok
    assert any("no column" in e and "available" in e for e in r.errors)


def test_cte_names_are_not_treated_as_tables(store):
    sql = """WITH recent AS (
               SELECT IGA, MARK FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE)
             SELECT IGA FROM recent LIMIT 10"""
    assert ok(sql, store).ok


# ── Join grounding ─────────────────────────────────────────────────────────


def test_join_between_unrelated_tables_is_rejected(store):
    sql = """SELECT * FROM PEP_GRADE g JOIN PEP_FLIGHT_DETAILS f ON g.GRADE_ID = f.ID
             WHERE g.P_IS_CURRENT = TRUE AND f.P_IS_CURRENT = TRUE LIMIT 5"""
    r = ok(sql, store)
    assert not r.ok
    assert any("no direct relationship" in e for e in r.errors)


def test_related_tables_joined_on_wrong_columns_is_rejected(store):
    """MENTOR_FEEDBACK and PEP_GRADE are related — on GRADE, not on IGA/GRADE_ID.
    Using the wrong columns fabricates rows that look like data, so it is an
    error rather than a warning."""
    sql = """SELECT * FROM MENTOR_FEEDBACK mf JOIN PEP_GRADE g ON mf.IGA = g.GRADE_ID
             WHERE mf.P_IS_CURRENT = TRUE AND g.P_IS_CURRENT = TRUE LIMIT 5"""
    r = ok(sql, store)
    assert not r.ok
    assert any("not a relationship the graph holds" in e for e in r.errors)


def test_verified_join_is_accepted(store):
    sql = """SELECT q.QUESTION FROM PEP_QUESTION_FEEDBACK qf
             JOIN PEP_QUESTIONS q ON qf.QUESTION_ID = q.QUESTION_ID
             WHERE qf.P_IS_CURRENT = TRUE AND q.P_IS_CURRENT = TRUE LIMIT 5"""
    assert ok(sql, store).ok


# ── The TEXT mark (G4) ─────────────────────────────────────────────────────


def test_aggregating_mark_without_try_cast_is_rejected(store):
    r = ok("SELECT AVG(MARK) FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 1", store)
    assert not r.ok
    assert any("TRY_CAST" in e for e in r.errors)


def test_try_cast_aggregate_is_accepted(store):
    r = ok("SELECT AVG(TRY_CAST(MARK AS DOUBLE)) FROM MENTOR_FEEDBACK "
           "WHERE P_IS_CURRENT = TRUE LIMIT 1", store)
    assert r.ok, r.errors


def test_selecting_mark_without_aggregating_is_fine(store):
    """Only aggregation breaks on an uncastable value."""
    r = ok("SELECT IGA, MARK FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 5", store)
    assert r.ok, r.errors


# ── Safety rails ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("sql", [
    "DROP TABLE MENTOR_FEEDBACK",
    "DELETE FROM MENTOR_FEEDBACK",
    "UPDATE MENTOR_FEEDBACK SET MARK = '100'",
    "INSERT INTO PEP_GRADE VALUES (9, 'X', 'X', TRUE)",
])
def test_mutations_are_rejected(store, sql):
    assert not ok(sql, store).ok


def test_stacked_statements_are_rejected(store):
    r = ok("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT=TRUE LIMIT 1; DROP TABLE PEP_GRADE",
           store)
    assert not r.ok


def test_missing_limit_is_rejected(store):
    r = ok("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE", store)
    assert not r.ok
    assert any("LIMIT" in e for e in r.errors)


def test_excessive_limit_is_rejected(store):
    r = ok("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 999999", store)
    assert not r.ok


def test_string_literals_cannot_smuggle_identifiers(store):
    """A table name inside a quoted string must not be read as a reference."""
    sql = ("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE "
           "AND GRADE = 'PEPCard' LIMIT 5")
    assert ok(sql, store).ok


def test_comments_are_stripped_before_analysis(store):
    sql = """-- FROM NoSuchTable
             SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 5"""
    assert ok(sql, store).ok


def test_empty_query_is_rejected(store):
    assert not ok("   ", store).ok


def test_a_missing_direct_join_hands_back_the_indirect_route(store):
    """PEP and ServiceNow both key on IGA and are joined through CrewPortal's
    crew master, which carries both. Rejecting the direct join with only "do not
    invent joins" spent the whole retry budget: the agent rewrote the same
    two-table join four ways and gave up on a question the graph can answer in
    two hops. The route is one lookup away, so the rejection carries it."""
    v = validate(
        "SELECT COUNT(*) FROM EMPLOYEE_INFO e "
        "JOIN M_SN_CREW c ON c.IGA = e.IGA "
        "WHERE e.P_IS_CURRENT = TRUE AND c.P_IS_CURRENT = TRUE LIMIT 1",
        store,
    )
    assert not v.ok
    reason = v.reason()
    assert "no direct relationship" in reason
    assert "reachable in 2 hops" in reason
    assert "M_CREW_DETAILS" in reason, "the bridge table must be named"


def test_a_truly_unrelated_pair_gets_no_invented_route(store):
    """The hint must stay silent when the graph holds no path, or it becomes a
    licence to join anything to anything."""
    v = validate(
        "SELECT COUNT(*) FROM PEP_GRADE g JOIN M_AIRPORT_NAMES a ON a.ID = g.ID LIMIT 1",
        store,
    )
    assert not v.ok
    assert "reachable in" not in v.reason()
