"""HTTP surface over the agent pipeline.

Deliberately thin. Every endpoint delegates to the same code the CLI calls, so
there is one implementation of scoring and one of retrieval — a second path
through the agents would be a second place for them to disagree.

Asking a question streams: the pipeline takes tens of seconds when it derives a
mechanism and scores a population, and a spinner for that long tells the user
nothing. The stage events the orchestrator already yields are exactly what a
reader needs to see — which agent is running, and what it decided.

**The payload is split in two, and the split is the point.** `answer`, `crew` and
`table` are what a reader sees: plain English, real names, no identifiers. The
`detail` block carries the SQL, the signal identifiers (`sn_service_response_rate`
and friends), the weights and the audit — everything needed to check the answer,
none of it shown until asked for. A score nobody can read is not an answer, and a
score nobody can check is not trustworthy; separating them is how both hold.

Asking is also a *conversation*. `/api/ask` carries a session id, and the
orchestrator is handed the thread it belongs to, so a follow-up reaches Agent 8
with the previous answers and their computed numbers in front of it. The id is
the client's to mint and to discard; the server only keeps what it is given.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from crew_perf import config, policy, rules
from crew_perf.api.sessions import SessionStore
from crew_perf.graph_visualiser import routes as graph_visualiser

app = FastAPI(title="Crew Performance", docs_url="/api/docs")
STATIC = Path(__file__).parent / "static"

# The page is React, served from files rather than inlined: `app.js` and the
# vendored React build are real assets and a browser should be able to cache
# them. Vendored rather than fetched from a CDN so the page works on an aircrew
# network with no route to the public internet, and so a version can never
# change under a deployment that was signed off against a different one.
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# The Graph Visualiser tab, whole, from its own folder: the payload builder, the
# renderer and the route that joins them. It reads the built graph artifacts and
# never the warehouse, so it carries none of this module's threading care.
graph_visualiser.install(app)

_sessions = SessionStore()

# One long-lived worker, not the event loop's default executor.
#
# Two reasons, and the first is not optional: the default executor is torn down
# with its loop, so every request builds a fresh thread, and pyarrow's allocator
# segfaults on that re-initialisation — the second question a user asked took the
# whole process down. Reusing one thread also serialises pipeline runs, which is
# what we want anyway: the DuckDB connection is a shared singleton and is not
# safe to drive from two threads at once.
_pipeline = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")


async def _offload(fn, *args, **kwargs):
    """Run blocking database work on the single worker.

    Everything that touches the warehouse goes through here. Starlette would
    otherwise run a plain `def` endpoint in its own threadpool, so the page
    loading its status bar while a question streams would drive one DuckDB
    connection from two threads at once — which DuckDB does not allow.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pipeline, lambda: fn(*args, **kwargs))


def _plain(obj: Any) -> Any:
    """Make dataclasses and numpy scalars JSON-safe."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _plain(asdict(obj))
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):   # numpy scalar
        try:
            return obj.item()
        except Exception:  # noqa: BLE001
            return str(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


# ─── page ───────────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ─── ask (streaming) ────────────────────────────────────────────────────────


@app.get("/api/ask")
async def ask(q: str = Query(..., min_length=2), top: int = 5, session: str = ""):
    """Run the pipeline, streaming stage events then the result.

    `session` threads the conversation. A blank one starts a thread and the id
    comes back on the result, so the client never has to mint one itself.
    """
    from crew_perf.agents.orchestrator import stream

    conversation = _sessions.get(session or None)

    async def events():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def produce():
            try:
                for ev in stream(q, top_n=top, conversation=conversation):
                    if ev.get("stage") == "done":
                        loop.call_soon_threadsafe(queue.put_nowait, ("result", ev["result"]))
                    else:
                        loop.call_soon_threadsafe(queue.put_nowait, ("stage", ev))
            except Exception as exc:  # noqa: BLE001 - surfaced to the client
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("end", None))

        loop.run_in_executor(_pipeline, produce)

        while True:
            kind, payload = await queue.get()
            if kind == "end":
                break
            if kind == "stage":
                yield f"event: stage\ndata: {json.dumps(_plain(payload))}\n\n"
            elif kind == "error":
                yield f"event: error\ndata: {json.dumps({'message': payload})}\n\n"
            else:
                out = _result_payload(payload)
                out["session"] = conversation.id
                out["turn"] = len(conversation.turns)
                yield f"event: result\ndata: {json.dumps(out)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/api/session/{session_id}")
def end_session(session_id: str):
    """Forget a conversation. The client calls this on "new conversation"."""
    return {"session": session_id, "existed": _sessions.drop(session_id)}


def _result_payload(result) -> dict:
    """Split the pipeline's result into what a reader sees and what backs it up.

    Everything above `detail` is plain English: crew names, a score out of ten,
    the mentor's own words. Everything inside `detail` is the apparatus — the
    SQL, the signal identifiers, the weights, the audit. The frontend renders the
    first and hides the second behind a disclosure.

    The split is enforced here rather than left to the client because it is a
    property of the answer, not of one page's styling: `sn_service_response_rate`
    is a column name, and a reader handed it has been told nothing. The label and
    the description exist on every component for exactly this, and the identifier
    still travels — one click away — so a number can always be traced back.
    """
    out: dict[str, Any] = {
        "question": result.question,
        "kind": result.route.kind,
        # Agent 8's reasoning, in prose. Present on scoring and follow-up
        # answers; empty when the model was unreachable, which degrades the
        # answer rather than failing it — the numbers are the part that matters.
        "answer": result.narrative,
        "crew": [],
        "table": None,
        "detail": {
            "routed": f"{result.route.kind} — {result.route.reason}",
            "reasoning": list(result.notes),
            "ranked_on": result.rank_basis,
            "sql": None,
            "columns": [],
            "rows": [],
            "signals": [],
            "unavailable": [],
            "rejections": [],
            "audit": [],
        },
    }
    detail = out["detail"]

    if result.route.kind in ("followup", "retrieval"):
        r = result.followup if result.route.kind == "followup" else result.retrieval
        detail["sql"] = getattr(r, "sql", None) if r else None
        detail["rejections"] = getattr(r, "validation_failures", []) if r else []
        columns = list(getattr(r, "columns", []) or []) if r else []
        rows = _plain(list(getattr(r, "rows", []) or [])[:200]) if r else []
        detail["columns"], detail["rows"] = columns, rows
        # The rows ARE the answer to a data question, so they stay in the visible
        # half. Only the query that produced them is apparatus.
        if rows:
            out["table"] = {"columns": columns, "rows": rows}
        if result.route.kind == "retrieval" and r is not None:
            out["answer"] = r.answer or out["answer"]
            if r.unavailable:
                missing = r.unavailable.get("missing", "this")
                why = r.unavailable.get("reason", "not available")
                detail["unavailable"] = [f"{missing} — {why}"]
                out["answer"] = out["answer"] or (
                    "That is not answerable from the data currently onboarded: "
                    f"{r.unavailable.get('missing', 'the information needed')} is not "
                    f"held. {r.unavailable.get('reason', '')}".strip()
                )
        return out

    for card, audit in zip(result.scorecards, result.audits):
        out["crew"].append(_crew_view(card))
        detail["signals"].extend(_signal_detail(card))
        detail["unavailable"].extend(card.unavailable)
        detail["reasoning"].extend(card.findings)
        detail["audit"].extend(
            f"{f.severity}: {f.message}" for f in audit.findings if f.severity != "info"
        )
    return out


def _crew_view(card) -> dict:
    """One crew member as a reader should meet them: name, score, plain reasons.

    No identifiers, no z-scores, no weights. `strengths` and `watch` are the
    mentor's own recorded words where they exist, and a described signal label
    where they do not — never a column name.
    """
    return {
        "iga": card.iga,
        "name": card.iga,
        "base": card.base,
        "designation": card.designation,
        "score": card.composite_score,
        "out_of": 10,
        "assessment_mark": (round(float(card.native["mean_mark"]), 1)
                            if card.native.get("mean_mark") is not None else None),
        "grade": card.native.get("modal_grade") or "",
        "assessments": int(card.native.get("assessments") or 0),
        "confidence": card.confidence,
        # Deliberately a share of what could be measured, phrased as such. The
        # word "coverage" on its own reads as a score and is not one.
        "based_on": f"{card.coverage:.0%} of what we measure",
        "strengths": list(card.positives[:4]),
        "watch": list(card.negatives[:4]),
    }


def _signal_detail(card) -> list[dict]:
    """The apparatus behind one crew member's score — identifiers included.

    This is the only place a column name is allowed to travel, and it carries its
    own label beside it so the disclosure is readable too.
    """
    return [
        {
            "iga": card.iga,
            "signal": c.label or c.attribute,
            "identifier": c.attribute,
            "measures": c.measures,
            "value": c.raw,
            "percentile": c.percentile,
            "weight": c.weight,
            "contribution": c.contribution,
            "direction": c.direction,
        }
        for c in card.components
    ]


# ─── data for the page ──────────────────────────────────────────────────────


@app.get("/api/overview")
async def overview():
    return await _offload(_overview)


def _overview():
    from crew_perf.data.executor import get_executor
    from crew_perf.graph.store import get_store
    from crew_perf.sources import load_registry

    store, ex = get_store(), get_executor()
    registry = load_registry()
    resolved = rules.get(ex)

    counts = {}
    for name in ("EMPLOYEE_INFO", "MENTOR_FEEDBACK", "PEP_QUESTION_FEEDBACK"):
        try:
            counts[name] = int(ex.execute(f'SELECT COUNT(*) FROM "{name}"', limit=1).rows[0][0])
        except Exception:  # noqa: BLE001
            counts[name] = 0

    return {
        "engine": config.SNOWFLAKE_MODE,
        # The reasoning graph is built per source and the agents load one of
        # them; reporting its counts next to the whole warehouse's table count
        # without saying which is which reads as a contradiction.
        "graph": store.summary(),
        "graph_source": store.source,
        "tables_loaded": len(ex.list_tables()),
        "counts": counts,
        "sources": [
            {"name": s.name, "tables": len(s), "join_key": s.join_key,
             "crew_master": s.crew_master}
            for s in registry.available()
        ],
        "rules": resolved.provenance(),
        "rule_warnings": resolved.warnings,
        "out_of_scope": policy.OUT_OF_SCOPE,
    }


# No benchmark endpoint: the recovery harness scores estimators against hidden
# latent traits that only exist because the data is synthetic. Exposing it here
# would put a permanently-broken tab in the production UI. It stays in the CLI,
# where it belongs — `crewperf benchmark`.


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)
