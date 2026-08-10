"""The live Gremlin graph, read back into the same shape as the artifacts.

`payload.build_payload` draws from `graphs/*.json` by default, because those are
what `build-graph` writes and what `push_to_cosmos` uploads — the files are the
origin and Cosmos is a load target. This module exists for the one question the
files cannot answer: *what is actually in the graph right now.* A push that was
never run, a source built after the last push, or a vertex edited in the portal
all show up as a difference between the two origins, and being able to look at
both is the point.

The mapping back is not symmetric with the write. `_prop_clause` JSON-encodes
lists and dicts into string properties on the way in, so `columns` returns as
the *text* `"['IGA', 'BASE']"` rather than as a list; every structured property
is decoded here or the inspector renders a table's column list as one long
string.
"""

from __future__ import annotations

import json
from typing import Any

# Properties written as JSON strings by `cosmos._prop_clause`. Anything not
# named here is left exactly as Cosmos returned it — guessing at which strings
# are secretly JSON would turn a description that opens with a bracket into a
# parse error.
_STRUCTURED = {
    "columns", "column_roles", "measures", "identity_columns", "concepts",
    "enum_values", "decodes", "source_tables", "key_columns",
}


def read_cosmos() -> tuple[list[dict], list[dict], list[str]]:
    """`(vertices, edges, warnings)` from the live graph.

    Two round trips regardless of graph size. The alternative — a traversal per
    vertex to collect its edges — is the shape that makes a Cosmos bill
    interesting, and at ~100 vertices there is nothing to gain from it.
    """
    from crew_perf.graph.cosmos import close_cosmos_client, get_cosmos_client, run_gremlin

    client = get_cosmos_client()
    try:
        raw_vertices = run_gremlin(client, "g.V()", quiet=True)
        raw_edges = run_gremlin(client, "g.E()", quiet=True)
    finally:
        close_cosmos_client(client)

    vertices = [_vertex(v) for v in raw_vertices]
    vertices = [v for v in vertices if v]
    edges = [_edge(e) for e in raw_edges]
    edges = [e for e in edges if e]

    warnings = [
        ("Drawn from the live Cosmos graph. It holds what was last pushed with "
         "`crewperf build-graph --push`, which is not necessarily what is in "
         "graphs/ — switch origin to compare.")
    ]
    return vertices, edges, warnings


def _vertex(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or raw.get("type") != "vertex":
        return None
    out: dict[str, Any] = {"id": raw.get("id"), "label": raw.get("label")}
    for key, values in (raw.get("properties") or {}).items():
        out[key] = _decode(key, _single(values))
    return out if out["id"] and out["label"] else None


def _edge(raw: dict) -> dict | None:
    """One Gremlin edge, oriented the way it was written.

    `upsert_edge` builds every edge as `g.V(src).addE(label).to(V(dst))`, so the
    out-vertex is the source and the in-vertex is the target. Reversing them
    would draw `MENTOR_FEEDBACK -> PEP_FLIGHT_DETAILS` for a join whose columns
    read the other way, and the arrowhead is the only thing on the page saying
    which side is the many.
    """
    if not isinstance(raw, dict) or raw.get("type") != "edge":
        return None
    props = raw.get("properties") or {}
    out: dict[str, Any] = {
        "label": raw.get("label"),
        "from": raw.get("outV"),
        "to": raw.get("inV"),
    }
    # Edge properties come back flat in GraphSON v2 — no {id, value} wrapper.
    for key, value in props.items():
        out[key] = _decode(key, value)
    return out if out["from"] and out["to"] and out["label"] else None


def _single(values):
    """Cosmos returns vertex properties as a list of {id, value}; take the value."""
    if isinstance(values, list):
        if not values:
            return None
        first = values[0]
        return first.get("value") if isinstance(first, dict) else first
    if isinstance(values, dict):
        return values.get("value")
    return values


def _decode(key: str, value):
    """Undo the JSON encoding `_prop_clause` applied on the way in."""
    if key not in _STRUCTURED or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        # Written by an older builder, or hand-edited. Better to show the raw
        # string in the inspector than to drop the property entirely.
        return value
