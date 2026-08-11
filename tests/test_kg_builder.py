"""Agent 1 assembly. The LLM stages are exercised separately (they cost money and
time); these cover the deterministic scaffolding that must hold regardless of what
the model returns."""

import pytest

from crew_perf.agents.kg_builder import _table_brief, build, reconcile_coverage, render_markdown
from crew_perf.graph.schema import ColumnRole, EdgeLabel, VertexLabel, column_note
from crew_perf.sources import load_registry


@pytest.fixture(scope="module")
def pep():
    return load_registry()["PEP"]


@pytest.fixture(scope="module")
def result(pep, executor):
    return build(pep, executor=executor, use_llm=False)


def test_audit_columns_never_reach_the_llm(pep):
    """~38% of every PEP table is audit/SCD-2 bookkeeping. Sending it wastes
    tokens and invites the model to treat it as meaningful."""
    brief = _table_brief(pep.tables["MENTOR_FEEDBACK"])
    names = {c["name"] for c in brief["columns"]}
    assert not names & {"LOAD_DATE", "P_CREATED_BY", "P_IS_CURRENT", "ROW_HASH"}
    assert "MARK" in names and "IGA" in names
    assert brief["total_columns"] > len(brief["columns"])


def test_confirmed_semantics_are_surfaced_to_the_llm(pep):
    """Without this the model reads FEEDBACK as prose because the type is TEXT,
    and the graph then asserts that the primary boolean signal is narrative."""
    brief = _table_brief(pep.tables["PEP_QUESTION_FEEDBACK"])
    feedback = next(c for c in brief["columns"] if c["name"] == "FEEDBACK")
    assert "BOOLEAN" in feedback["confirmed_semantics"]
    assert feedback["role"] == ColumnRole.MEASURE.value


def test_mark_is_a_measure_despite_being_text(pep):
    """MENTOR_FEEDBACK.MARK is the label every correlation runs against. Typed
    TEXT, it would otherwise be classified as a dimension and never enter the
    candidate attribute pool at all."""
    mf = pep.tables["MENTOR_FEEDBACK"]
    mark = next(c for c in mf.columns if c.name == "MARK")
    assert mark.role == ColumnRole.MEASURE
    assert mark.is_scorable
    assert "TRY_CAST" in column_note("MENTOR_FEEDBACK", "MARK")


def test_every_table_becomes_a_vertex(pep, result):
    assert len(result.nodes) == len(pep.tables)
    assert all(n["label"] == VertexLabel.TABLE for n in result.nodes)
    assert all(n["id"].startswith("PEP__table__") for n in result.nodes)


def test_vertices_carry_roles_and_scd2_flags(result):
    node = next(n for n in result.nodes if n["name"] == "MENTOR_FEEDBACK")
    assert node["scd2"] is True
    assert node["column_roles"]["MARK"] == ColumnRole.MEASURE.value
    assert "MARK" in node["measures"]
    assert "IGA" in node["identity_columns"]


def test_category_carries_the_real_template_link(pep):
    """The delivered schema has no template-to-category mapping table, so
    PEP_CATEGORY.TEMPLATE_ID is the actual relationship. It was previously
    flagged as a denormalised trap; keeping that flag would suppress the one
    join the assessment chain depends on."""
    from crew_perf.agents import joins as J
    from crew_perf.graph.schema import CONFIRMED_COLUMN_NOTES

    note = CONFIRMED_COLUMN_NOTES.get(("PEP_CATEGORY", "TEMPLATE_ID"), "")
    assert not note.upper().startswith("DENORMALISED")
    assert not J._is_denormalised("PEP_CATEGORY", "TEMPLATE_ID")


def test_only_declared_tables_exist(pep):
    """schemas/ is the only input; the graph must not contain tables no CSV
    declares."""
    assert set(pep.tables) == {
        "EMPLOYEE_INFO", "MENTOR_FEEDBACK", "PEP_CATEGORY", "PEP_DEVIATION_MATRIX",
        "PEP_FLIGHT_DETAILS", "PEP_GRADE", "PEP_QUESTIONS", "PEP_QUESTION_FEEDBACK",
        "PEP_SCHEDULER", "PEP_TEMPLATE",
    }


def test_subset_membership_is_recorded(result):
    """pep_schema.csv IS the schema now — there is no wider superset to be a
    subset of, so every table is in scope."""
    assert all(n["in_subset"] for n in result.nodes)
    assert len(result.nodes) == 10


def test_join_edges_carry_evidence_into_the_graph(result):
    joins = [e for e in result.edges if e["label"] == EdgeLabel.JOINS_TO]
    assert joins
    for e in joins:
        assert e["evidence"] and e["source_column"] and e["target_column"]
        assert 0.0 < e["confidence"] <= 1.0
        assert e["join_source"] in {"declared", "inferred", "asserted"}




def test_reconcile_coverage_attaches_uncovered_tables():
    concepts = [{"id": "grading", "label": "Grading Scale & Deviation Rules",
                 "source_tables": ["PEP_GRADE"]}]
    enriched = [
        {"table": "PEP_GRADE", "concepts": ["Grading Scale"]},
        {"table": "PEP_DEVIATION_MATRIX", "concepts": ["Grading Scale & Deviation Rules"]},
    ]
    out, notes = reconcile_coverage(concepts, enriched, ["PEP_GRADE", "PEP_DEVIATION_MATRIX"])
    assert "PEP_DEVIATION_MATRIX" in out[0]["source_tables"]
    assert notes and "PEP_DEVIATION_MATRIX" in notes[0]


def test_reconcile_coverage_refuses_a_bad_match():
    """Force-fitting a table into an unrelated concept is worse than leaving the
    gap visible."""
    concepts = [{"id": "grading", "label": "Grading Scale", "source_tables": ["PEP_GRADE"]}]
    enriched = [{"table": "PEP_SLA_MAIL_TEMPLATE", "concepts": ["Email Notification Templates"]}]
    out, notes = reconcile_coverage(concepts, enriched, ["PEP_SLA_MAIL_TEMPLATE"])
    assert "PEP_SLA_MAIL_TEMPLATE" not in out[0]["source_tables"]
    assert not notes


def test_graph_serialises_with_stats(result):
    graph = result.to_graph()
    assert graph["stats"]["tables"] == len(result.nodes)
    assert graph["stats"]["verified_joins"] > 0
    assert graph["source"] == "PEP"


def test_markdown_renders_without_concepts(result):
    md = render_markdown(result.to_graph(), result)
    assert "# Concept Graph — PEP" in md
    assert "MENTOR_FEEDBACK" in md
    assert "## Join edges" in md




