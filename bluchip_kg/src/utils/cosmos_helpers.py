"""
Cosmos DB (Apache Gremlin API) connection and query helpers.

Patterns adapted from the Indigo Knowledge Layer reference implementation:
  - Exponential back-off on HTTP 429 (throttle)
  - Silent skip on HTTP 409 (vertex/edge already exists) for idempotent upserts
  - Deterministic vertex ID namespacing to avoid collisions
"""

import json
import logging
import os
import time

from gremlin_python.driver import client as gremlin_client, serializer

logger = logging.getLogger(__name__)


# ── Connection ────────────────────────────────────────────────────────────────

def get_cosmos_client(database: str | None = None, graph: str | None = None):
    """Return an authenticated Gremlin client connected to Cosmos DB."""
    endpoint = os.environ["COSMOS_DB_ENDPOINT"]          # e.g. myaccount.gremlin.cosmos.azure.com
    key      = os.environ["COSMOS_DB_KEY"]
    db       = database or os.getenv("COSMOS_DB_DATABASE", "BluChipKG")
    g        = graph    or os.getenv("COSMOS_DB_GRAPH",    "BluChip_360")

    return gremlin_client.Client(
        f"wss://{endpoint}:443/",
        "g",
        username=f"/dbs/{db}/colls/{g}",
        password=key,
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )


# ── Query execution ───────────────────────────────────────────────────────────

def run_gremlin(client, query: str, max_retries: int = 3, ignore_conflict: bool = False) -> list:
    """
    Execute a Gremlin query with retry logic.

    - 429 (throttled): exponential back-off (2^attempt seconds).
    - 409 (conflict):  silently return [] when ignore_conflict=True,
                       which enables idempotent vertex/edge creation.
    """
    for attempt in range(max_retries):
        try:
            future = client.submitAsync(query)
            return future.result().all().result()
        except Exception as exc:
            err = str(exc)
            if "409" in err or "GraphVertex already exists" in err or "GraphEdge already exists" in err:
                if ignore_conflict:
                    return []
                raise
            if "429" in err:
                wait = 2 ** attempt
                logger.warning("Cosmos DB throttled (429). Retry %d/%d in %ds.", attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            logger.error("Gremlin error: %s\nQuery: %.200s", err, query)
            raise
    raise RuntimeError(f"Gremlin query failed after {max_retries} retries.\nQuery: {query[:200]}")


# ── String / value helpers ────────────────────────────────────────────────────

def escape_gremlin(value) -> str:
    """Escape a value so it is safe to embed inside a Gremlin string literal."""
    if value is None:
        return ""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def serialize_list(values: list) -> str:
    """JSON-encode a list for storage as a single Gremlin string property."""
    return json.dumps(values if values else [])


def make_vertex_id(node_type: str, *parts: str) -> str:
    """
    Build a deterministic, collision-free vertex ID.

    Examples:
        make_vertex_id("tier",    "Silver")         → "tier__silver"
        make_vertex_id("member",  "IB12345678")     → "member__IB12345678"
        make_vertex_id("route",   "DEL", "BOM")     → "route__DEL__BOM"
        make_vertex_id("airport", "DEL")             → "airport__DEL"
    """
    safe_parts = [p.lower().replace(" ", "_").replace("'", "").replace("/", "_") for p in parts]
    return node_type + "__" + "__".join(safe_parts)


# ── Vertex / edge builders ────────────────────────────────────────────────────
# Each function returns a Gremlin query string ready for run_gremlin().
# All vertices carry a 'pk' property matching the container partition key path.

_PK = "bluchip"   # single-partition strategy; split by ffn_id in high-scale production


def _props(**kwargs) -> str:
    """Render keyword arguments as chained .property('k', v) clauses."""
    clauses = []
    for k, v in kwargs.items():
        if v is None:
            v = ""
        if isinstance(v, bool):
            clauses.append(f".property('{k}', {str(v).lower()})")
        elif isinstance(v, (int, float)):
            clauses.append(f".property('{k}', {v})")
        else:
            clauses.append(f".property('{k}', '{escape_gremlin(v)}')")
    return "".join(clauses)


def add_vertex(label: str, vertex_id: str, **properties) -> str:
    base = (
        f"g.addV('{label}')"
        f".property('id', '{vertex_id}')"
        f".property('pk', '{_PK}')"
    )
    return base + _props(**properties)


def add_edge(label: str, src_id: str, tgt_id: str, **properties) -> str:
    base = (
        f"g.V('{src_id}').addE('{label}')"
        f".to(g.V('{tgt_id}'))"
        f".property('pk', '{_PK}')"
    )
    return base + _props(**properties)
