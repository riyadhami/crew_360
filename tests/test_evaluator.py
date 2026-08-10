"""Agent 5 — the evaluator.

An auditor that can be argued out of a finding is not an auditor, so everything
here is recomputation and comparison rather than a model's opinion. The tests
check that it actually catches injected defects, not merely that it runs.
"""

import copy

import pytest

from crew_perf.data.executor import DuckDBExecutor

from crew_perf.agents import evaluator, scoring, weighting
from crew_perf.agents.attributes import build_frame, discover
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def ws(weights):
    return weights


@pytest.fixture(scope="module")
def population(store, executor):
    return build_frame(executor, discover(store, executor))


@pytest.fixture(scope="module")
def card(ws, store, executor, population):
    r = executor.execute("""
        SELECT mf.IGA FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT AND ps.P_IS_CURRENT AND ps.STATUS = 2
        GROUP BY 1 HAVING COUNT(*) >= 8 LIMIT 1
    """, limit=1)
    return scoring.score(r.rows[0][0], ws, store=store, executor=executor, population=population)


# ─── 5a: score audit ────────────────────────────────────────────────────────


def test_a_clean_scorecard_passes(card, ws, store, population):
    audit = evaluator.audit_score(card, ws, store=store, population=population)
    assert audit.passed, [f.message for f in audit.errors()]


def test_tampered_arithmetic_is_caught(card, ws, store):
    """The point of the audit: a number that does not reproduce is rejected."""
    bad = copy.deepcopy(card)
    bad.composite_score = 9.9
    audit = evaluator.audit_score(bad, ws, store=store)
    assert not audit.passed
    assert any(f.check == "arithmetic" for f in audit.errors())


def test_weight_not_matching_the_weightset_is_caught(card, ws, store):
    bad = copy.deepcopy(card)
    bad.components[0].weight = 0.99
    audit = evaluator.audit_score(bad, ws, store=store)
    assert not audit.passed
    assert any(f.check == "provenance" for f in audit.errors())


def test_citing_a_nonexistent_policy_is_caught(card, ws, store):
    bad = copy.deepcopy(card)
    bad.components[0].rule_ref = "99. Invented Policy"
    audit = evaluator.audit_score(bad, ws, store=store)
    assert not audit.passed
    assert any("does not exist" in f.message for f in audit.errors())


def test_dishonest_coverage_is_caught(card, ws, store):
    bad = copy.deepcopy(card)
    bad.coverage = 1.0 if bad.coverage < 1.0 else 0.5
    audit = evaluator.audit_score(bad, ws, store=store)
    assert not audit.passed


def test_absorbed_safety_finding_is_caught(ws, store, safety_flagged_db, population):
    """The criticality rule: a scorecard showing safety failures must say they are not offset.

    Against a snapshot with the flag set — the delivered extract carries none,
    so the rule is dormant in production but must still be enforced."""
    executor = safety_flagged_db
    r = executor.execute("""
        SELECT mf.IGA FROM PEP_QUESTION_FEEDBACK qf
        JOIN PEP_QUESTIONS q ON q.QUESTION_ID = qf.QUESTION_ID
        JOIN MENTOR_FEEDBACK mf ON mf.ID = qf.FEEDBACK_ID
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE qf.FEEDBACK='false' AND q.SAFETY_ACTION_PARAMETER
          AND mf.P_IS_CURRENT AND ps.P_IS_CURRENT AND ps.STATUS=2
        GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1
    """, limit=1)
    assert r.rows, "fixture failed to create a safety failure"
    c = scoring.score(r.rows[0][0], ws, store=store, executor=executor, population=population)
    tampered = copy.deepcopy(c)
    tampered.findings = [f for f in tampered.findings if "NOT offset" not in f]
    audit = evaluator.audit_score(tampered, ws, store=store)
    assert not audit.passed
    assert any(f.check == "criticality" for f in audit.errors())


def test_rank_stability_is_measured(ws, population):
    """Weights are judgements with error bars. If ±20% reshuffles the top ten,
    the ordering is an artefact of the exact numbers chosen."""
    s = evaluator.rank_stability(ws, population)
    assert 0.0 <= s["agreement"] <= 1.0
    assert s["trials"] > 0
    assert s["agreement"] > 0.5, "ranking is essentially arbitrary"


def test_thin_sample_downgrades_confidence(ws, store, executor, population):
    r = executor.execute("""
        SELECT mf.IGA FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT AND ps.P_IS_CURRENT AND ps.STATUS=2
        GROUP BY 1 HAVING COUNT(*) = 1 LIMIT 1
    """, limit=1)
    if not r.rows:
        pytest.skip("no single-assessment crew")
    c = scoring.score(r.rows[0][0], ws, store=store, executor=executor, population=population)
    audit = evaluator.audit_score(c, ws, store=store)
    assert audit.confidence_downgrade == "indicative"


# ─── Gap reporting ──────────────────────────────────────────────────────────


def test_gap_report_records_findings_without_applying_them(card, ws, store, population):
    """Findings are recorded, never auto-applied: an amendment to a rule that
    decides performance is a human decision."""
    audit = evaluator.audit_score(card, ws, store=store, population=population)
    path = evaluator.save(audit, name="test")
    import json

    loaded = json.loads(path.read_text())
    assert loaded["subject"] == audit.subject
    assert len(loaded["findings"]) == len(audit.findings)


def test_components_must_cite_a_rule_that_exists(card, ws, store):
    """Rules live in crew_perf/policy.py now, not as graph vertices — but a
    component still has to name one, so a number traces to what produced it."""
    from crew_perf import policy

    for c in card.components:
        if c.rule_ref:
            assert policy.rule_exists(c.rule_ref), c.attribute


@pytest.fixture(scope="module")
def safety_flagged_db(duckdb_snapshot, tmp_path_factory):
    """A snapshot with one question flagged SAFETY_ACTION_PARAMETER.

    The delivered PEP_QUESTIONS extract has that flag FALSE on all 345 rows, and
    CRITICAL true on only four — three inactive, one worth zero marks. So the
    criticality rule has nothing to fire on against real data.

    The rule still has to work, because a future extract may populate the flags
    and the guarantee it enforces ("a high mark does not cancel a safety
    failure") is one of the system's load-bearing claims. Rather than delete the
    tests or let them silently pass on absent data, they run against a snapshot
    where the flag is set — proving the machinery, not the fixture.
    """
    import shutil

    import duckdb

    dest = tmp_path_factory.mktemp("safety") / "flagged.duckdb"
    shutil.copy2(duckdb_snapshot, dest)
    con = duckdb.connect(str(dest))
    # Flag the question that is failed most often, so there is a crew member
    # with a real recorded failure against it.
    qid = con.execute("""
        SELECT qf.QUESTION_ID FROM PEP_QUESTION_FEEDBACK qf
        WHERE qf.FEEDBACK = 'false' AND qf.P_IS_CURRENT
        GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1
    """).fetchone()[0]
    con.execute(
        "UPDATE PEP_QUESTIONS SET SAFETY_ACTION_PARAMETER = TRUE WHERE QUESTION_ID = ?",
        [qid])
    con.close()
    ex = DuckDBExecutor(dest, read_only=True)
    yield ex
    ex.close()
