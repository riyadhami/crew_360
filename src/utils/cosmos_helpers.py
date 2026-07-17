"""
cosmos_helpers.py — Shared Cosmos DB Gremlin connection & query helpers.

Mirrors the API surface of neo4j_helpers.py for the Cosmos DB Gremlin backend.
All layers import from here for Cosmos DB access.

Required env vars (loaded from .env):
    COSMOS_DB_ENDPOINT   – e.g. indigo-kg-cosmos-dev-ule3rpcbgoalq.gremlin.cosmos.azure.com
    COSMOS_DB_KEY        – Primary key from Azure Portal / az cosmosdb keys list
    COSMOS_DB_DATABASE   – Gremlin database name (e.g. IndigoKG)
    COSMOS_DB_GRAPH      – Graph container name (e.g. Unified_Knowledge_graph)
"""

import json
import os
import time
import asyncio
import platform

# Fix for Python 3.13 + Windows + gremlinpython asyncio compatibility issue
# Must be set before any gremlin_client imports/usage
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
from gremlin_python.driver import client as gremlin_client, serializer
from gremlin_python.driver.protocol import GremlinServerError

load_dotenv()

# Cosmos DB connection settings (from .env)
COSMOS_ENDPOINT = os.getenv("COSMOS_DB_ENDPOINT", "")
COSMOS_KEY = os.getenv("COSMOS_DB_KEY", "")
COSMOS_DATABASE = os.getenv("COSMOS_DB_DATABASE", "")
COSMOS_GRAPH = os.getenv("COSMOS_DB_GRAPH", "")


def get_cosmos_client(graph_container: str | None = None) -> gremlin_client.Client:
    """Create an authenticated Gremlin client for Azure Cosmos DB.
    
    Args:
        graph_container: Optional graph container name. If not provided, uses COSMOS_DB_GRAPH from .env.
    """
    if not all([COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE]):
        raise RuntimeError(
            "Missing Cosmos DB env vars. "
            "Set COSMOS_DB_ENDPOINT, COSMOS_DB_KEY, COSMOS_DB_DATABASE in .env"
        )
    
    # Use provided graph_container or fall back to env var
    graph = graph_container or COSMOS_GRAPH
    if not graph:
        raise RuntimeError(
            "No graph container specified. "
            "Provide graph_container parameter or set COSMOS_DB_GRAPH in .env"
        )
    
    return gremlin_client.Client(
        url=f"wss://{COSMOS_ENDPOINT}:443/",
        traversal_source="g",
        username=f"/dbs/{COSMOS_DATABASE}/colls/{graph}",
        password=COSMOS_KEY,
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )


def run_gremlin(client: gremlin_client.Client, query: str, max_retries: int = 3,
                ignore_conflict: bool = False) -> list:
    """Submit a Gremlin query and return the result set.

    Includes basic retry with backoff for 429 (Request Rate Too Large) errors
    which are common when loading many vertices/edges at 400 RU/s.

    If *ignore_conflict* is True, 409 (Conflict / "already exists") errors are
    silently swallowed — useful for idempotent vertex creation.
    """
    for attempt in range(max_retries):
        try:
            callback = client.submitAsync(query)
            result = callback.result()
            return result.all().result()
        except GremlinServerError as exc:
            # Cosmos DB sets x-ms-status-code in the Gremlin response attributes
            attrs = getattr(exc, "status_attributes", {}) or {}
            ms_status = attrs.get("x-ms-status-code", 0)

            if ms_status == 409 and ignore_conflict:
                return []  # vertex/edge already exists — skip

            if ms_status == 429 and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    ⏳ Throttled (429). Retrying in {wait}s… (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            raise
        except Exception as exc:
            raise


def close_cosmos_client(client: gremlin_client.Client):
    """Close the Gremlin client connection."""
    client.close()


def escape_gremlin(value: str) -> str:
    """Escape a string for safe embedding in a Gremlin query.

    Handles single quotes (used in Gremlin string literals) and backslashes.
    """
    if not value:
        return ""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def make_vertex_id(database: str, node_type: str, name: str) -> str:
    """Build a deterministic, globally-unique vertex ID for the single-container model.

    Format:  {database}__{type}__{normalized_name}
    Example: CLMS__table__CrewMaster
    """
    return f"{database}__{node_type}__{name}"


def serialize_list(values: list) -> str:
    """Serialize a list to a JSON string for storage as a single Gremlin property.

    Cosmos DB Gremlin multi-value properties add complexity; storing as a JSON
    string is simpler and the consuming code can json.loads() it back.
    """
    return json.dumps(values, ensure_ascii=False)
