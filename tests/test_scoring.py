"""Agent 4 — the two-track scorer.

Scoring people is consequential, so the properties tested here are the ones that
make a score defensible: it must be reproducible from its own components, a
critical safety failure must never be absorbed into a good average, and missing
data must shrink stated coverage rather than the number.
"""

import pytest

from crew_perf.agents import dynamic, scoring
from crew_perf.data.executor import DuckDBExecutor
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def weightset(weights):
    return weights


@pytest.fixture(scope="module")
def busy_crew(executor):
    """A crew member with enough history to score confidently."""
    r = executor.execute("""
        SELECT mf.IGA FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT AND ps.P_IS_CURRENT AND ps.STATUS = 2
        GROUP BY 1 HAVING COUNT(*) >= 8 ORDER BY COUNT(*) DESC LIMIT 1
    """, limit=1)
    return r.rows[0][0]


@pytest.fixture(scope="module")
def card(busy_crew, weightset, store, executor):
    return scoring.score(busy_crew, weightset, store=store, executor=executor)


def test_both_tracks_are_computed(card):
    """Reporting only one track makes the score impossible to reconcile against
    production, and hides the divergence that is the whole point."""
    assert card.native.get("assessments", 0) > 0
    assert card.native.get("mean_mark") is not None
    assert card.composite_score is not None
    assert 0 <= card.composite_score <= 10


def test_score_is_reproducible_from_its_components(card):
    """A number nobody can recompute is not defensible.

    Contributions are weighted z-scores; the 0-10 figure is a linear presentation
    of their coverage-weighted mean, so both steps have to reproduce."""
    weighted_z = sum(c.contribution for c in card.components) / card.coverage
    assert abs(weighted_z - card.composite_z) < 1e-3
    expected = min(max(5.0 + 1.667 * weighted_z, 0.0), 10.0)
    assert abs(expected - card.composite_score) < 0.01


def test_components_are_weighted_on_z_not_percentile(card):
    """Percentiles discard magnitude: weighting ranks instead of values cost more
    than half of what Agent 2's weighting bought (recovery +0.82 -> +0.39). The
    percentile is kept for presentation only and must never enter the sum."""
    for c in card.components:
        assert c.percentile is not None and 0 <= c.percentile <= 100
        assert abs(c.contribution - c.weight * c.normalized) < 1e-3
        assert abs(c.normalized) < 12, "normalized should be a z-score, not a percentile"


def test_every_component_carries_evidence(card):
    for c in card.components:
        assert c.evidence.get("population_n", 0) > 0
        # A citation is optional now that no declared rule sets a weight, but a
        # dangling one is worse than none: it points a reader at a rule that is
        # not there.
        if c.rule_ref:
            from crew_perf import policy

            assert policy.rule_exists(c.rule_ref), f"{c.attribute} cites a missing rule"
        assert c.direction in {"higher_is_better", "lower_is_better"}


def test_weights_sum_to_the_stated_coverage(card):
    assert abs(sum(c.weight for c in card.components) - card.coverage) < 1e-5  # 6dp rounding across many weights accumulates ~5e-6


def test_the_two_tracks_are_compared_on_the_same_scale(card):
    """Native is a mark out of 100; composite is a percentile standing where 5.0
    is the median. Differencing them directly made a 98.6 mark look
    catastrophically worse than a 71st-percentile composite — an artefact of
    scale, not a finding. Both are ranked against the same population instead."""
    assert "percentile" in card.native
    assert 0 <= card.native["percentile"] <= 100
    standing = [f for f in card.findings if f.startswith("standing:")]
    assert standing, "no like-for-like standing comparison reported"
    assert scoring.COMPOSITE_BASIS in standing[0]


def test_the_delivered_data_has_no_criticality_to_report(executor):
    """Recorded, not assumed. Every SAFETY_ACTION_PARAMETER in the delivered
    extract is FALSE, so the criticality rule is dormant — implemented and
    enforced, but with nothing to fire on. If a future extract populates the
    flags this test starts failing, which is the signal to celebrate rather
    than to patch."""
    n = executor.execute("""
        SELECT COUNT(*) FROM PEP_QUESTIONS
        WHERE P_IS_CURRENT AND ACTIVE AND SAFETY_ACTION_PARAMETER
    """, limit=1).rows[0][0]
    assert n == 0, "safety flags are now populated — re-enable the live assertions"


def test_critical_failures_are_not_netted_into_the_score(store, weightset, safety_flagged_db):
    """The criticality rule: a high mark does not cancel a critical failure. A scorecard that
    shows only the aggregate hides the finding that matters most.

    Run against a snapshot with the flag set, because the delivered data has
    none — see the `safety_flagged_db` fixture."""
    executor = safety_flagged_db
    r = executor.execute("""
        SELECT mf.IGA, COUNT(*) AS fails
        FROM PEP_QUESTION_FEEDBACK qf
        JOIN PEP_QUESTIONS q ON q.QUESTION_ID = qf.QUESTION_ID
        JOIN MENTOR_FEEDBACK mf ON mf.ID = qf.FEEDBACK_ID
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE qf.FEEDBACK = 'false' AND q.SAFETY_ACTION_PARAMETER
          AND mf.P_IS_CURRENT AND ps.P_IS_CURRENT AND ps.STATUS = 2
        GROUP BY 1 ORDER BY 2 DESC LIMIT 1
    """, limit=1)
    assert r.rows, "fixture failed to create a safety failure"
    iga = r.rows[0][0]
    c = scoring.score(iga, weightset, store=store, executor=executor)

    assert any("safety finding" in n for n in c.negatives)
    assert any("NOT offset" in f for f in c.findings)
    # The failures must be visible as their own items, not folded into a total.
    assert len([n for n in c.negatives if "safety finding" in n]) >= 1


def test_missing_data_reduces_coverage_not_score(card):
    """The fairness rule: a gap is missing data, never a zero."""
    assert card.unavailable, "unavailable sources must be stated explicitly"
    reasons = ("out of scope", "no value", "not present", "insufficient")
    assert all(any(r in u for r in reasons) for u in card.unavailable)
    assert card.coverage > 0


def test_out_of_scope_data_is_stated_as_permanent(card):
    """Duty hours are descoped, not pending. Saying "deferred" invites a reader to
    wait for a number that is never coming, and invites the agent to approximate
    it in the meantime."""
    duty = [u for u in card.unavailable if "duty hours" in u]
    assert duty, "duty hours must be named as unavailable"
    assert "out of scope" in duty[0]
    assert "deferred" not in duty[0] and "COPS" not in duty[0]


def test_confidence_reflects_sample_size(store, weightset, executor):
    r = executor.execute("""
        SELECT mf.IGA FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT AND ps.P_IS_CURRENT AND ps.STATUS = 2
        GROUP BY 1 HAVING COUNT(*) = 1 LIMIT 1
    """, limit=1)
    if not r.rows:
        pytest.skip("no single-assessment crew in this dataset")
    c = scoring.score(r.rows[0][0], weightset, store=store, executor=executor)
    assert c.confidence == "indicative"
    assert any("not a basis for ranking" in f for f in c.findings)


def test_unknown_crew_returns_an_empty_card_not_an_error(store, weightset, executor):
    c = scoring.score("IGA00000", weightset, store=store, executor=executor)
    assert c.native.get("assessments", 0) == 0
    assert c.composite_score is None
    assert any("nothing to score" in f for f in c.findings)


def test_repeated_mentor_phrasing_is_collapsed(card):
    """Mentors reuse wording; listing it four times reads as four observations."""
    texts = [p.split("  (noted")[0] for p in card.positives]
    assert len(texts) == len(set(texts))


def test_qualitative_text_is_quoted_verbatim(card):
    """The qualitative rule: free text is never compressed into a label or a sentiment score."""
    for p in card.positives:
        assert not p.startswith(("positive", "POSITIVE", "score:"))
    assert all(isinstance(p, str) and p.strip() for p in card.positives)


def test_scorecard_serialises(card, tmp_path):
    import json

    path = scoring.save(card, tmp_path / "card.json")
    loaded = json.loads(path.read_text())
    assert loaded["iga"] == card.iga
    assert len(loaded["components"]) == len(card.components)
    assert loaded["weightset_version"] == card.weightset_version


def test_an_unpopulated_source_is_visible_not_renormalised_away(
    tmp_path, duckdb_snapshot, store, sample_iga
):
    """`coverage` measures what was computable for ONE crew member. An attribute
    dropped because nobody has data is renormalised out of the denominator, so a
    whole unloaded source leaves coverage at 100% while the score quietly ignores
    it. In production — where a source may not be populated yet — that is a score
    that looks complete and is not."""
    import shutil

    import duckdb

    from crew_perf.agents.attributes import build_frame, discover
    from crew_perf.data.executor import DuckDBExecutor

    sparse = tmp_path / "sparse.duckdb"
    shutil.copy2(duckdb_snapshot, sparse)
    con = duckdb.connect(str(sparse))
    con.execute("DELETE FROM T_SPL_APPRECIATION")
    con.execute("DROP TABLE T_CHECKIN")
    con.close()

    ex = DuckDBExecutor(sparse, read_only=True)
    try:
        ws, _ = dynamic.design(store=store, executor=ex, use_llm=False)
        pop = build_frame(ex, discover(store, ex))
        c = scoring.score(sample_iga, ws, store=store, executor=ex, population=pop)

        assert c.composite_score is not None, "must still produce a score"
        assert c.signal_coverage < 0.995, "lost signals must be visible"
        warning = [f for f in c.findings if "was usable here" in f]
        assert warning, "an unloaded source must be reported on the scorecard"

        # Named in the words the reader has, not by column. "appreciation_count"
        # tells whoever reads a scorecard nothing they can act on; "Appreciations
        # received" tells them a whole recognition source is dark.
        attrs = {a.name: a for a in discover(store, ex).attributes}
        expected = [attrs[a].display_label if a in attrs else a
                    for a in ws.dropped_sources[:3]]
        assert any(label in warning[0] for label in expected), \
            "the warning must name what is missing, not just that something is"
        assert not any(a in warning[0] for a in ws.dropped_sources[:3] if a in attrs), \
            "a scorecard must not fall back to column names in front of a reader"
    finally:
        ex.close()


def test_signal_coverage_names_the_known_coverage_hole(card, weightset):
    """Even on complete data, coaching is unobservable for ~78% of crew — that
    costs real declared importance and should be stated, not absorbed."""
    assert card.signal_coverage <= 1.0
    if card.signal_coverage < 0.995:
        assert weightset.dropped_sources
        assert any("was usable here" in f for f in card.findings)


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


def test_a_scorecard_says_what_the_composite_is_built_from(store, executor):
    """A percentile with no stated basis is a number the reader cannot place, and
    the basis has to be a phrase rather than a term of art: a scorecard is read by
    people who do not work on this codebase, so "data-derived composite" and a
    column name are equally useless there."""
    from crew_perf.agents import dynamic, scoring
    from crew_perf.agents.attributes import build_frame, discover

    pop = build_frame(executor, discover(store, executor))
    iga = next(i for i in pop.index if pop.loc[i].get("mean_mark") == pop.loc[i].get("mean_mark"))

    ws, _ = dynamic.design(store=store, executor=executor, use_llm=False)
    card = scoring.score(iga, ws, store=store, executor=executor, population=pop)
    standing = [f for f in card.findings if f.startswith("standing:")]
    if standing:
        assert scoring.COMPOSITE_BASIS in standing[0]
        assert "business-weighted" not in standing[0]
        # No identifier may reach a finding: these are rendered to the reader.
        assert "_rate" not in standing[0] and "pass_rate_" not in standing[0]
