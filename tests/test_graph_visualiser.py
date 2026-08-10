"""The graph, shaped for drawing.

The thing worth pinning here is completeness. A visualiser that silently drops
the tail is worse than no visualiser: the reason to look at the graph is to find
the table with no edges or the source with no bridge into it, and that node is
never the popular one. So the tests assert that everything on disk reaches the
payload, and that nothing in the payload points at something that is not there —
a dangling edge draws a line to nowhere and reads as a join that exists.

The rest is the mapping being faithful: a join keeps the column pair it was
verified on, two joins between the same tables on different columns stay two
edges, and a cross-source edge stays marked as one.
"""

from __future__ import annotations

import json

import pytest

from crew_perf import config
from crew_perf.graph_visualiser import payload as gv


@pytest.fixture(scope="module")
def built():
    """The payload, built from whatever this checkout has actually built."""
    if not list(config.GRAPHS_DIR.glob("*_concept_graph.json")):
        pytest.skip("no concept graph — run `crewperf build-graph PEP` first")
    return gv.build_payload()


@pytest.fixture(scope="module")
def artifacts():
    """The raw vertices and edges the payload is built from."""
    return gv._read_artifacts()


def test_every_built_vertex_is_drawn(built, artifacts):
    """Nothing is sampled and nothing is capped.

    A cap would be invisible: the picture still looks like a graph, and the
    tables that fell off the end are exactly the ones nobody would notice were
    missing.
    """
    raw_vertices, _, _ = artifacts
    expected = {v["id"] for v in raw_vertices if v.get("label") in ("Table", "Concept")}
    drawn = {n["id"] for n in built["nodes"] if n["kind"] in ("table", "concept")}
    assert drawn == expected, f"missing from the payload: {sorted(expected - drawn)}"


def test_every_edge_lands_on_a_node_that_exists(built):
    """A dangling edge is drawn as a join and is not one."""
    ids = {n["id"] for n in built["nodes"]}
    dangling = [e["id"] for e in built["edges"] if e["from"] not in ids or e["to"] not in ids]
    assert not dangling, f"edges into nothing: {dangling[:5]}"


def test_a_schema_node_exists_for_every_source_and_owns_its_vertices(built):
    """Schema vertices are synthesised — Cosmos has no such thing.

    Without them the picture is one undifferentiated cloud and the first
    question anyone asks of it, *which system is this from*, has to be answered
    by reading labels one at a time.
    """
    schemas = {n["name"] for n in built["nodes"] if n["kind"] == "schema"}
    sources = {n["source"] for n in built["nodes"] if n["kind"] != "schema"}
    assert schemas == sources

    contains = {(e["from"], e["to"]) for e in built["edges"] if e["kind"] == "contains"}
    for node in built["nodes"]:
        if node["kind"] == "schema":
            continue
        assert (f"{gv.SCHEMA_PREFIX}{node['source']}", node["id"]) in contains


def test_a_join_carries_the_columns_it_was_verified_on(built):
    """`joins` on its own is not a join. The column pair is the join."""
    joins = [e for e in built["edges"] if e["kind"] == "joins_to"]
    assert joins, "no join edges at all"
    for e in joins:
        assert e["detail"]["source_column"], e["id"]
        assert e["detail"]["target_column"], e["id"]
        assert 0.0 <= e["confidence"] <= 1.0


def test_two_joins_between_the_same_tables_on_different_columns_stay_two_edges():
    """The dedupe rule `upsert_edge` uses, applied to the drawing.

    `PEP_CATEGORY -> PEP_TEMPLATE` legitimately exists on both `TEMPLATE_ID` and
    `TEMPLATE_CODE`. Collapsing them on (from, to, label) discards a usable join
    path, and the picture would assert there is one way across where there are
    two.
    """
    raw = [
        {"label": "JOINS_TO", "from": "A", "to": "B",
         "source_column": "TEMPLATE_ID", "target_column": "TEMPLATE_ID"},
        {"label": "JOINS_TO", "from": "A", "to": "B",
         "source_column": "TEMPLATE_CODE", "target_column": "TEMPLATE_CODE"},
        # ...but the identical edge twice is one edge.
        {"label": "JOINS_TO", "from": "A", "to": "B",
         "source_column": "TEMPLATE_ID", "target_column": "TEMPLATE_ID"},
    ]
    edges = gv._edges(raw, {"A", "B"})
    assert len(edges) == 2
    assert {e["detail"]["source_column"] for e in edges} == {"TEMPLATE_ID", "TEMPLATE_CODE"}


def test_an_edge_into_an_unbuilt_source_is_dropped_not_drawn():
    """The bridge names every pair it knows, whoever has run `build-graph`."""
    raw = [{"label": "JOINS_TO", "from": "A", "to": "NOT_BUILT",
            "source_column": "IGA", "target_column": "IGA"}]
    assert gv._edges(raw, {"A"}) == []


def test_cross_source_joins_stay_marked(built):
    """The four identity edges are the only reason the graph is not five islands,
    and they are drawn differently because that is what they are."""
    cross = [e for e in built["edges"] if e["kind"] == "joins_to" and e["cross_source"]]
    if len({n["source"] for n in built["nodes"] if n["kind"] == "schema"}) > 1:
        assert cross, "sources are built but nothing joins them"
    for e in cross:
        a = next(n for n in built["nodes"] if n["id"] == e["from"])
        b = next(n for n in built["nodes"] if n["id"] == e["to"])
        assert a["source"] != b["source"]


def test_columns_keep_their_roles(built):
    """The role is the reason the inspector is worth opening.

    `MEASURE` is the only role a scoring signal can be built from; a column list
    without roles shows the schema and says nothing about what the pipeline can
    do with it.
    """
    tables = [n for n in built["nodes"] if n["kind"] == "table"]
    assert tables
    mentor = next((n for n in tables if n["name"] == "MENTOR_FEEDBACK"), None)
    if mentor is None:
        pytest.skip("PEP not built in this checkout")
    roles = {c["name"]: c["role"] for c in mentor["detail"]["columns"]}
    # Asserted in `graph.schema.CONFIRMED_ROLE_OVERRIDES`: MARK is stored TEXT
    # but is the assessment score every correlation is computed against.
    assert roles.get("MARK") == "measure"
    assert roles.get("IGA") == "identity"


def test_stats_count_what_is_actually_in_the_payload(built):
    """The counter on the canvas reads "105 of 105" and has to be true."""
    s = built["stats"]
    assert s["nodes"] == len(built["nodes"])
    assert s["edges"] == len(built["edges"])
    assert s["tables"] == sum(1 for n in built["nodes"] if n["kind"] == "table")
    assert s["concepts"] == sum(1 for n in built["nodes"] if n["kind"] == "concept")
    assert s["joins"] == sum(1 for e in built["edges"] if e["kind"] == "joins_to")


def test_cosmos_properties_are_decoded_back_into_structure():
    """`_prop_clause` JSON-encodes lists on the way into Cosmos.

    Left encoded on the way out, a table's column list renders in the inspector
    as one long string — the property is present, readable, and useless.
    """
    from crew_perf.graph_visualiser import cosmos_source as cs

    vertex = cs._vertex({
        "type": "vertex", "id": "PEP__table__X", "label": "Table",
        "properties": {
            "name": [{"id": "1", "value": "X"}],
            "source": [{"id": "2", "value": "PEP"}],
            "columns": [{"id": "3", "value": json.dumps(["IGA", "BASE"])}],
            "description": [{"id": "4", "value": "[not] json"}],
        },
    })
    assert vertex["columns"] == ["IGA", "BASE"]
    # Not on the structured list, so it is left exactly as it came back.
    assert vertex["description"] == "[not] json"


def test_an_edge_keeps_the_direction_it_was_written_in():
    """`addE(...).to(dst)` means outV is the source. Reversing it points the
    arrowhead at the many side of a many-to-one."""
    from crew_perf.graph_visualiser import cosmos_source as cs

    edge = cs._edge({
        "type": "edge", "label": "JOINS_TO", "outV": "A", "inV": "B",
        "properties": {"source_column": "PEP_ID", "target_column": "PEP_ID"},
    })
    assert edge["from"] == "A"
    assert edge["to"] == "B"
    assert edge["source_column"] == "PEP_ID"


# ─── the endpoint ───────────────────────────────────────────────────────────

fastapi = pytest.importorskip("fastapi", reason="API extra not installed")
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from crew_perf.api.app import app

    return TestClient(app)


def test_the_visualiser_does_not_reopen_the_removed_graph_explorer(client):
    """`/api/graph` belonged to the explorer that was removed along with the crew
    directory and the weights table — three more ways to reach a score, each
    rendering it differently. This tab reaches no score, so it took its own path
    and left that guarantee assertable."""
    assert client.get("/api/graph").status_code == 404
    assert client.get("/api/graph-view").status_code == 200


def test_the_payload_carries_no_score(client):
    """Structure only. A number about a crew member arriving through this route
    would be a second answer waiting to disagree with the pipeline's."""
    body = client.get("/api/graph-view").text.lower()
    for leaked in ("composite_score", "scorecard", "percentile", "iga60"):
        assert leaked not in body, f"{leaked} reached the visualiser payload"


def test_an_unknown_origin_is_refused_rather_than_guessed(client):
    assert client.get("/api/graph-view", params={"origin": "wherever"}).status_code == 422


def test_the_renderer_and_its_styles_are_served(client):
    """The page is assembled from files; a 404 here is a blank tab."""
    for path in ("/visualiser/visualiser.js", "/visualiser/visualiser.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.content


def test_the_cache_notices_a_rebuilt_graph(client, tmp_path):
    """A stale picture of the graph is the one failure this tab must not have:
    it would show a join that was corrected an hour ago and look authoritative."""
    from crew_perf.graph_visualiser import routes

    first = client.get("/api/graph-view").json()
    assert client.get("/api/graph-view").json()["stats"] == first["stats"]

    # A changed signature must miss the cache, whatever is in it.
    routes._cache["artifacts"] = (("stale",), {"stats": {"nodes": -1}})
    again = client.get("/api/graph-view").json()
    assert again["stats"]["nodes"] == first["stats"]["nodes"]


# ─── one crew member's ego view ─────────────────────────────────────────────


def test_an_identifier_that_is_not_one_is_refused_before_it_reaches_sql():
    """The identifier is interpolated into SQL, because parameter placeholders
    are not portable across DuckDB and the Snowflake connector. That makes the
    pattern the whole defence, so it is asserted rather than assumed."""
    from crew_perf.graph_visualiser.crew_ego import UnsafeIdentifier, build_ego

    for hostile in ("IGA1'; DROP TABLE MENTOR_FEEDBACK--", "a b", "x" * 40,
                    "IGA1 OR 1=1", "", "--", "IGA1/*"):
        with pytest.raises(UnsafeIdentifier):
            build_ego(hostile)


def test_the_bridge_translates_in_both_directions(executor):
    """CLMS is keyed on CREW_ID and everything else on IGA. Typing either must
    reach the same person, because one is never the other with a prefix edited
    on — the translation is a lookup, not string surgery."""
    from crew_perf.graph_visualiser.crew_ego import build_ego

    by_iga = build_ego("IGA60024", executor=executor)
    by_crew = build_ego(by_iga["keys"]["CREW_ID"], executor=executor)
    assert by_iga["keys"] == by_crew["keys"]
    assert by_iga["stats"]["total_rows"] == by_crew["stats"]["total_rows"]


def test_a_table_is_probed_on_its_source_key_not_any_id_column(executor):
    """`EMPLOYEE_INFO` carries `EMPLOYEE_ID` as well as `IGA`, and counting on
    the wrong one answers a different question."""
    from crew_perf.graph_visualiser.crew_ego import build_ego

    ego = build_ego("IGA60024", executor=executor)
    probes = {p["table"]: p for p in ego["presence"]}
    assert probes["EMPLOYEE_INFO"]["column"] == "IGA"
    assert probes["M_CLMS_CREW"]["column"] == "CREW_ID"
    # Reference tables carry no crew key at all and must not be probed.
    assert "PEP_GRADE" not in probes
    assert "M_AIRCRAFT" not in probes


def test_zero_rows_and_unreadable_are_different_answers(executor):
    """Zero means nothing was recorded. An error means nobody could look. A
    permission gap rendered as zero says a crew member has no leave when the
    truth is that the schema was never readable."""
    from crew_perf.graph_visualiser.crew_ego import build_ego

    ego = build_ego("IGA60024", executor=executor)
    for row in ego["presence"]:
        assert (row["rows"] is None) == bool(row["error"]), row["table"]
        if row["error"] is None:
            assert isinstance(row["rows"], int)


def test_an_scd2_table_is_counted_on_current_rows_only(executor):
    """Otherwise a crew member who has changed base three times counts as three
    people's worth of rows, and the view reports history as volume."""
    from crew_perf.graph_visualiser.crew_ego import build_ego

    ego = build_ego("IGA60024", executor=executor)
    scd2 = [p for p in ego["presence"] if p["scd2"]]
    assert scd2, "no SCD-2 table probed — the filter would be untested"
    for p in scd2:
        assert "P_IS_CURRENT = TRUE" in p["sql"]


def test_a_source_holding_nothing_is_named_as_missing_data(executor):
    """The half of the answer that explains a partial-coverage score. A source
    with no rows is missing evidence, not a low score, and the view has to say
    which one rather than leaving a quietly narrower picture."""
    from crew_perf.graph_visualiser.crew_ego import build_ego

    ego = build_ego("IGA60406", executor=executor)
    assert ego["found"]
    assert ego["absent_sources"], "IGA60406 is absent from ServiceNow and CAC"
    for source in ego["absent_sources"]:
        assert not ego["rows_by_source"].get(source)
    assert any("missing data" in w for w in ego["warnings"])


def test_an_unknown_identifier_degrades_rather_than_failing(executor):
    """A typo must produce an empty, explained answer — not a 500, and not a
    confident picture of a crew member who does not exist."""
    from crew_perf.graph_visualiser.crew_ego import build_ego

    ego = build_ego("IGA99999", executor=executor)
    assert ego["found"] is False
    assert ego["stats"]["total_rows"] == 0
    assert any("not in M_CREW_DETAILS" in w for w in ego["warnings"])


def test_the_crew_lookup_survives_a_concurrent_request(client):
    """Regression. This endpoint touches the data layer, so it must run on
    `api.app`'s single pipeline worker rather than Starlette's threadpool.

    As a plain `def` it drove the shared DuckDB connection from a second thread
    while the page loaded `/api/overview` on the first, and the failure was not
    an exception — it returned the *other* query's rows, so a crew member with
    30 rows across 9 tables reported zero everywhere and the page drew a
    confident, entirely wrong picture of missing data.
    """
    from concurrent.futures import ThreadPoolExecutor

    expected = client.get("/api/graph-view/crew/IGA60406").json()
    assert expected["stats"]["total_rows"] > 0, "fixture crew member has no rows"

    def hit(path):
        return client.get(path).json()

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(hit, "/api/graph-view/crew/IGA60406" if i % 2 else "/api/overview")
            for i in range(12)
        ]
        results = [f.result() for f in futures]

    for got in results:
        if "keys" not in got:
            continue
        assert got["keys"] == expected["keys"]
        assert got["stats"]["total_rows"] == expected["stats"]["total_rows"]


def test_the_endpoint_refuses_a_hostile_identifier(client):
    assert client.get("/api/graph-view/crew/IGA1%20OR%201%3D1").status_code == 400
