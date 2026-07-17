# utils — Shared utilities for KG Ontology Semantic Layer

from __future__ import annotations

import os


def get_graph_db(backend: str | None = None):
    """Factory that returns the appropriate graph-DB traversal client.

    Args:
        backend: ``"neo4j"`` or ``"cosmos"``.  If *None*, falls back to the
                 ``GRAPH_BACKEND`` env var (default ``"cosmos"``).

    Returns:
        A ``GraphDB`` (Neo4j) or ``CosmosGraphDB`` (Cosmos DB) instance.
    """
    backend = (backend or os.getenv("GRAPH_BACKEND", "cosmos")).lower().strip()
    if backend == "cosmos":
        from src.utils.cosmos_graph_traversal import CosmosGraphDB
        return CosmosGraphDB()
    else:
        from utils.graph_traversal import GraphDB
        return GraphDB()
