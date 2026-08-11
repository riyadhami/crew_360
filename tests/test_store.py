"""Graph store — what the retrieval agent's tools actually read."""

import pytest

from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


def test_store_loads_every_onboarded_source(store):
    """Loading PEP alone left the retrieval agent seeing ten of forty-odd tables
    — it answered questions about leave or check-in by reporting the data
    unavailable, which is a false answer, not a missing one.

    The onboarded set is read from the registry rather than written out here, and
    only the ungated sources carry an exact count. CrewPortal's is decided by an
    LLM relevance gate that has returned 13, 16 and 17 across builds of the same
    data, so pinning it made this test fail on a rerun and pass on the next —
    which tells a reader nothing about whether every source loaded.
    """
    from crew_perf.sources import load_registry

    s = store.summary()
    onboarded = {src.name for src in load_registry().available() if src.onboarded}
    assert set(s["sources"]) == onboarded
    assert s["tables"] == sum(s["tables_by_source"].values())

    # Ungated sources: every scoped table must arrive.
    exact = {"PEP": 10, "CLMS": 15, "ServiceNow": 5, "CAC": 2}
    assert {k: v for k, v in s["tables_by_source"].items() if k in exact} == exact
    assert s["tables_by_source"]["CrewPortal"] >= 10, "the gate dropped most of CrewPortal"
    assert s["concepts"] > 0
    assert s["rules"] >= 6


def test_a_concept_named_by_several_sources_spans_them():
    """Overwriting on merge would leave a shared concept pointing only at the last
    source's tables.

    Asserted against constructed graphs rather than the built ones: which concept
    an enrichment run *names* varies between builds (PEP's crew concept has been
    both `crew_identity` and `crew_identity_and_context`), and a test that pins
    the label fails on a rename while still missing the merge bug it exists for.
    """
    from crew_perf.graph.store import GraphStore

    merged = GraphStore.__new__(GraphStore)
    merged.sources, merged.warnings = [], []
    merged.tables, merged.concepts, merged.joins = {}, {}, []
    merged._membership = {}
    for source, table in (("PEP", "EMPLOYEE_INFO"), ("CLMS", "M_CLMS_CREW")):
        merged._absorb({
            "source": source,
            "nodes": [],
            "concepts": [{"id": f"{source}__concept__crew_identity",
                          "name": "crew_identity",
                          "display_label": "Crew Identity",
                          "description": "who a crew member is",
                          "source_tables": [table], "key_columns": []}],
            "edges": [],
        })
    assert set(merged.concepts["crew_identity"].source_tables) == {
        "EMPLOYEE_INFO", "M_CLMS_CREW"}


def test_every_source_crew_master_is_covered_by_some_concept(store):
    """The label may vary; a crew master belonging to no concept at all is a hole
    in the graph the retrieval agent explores."""
    for master in ("EMPLOYEE_INFO", "M_CLMS_CREW", "M_CREW_DETAILS", "M_SN_CREW"):
        assert store.concepts_of(master), f"{master} belongs to no concept"


def test_scd2_tables_are_identified(store):
    """Every delivered table is versioned; if this set is empty the currency rule
    silently stops firing and every query starts double-counting.

    The equality is the whole assertion. The population it holds over is whatever
    was built — a count pinned here fails when a source is onboarded, which is
    the one change that should leave this property untouched.
    """
    assert store.tables, "no tables loaded at all"
    assert len(store.scd2_tables) == len(store.tables)


def test_column_roles_survive_into_the_store(store):
    mf = store.table("MENTOR_FEEDBACK")
    assert mf.role_of("MARK") == "measure"
    assert mf.role_of("LOAD_DATE") == "audit"
    assert "IGA" in mf.identity_columns


def test_join_path_finds_the_assessment_chain(store):
    path = store.join_path("PEP_QUESTION_FEEDBACK", "PEP_TEMPLATE")
    assert path is not None
    assert len(path) <= 4


def test_join_path_refuses_to_route_through_a_guess(store):
    """A path assembled from low-confidence edges produces SQL that runs and
    returns the wrong rows — worse than admitting no path exists. With only ten
    tables almost everything is reachable via MENTOR_FEEDBACK, so the property
    worth testing is the confidence bar itself, not unreachability."""
    assert store.join_path("PEP_GRADE", "PEP_FLIGHT_DETAILS") is not None
    assert store.join_path("PEP_GRADE", "PEP_FLIGHT_DETAILS", min_confidence=1.01) is None


def test_join_between_returns_all_columns(store):
    """A table pair can be joinable on more than one column, and the store must
    surface every one — collapsing them loses a usable path.

    (Previously asserted on PEP_CATEGORY -> PEP_TEMPLATE; that edge is now
    correctly suppressed because TEMPLATE_ID there is denormalised, so the
    property is checked on a pair where both columns are genuine.)"""
    edges = store.join_between("PEP_QUESTIONS", "PEP_CATEGORY", min_confidence=0.8)
    cols = {e.source_column for e in edges}
    assert "CATEGORY_ID" in cols


def test_scoring_parameters_come_from_the_policy_module(store):
    """Rules are plain configuration now. The graph holds schema and
    relationships; it no longer holds policy text."""
    params = store.scoring_parameters()
    for key in ["grading", "mark_aggregation", "template_selection", "confidence"]:
        assert key in params
    assert params["grading"]["floor_grade"] == "C"



def test_digest_orients_without_exploration(store):
    """The agent burns its whole turn budget rediscovering basics without this."""
    d = store.digest()
    assert "EMPLOYEE_INFO" in d and "BASE" in d
    assert "PEP_SCHEDULER" in d and "STATUS" in d
    assert "MENTOR_FEEDBACK.IGA = EMPLOYEE_INFO.IGA" in d
    # Audit noise must not be spent on tokens.
    assert "P_CREATED_BY" not in d and "ROW_HASH" not in d
    # Budgeted per table rather than as a fixed ceiling. The digest grows with the
    # graph — enum capture is not gated on table size, so fact tables contribute
    # their closed lists too, which is what lets an agent see 'DEVIATION' is a
    # value in SN_SERVICE_CHECK rather than hunt for it in PEP's grading tables.
    # A fixed number therefore fails the next time a source is onboarded, when
    # nothing about orientation has actually regressed. ~550 characters per table
    # is the shape to hold; the ceiling catches a table that starts dumping prose.
    budget = 600 * len(store.tables)
    assert len(d) < budget, (
        f"{len(d)} chars across {len(store.tables)} tables — orientation is "
        f"meant to cost ~550 per table, not {len(d) // max(len(store.tables), 1)}"
    )


def test_the_digest_carries_codes_from_every_source_not_just_pep(store):
    """Enum capture used to skip any table over 200 rows, which is every fact
    table outside PEP's reference set. PEP arrived with its codes spelled out and
    the other three sources arrived with none, so every graph lookup surfaced PEP
    — a structural bias in the enrichment, not in any prompt."""
    d = store.digest()
    assert "'DEVIATION'" in d or "DEVIATION" in d, "ServiceNow's service checks carry no codes"
    assert "Crew Feedback" in d, "ServiceNow's report categories are missing"


def test_search_matches_stored_values_not_only_names(store):
    """A business word is usually a value, not an identifier. Searching names
    alone returned PEP_DEVIATION_MATRIX for "deviation" and nothing from
    ServiceNow, and the agent concluded service deviations lived in PEP's
    grading tables."""
    hits = store.search("deviation")
    tables = {h.get("table") or h.get("name") for h in hits}
    assert "SN_SERVICE_CHECK" in tables

    feedback = {h.get("table") or h.get("name") for h in store.search("crew feedback")}
    assert "SN_FLIGHT_REPORT" in feedback


def test_the_digest_shows_every_table_not_the_first_n(store):
    """It used to cap at 26. Merging three sources pushed twelve tables off the
    end, and a table the agent cannot see is a question it wrongly refuses."""
    d = store.digest()
    for name in store.tables:
        assert f"- {name} —" in d, f"{name} missing from the digest"
    for source in ("PEP", "CLMS", "CrewPortal"):
        assert f"-- {source} (" in d, "tables must be grouped by source"


def test_the_digest_advertises_the_cross_source_bridge(store):
    """Without the bridge lines the agent has no way to know a CLMS question
    about a crew member named by IGA is answerable at all."""
    d = store.digest()
    assert "crosses sources" in d
    assert "M_CREW_DETAILS.IGA = EMPLOYEE_INFO.IGA" in d


def test_digest_only_advertises_verified_joins(store):
    d = store.digest()
    # Bounded at the next heading: the identity brief that follows also uses
    # "- " bullets, and reading those as joins made this test fail on prose.
    joins_section = d.split("### Verified joins")[1].split("###")[0]
    checked = 0
    for line in joins_section.strip().splitlines():
        if not line.startswith("- "):
            continue
        left, right = line[2:].split("<<")[0].strip().split(" = ")
        lt, _ = left.split(".", 1)
        rt, _ = right.split(".", 1)
        edges = store.join_between(lt, rt, min_confidence=0.9)
        assert edges, f"digest advertises an unverified join: {line}"
        checked += 1
    assert checked > 10, "the joins section should not be near-empty"


def test_search_finds_across_kinds(store):
    kinds = {h["kind"] for h in store.search("grade")}
    assert "table" in kinds or "column" in kinds
    assert store.search("mentor")


def test_small_dimensions_publish_their_real_values(store):
    """The agent filtered on `TEMPLATE_CODE ILIKE '%LEAD%'` against a column whose
    values are `LCA`, and got zero rows from a query that looked correct. The
    codes cost nothing to carry and remove the guess entirely."""
    tmpl = store.table("PEP_TEMPLATE")
    assert "LCA" in tmpl.enum_values.get("TEMPLATE_CODE", [])


def test_enum_values_reach_the_prompt_digest(store):
    d = store.digest()
    assert "LCA" in d


def test_large_tables_do_not_publish_values(store):
    """Only small dimensions are enumerable; a fact table would blow the prompt."""
    mf = store.table("MENTOR_FEEDBACK")
    assert not mf.enum_values.get("IGA")


def test_a_code_lookup_is_shown_as_pairs_not_two_sorted_lists(store):
    """The digest used to print two independently sorted lists, which implied a
    pairing that was wrong.

    The real mapping (from the dev extract) makes the point sharper than the
    invented one did: ids 3 and 4 are `CarryForword` and `Earned` — balance
    lifecycle states, not request states at all. A guess of "Pending"/"Withdrawn"
    would have produced queries that silently selected accrual rows."""
    t = store.table("M_LMS_STATUS_TYPE")
    decode = t.decodes["STATUS_TYPE_ID"]
    assert decode["1"] == "Approved"
    assert decode["2"] == "Not Approved"
    assert decode["3"] == "CarryForword"
    assert decode["9"] == "Accepted"
    d = store.digest()
    assert "1=Approved, 2=Not Approved" in d
    assert "STATUS_TYPE in (" not in d, "the misleading paired list must not survive"


def test_a_decode_is_only_published_when_the_code_is_unique(store):
    """DESIGNATION_CODE repeats across PEP_TEMPLATE's four templates. Collapsing
    it produced whichever row happened to be last, presented as the definition."""
    t = store.table("PEP_TEMPLATE")
    assert "DESIGNATION_CODE" not in t.decodes
    assert t.decodes["TEMPLATE_ID"]["3"] == "A320 — Cabin Attendant"


def test_a_code_meaning_appears_where_the_filter_gets_written(store):
    """Knowing what a status id means is no use while writing a predicate on
    T_CLMS_LEAVE_REQ_MASTER. The agent wrote STATUS_TYPE_ID IN (1,2) and counted
    refused leave as taken.

    Both decodes must reach the fact table: with 20 real leave types, three of
    which are unpaid, an agent filtering LWP by guessing an id is near-certain
    to pick the wrong one."""
    reachable = dict((col, m) for col, _, m in
                     store.foreign_decodes("T_CLMS_LEAVE_REQ_MASTER"))
    assert reachable["STATUS_TYPE_ID"]["2"] == "Not Approved"
    assert reachable["LEAVE_TYPE_ID"]["10"] == "LWP"
    d = store.digest()
    assert "= STATUS_TYPE_ID -> M_LMS_STATUS_TYPE: 1=Approved" in d
    assert "10=LWP" in d


def test_the_identity_brief_states_the_keys_are_not_convertible(store):
    """The agent stripped 'IGA' off IGA60406 and filtered CLMS on '60406' — a
    valid query returning nothing, reported as 'no leave taken'."""
    brief = store.identity_brief()
    assert "CREW_ID" in brief and "IGA" in brief
    assert "NEVER" in brief
    assert "M_CREW_DETAILS" in brief


def test_no_personal_data_reaches_the_graph_or_the_digest(store):
    """The warehouse hashes personal data behind the crew identifier, so IGA is
    who a crew member is and a name or an email is neither a signal nor a join
    key. Enforced by dropping the columns where the exports are parsed — if one
    survives into the graph it reaches every prompt that lists a table, every
    generated query, and every scorecard cached on disk.
    """
    from crew_perf.graph.schema import PII_COLUMNS

    leaked = [f"{t.name}.{c}" for t in store.tables.values()
              for c in (t.columns or []) if c.upper() in PII_COLUMNS]
    assert not leaked, f"personal data reachable in the graph: {leaked}"


def test_a_query_asking_for_personal_data_is_refused(store):
    """The graph no longer lists these columns, but the physical tables still
    have them: the warehouse hashes the values, it does not drop the columns. So
    an unqualified `SELECT EMPLOYEE_NAME` — which the column check never sees,
    because it only inspects qualified references — and `SELECT *` both still
    reach real personal data unless refused by name.
    """
    from crew_perf.data.validator import validate

    current = " WHERE P_IS_CURRENT = TRUE LIMIT 5"
    for sql in (f"SELECT EMPLOYEE_NAME FROM EMPLOYEE_INFO{current}",
                f"SELECT e.EMAIL_ID FROM EMPLOYEE_INFO e{current}",
                f"SELECT * FROM EMPLOYEE_INFO{current}",
                f"SELECT e.* FROM EMPLOYEE_INFO e{current}"):
        assert not validate(sql, store).ok, f"not refused: {sql}"

    # and the rule must not swallow reference labels that merely contain "NAME",
    # nor a legitimate value that happens to collide with a PII column name
    assert validate(f"SELECT IGA, BASE, DESIGNATION FROM EMPLOYEE_INFO{current}", store).ok
    assert validate(
        "SELECT IGA_CODE FROM T_CHECKIN WHERE P_IS_CURRENT = TRUE "
        "AND CREW_CHECKIN_MODEL = 'MOBILE' LIMIT 5", store).ok
