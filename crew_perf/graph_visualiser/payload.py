"""The knowledge graph as nodes and edges a browser can draw.

Three vertex kinds and three edge kinds, and the choice of those six is the
whole design:

    schema  ──contains──>  table  ──belongs_to──>  concept
                             │
                             └────joins_to───────>  table

`schema` vertices are **synthesised here** — they are not in Cosmos and never
have been. A source is a fact about where a table lives, carried as a property
on the table vertex rather than as a vertex of its own, which is right for a
graph that gets traversed and wrong for one that gets looked at: without them
the picture is 48 tables in an undifferentiated cloud, and the first question
anyone asks of it — *which system is this from* — has to be answered by reading
labels one at a time. Making the source a node answers it with position.

The warehouse schema each table resolves to (`PEP`, `CLMS`, `CREWPORTAL`, or a
local extract) is read from `crew_perf.data.scope`, the same allow-list the SQL
qualifier uses, so the picture cannot claim a table lives somewhere the executor
would not look for it.

**The artifacts are the origin, not Cosmos.** `graphs/*_concept_graph.json` is
what `build-graph` writes and what `push_to_cosmos` uploads, so drawing from the
files shows the current graph whether or not anyone has pushed it — and a
`doctor` reporting 82 vertices against 100 on disk is exactly the drift a
visualiser should be able to show you rather than hide. `?origin=cosmos` reads
the live graph instead, for checking what actually landed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crew_perf import config
from crew_perf.graph.schema import EdgeLabel, VertexLabel

# Synthesised schema vertices get a prefix that cannot collide with a real one:
# every Cosmos id is `{source}__{type}__{name}` and no source is the empty string.
SCHEMA_PREFIX = "__schema__"

CROSS_SOURCE_FILE = "cross_source_joins.json"

# Node kinds as the renderer knows them. Lowercased and separated from the
# Gremlin vocabulary on purpose: `VertexLabel.TABLE` is a graph fact, `"table"`
# is a thing with a colour and a radius, and the two drift for good reasons.
KIND_SCHEMA, KIND_TABLE, KIND_CONCEPT = "schema", "table", "concept"
EDGE_CONTAINS, EDGE_BELONGS_TO, EDGE_JOINS_TO = "contains", "belongs_to", "joins_to"


def build_payload(origin: str = "artifacts") -> dict[str, Any]:
    """Every vertex and every edge, plus what the inspector needs to explain them.

    Nothing is sampled and nothing is capped. A visualiser that quietly drops the
    tail is worse than no visualiser: the whole reason to look at the graph is to
    find the node that is missing an edge, and that node is never the popular one.
    """
    if origin == "cosmos":
        from crew_perf.graph_visualiser.cosmos_source import read_cosmos

        vertices, raw_edges, warnings = read_cosmos()
    else:
        origin = "artifacts"
        vertices, raw_edges, warnings = _read_artifacts()

    layout, local_tables = _schema_layout()
    nodes: list[dict] = []
    by_source: dict[str, dict[str, int]] = {}
    seen_ids: set[str] = set()

    for v in vertices:
        node = _node(v, layout, local_tables)
        if node is None or node["id"] in seen_ids:
            continue
        seen_ids.add(node["id"])
        nodes.append(node)
        tally = by_source.setdefault(node["source"], {KIND_TABLE: 0, KIND_CONCEPT: 0})
        tally[node["kind"]] = tally.get(node["kind"], 0) + 1

    schema_nodes = _schema_nodes(by_source, layout, local_tables, nodes)
    edges = _edges(raw_edges, seen_ids)
    edges.extend(_contains_edges(nodes, {s["id"] for s in schema_nodes}))
    nodes = schema_nodes + nodes

    degree: dict[str, int] = {}
    for e in edges:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)

    return {
        "origin": origin,
        "nodes": nodes,
        "edges": edges,
        "warnings": warnings,
        "stats": _stats(nodes, edges),
    }


# ─── reading the artifacts ──────────────────────────────────────────────────


def _read_artifacts() -> tuple[list[dict], list[dict], list[str]]:
    """Every built source graph, plus the bridge that spans them.

    Read raw rather than through `GraphStore`, which merges a concept named by
    several sources into one node. That merge is right for an agent asking "what
    does crew_identity cover" and wrong here: Cosmos holds `PEP__concept__…` and
    `CLMS__concept__…` as separate vertices, and a picture that shows one is not
    a picture of the graph.
    """
    paths = sorted(config.GRAPHS_DIR.glob("*_concept_graph.json"))
    if not paths:
        raise FileNotFoundError(
            f"No concept graph in {config.GRAPHS_DIR}. "
            f"Run `crewperf build-graph PEP` (and the other sources) first."
        )

    vertices: list[dict] = []
    edges: list[dict] = []
    warnings: list[str] = []

    for path in paths:
        raw = json.loads(path.read_text())
        source = raw.get("source", path.stem.split("_")[0])
        for n in raw.get("nodes", []):
            vertices.append({**n, "source": n.get("source", source)})
        for c in raw.get("concepts", []):
            vertices.append({**c, "source": c.get("source", source)})
        edges.extend(raw.get("edges", []))
        warnings.extend(f"{source}: {w}" for w in raw.get("warnings", []))

    bridge = config.GRAPHS_DIR / CROSS_SOURCE_FILE
    if bridge.exists():
        raw = json.loads(bridge.read_text())
        for e in raw.get("edges", []):
            edges.append({**e, "cross_source": True})
        warnings.extend(f"bridge: {w}" for w in raw.get("warnings", []))
    elif len({v["source"] for v in vertices}) > 1:
        warnings.append(
            f"{CROSS_SOURCE_FILE} is missing — the sources will draw as separate "
            f"islands because nothing joins them. Run `crewperf build-bridge`."
        )

    return vertices, edges, warnings


def _schema_layout() -> tuple[dict[str, str], set[str]]:
    """TABLE -> warehouse schema, and the set served from the local extract.

    Taken from the scope rather than from the graph so the picture and the SQL
    qualifier cannot disagree about where a table lives. A failure here is not
    fatal — the graph draws fine without schema names on it.
    """
    try:
        from crew_perf.data.scope import get_scope

        scope = get_scope()
        return dict(scope.layout), set(scope.local)
    except Exception:  # noqa: BLE001 - an unreadable registry must not blank the page
        return {}, set()


# ─── vertices ───────────────────────────────────────────────────────────────


def _node(v: dict, layout: dict[str, str], local: set[str]) -> dict | None:
    """One Cosmos vertex as a drawable node, with its full detail attached.

    Unknown labels are dropped rather than drawn as a generic dot: `WeightSet`,
    `CrewIdentity` and `Feedback` exist in the vocabulary, none of them is built
    by `build-graph` today, and inventing a shape for one would put a node on the
    page that no legend explains.
    """
    label = v.get("label")
    name = v.get("name") or ""
    if not name or not v.get("id"):
        return None

    if label == VertexLabel.TABLE:
        key = name.upper()
        return {
            "id": v["id"],
            "kind": KIND_TABLE,
            "name": name,
            "label": v.get("display_label") or name,
            "source": v.get("source", "?"),
            "engine": v.get("engine", "snowflake"),
            "schema": layout.get(key) or ("local extract" if key in local else ""),
            "in_subset": bool(v.get("in_subset", True)),
            "detail": {
                "description": v.get("description", ""),
                "grain": v.get("grain", ""),
                "scd2": bool(v.get("scd2")),
                "columns": _columns(v),
                "measures": list(v.get("measures") or []),
                "identity_columns": list(v.get("identity_columns") or []),
                "enum_values": v.get("enum_values") or {},
                "decodes": v.get("decodes") or {},
            },
        }

    if label == VertexLabel.CONCEPT:
        return {
            "id": v["id"],
            "kind": KIND_CONCEPT,
            "name": name,
            "label": v.get("display_label") or name,
            "source": v.get("source", "?"),
            "engine": "",
            "schema": "",
            "in_subset": True,
            "detail": {
                "description": v.get("description", ""),
                "source_tables": list(v.get("source_tables") or []),
                "key_columns": list(v.get("key_columns") or []),
            },
        }

    return None


def _columns(v: dict) -> list[dict]:
    """Columns paired with their role, in declared order.

    The role is the reason the inspector is worth opening: `MEASURE` is the only
    role a scoring signal can be built from and `TEMPORAL_CONTROL` is the one
    that forces a currency filter, so a column list without roles shows you the
    schema and tells you nothing about what the pipeline can do with it.
    """
    roles = v.get("column_roles") or {}
    return [
        {"name": c, "role": roles.get(c) or roles.get(str(c).upper()) or ""}
        for c in (v.get("columns") or [])
    ]


def _schema_nodes(
    by_source: dict[str, dict[str, int]],
    layout: dict[str, str],
    local: set[str],
    nodes: list[dict],
) -> list[dict]:
    """One vertex per source, carrying what the registry declares about it."""
    try:
        from crew_perf.sources import load_registry

        registry = {s.name: s for s in load_registry().available()}
    except Exception:  # noqa: BLE001
        registry = {}

    schema_of: dict[str, set[str]] = {}
    for n in nodes:
        if n["kind"] == KIND_TABLE and n.get("schema"):
            schema_of.setdefault(n["source"], set()).add(n["schema"])

    out = []
    for source, tally in sorted(by_source.items()):
        spec = registry.get(source)
        schemas = sorted(schema_of.get(source, set()))
        out.append({
            "id": f"{SCHEMA_PREFIX}{source}",
            "kind": KIND_SCHEMA,
            "name": source,
            "label": source,
            "source": source,
            "engine": getattr(spec, "engine", ""),
            "schema": ", ".join(schemas),
            "in_subset": True,
            "detail": {
                "description": getattr(spec, "context", ""),
                "join_key": getattr(spec, "join_key", ""),
                "crew_master": getattr(spec, "crew_master", ""),
                "tables": tally.get(KIND_TABLE, 0),
                "concepts": tally.get(KIND_CONCEPT, 0),
                # A source is either a warehouse schema or a file extract, and
                # which one it is decides whether a query can join it to another.
                "warehouse": getattr(spec, "engine", "").lower() == "snowflake",
            },
        })
    return out


# ─── edges ──────────────────────────────────────────────────────────────────


def _edges(raw: list[dict], known: set[str]) -> list[dict]:
    """Graph edges, deduplicated on what actually makes two edges different.

    A join is identified by its column pair, not by its endpoints:
    `PEP_CATEGORY -> PEP_TEMPLATE` exists on both `TEMPLATE_ID` and
    `TEMPLATE_CODE`, and collapsing the two would hide a usable join path — the
    same rule `upsert_edge` applies when writing to Cosmos.
    """
    out: list[dict] = []
    seen: set[str] = set()

    for e in raw:
        src, dst = e.get("from"), e.get("to")
        if src not in known or dst not in known:
            # An edge into a source that was never built. Not an error: the
            # bridge names every pair it knows, whoever has run `build-graph`.
            continue

        if e.get("label") == EdgeLabel.JOINS_TO:
            src_col, tgt_col = e.get("source_column", ""), e.get("target_column", "")
            eid = f"{EDGE_JOINS_TO}:{src}:{dst}:{src_col}>{tgt_col}"
            if eid in seen:
                continue
            seen.add(eid)
            out.append({
                "id": eid,
                "from": src,
                "to": dst,
                "kind": EDGE_JOINS_TO,
                "label": f"{src_col} = {tgt_col}" if src_col else "joins",
                "confidence": float(e.get("confidence") or 0.0),
                "verified": bool(e.get("verified")),
                "cross_source": bool(e.get("cross_source")),
                "detail": {
                    "source_column": src_col,
                    "target_column": tgt_col,
                    "cardinality": e.get("cardinality") or "",
                    "coverage": e.get("coverage"),
                    "join_source": e.get("join_source") or "",
                    "evidence": e.get("evidence") or "",
                    "note": e.get("note") or "",
                },
            })
        elif e.get("label") == EdgeLabel.BELONGS_TO:
            eid = f"{EDGE_BELONGS_TO}:{src}:{dst}"
            if eid in seen:
                continue
            seen.add(eid)
            out.append({
                "id": eid,
                "from": src,
                "to": dst,
                "kind": EDGE_BELONGS_TO,
                "label": "belongs to",
                "confidence": float(e.get("confidence") or 1.0),
                "verified": True,
                "cross_source": False,
                "detail": {},
            })

    return out


def _contains_edges(nodes: list[dict], schema_ids: set[str]) -> list[dict]:
    """Schema -> everything it declares. Synthesised alongside the schema nodes."""
    out = []
    for n in nodes:
        sid = f"{SCHEMA_PREFIX}{n['source']}"
        if sid not in schema_ids:
            continue
        out.append({
            "id": f"{EDGE_CONTAINS}:{sid}:{n['id']}",
            "from": sid,
            "to": n["id"],
            "kind": EDGE_CONTAINS,
            "label": "contains",
            "confidence": 1.0,
            "verified": True,
            "cross_source": False,
            "detail": {},
        })
    return out


def _stats(nodes: list[dict], edges: list[dict]) -> dict:
    joins = [e for e in edges if e["kind"] == EDGE_JOINS_TO]
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "schemas": sum(1 for n in nodes if n["kind"] == KIND_SCHEMA),
        "tables": sum(1 for n in nodes if n["kind"] == KIND_TABLE),
        "concepts": sum(1 for n in nodes if n["kind"] == KIND_CONCEPT),
        "joins": len(joins),
        "verified_joins": sum(1 for e in joins if e["verified"]),
        "cross_source_joins": sum(1 for e in joins if e["cross_source"]),
        "belongs_to": sum(1 for e in edges if e["kind"] == EDGE_BELONGS_TO),
        "sources": sorted({n["source"] for n in nodes if n["kind"] == KIND_SCHEMA}),
    }


def graphs_dir() -> Path:
    return config.GRAPHS_DIR
