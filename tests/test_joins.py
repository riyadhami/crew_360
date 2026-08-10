"""Join resolution. PEP has no FKs, so nearly every join is inferred — which
makes the evidence, confidence and data-verification behaviour load-bearing."""

import pytest

from crew_perf.agents import joins as J
from crew_perf.graph.schema import JoinSource
from crew_perf.sources import load_registry


@pytest.fixture(scope="module")
def pep():
    return load_registry()["PEP"]


@pytest.fixture(scope="module")
def resolved(pep, executor):
    return J.prune_refuted(J.resolve_joins(pep, executor))


def test_stem_survives_the_schemas_own_typo():
    """`PEP_SCHDULER_ID` must still relate to `PEP_SCHEDULER`; no exact-match
    rule would ever connect them."""
    assert J._similar(J._stem("PEP_SCHDULER_ID"), J._stem("PEP_SCHEDULER")) >= 0.80


def test_stem_strips_key_suffixes_and_source_prefix():
    assert J._stem("PEP_TEMPLATE") == J._stem("TEMPLATE_ID")
    assert J._stem("CATEGORY_ID") == "CATEGORY"


def test_row_hash_never_becomes_a_join_key(pep):
    """Without the blocklist, every table 'joins' every other on ROW_HASH."""
    edges, _ = J.infer_edges(pep)
    assert not [e for e in edges if e.source_column.upper() == "ROW_HASH"]
    assert not [e for e in edges if e.source_column.upper().startswith("P_")]


def test_the_assessment_chain_is_traversable(resolved):
    """PLAN.md S3.1 — the chain the whole system depends on."""
    links = {(e.source_table, e.target_table) for e in resolved.edges if e.confidence >= 0.80}
    required = [
        ("PEP_QUESTION_FEEDBACK", "PEP_QUESTIONS"),
        ("PEP_QUESTIONS", "PEP_CATEGORY"),
        ("PEP_CATEGORY", "PEP_TEMPLATE"),
        ("PEP_DEVIATION_MATRIX", "PEP_TEMPLATE"),
        ("MENTOR_FEEDBACK", "PEP_SCHEDULER"),
        ("MENTOR_FEEDBACK", "PEP_FLIGHT_DETAILS"),
    ]
    for link in required:
        assert link in links, f"assessment chain broken at {link}"


def test_identity_backbone_resolves_to_the_crew_master(resolved):
    """IGA is shared by five tables and name-matches none of them. Without the
    registry-declared-key path, EMPLOYEE_INFO is orphaned from the whole graph."""
    iga = [e for e in resolved.edges if e.source_column == "IGA" and e.confidence >= 0.80]
    assert iga, "no high-confidence IGA edges resolved"
    assert all(e.target_table == "EMPLOYEE_INFO" for e in iga)
    children = {e.source_table for e in iga}
    assert {"MENTOR_FEEDBACK", "PEP_SCHEDULER"} <= children


def test_scd2_history_does_not_defeat_uniqueness(resolved):
    """PEP_SCHEDULER carries superseded rows. Counting them makes its primary key
    look non-unique and demotes a real parent to 'wrong direction'."""
    edge = next(
        e for e in resolved.edges
        if e.source_table == "MENTOR_FEEDBACK"
        and e.target_table == "PEP_SCHEDULER"
        and e.source_column == "PEP_SCHDULER_ID"
    )
    assert edge.cardinality == "many-to-one"
    assert edge.confidence >= 0.90
    assert edge.verified


def test_reference_lookups_join_by_value(resolved):
    """Grades join by value, not by _ID. Reference tables carry the meaning the
    scoring agents need, so leaving them isolated strands the vocabulary.
    (PEP_STATUS no longer exists in the delivered schema, so STATUS is a bare
    numeric code with nothing to resolve it against.)"""
    links = {
        (e.source_table, e.source_column, e.target_table)
        for e in resolved.edges if e.confidence >= 0.80
    }
    assert ("MENTOR_FEEDBACK", "GRADE", "PEP_GRADE") in links


def test_every_edge_carries_evidence(resolved):
    for e in resolved.edges:
        assert e.evidence, f"{e.source_table}->{e.target_table} has no evidence"
        assert 0.0 < e.confidence <= 1.0


def test_refuted_edges_are_pruned_not_silently_kept(pep, executor):
    report = J.resolve_joins(pep, executor)
    before = len(report.edges)
    J.prune_refuted(report)
    assert len(report.edges) < before, "nothing pruned — verification is not biting"
    assert report.skipped, "pruning must record why"










def test_table_only_source_cannot_infer(pep):
    """A schema without columns must say so, not silently return nothing."""
    from crew_perf.sources import SourceSchema

    src = SourceSchema(name="X", engine="snowflake", detail="tables",
                       join_key="CrewID", context="", tables={})
    edges, skipped = J.infer_edges(src)
    assert edges == []
    assert skipped and "column detail" in skipped[0]["reason"]


def test_boolean_flags_are_never_join_keys(resolved):
    """`EMPLOYEE_INFO.ACTIVE = PEP_TEMPLATE.ACTIVE` passes every name and coverage
    test — 100% name match, ~95% of values resolve — and is meaningless: every
    `true` matches every `true`, so the join multiplies rows instead of relating
    them. The retrieval agent used exactly this edge to build a plausible,
    completely wrong query."""
    flags = {"ACTIVE", "IS_ACTIVE", "PRINTABLE", "CRITICAL", "CRUCIAL",
             "PLANNED_FOR", "NEW_JOINEE", "EMAIL_STATUS"}
    bad = [e for e in resolved.edges if e.source_column.upper() in flags]
    assert not bad, f"boolean flag used as a join key: {[(e.source_table, e.source_column) for e in bad]}"




def test_identity_spine_is_asserted_from_the_registry():
    """An identity key rarely names its own table — `IGA` resembles neither
    `EMPLOYEE_INFO` nor `M_CREW_DETAILS` — so name similarity is worst at exactly
    the most important relationship in each source. The registry names the crew
    master instead."""
    from crew_perf.graph.schema import JoinSource as JS

    for name, master in [("PEP", "EMPLOYEE_INFO"), ("CLMS", "M_CLMS_CREW"),
                         ("CrewPortal", "M_CREW_DETAILS")]:
        src = load_registry()[name]
        edges = J.identity_edges(src)
        assert edges, f"{name}: no identity spine"
        assert all(e.target_table == master for e in edges)
        assert all(e.source == JS.ASSERTED and e.confidence >= 0.85 for e in edges)


def test_every_spelling_of_the_identity_key_is_recognised():
    """CrewPortal writes IGA, CREW_IGA and IGA_CODE, and attributes a flight
    issue to seven crew by seat position. Treated as unrelated columns, the
    source cannot be joined at all."""
    src = load_registry()["CrewPortal"]
    cols = {e.source_column for e in J.identity_edges(src)}
    assert {"IGA", "CREW_IGA", "IGA_CODE"} <= cols
    assert {"L1_IGA", "L2_IGA", "CAPT_IGA"} <= cols


def test_clms_ground_codes_join_via_its_own_alias():
    """CLMS uses CREW_ID everywhere except ground codes, which say EMP_NO."""
    src = load_registry()["CLMS"]
    gnd = [e for e in J.identity_edges(src) if e.source_table == "T_CLMS_GND_CODES"]
    assert gnd and gnd[0].source_column == "EMP_NO"


def test_a_source_without_data_still_has_usable_joins():
    """CrewPortal ships no declared FKs and has no data to verify against. With
    only inference it had 0 edges above the validator's bar — onboarded but
    useless."""
    src = load_registry()["CrewPortal"]
    report = J.prune_refuted(J.resolve_joins(src))       # deliberately no executor
    usable = [e for e in report.edges if e.confidence >= 0.8]
    assert len(usable) >= 8


def test_every_edge_names_an_origin_the_system_actually_has():
    """No source system publishes a foreign key, so an edge is only ever declared
    by the curated relationship export, asserted as the identity spine, or
    inferred from column naming and then checked against data. An edge claiming
    any other origin is one whose provenance nobody can audit."""
    reg = load_registry()
    known = (JoinSource.DECLARED, JoinSource.ASSERTED, JoinSource.INFERRED)
    for name in ("PEP", "CLMS", "CrewPortal"):
        src = reg[name]
        edges = J.resolve_joins(src, registry=reg).edges   # no executor: nothing verified
        assert edges and all(e.source in known for e in edges), \
            f"{name}: an edge claims an origin we cannot account for"
        assert J.identity_edges(src), f"{name}: no identity spine to fall back on"


def test_the_curated_export_only_ever_names_real_columns():
    """The export is written in the report author's casing and covers tables that
    are not in the scoped schemas, so every name in it is resolved against the
    column exports rather than trusted. A declared edge naming a column that does
    not exist would fail at query time, not at load, which is the worst place for
    a curated fact to be wrong."""
    reg = load_registry()
    assert reg.declared_joins, "the relationship export produced nothing at all"
    for source_name, joins in reg.declared_joins.items():
        tables = reg[source_name].tables
        for j in joins:
            for table, column in ((j.left_table, j.left_column),
                                  (j.right_table, j.right_column)):
                assert table in tables, f"{table} is not in {source_name}"
                assert column in {c.name for c in tables[table].columns}, \
                    f"{table}.{column} does not exist"
            assert j.left_table != j.right_table, f"{j.raw!r} resolved to a self-join"


def test_what_the_curated_export_could_not_supply_is_reported():
    """Roughly half the export does not become an edge: it documents tables
    outside the three scoped schemas, and several rows describe a table in prose
    ("Parent Transaction Table") rather than naming a join. Both are fine, but a
    curated join that silently failed to load is worse than one nobody wrote —
    nobody goes looking for it."""
    reg = load_registry()
    assert reg.declared_join_notes, "nothing was skipped, which the export cannot support"
    for note in reg.declared_join_notes:
        assert note.get("reason"), "a skipped row must say why it was skipped"


def test_a_declared_edge_is_oriented_by_the_data_not_by_the_text(executor):
    """The export documents a relationship, not a direction — it writes the crew
    position master on the left of what is a many-to-one *into* that master.
    Everything downstream reads `source_table` as the child, so entered from the
    wrong end this edge measured 2% coverage against a non-unique parent, which
    is indistinguishable from a refuted name coincidence: confidence 0.05, below
    the 0.8 an agent may traverse, so the join was unusable.
    """
    reg = load_registry()
    # prune_refuted because that is what the build runs, and the wrong-direction
    # reading of this same pair is exactly what it exists to remove.
    report = J.prune_refuted(
        J.resolve_joins(reg["CrewPortal"], executor=executor, registry=reg))
    by_pair = {(e.source_table, e.target_table): e for e in report.edges}

    edge = by_pair.get(("T_FLIGHT_REPORT_WORK_POSITION_DETAILS", "M_CREW_POSITION"))
    assert edge, "the position lookup is pointing at the transaction table as its parent"
    assert edge.cardinality == "many-to-one"
    assert edge.confidence >= 0.8, "an agent cannot traverse this join"

    # and the reverse does not survive beside it, or the graph holds one
    # relationship twice pointing opposite ways
    assert ("M_CREW_POSITION", "T_FLIGHT_REPORT_WORK_POSITION_DETAILS") not in by_pair


def test_the_declared_tier_reaches_joins_naming_alone_cannot_find(executor):
    """The flight-report spine is the case that motivated reading the export.
    `FLIGHT_TYPE_ID -> AIRCRAFT_ID` share no name, and compliance answers reach
    the report header through REPORT_ID on tables whose names agree with several
    others — so inference either missed them or scored them below traversal, and
    `find_join_path` answered "these tables cannot be joined"."""
    reg = load_registry()
    edges = J.resolve_joins(reg["CrewPortal"], executor=executor, registry=reg).edges
    usable = {(e.source_table, e.target_table) for e in edges if e.confidence >= 0.8}

    assert ("T_FLIGHT_ISSUE_GENERAL_INFO", "M_AIRCRAFT") in usable
    assert ("T_FLIGHT_PROCESS_COMPLIANCE", "T_FLIGHT_ISSUE_GENERAL_INFO") in usable
    assert ("T_FLIGHT_ISSUE_DETAILS", "T_FLIGHT_ISSUE_GENERAL_INFO") in usable


def test_the_identity_bridge_between_sources_is_recorded():
    """O9/G9: CLMS.CREW_ID must not be joined straight to PEP.IGA. The bridge is
    CREWPORTAL.M_CREW_DETAILS, which carries both."""
    reg = load_registry()
    bridge = reg.identity["bridge"]
    assert bridge["table"] == "M_CREW_DETAILS" and bridge["source"] == "CrewPortal"
    master = reg["CrewPortal"].tables["M_CREW_DETAILS"]
    names = {c.name for c in master.columns}
    assert {"IGA", "CREW_ID"} <= names


def test_the_bridge_produces_edges_that_actually_span_sources():
    """Recording the bridge in the registry is not the same as having an edge.
    Per-source graphs are built in isolation, so nothing inside one can reach
    another: merged without this, the graph is three islands and every
    cross-source question is refused as unavailable."""
    reg = load_registry()
    edges = J.bridge_edges(reg)
    pairs = {(e.source_table, e.source_column, e.target_table, e.target_column) for e in edges}
    assert ("M_CREW_DETAILS", "IGA", "EMPLOYEE_INFO", "IGA") in pairs
    assert ("M_CREW_DETAILS", "CREW_ID", "M_CLMS_CREW", "CREW_ID") in pairs
    # Never the shortcut the bridge exists to prevent.
    assert not any(e.source_column == "CREW_ID" and e.target_column == "IGA" for e in edges)
    assert all(e.source == JoinSource.ASSERTED for e in edges), \
        "no inference proposes CREW_ID = IGA"


def test_the_bridge_holds_against_real_data(executor):
    """A bridge that resolves NOTHING is worse than no bridge: it produces empty
    results for every cross-source question rather than an honest refusal.

    Partial resolution is a different thing and must not be conflated with it.
    ServiceNow's extract is a two-month window naming 14k crew, of whom ~18% are
    in the modelled fleet — the join is exactly right for those, and the number
    to check is that the shortfall is *recorded*, not that it is absent."""
    reg = load_registry()
    tables = set(executor.list_tables())
    verified = J.verify_edges(J.bridge_edges(reg), executor, tables, scd2_tables=tables)
    assert verified, "no bridge edges to verify"
    for e in verified:
        assert e.coverage is not None and e.coverage > 0.01, \
            f"{e.source_table}.{e.source_column} resolves nothing — the identifiers " \
            f"do not relate at all"
        assert e.confidence >= 0.8, \
            f"{e.source_table}.{e.source_column} is not usable by the validator"
        if e.coverage <= 0.95:
            assert "populations differ" in e.evidence or "partially verified" in e.evidence, \
                f"{e.source_table}.{e.source_column} resolves {e.coverage:.1%} and does " \
                f"not say so in its evidence"
