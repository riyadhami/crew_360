"""
inference_agent.py — ReAct agent that explores the knowledge graph to
reformulate a user query into a contextually-rich execution plan.

Usage:
    python -m src.agents.inference_agent "Which tables store crew leave data?"
    python -m src.agents.inference_agent   # interactive mode
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from src.utils import get_graph_db
from src.utils.graph_traversal import GraphDB
from src.utils.llm import get_llm_client, LLM_MODEL
from src.utils.agent_logger import (
    setup_agent_logger,
    log_subgraph_extraction,
    log_query_plan,
    log_tool_execution,
)

# Initialize logger
logger = setup_agent_logger("inference_agent")


# ═══════════════════════════════════════════════════════════════════════════════
#  Tool definitions (mirrors GraphDB methods for the LLM)
# ═══════════════════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schema",
            "description": (
                "Get the full knowledge-graph schema: node labels, "
                "relationship types, node counts per label, and total edge count. "
                "Call this first to understand the graph structure."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_concepts",
            "description": (
                "List all Concept nodes with their name, description, database, "
                "and source tables.  Use this to discover which business domains "
                "exist in the graph."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "concept_links",
            "description": (
                "For a given concept name, return the tables and related concepts "
                "it connects to, grouped by database.  Use this to drill into a "
                "specific business domain."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "concept_name": {"type": "string", "description": "Name of the concept to look up."},
                    "database": {
                        "type": "string",
                        "description": "Optional — filter to a specific database (CLMS, CrewPortal, PEP).",
                    },
                },
                "required": ["concept_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "node_details",
            "description": (
                "Get full properties and all neighbours of any node by its exact name.  "
                "Returns columns, primary keys, description, and every relationship."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact name of the node."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": (
                "List all Table nodes.  Optionally filter by database name "
                "(CLMS, CrewPortal, PEP)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Optional database filter.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Case-insensitive keyword search across node names, labels, and "
                "descriptions.  Best first step for vague or exploratory queries.  "
                "Optionally filter results to a single database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search keyword."},
                    "database": {
                        "type": "string",
                        "description": "Optional — only return results from this database (CLMS, CrewPortal, PEP).",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_columns",
            "description": (
                "Find tables whose column list contains the given keyword.  "
                "Use for 'where does column X live?' questions.  "
                "Optionally filter to a single database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {"type": "string", "description": "Column name or keyword to search for."},
                    "database": {
                        "type": "string",
                        "description": "Optional — only return results from this database (CLMS, CrewPortal, PEP).",
                    },
                },
                "required": ["column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "path_between",
            "description": (
                "Find the shortest path between any two nodes in the graph.  "
                "Useful for understanding how two entities relate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name1": {"type": "string", "description": "Name of the first node."},
                    "name2": {"type": "string", "description": "Name of the second node."},
                },
                "required": ["name1", "name2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subgraph",
            "description": (
                "Get the N-hop neighbourhood around a node — all reachable nodes "
                "and edges within a given depth.  Good for expanding context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Center node name."},
                    "depth": {
                        "type": "integer",
                        "description": "Hops to expand (default 2, max 4).",
                        "default": 2,
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shared_concepts",
            "description": (
                "Return concepts that bridge multiple databases — the high-value "
                "cross-domain nodes.  Use to find integration points."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "database_summary",
            "description": (
                "Quick overview of one database: table count, connected concepts, "
                "and top tables by connection count."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name (CLMS, CrewPortal, PEP)."},
                },
                "required": ["database"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cross_db_edges",
            "description": (
                "List all edges that connect nodes from different databases.  "
                "Use to understand cross-system relationships."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_cross_db",
            "description": (
                "Starting from a concept or table, find what it connects to in a "
                "specific target database (1-hop and 2-hop).  This is the KEY tool "
                "for cross-database questions like 'how does Leave Management in CLMS "
                "relate to CrewPortal?'  Returns direct connections and 2-hop paths "
                "through intermediate nodes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Starting node name (concept or table)."},
                    "target_database": {
                        "type": "string",
                        "description": "Target database to trace connections into (CLMS, CrewPortal, PEP).",
                    },
                },
                "required": ["name", "target_database"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Graph-hint extraction for live visualisation
# ═══════════════════════════════════════════════════════════════════════════════

def extract_graph_hints(tool_name: str, result: Any) -> dict:
    """Extract lightweight nodes + edges from a tool result for the live
    graph panel.  Returns ``{"nodes": [...], "edges": [...]}``.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    _sn: set[tuple] = set()
    _se: set[tuple] = set()

    def _n(name: str, ntype: str, database: str = ""):
        if not name:
            return
        key = (name, database)
        if key not in _sn:
            _sn.add(key)
            nodes.append({"name": name, "type": ntype, "database": database or ""})

    def _e(src: str, tgt: str, label: str = "", src_db: str = "", tgt_db: str = ""):
        if not src or not tgt:
            return
        key = (src, src_db, tgt, tgt_db)
        if key not in _se:
            _se.add(key)
            edges.append({"source": src, "target": tgt, "label": label,
                          "source_db": src_db, "target_db": tgt_db})

    def _type_from_labels(labels: list) -> str:
        return "Concept" if "Concept" in labels else "Table"

    try:
        match tool_name:
            case "list_concepts":
                if isinstance(result, list):
                    for c in result:
                        _n(c.get("name"), "Concept", c.get("database", ""))

            case "concept_links":
                if isinstance(result, list):
                    for entry in result:
                        cn = entry.get("concept", "")
                        cdb = entry.get("concept_database", "")
                        _n(cn, "Concept", cdb)
                        for t in entry.get("linked_tables", []):
                            tn, tdb = t.get("table_name", ""), t.get("database", "")
                            _n(tn, "Table", tdb)
                            _e(cn, tn, t.get("relationship", ""), cdb, tdb)
                        for rc in entry.get("related_concepts", []):
                            _n(rc.get("concept", ""), "Concept", rc.get("database", ""))
                            _e(cn, rc.get("concept", ""), rc.get("relationship", ""),
                               cdb, rc.get("database", ""))

            case "node_details":
                if isinstance(result, dict):
                    node = result.get("node", {})
                    props = node.get("props", {})
                    nm, db = props.get("name", ""), props.get("database", "")
                    _n(nm, _type_from_labels(node.get("labels", [])), db)
                    for nb in result.get("neighbours", []):
                        nbn = nb.get("neighbour_name", "")
                        nbdb = nb.get("neighbour_database", "")
                        _n(nbn, _type_from_labels(nb.get("neighbour_labels", [])), nbdb)
                        _e(nm, nbn, nb.get("relationship", ""), db, nbdb)

            case "list_tables":
                if isinstance(result, list):
                    for t in result:
                        _n(t.get("name", ""), "Table", t.get("database", ""))

            case "search":
                if isinstance(result, list):
                    for r in result:
                        _n(r.get("name", ""), _type_from_labels(r.get("labels", [])),
                           r.get("database", ""))

            case "find_columns":
                if isinstance(result, list):
                    for t in result:
                        _n(t.get("table_name", ""), "Table", t.get("database", ""))

            case "path_between":
                if isinstance(result, list):
                    for step in result:
                        _n(step.get("source", ""), "Unknown")
                        _n(step.get("target", ""), "Unknown")
                        _e(step.get("source", ""), step.get("target", ""),
                           step.get("relationship", ""))

            case "subgraph":
                if isinstance(result, dict):
                    db_map: dict[str, str] = {}
                    for n in result.get("nodes", []):
                        nm = n.get("name", "")
                        db = n.get("database", "")
                        db_map[nm] = db
                        _n(nm, _type_from_labels(n.get("labels", [])), db)
                    for e in result.get("edges", []):
                        s, t = e.get("source", ""), e.get("target", "")
                        _e(s, t, e.get("relationship", ""),
                           db_map.get(s, ""), db_map.get(t, ""))

            case "shared_concepts":
                if isinstance(result, list):
                    for r in result:
                        if r.get("bridge_type") == "same_name":
                            for db in r.get("databases", []):
                                _n(r.get("concept", ""), "Concept", db)
                        elif r.get("bridge_type") == "cross_edge":
                            _n(r.get("concept", ""), "Concept", r.get("database", ""))

            case "database_summary":
                if isinstance(result, dict):
                    dbname = result.get("database", "")
                    for t in result.get("tables", []):
                        _n(t.get("name", ""), "Table", dbname)
                    for c in result.get("connected_concepts", []):
                        _n(c.get("concept", ""), "Concept",
                           c.get("concept_db", dbname))

            case "cross_db_edges":
                if isinstance(result, list):
                    for e in result:
                        _n(e.get("source", ""), "Unknown", e.get("source_db", ""))
                        _n(e.get("target", ""), "Unknown", e.get("target_db", ""))
                        _e(e.get("source", ""), e.get("target", ""),
                           e.get("relationship", ""),
                           e.get("source_db", ""), e.get("target_db", ""))

            case "trace_cross_db":
                if isinstance(result, dict):
                    src = result.get("source", "")
                    for d in result.get("direct_connections", []):
                        sdb = d.get("source_db", "")
                        tn, tdb = d.get("target_name", ""), d.get("target_db", "")
                        _n(src, "Unknown", sdb)
                        _n(tn, _type_from_labels(d.get("target_labels", [])), tdb)
                        _e(src, tn, d.get("relationship", ""), sdb, tdb)
                    for h in result.get("two_hop_connections", []):
                        via, vdb = h.get("via_node", ""), h.get("via_db", "")
                        tn, tdb = h.get("target_name", ""), h.get("target_db", "")
                        _n(via, _type_from_labels(h.get("via_labels", [])), vdb)
                        _n(tn, _type_from_labels(h.get("target_labels", [])), tdb)
                        _e(src, via, h.get("rel1", ""), "", vdb)
                        _e(via, tn, h.get("rel2", ""), vdb, tdb)
    except Exception:
        pass  # never break the SSE stream for visualisation

    return {"nodes": nodes, "edges": edges}


# ═══════════════════════════════════════════════════════════════════════════════
#  Tool dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

def dispatch_tool(db: GraphDB, name: str, args: dict) -> Any:
    """Route a tool call to the corresponding GraphDB method."""
    match name:
        case "schema":
            result = db.schema()
        case "list_concepts":
            result = db.list_concepts()
        case "concept_links":
            result = db.concept_links(args["concept_name"], args.get("database"))
        case "node_details":
            result = db.node_details(args["name"])
        case "list_tables":
            result = db.list_tables(args.get("database"))
        case "search":
            result = db.search(args["keyword"])
            if db_filter := args.get("database"):
                result = [r for r in result if r.get("database") == db_filter]
        case "find_columns":
            result = db.find_columns(args["column"])
            if db_filter := args.get("database"):
                result = [r for r in result if r.get("database") == db_filter]
        case "path_between":
            result = db.path_between(args["name1"], args["name2"])
        case "subgraph":
            result = db.subgraph(args["name"], args.get("depth", 2))
        case "shared_concepts":
            result = db.shared_concepts()
        case "database_summary":
            result = db.database_summary(args["database"])
        case "cross_db_edges":
            result = db.cross_db_edges()
        case "trace_cross_db":
            result = db.trace_cross_db(args["name"], args["target_database"])
        case _:
            result = {"error": f"Unknown tool: {name}"}
    
    # Log sub-graph extraction
    result_summary = {}
    if isinstance(result, dict):
        if 'nodes' in result:
            result_summary['node_count'] = len(result['nodes'])
        if 'edges' in result:
            result_summary['edge_count'] = len(result['edges'])
        if 'tables' in result:
            result_summary['tables'] = [t.get('name', t.get('table', '?')) for t in result['tables'][:5]]
        if 'concepts' in result:
            result_summary['concepts'] = [c.get('name', '?') for c in result['concepts'][:5]]
    elif isinstance(result, list) and len(result) > 0:
        result_summary['count'] = len(result)
        if 'database' in result[0]:
            result_summary['databases'] = list(set(r.get('database', '?') for r in result[:20]))
    
    log_subgraph_extraction(logger, name, args, result_summary)
    log_tool_execution(logger, name, args, result)
    
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  System prompt
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Knowledge Graph exploration agent.  Your job is to take a user's
    natural-language question about the Indigo Airlines crew-management databases
    and produce a **rich, contextual execution plan**.

    You have access to a unified knowledge graph containing tables and concepts
    from three databases: CLMS, CrewPortal, and PEP.

    **Strategy — follow this pattern:**
    1. Start broad: use `search` or `list_concepts` to find relevant entry points.
    2. Narrow down: use `concept_links`, `node_details`, or `find_columns` to
       drill into specific tables and columns.
    3. Connect the dots: use `path_between` or `subgraph` to discover how
       entities relate across databases.
    4. Synthesise: once you have enough context, produce your final answer.

    **Your final answer MUST be a JSON object** with these keys:
    {
      "original_query": "<the user's original question>",
      "reformulated_query": "<a detailed, contextually-rich version of the query>",
      "relevant_databases": ["<DB1>", ...],
      "relevant_tables": [
        {
          "database": "<DB>",
          "table": "<table_name>",
          "label": "<human-readable label>",
          "key_columns": ["<col1>", ...],
          "role": "<why this table is relevant>"
        }, ...
      ],
      "relevant_concepts": ["<concept1>", ...],
      "query_plan": [
        "Step 1: ...",
        "Step 2: ...",
        ...
      ],
      "join_hints": ["<how tables connect across DBs>", ...],
      "notes": "<any caveats or additional context>"
    }

    Be thorough — explore multiple paths before concluding.  Do NOT guess table
    or column names; always verify via tool calls.

    **IMPORTANT rules:**
    - NEVER call the same tool with the same arguments twice.  If a tool returned
      no results, that information is definitive — do not retry.
    - If a database has no matching tables for a topic, state that clearly in
      your final answer rather than searching again.
    - Aim to finish within 8-10 tool calls.  Once you have enough context,
      produce the final JSON immediately.
""")


# ═══════════════════════════════════════════════════════════════════════════════
#  ReAct loop
# ═══════════════════════════════════════════════════════════════════════════════

MAX_ITERATIONS = 20


def run_agent(query: str, verbose: bool = True, backend: str | None = None) -> dict:
    """Run the ReAct agent on a user query and return the final plan."""

    llm = get_llm_client()
    db = get_graph_db(backend)
    seen_calls: set[str] = set()  # duplicate-call detection

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            if verbose:
                print(f"\n{'─'*60}")
                print(f"  Iteration {iteration}/{MAX_ITERATIONS}")
                print(f"{'─'*60}")

            response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.9,
            )

            msg = response.choices[0].message
            messages.append(msg)

            # ── No tool calls → agent is done ─────────────────────────
            if not msg.tool_calls:
                if verbose:
                    print("  Agent finished reasoning.")
                    print(f"\n{'═'*60}")
                    print("  FINAL ANSWER")
                    print(f"{'═'*60}\n")
                    print(msg.content)
                
                # Try to parse and log query plan
                try:
                    plan_dict = json.loads(msg.content) if msg.content and msg.content.strip().startswith('{') else None
                    if plan_dict and isinstance(plan_dict, dict):
                        log_query_plan(logger, query, plan_dict)
                except:
                    pass  # Answer is not JSON, that's okay
                
                logger.info(f"Query completed in {iteration} iterations")
                return {
                    "iterations": iteration,
                    "answer": msg.content,
                }

            # ── Execute tool calls ────────────────────────────────────
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}

                if verbose:
                    args_str = ", ".join(f"{k}={v!r}" for k, v in fn_args.items())
                    print(f"  → {fn_name}({args_str})")

                # Duplicate-call detection
                call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
                if call_key in seen_calls:
                    result = {
                        "note": "You already called this tool with identical arguments. "
                        "Use the previous result or try a different approach."
                    }
                    if verbose:
                        print(f"    \u2190 [DUPLICATE CALL — returning cached hint]")
                else:
                    seen_calls.add(call_key)
                    try:
                        result = dispatch_tool(db, fn_name, fn_args)
                    except Exception as e:
                        result = {"error": str(e)}

                result_json = json.dumps(result, indent=2, default=str)

                # Truncate very large results to keep context manageable
                if len(result_json) > 8000:
                    result_json = result_json[:8000] + "\n... (truncated)"

                if verbose:
                    preview = result_json[:300]
                    if len(result_json) > 300:
                        preview += "..."
                    print(f"    ← {preview}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        # Max iterations reached
        if verbose:
            print(f"\n  Warning: Reached max iterations ({MAX_ITERATIONS})")
        # Find the last assistant message
        last_answer = None
        for m in reversed(messages):
            content = m.content if hasattr(m, "content") else m.get("content")
            role = m.role if hasattr(m, "role") else m.get("role")
            if role == "assistant" and content:
                last_answer = content
                break
        return {
            "iterations": MAX_ITERATIONS,
            "answer": last_answer,
            "warning": "Max iterations reached",
        }

    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Streaming generator (for Flask SSE)
# ═══════════════════════════════════════════════════════════════════════════════

def run_agent_stream(query: str, backend: str | None = None):
    """Generator that yields JSON event dicts as the agent works.

    Event types:
      {"type": "iteration", "number": N, "max": 20}
      {"type": "tool_call", "name": "...", "args": {...}}
      {"type": "tool_result", "name": "...", "preview": "...", "duplicate": bool}
      {"type": "done", "iterations": N, "answer": "..."}
      {"type": "error", "message": "..."}
    """
    llm = get_llm_client()
    db = get_graph_db(backend)
    seen_calls: set[str] = set()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            yield {"type": "iteration", "number": iteration, "max": MAX_ITERATIONS}

            response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.9,
            )

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                yield {"type": "done", "iterations": iteration, "answer": msg.content}
                return

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}

                yield {"type": "tool_call", "name": fn_name, "args": fn_args}

                call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
                is_dup = call_key in seen_calls
                if is_dup:
                    result = {
                        "note": "You already called this tool with identical arguments. "
                        "Use the previous result or try a different approach."
                    }
                else:
                    seen_calls.add(call_key)
                    try:
                        result = dispatch_tool(db, fn_name, fn_args)
                    except Exception as e:
                        result = {"error": str(e)}

                result_json = json.dumps(result, indent=2, default=str)
                if len(result_json) > 8000:
                    result_json = result_json[:8000] + "\n... (truncated)"

                preview = result_json[:500] + ("..." if len(result_json) > 500 else "")
                hints = extract_graph_hints(fn_name, result) if not is_dup else {"nodes": [], "edges": []}
                yield {"type": "tool_result", "name": fn_name, "preview": preview, "duplicate": is_dup, "graph": hints}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        # Max iterations
        last_answer = None
        for m in reversed(messages):
            content = m.content if hasattr(m, "content") else m.get("content")
            role = m.role if hasattr(m, "role") else m.get("role")
            if role == "assistant" and content:
                last_answer = content
                break
        yield {
            "type": "done",
            "iterations": MAX_ITERATIONS,
            "answer": last_answer,
            "warning": "Max iterations reached",
        }

    except Exception as e:
        yield {"type": "error", "message": str(e)}

    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Inference Agent — KG exploration")
    parser.add_argument("query", nargs="*", help="Query to run (omit for interactive mode)")
    parser.add_argument("--graph-backend", choices=["neo4j", "cosmos"], default=None,
                        help="Graph DB backend (default: GRAPH_BACKEND env var or neo4j)")
    args = parser.parse_args()
    backend = args.graph_backend

    if args.query:
        run_agent(" ".join(args.query), backend=backend)
    else:
        label = f"cosmos" if backend == "cosmos" else "neo4j"
        print(f"Inference Agent [{label}] — explore the KG and build query plans")
        print("Type your question (or 'quit' to exit)\n")
        while True:
            try:
                q = input("? ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in ("quit", "exit"):
                break
            run_agent(q, backend=backend)
            print()


if __name__ == "__main__":
    main()
