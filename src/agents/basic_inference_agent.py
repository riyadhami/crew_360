"""
basic_inference_agent.py — Single-shot inference agent.

Unlike the ReAct agents, this agent makes exactly ONE planning call to the
LLM (which can return many tool calls in parallel), executes them all, then
feeds everything back into a single synthesis call.

    LLM plan  →  parallel tool calls  →  LLM synthesis  →  answer

Usage:
    python -m src.agents.basic_inference_agent "How do notifications differ across databases?"
    python -m src.agents.basic_inference_agent   # interactive mode
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from src.utils import get_graph_db
from src.utils.llm import get_llm_client, LLM_MODEL
from src.agents.inference_agent import TOOLS, dispatch_tool, extract_graph_hints
from src.utils.agent_logger import (
    setup_agent_logger,
    log_subgraph_extraction,
    log_query_plan,
    log_tool_execution,
)

# Initialize logger
logger = setup_agent_logger("basic_inference")


# ═══════════════════════════════════════════════════════════════════════════════
#  System prompts
# ═══════════════════════════════════════════════════════════════════════════════

PLAN_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Knowledge Graph query planner for the Indigo Airlines
    crew-management databases.  The knowledge graph contains tables and
    concepts from three databases: CLMS, CrewPortal, and PEP.

    Given a user question, your job is to decide IN A SINGLE RESPONSE which
    tool calls to make to gather enough context to answer the question.
    You may (and should) call MULTIPLE tools at once — the system will
    execute them all in parallel.

    **Planning strategy — think about what data you need:**
    • For cross-database questions → call `shared_concepts`, plus `search`
      with key terms, plus `subgraph` around the central concepts.
    • For specific table/column questions → call `search`, `find_columns`,
      and `concept_links` for the relevant concept.
    • For "how do X relate to Y" questions → call `subgraph` on both X and Y,
      or `path_between` if they are specific nodes.
    • When in doubt → call `list_concepts` + `search` with the main keywords.

    **Rules:**
    • Issue ALL the tool calls you need in ONE response.  You will not get
      another chance to call tools.
    • Aim for 4-8 tool calls — enough for broad coverage without being wasteful.
    • Always include at least one `search` call with the user's core keywords.
    • For cross-DB queries, include `shared_concepts` or `cross_db_edges`.
    • Prefer `subgraph` (depth 2) over `node_details` when you want context.
""")


SYNTHESIS_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Knowledge Graph synthesis agent for the Indigo Airlines
    crew-management databases (CLMS, CrewPortal, PEP).

    You have been given:
    1. The user's original question.
    2. The results of several knowledge-graph tool calls.

    Your job is to synthesise these results into a single, comprehensive
    execution plan that answers the user's question.

    **Your answer MUST be a JSON object** with these keys:
    {
      "original_query": "<the user's original question>",
      "reformulated_query": "<a detailed, contextually-rich version>",
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

    Be specific about column names, join keys, and cross-database links.
    Deduplicate tables if they appear in multiple tool results.
""")


# ═══════════════════════════════════════════════════════════════════════════════
#  Core agent logic
# ═══════════════════════════════════════════════════════════════════════════════

def run_agent(query: str, verbose: bool = True, backend: str | None = None) -> dict:
    """Single-shot: plan → parallel tools → synthesise."""
    llm = get_llm_client()
    db = get_graph_db(backend)

    try:
        # ── Step 1: Plan — LLM emits all tool calls at once ──────────
        if verbose:
            print(f"\n{'═'*60}")
            print("  STEP 1 — Planning tool calls")
            print(f"{'═'*60}")

        plan_response = llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            tools=TOOLS,
            tool_choice="required",
            temperature=0.9,
        )
        plan_msg = plan_response.choices[0].message

        if not plan_msg.tool_calls:
            return {"answer": plan_msg.content or "No tool calls planned."}

        if verbose:
            print(f"  Planned {len(plan_msg.tool_calls)} tool calls")
        
        logger.info(f"Query: {query}")
        logger.info(f"Planned {len(plan_msg.tool_calls)} tool calls")

        # ── Step 2: Execute all tool calls ───────────────────────────
        if verbose:
            print(f"\n{'═'*60}")
            print("  STEP 2 — Executing tools")
            print(f"{'═'*60}")

        tool_messages = []
        for tc in plan_msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}

            if verbose:
                args_str = ", ".join(f"{k}={v!r}" for k, v in fn_args.items())
                print(f"  → {fn_name}({args_str})")

            try:
                result = dispatch_tool(db, fn_name, fn_args)
                
                # Log sub-graph extraction
                result_summary = {}
                if isinstance(result, dict):
                    if 'nodes' in result:
                        result_summary['node_count'] = len(result['nodes'])
                    if 'edges' in result:
                        result_summary['edge_count'] = len(result['edges'])
                    if 'tables' in result:
                        result_summary['tables'] = [t.get('name', t.get('label', '?')) for t in result['tables'][:5]]
                    if 'concepts' in result:
                        result_summary['concepts'] = [c.get('name', '?') for c in result['concepts'][:5]]
                
                log_subgraph_extraction(logger, fn_name, fn_args, result_summary)
                log_tool_execution(logger, fn_name, fn_args, result)
            except Exception as e:
                result = {"error": str(e)}
                logger.error(f"Tool execution error: {fn_name} - {str(e)}")

            result_json = json.dumps(result, indent=2, default=str)
            if len(result_json) > 8000:
                result_json = result_json[:8000] + "\n... (truncated)"

            if verbose:
                preview = result_json[:200] + ("..." if len(result_json) > 200 else "")
                print(f"    ← {preview}")

            tool_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_json,
            })

        # ── Step 3: Synthesise ───────────────────────────────────────
        if verbose:
            print(f"\n{'═'*60}")
            print("  STEP 3 — Synthesis")
            print(f"{'═'*60}")

        synth_response = llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": query},
                plan_msg,
                *tool_messages,
            ],
            temperature=0.9,
        )

        answer = synth_response.choices[0].message.content
        if verbose:
            print(f"\n{'═'*60}")
            print("  FINAL ANSWER")
            print(f"{'═'*60}\n")
            print(answer)
        
        # Try to parse and log query plan
        try:
            plan_dict = json.loads(answer) if answer.strip().startswith('{') else None
            if plan_dict and isinstance(plan_dict, dict):
                log_query_plan(logger, query, plan_dict)
        except:
            pass  # Answer is not JSON, that's okay
        
        logger.info("Query processing complete")

        return {"answer": answer}

    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Streaming generator (for Flask SSE)
# ═══════════════════════════════════════════════════════════════════════════════

def run_agent_stream(query: str, backend: str | None = None):
    """Generator yielding JSON event dicts for the Flask SSE endpoint.

    Event types:
      {"type": "phase",       "phase": 1|2, "description": "..."}
      {"type": "tool_call",   "name": "...", "args": {...}, "index": N, "total": N}
      {"type": "tool_result", "name": "...", "preview": "...", "graph": {...},
                               "index": N, "total": N}
      {"type": "done",        "answer": "...", "tool_count": N}
      {"type": "error",       "message": "..."}
    """
    try:
        llm = get_llm_client()
        db = get_graph_db(backend)

        try:
            # ── Phase 1: Planning ────────────────────────────────────
            yield {"type": "phase", "phase": 1,
                   "description": "Planning tool calls"}

            plan_response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
                tools=TOOLS,
                tool_choice="required",
                temperature=0.9,
            )
            plan_msg = plan_response.choices[0].message

            if not plan_msg.tool_calls:
                yield {"type": "done", "answer": plan_msg.content or "No tools called.",
                       "tool_count": 0}
                return

            total = len(plan_msg.tool_calls)

            # ── Phase 2: Executing tools + synthesis ─────────────────
            yield {"type": "phase", "phase": 2,
                   "description": f"Executing {total} tools & synthesising"}

            tool_messages = []
            for idx, tc in enumerate(plan_msg.tool_calls, 1):
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}

                yield {"type": "tool_call", "name": fn_name, "args": fn_args,
                       "index": idx, "total": total}

                try:
                    result = dispatch_tool(db, fn_name, fn_args)
                except Exception as e:
                    result = {"error": str(e)}

                result_json = json.dumps(result, indent=2, default=str)
                if len(result_json) > 8000:
                    result_json = result_json[:8000] + "\n... (truncated)"

                preview = result_json[:500] + ("..." if len(result_json) > 500 else "")
                hints = extract_graph_hints(fn_name, result)

                yield {"type": "tool_result", "name": fn_name, "preview": preview,
                       "graph": hints, "index": idx, "total": total}

                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

            # Synthesis
            synth_response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                    plan_msg,
                    *tool_messages,
                ],
                temperature=0.9,
            )

            answer = synth_response.choices[0].message.content
            yield {"type": "done", "answer": answer, "tool_count": total}

        finally:
            db.close()

    except Exception as e:
        yield {"type": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Basic Inference Agent — single-shot KG query planner")
    parser.add_argument("query", nargs="*", help="Query to run (omit for interactive mode)")
    parser.add_argument("--graph-backend", choices=["neo4j", "cosmos"], default=None,
                        help="Graph DB backend (default: GRAPH_BACKEND env var or neo4j)")
    args = parser.parse_args()
    backend = args.graph_backend

    if args.query:
        run_agent(" ".join(args.query), backend=backend)
    else:
        label = f"cosmos" if backend == "cosmos" else "neo4j"
        print(f"Basic Inference Agent [{label}] — single-shot KG query planner")
        print("Type your question (or 'quit' to exit)\n")
        while True:
            try:
                q = input("? ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in ("quit", "exit"):
                break
            run_agent(q, backend=backend)


if __name__ == "__main__":
    main()
