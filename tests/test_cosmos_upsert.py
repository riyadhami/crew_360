"""Gremlin query construction. No network — the queries are captured and asserted.

The upsert semantics matter more than they look: these run additively against a
live graph, so a wrong identity predicate either duplicates vertices on every
rebuild or silently discards edges.
"""

import pytest

from crew_perf.graph import cosmos


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(cosmos, "run_gremlin", lambda client, q, **kw: calls.append(q) or [])
    return calls


def test_edge_identity_includes_join_columns(captured):
    """`PEP_CATEGORY -> PEP_TEMPLATE` exists on both TEMPLATE_ID and TEMPLATE_CODE.
    Keying only on (source, target, label) collapses them and throws away a
    usable join path."""
    cosmos.upsert_edge(None, "A", "B", "JOINS_TO",
                       {"source_column": "TEMPLATE_ID", "target_column": "TEMPLATE_ID"})
    q = captured[0]
    assert "has('source_column', 'TEMPLATE_ID')" in q
    assert "has('target_column', 'TEMPLATE_ID')" in q


def test_edge_upsert_uses_anonymous_traversal(captured):
    """`addE(...).to(g.V(...))` is invalid mid-traversal on Cosmos; it must be __.V()."""
    cosmos.upsert_edge(None, "A", "B", "BELONGS_TO", {})
    assert "__.V('B')" in captured[0]
    assert ".to(g.V(" not in captured[0]


def test_edge_without_identity_props_still_dedups_on_endpoints(captured):
    cosmos.upsert_edge(None, "A", "B", "BELONGS_TO", {"confidence": 1.0})
    q = captured[0]
    assert "outE('BELONGS_TO')" in q and "hasId('B')" in q


def test_vertex_upsert_never_rewrites_the_partition_key(captured):
    """Cosmos rejects re-setting a partition key on an existing vertex, so the
    update branch must omit it while the create branch includes it."""
    cosmos.upsert_vertex(None, "V1", "Table", {"source": "PEP", "name": "X"})
    q = captured[0]
    update_branch, create_branch = q.split("addV")
    assert "property('source'" not in update_branch
    assert "property('source', 'PEP')" in create_branch


def test_vertex_upsert_requires_the_partition_key():
    with pytest.raises(ValueError, match="partition key"):
        cosmos.upsert_vertex(None, "V1", "Table", {"name": "X"})


def test_none_values_never_overwrite_existing_properties(captured):
    cosmos.upsert_vertex(None, "V1", "Table", {"source": "PEP", "coverage": None})
    assert "coverage" not in captured[0]


def test_types_are_preserved_not_stringified(captured):
    cosmos.upsert_vertex(None, "V1", "Table",
                         {"source": "PEP", "n": 3, "ok": True, "score": 0.5,
                          "cols": ["a", "b"]})
    q = captured[0]
    assert "property('n', 3)" in q
    assert "property('ok', true)" in q
    assert "property('score', 0.5)" in q
    assert 'property(\'cols\', \'["a", "b"]\')' in q  # lists serialise to JSON


def test_escaping_prevents_quote_injection():
    assert cosmos.escape_gremlin("it's") == "it\\'s"
    assert cosmos.escape_gremlin("a\nb") == "a\\nb"


def test_protected_graphs_are_refused(monkeypatch):
    """The donor project's live graph must never be writable from here."""
    monkeypatch.setattr(cosmos.config, "COSMOS_GRAPH", "Unified_Knowledge_graph")
    with pytest.raises(RuntimeError, match="protected graph"):
        cosmos.get_cosmos_client()
