"""The graph and SQL tool surface, shared by every agent that talks to a user.

Agent 3 (retrieval) and Agent 8 (conversation) need the same ability to explore
the graph and run one validated query, and they need it to behave *identically*.
When the surface lived inside `RetrievalAgent` the follow-up agent could only get
at it by subclassing a retrieval loop it did not want, or by re-implementing the
tools — and a re-implementation is where the two would silently diverge: a
follow-up that skipped the SCD-2 validator would answer "why is their leave
low?" from history rows while the first answer used current ones, and the two
numbers would disagree with nothing to explain it.

The toolset owns the query state (`result`, `failures`, `unavailable`) because
that state IS the outcome of running a tool. An agent reads it after the loop
rather than tracking it in parallel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from crew_perf.data.executor import QueryResult, SqlExecutor, get_executor
from crew_perf.data.validator import validate
from crew_perf.graph.store import GraphStore, get_store
from crew_perf.llm import call_llm_messages

# Schema fragments, so the tools an agent chooses to expose stay a per-agent
# decision while their contract stays one definition.
GRAPH_TOOLS: list[dict] = [
    {"type": "function", "function": {
        "name": "summary",
        "description": "High-level shape of the graph: tables per source, concepts, "
                       "and how many joins are verified or cross-source.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "search",
        "description": "Substring search across every source at once — table and column "
                       "names, concepts, AND the stored values of small dimension "
                       "columns. Use the question's own words here first: a business "
                       "term is often a VALUE ('Crew Feedback' is a category, "
                       "'DEVIATION' is a check code) rather than a column name.",
        "parameters": {"type": "object", "properties": {
            "term": {"type": "string", "description": "Word or fragment to look for."},
        }, "required": ["term"]},
    }},
    {"type": "function", "function": {
        "name": "list_tables",
        "description": "All tables with their business label and grain.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "list_concepts",
        "description": "Business concepts and the tables that make them up.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "table_info",
        "description": "Columns, roles, grain and SCD-2 flag for one table. "
                       "Call this before naming any column.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"},
        }, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "find_join_path",
        "description": "Verified join path between two tables. Use before joining.",
        "parameters": {"type": "object", "properties": {
            "from_table": {"type": "string"},
            "to_table": {"type": "string"},
        }, "required": ["from_table", "to_table"]},
    }},
    {"type": "function", "function": {
        "name": "resolve_crew",
        "description": (
            "Translate one crew identifier into every source's key. Call this FIRST "
            "whenever a question names a crew member and the answer needs a source "
            "keyed differently (e.g. an IGA and CLMS data). Never derive one "
            "identifier from another by editing the string."
        ),
        "parameters": {"type": "object", "properties": {
            "identifier": {"type": "string", "description": "e.g. IGA60406 or a CREW_ID"},
        }, "required": ["identifier"]},
    }},
    {"type": "function", "function": {
        "name": "sample_rows",
        "description": "A few rows from one table, to see real values.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"},
            "limit": {"type": "integer", "description": "default 5, max 20"},
        }, "required": ["table"]},
    }},
]

RUN_SQL_TOOL: dict = {
    "type": "function", "function": {
        "name": "run_sql",
        "description": "Validate and execute a Snowflake SQL query.",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string"},
            "purpose": {"type": "string", "description": "One line: what this returns."},
        }, "required": ["sql"]},
    },
}

UNAVAILABLE_TOOL: dict = {
    "type": "function", "function": {
        "name": "report_unavailable",
        "description": "Declare that the question cannot be answered from the onboarded "
                       "schema. Use this INSTEAD of writing a query against a related-but-"
                       "different table. This is a correct, expected outcome — not a failure.",
        "parameters": {"type": "object", "properties": {
            "missing": {"type": "string",
                        "description": "What data would be needed, e.g. 'duty hours per crew'."},
            "reason": {"type": "string",
                       "description": "Why the onboarded schema cannot supply it."},
        }, "required": ["missing", "reason"]},
    },
}

RETRIEVAL_TOOLS: list[dict] = [*GRAPH_TOOLS, RUN_SQL_TOOL, UNAVAILABLE_TOOL]


class GraphToolset:
    """Graph exploration plus one gated SQL escape hatch.

    Every query goes through `validate` first. The model cannot reach the
    warehouse any other way, so a tool it was never handed is a capability it
    does not have — which is the only durable way to bound what it can run.
    """

    def __init__(self, store: GraphStore | None = None, executor: SqlExecutor | None = None,
                 max_sql: int = 4):
        self.store = store or get_store()
        self.executor = executor or get_executor()
        self.max_sql = max_sql
        self.reset()

    def reset(self) -> None:
        """Clear query state. Called between questions, never mid-question."""
        self.failures: list[str] = []
        self.sql: str | None = None
        self.result: QueryResult | None = None
        self.unavailable: dict | None = None
        self._attempts = 0

    # ── graph ──────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return self.store.summary()

    def search(self, term: str) -> dict:
        return {"term": term, "hits": self.store.search(term)}

    def list_tables(self) -> dict:
        return {"tables": [
            {"name": t.name, "label": t.display_label, "grain": t.grain,
             "columns": len(t.columns), "scd2": t.scd2}
            for t in sorted(self.store.tables.values(), key=lambda t: t.name)
        ]}

    def list_concepts(self) -> dict:
        return {"concepts": [
            {"name": c.name, "label": c.display_label, "tables": c.source_tables,
             "description": c.description[:200]}
            for c in self.store.concepts.values()
        ]}

    def table_info(self, table: str) -> dict:
        t = self.store.table(table)
        if t is None:
            close = [n for n in self.store.tables if table.upper() in n.upper()]
            return {"error": f"no table {table!r} in the graph",
                    "did_you_mean": close[:5] or sorted(self.store.tables)[:10]}
        return {
            "table": t.name, "label": t.display_label, "grain": t.grain,
            "description": t.description, "scd2": t.scd2,
            "requires_filter": "P_IS_CURRENT = TRUE" if t.scd2 else None,
            "columns": [{"name": c, "role": t.role_of(c)} for c in t.columns],
            "measures": t.measures, "identity_columns": t.identity_columns,
            "concepts": self.store.concepts_of(t.name),
            # `note` carries the curated export's reason for the relationship
            # ("Converts compliance codes into readable values"), which is what
            # tells an agent that a lookup is a decode rather than a filter — the
            # column names alone do not, and joining a decode table as if it
            # narrowed the population is how a count silently comes back short.
            "joins": [
                {"to": (j.target_table if j.source_table == t.name else j.source_table),
                 "on": f"{j.source_column} = {j.target_column}",
                 "confidence": j.confidence, "verified": j.verified,
                 **({"note": j.note} if j.note else {})}
                for j in self.store.joins_for(t.name, min_confidence=0.8)
            ],
        }

    def find_join_path(self, from_table: str, to_table: str) -> dict:
        a, b = self.store.table(from_table), self.store.table(to_table)
        if a is None or b is None:
            return {"error": f"unknown table(s): "
                             f"{from_table if a is None else ''} "
                             f"{to_table if b is None else ''}".strip()}
        path = self.store.join_path(a.name, b.name)
        if path is None:
            return {"path": None,
                    "note": f"no verified join path between {a.name} and {b.name}. "
                            f"Do not invent one — these tables cannot be joined."}
        return {"path": [
            {"from": j.source_table, "to": j.target_table,
             "on": f"{j.source_table}.{j.source_column} = {j.target_table}.{j.target_column}",
             "confidence": j.confidence, "cardinality": j.cardinality}
            for j in path
        ], "hops": len(path)}

    def sample_rows(self, table: str, limit: int = 5) -> dict:
        t = self.store.table(table)
        if t is None:
            return {"error": f"no table {table!r}"}
        limit = max(1, min(int(limit or 5), 20))
        where = "WHERE P_IS_CURRENT = TRUE" if t.scd2 else ""
        res = self.executor.execute(
            f'SELECT * FROM "{t.name}" {where} LIMIT {limit}', limit=limit)
        keep = [c for c in res.columns if not c.upper().startswith("P_")][:14]
        idx = [res.columns.index(c) for c in keep]
        return {"table": t.name, "columns": keep,
                "rows": [[r[i] for i in idx] for r in res.rows]}

    def resolve_crew(self, identifier: str) -> dict:
        """Look one person up in the bridge table and return every key they hold.

        Left to reason about it, the agent either strips the prefix off an IGA
        and filters CLMS on the remainder — a valid query that returns nothing,
        reported as "this crew member has no leave" — or concludes no mapping
        exists at all and refuses a question the data can answer. Both are wrong
        answers. The lookup is one indexed row; making it a tool removes the
        reasoning step rather than trying to improve it.
        """
        from crew_perf.sources import load_registry

        bridge = (load_registry().identity or {}).get("bridge") or {}
        table = bridge.get("table")
        node = self.store.table(table) if table else None
        if node is None:
            return {"error": "no identity bridge is configured; identifiers cannot be translated"}

        keys = [c for c in node.columns if c.upper() in {"IGA", "CREW_ID", "EMP_NO"}]
        safe = str(identifier).replace("'", "''")
        where = " OR ".join(f'"{k}" = \'{safe}\'' for k in keys)
        res = self.executor.execute(
            f'SELECT {", ".join(chr(34) + k + chr(34) for k in keys)} '
            f'FROM "{node.name}" WHERE P_IS_CURRENT = TRUE AND ({where}) LIMIT 2',
            limit=2,
        )
        if not res.rows:
            return {"found": False, "identifier": identifier,
                    "note": f"no crew member in {node.name} holds that identifier — "
                            f"do not guess a variant of it"}
        row = dict(zip(res.columns, res.rows[0]))
        return {
            "found": True, "identifier": identifier, "keys": row, "bridge_table": node.name,
            "usage": (f"filter on {node.name} and join outward from it; do not put "
                      f"{identifier!r} into a column of another source"),
        }

    # ── gated execution ────────────────────────────────────────────────────

    def run_sql(self, sql: str, purpose: str = "") -> dict:
        if self.unavailable:
            return {"rejected": True,
                    "errors": ["you already reported this question as unanswerable; "
                               "do not now answer it from a different table"]}
        self._attempts += 1
        if self._attempts > self.max_sql:
            return {"rejected": True,
                    "errors": ["retry budget exhausted; stop and report failure"]}

        verdict = validate(sql, self.store)
        if not verdict.ok:
            self.failures.append(verdict.reason())
            return {"rejected": True, "errors": verdict.errors,
                    "hint": "Fix the query and call run_sql again."}
        try:
            res = self.executor.execute(sql, limit=500)
        except Exception as e:  # noqa: BLE001 - surfaced to the model to retry
            self.failures.append(f"{type(e).__name__}: {e}")
            return {"rejected": True, "errors": [f"execution failed: {e}"],
                    "hint": "Fix the query and call run_sql again."}

        self.sql, self.result = sql, res
        preview = [dict(zip(res.columns, r)) for r in res.rows[:25]]
        return {"ok": True, "purpose": purpose, "columns": res.columns,
                "row_count": len(res.rows), "truncated": res.truncated,
                "warnings": verdict.warnings, "rows": preview}

    def report_unavailable(self, missing: str, reason: str) -> dict:
        """Record an honest refusal. Not an error path — a correct outcome."""
        self.unavailable = {"missing": missing, "reason": reason}
        return {"acknowledged": True, "missing": missing, "reason": reason,
                "next": "Stop here and tell the user what is missing and why."}

    # ── dispatch ───────────────────────────────────────────────────────────

    def handlers(self) -> dict[str, Callable]:
        """Tool name -> bound method. Subclasses extend this, never replace it."""
        return {
            "summary": self.summary, "search": self.search,
            "list_tables": self.list_tables, "list_concepts": self.list_concepts,
            "table_info": self.table_info, "find_join_path": self.find_join_path,
            "sample_rows": self.sample_rows, "resolve_crew": self.resolve_crew,
            "run_sql": self.run_sql, "report_unavailable": self.report_unavailable,
        }

    def dispatch(self, name: str, args: dict) -> Any:
        fn = self.handlers()
        if name not in fn:
            return {"error": f"unknown tool {name!r}", "available": sorted(fn)}
        try:
            return fn[name](**args)
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}
        except Exception as e:  # noqa: BLE001
            # A failing tool must degrade to a message the model can react to.
            # Letting it propagate kills the whole loop over one bad lookup.
            return {"error": f"{name} failed: {type(e).__name__}: {e}"}


# ─── The tool-calling loop ──────────────────────────────────────────────────


@dataclass
class LoopOutcome:
    answer: str = ""
    trace: list[dict] = field(default_factory=list)
    completed: bool = False      # False means the turn budget ran out mid-thought


def run_loop(
    messages: list[dict],
    tools: list[dict],
    toolset: GraphToolset,
    *,
    max_turns: int = 20,
    temperature: float = 0.1,
    nudge: str | None = None,
) -> Iterator[dict]:
    """Drive a tool-calling conversation. The last event carries a LoopOutcome.

    `messages` is mutated as the loop runs, so a caller that wants the full
    exchange back — a conversation continuing across turns does — simply keeps
    its reference.

    `nudge` guards the one failure mode with no other symptom: this deployment
    occasionally returns a turn with neither content nor a tool call, which is
    indistinguishable from "I am finished" and silently produces an empty answer.
    One prod, then take the model at its word.
    """
    outcome = LoopOutcome()
    nudged = False

    for turn in range(max_turns):
        message = call_llm_messages(messages, tools=tools, temperature=temperature)
        calls = getattr(message, "tool_calls", None)

        if not calls:
            content = (message.content or "").strip()
            if not content and nudge and not nudged:
                nudged = True
                messages.append({"role": "user", "content": nudge})
                continue
            messages.append({"role": "assistant", "content": content})
            outcome.answer = content
            outcome.completed = True
            break

        messages.append({
            "role": "assistant", "content": message.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ],
        })

        for call in calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            payload = toolset.dispatch(call.function.name, args)

            event = {"type": "tool", "turn": turn, "name": call.function.name,
                     "args": args, "result": payload}
            outcome.trace.append(event)
            yield event

            # No `name` key here: including it on a tool-role message makes this
            # deployment return empty turns instead of continuing.
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": json.dumps(payload, default=str)[:12_000],
            })

    yield {"type": "done", "outcome": outcome}
