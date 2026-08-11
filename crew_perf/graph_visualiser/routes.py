"""HTTP surface for the Graph Visualiser: one endpoint and one static mount.

Kept in this folder rather than in `api/app.py` so the whole feature is one
directory — the payload, the renderer and the route that joins them. `app.py`
calls `install(app)` and knows nothing else about it.

The endpoint is a plain `def`, which Starlette runs on its own threadpool.
That is safe *here* and nowhere else in this app: building the payload reads
JSON files and the schema exports, and never touches the DuckDB connection that
`api.app` is careful to drive from exactly one thread.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from crew_perf.graph_visualiser.payload import build_payload

STATIC = Path(__file__).parent / "static"
MOUNT = "/visualiser"

# Deliberately NOT `/api/graph`. That path belonged to the graph *explorer* that
# was removed along with the crew directory and the weights table — three more
# ways to reach a score, each rendering it differently — and `test_api` still
# asserts it 404s. This endpoint reaches no score at all: it returns the shape of
# the knowledge graph and nothing computed from the warehouse. Reusing the old
# path would have made that guarantee unassertable.
DATA_ROUTE = "/api/graph-view"

router = APIRouter(tags=["graph-visualiser"])

# The built graph changes only when `build-graph`, `build-bridge` or
# `refresh-values` runs, and the payload is a few hundred kilobytes of pure
# transformation over ~350KB of JSON. Caching it keyed on what the files
# actually are means switching tabs is instant while a rebuild still shows up
# without restarting `serve` — a stale picture of a graph is the one failure
# this tab must not have.
_cache: dict[str, tuple[object, dict]] = {}


def _signature() -> tuple:
    """(name, size, mtime) for every artifact the payload is built from."""
    from crew_perf import config

    files = sorted(config.GRAPHS_DIR.glob("*_concept_graph.json"))
    bridge = config.GRAPHS_DIR / "cross_source_joins.json"
    if bridge.exists():
        files.append(bridge)
    return tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files)


@router.get("/api/graph-view")
def graph(origin: str = Query("artifacts", pattern="^(artifacts|cosmos)$")):
    """The whole knowledge graph — every vertex, every edge, nothing sampled.

    `origin=cosmos` reads the live Gremlin graph instead of the files. It is not
    cached: the reason to ask for it is to find out what is in there *now*.
    """
    if origin == "cosmos":
        try:
            return build_payload("cosmos")
        except Exception as exc:  # the reason belongs on the page, not in a 500
            raise HTTPException(
                status_code=502,
                detail=f"Could not read the live graph: {exc}",
            ) from exc

    try:
        signature = _signature()
    except OSError:
        signature = None

    cached = _cache.get("artifacts")
    if cached and signature is not None and cached[0] == signature:
        return cached[1]

    try:
        payload = build_payload("artifacts")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _cache["artifacts"] = (signature, payload)
    return payload


@router.get("/api/graph-view/crew/{identifier}")
async def crew(identifier: str):
    """Where one crew member appears across every source.

    Not cached, and deliberately: it is a question about the live data, and the
    whole point of the answer is which tables hold rows *now*.

    **`async def`, and offloaded onto `api.app`'s single pipeline worker.** This
    is the one route in this module that touches the data layer, so it is the one
    route that may not run on Starlette's threadpool. A plain `def` here drives
    the shared DuckDB connection from a second thread while the page is loading
    `/api/overview` on the first, and DuckDB does not allow that: the observed
    failure was not an exception but this endpoint returning the *other* query's
    rows, so a crew member with 30 rows across 9 tables reported zero everywhere
    and the page drew a confident, entirely wrong picture of missing data.
    """
    from crew_perf.api.app import _offload
    from crew_perf.graph_visualiser.crew_ego import UnsafeIdentifier, build_ego

    try:
        return await _offload(build_ego, identifier)
    except UnsafeIdentifier as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # the reason belongs on the page, not in a 500
        raise HTTPException(
            status_code=503, detail=f"Could not read the data layer: {exc}",
        ) from exc


def install(app) -> None:
    """Attach the endpoint and serve the renderer's own assets."""
    app.include_router(router)
    app.mount(MOUNT, StaticFiles(directory=STATIC), name="visualiser")
