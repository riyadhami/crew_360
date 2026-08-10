"""Cosmos DB (Gremlin API) connection & query helpers.

Ported from Indigo_Knowledge_Layer-main/src/utils/cosmos_helpers.py. Changes:
  - config comes from crew_perf.config (single source of truth)
  - writes are guarded against the donor project's protected graphs
  - `upsert_vertex` / `upsert_edge` added: the donor open-coded these Gremlin
    strings in three separate files with slightly different escaping.
"""

from __future__ import annotations

import asyncio
import json
import platform
import time

# gremlinpython + asyncio on Windows needs the selector policy set before use.
if platform.system() == "Windows":  # pragma: no cover
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from gremlin_python.driver import client as gremlin_client
from gremlin_python.driver import serializer
from gremlin_python.driver.protocol import GremlinServerError

from crew_perf import config


def get_cosmos_client(graph_container: str | None = None) -> gremlin_client.Client:
    """Create an authenticated Gremlin client for Cosmos DB."""
    if not all([config.COSMOS_ENDPOINT, config.COSMOS_KEY, config.COSMOS_DATABASE]):
        raise RuntimeError(
            "Missing Cosmos DB env vars. Set COSMOS_DB_ENDPOINT, COSMOS_DB_KEY, "
            "COSMOS_DB_DATABASE in .env"
        )
    graph = graph_container or config.COSMOS_GRAPH
    if not graph:
        raise RuntimeError("No graph container specified. Set COSMOS_DB_GRAPH in .env")
    if graph in config.PROTECTED_GRAPHS:
        raise RuntimeError(
            f"Refusing to connect to protected graph {graph!r} — it belongs to the "
            f"existing Indigo knowledge layer. Use a project-owned graph."
        )

    return gremlin_client.Client(
        url=f"wss://{config.COSMOS_ENDPOINT}:443/",
        traversal_source="g",
        username=f"/dbs/{config.COSMOS_DATABASE}/colls/{graph}",
        password=config.COSMOS_KEY,
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )


def run_gremlin(
    client: gremlin_client.Client,
    query: str,
    max_retries: int = 5,
    ignore_conflict: bool = False,
    quiet: bool = False,
) -> list:
    """Submit a Gremlin query and return results, retrying on throttling.

    Cosmos returns 429 (Request Rate Too Large) freely at low RU/s, so backoff is
    the normal path rather than an error case. 409 (already exists) can be
    swallowed for idempotent creates.
    """
    for attempt in range(max_retries):
        try:
            return client.submitAsync(query).result().all().result()
        except GremlinServerError as exc:
            attrs = getattr(exc, "status_attributes", {}) or {}
            status = attrs.get("x-ms-status-code", 0)

            if status == 409 and ignore_conflict:
                return []
            if status == 429 and attempt < max_retries - 1:
                wait = attrs.get("x-ms-retry-after-ms")
                delay = (float(wait) / 1000.0) if wait else float(2**attempt)
                if not quiet:
                    print(f"    throttled (429), retrying in {delay:.1f}s "
                          f"({attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            raise
    return []  # pragma: no cover


def close_cosmos_client(client: gremlin_client.Client) -> None:
    client.close()


def escape_gremlin(value: str) -> str:
    """Escape a string for embedding in a Gremlin query literal."""
    if not value:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def make_vertex_id(source: str, node_type: str, name: str) -> str:
    """Deterministic globally-unique vertex ID: `{source}__{type}__{name}`.

    Namespacing by source is what keeps identically-named tables from two
    schemas (e.g. PEP.DESIGNATION vs CLMS.M_Designations) from colliding.
    """
    safe = str(name).strip().replace(" ", "_")
    return f"{source}__{node_type}__{safe}"


# ─── Idempotent upserts ─────────────────────────────────────────────────────


def _prop_clause(props: dict) -> str:
    """Render a property dict as chained `.property(...)` calls.

    Non-scalar values are JSON-encoded; None values are skipped entirely so an
    absent property never overwrites an existing one with a null.
    """
    parts = []
    for key, val in props.items():
        if val is None:
            continue
        if isinstance(val, bool):
            parts.append(f".property('{key}', {str(val).lower()})")
        elif isinstance(val, (int, float)):
            parts.append(f".property('{key}', {val})")
        elif isinstance(val, (list, dict)):
            parts.append(f".property('{key}', '{escape_gremlin(json.dumps(val, ensure_ascii=False))}')")
        else:
            parts.append(f".property('{key}', '{escape_gremlin(str(val))}')")
    return "".join(parts)


def upsert_vertex(
    client: gremlin_client.Client,
    vertex_id: str,
    label: str,
    props: dict,
    partition_key: str = "source",
) -> None:
    """Create the vertex if absent, else update its properties in place.

    Additive by design: never drops, never duplicates. `partition_key` must be
    present in `props` — Cosmos requires the partition key on every vertex.
    """
    if partition_key not in props:
        raise ValueError(f"props must include the partition key {partition_key!r}")

    vid = escape_gremlin(vertex_id)
    # Cosmos rejects any attempt to re-set the partition key on an existing
    # vertex ("Partition key property of a vertex is readonly"), so the update
    # branch must omit it while the create branch must include it.
    create_clause = _prop_clause(props)
    update_clause = _prop_clause({k: v for k, v in props.items() if k != partition_key})
    query = (
        f"g.V('{vid}').fold()"
        f".coalesce("
        f"  unfold(){update_clause},"
        f"  addV('{escape_gremlin(label)}').property('id', '{vid}'){create_clause}"
        f")"
    )
    run_gremlin(client, query)


def upsert_edge(
    client: gremlin_client.Client,
    src_id: str,
    dst_id: str,
    label: str,
    props: dict | None = None,
    identity_props: tuple[str, ...] = ("source_column", "target_column"),
) -> None:
    """Create an edge between two existing vertices if it doesn't already exist.

    `identity_props` is what makes two edges *the same* edge. Matching on
    (source, target, label) alone is wrong for joins: `PEP_CATEGORY ->
    PEP_TEMPLATE` legitimately exists on both `TEMPLATE_ID` and `TEMPLATE_CODE`,
    and collapsing them silently discards a usable join path.
    """
    props = props or {}
    src, dst = escape_gremlin(src_id), escape_gremlin(dst_id)
    clause = _prop_clause(props)

    match = "".join(
        f".has('{key}', '{escape_gremlin(str(props[key]))}')"
        for key in identity_props
        if props.get(key) is not None
    )
    query = (
        f"g.V('{src}')"
        f".coalesce("
        f"  outE('{escape_gremlin(label)}').where(inV().hasId('{dst}')){match},"
        f"  addE('{escape_gremlin(label)}').to(__.V('{dst}')){clause}"
        f")"
    )
    run_gremlin(client, query, ignore_conflict=True)


def graph_counts(client: gremlin_client.Client) -> dict:
    """Vertex/edge counts, and vertex counts by label — used by `crewperf doctor`."""
    vertices = run_gremlin(client, "g.V().count()")
    edges = run_gremlin(client, "g.E().count()")
    by_label = run_gremlin(client, "g.V().groupCount().by(label)")
    return {
        "vertices": vertices[0] if vertices else 0,
        "edges": edges[0] if edges else 0,
        "by_label": by_label[0] if by_label else {},
    }
