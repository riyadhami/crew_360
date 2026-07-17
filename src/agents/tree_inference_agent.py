"""
tree_inference_agent.py — Three-phase inference agent that explores
the knowledge graph with parallel concept exploration.

Phase 1:  Concept Discovery   — identify all concepts relevant to the query.
Phase 2:  Parallel Exploration — drill into each concept concurrently.
Phase 3:  Aggregation          — synthesise findings into a final plan.

Usage:
    python -m src.agents.tree_inference_agent "Which tables store crew leave data?"
    python -m src.agents.tree_inference_agent   # interactive mode
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
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
logger = setup_agent_logger("tree_inference")


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase-specific system prompts
# ═══════════════════════════════════════════════════════════════════════════════

PHASE1_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Knowledge Graph concept-discovery agent.  Given a user's
    natural-language question about the Indigo Airlines crew-management
    databases, your ONLY job is to identify which concepts in the knowledge
    graph are relevant to answering the question.

    You have access to a unified knowledge graph containing tables and concepts
    from three databases: CLMS, CrewPortal, and PEP.

    **Strategy:**
    1. Use `search` with keywords from the query to find matching nodes.
    2. Use `list_concepts` to see all available concepts if needed.
    3. Use `shared_concepts` if the query seems cross-database.
    4. Stop as soon as you have a confident list of relevant concepts.

    **Your final answer MUST be a JSON object** with exactly these keys:
    {
      "relevant_concepts": [
        {
          "name": "<exact concept name as it appears in the graph>",
          "database": "<which DB it belongs to — CLMS, CrewPortal, or PEP>",
          "relevance": "<one sentence on why this concept matters for the query>"
        },
        ...
      ],
      "reasoning": "<brief explanation of how you identified these concepts>"
    }

    **Rules:**
    - Return the EXACT concept names as they appear in the graph.
    - Include concepts from ALL relevant databases.
    - Do NOT explore tables or columns — that happens later.
    - Aim to finish within 3-5 tool calls.
    - NEVER call the same tool with the same arguments twice.
""")


PHASE2_SYSTEM_PROMPT_TEMPLATE = textwrap.dedent("""\
    You are a Knowledge Graph exploration agent focused on a SINGLE concept.
    Your job is to deeply explore the concept "{concept_name}" (database:
    {concept_database}) in the context of this user question:

    "{user_query}"

    Gather as much relevant detail as possible about this concept: its linked
    tables, key columns, cross-database connections, and relationships with
    other concepts.

    **Strategy:**
    1. Use `concept_links` to find tables and related concepts.
    2. Use `node_details` on the most relevant tables to get columns.
    3. Use `trace_cross_db` if cross-database connections are needed.
    4. Use `find_columns` for specific column lookups.

    **Your final answer MUST be a JSON object** with exactly these keys:
    {{
      "concept": "{concept_name}",
      "database": "{concept_database}",
      "tables": [
        {{
          "name": "<table name>",
          "database": "<DB>",
          "label": "<human-readable label>",
          "key_columns": ["<col1>", ...],
          "role": "<why this table matters for the query>"
        }},
        ...
      ],
      "cross_db_connections": [
        "<description of how this concept connects to other databases>"
      ],
      "related_concepts": ["<concept1>", ...],
      "key_findings": "<summary of what you discovered>"
    }}

    **Rules:**
    - Be thorough but focused — only explore what's relevant to the query.
    - NEVER call the same tool with the same arguments twice.
    - Aim to finish within 5-7 tool calls.
""")


PHASE3_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a Knowledge Graph synthesis agent.  You have been given the
    results of parallel deep-dives into multiple concepts from the Indigo
    Airlines knowledge graph.

    Your job is to combine these findings into a single, coherent execution
    plan that answers the user's original question.

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

    Deduplicate tables that appear in multiple concept explorations.
    Prioritise cross-database join hints.  Be specific about column names.
""")


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 1 — Concept Discovery (ReAct loop)
# ═══════════════════════════════════════════════════════════════════════════════

PHASE1_MAX_ITERATIONS = 8

# Phase 1 uses only the broad-search tools
PHASE1_TOOLS = [t for t in TOOLS if t["function"]["name"] in {
    "search", "list_concepts", "shared_concepts", "schema", "database_summary",
}]


def _run_phase1(query: str, llm, db, verbose: bool = True) -> list[dict]:
    """Identify relevant concepts.  Returns a list of
    ``{"name": ..., "database": ..., "relevance": ...}`` dicts.
    """
    if verbose:
        print(f"\n{'═'*60}")
        print("  PHASE 1 — Concept Discovery")
        print(f"{'═'*60}")

    seen_calls: set[str] = set()
    messages = [
        {"role": "system", "content": PHASE1_SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    for iteration in range(1, PHASE1_MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n  [P1] Iteration {iteration}/{PHASE1_MAX_ITERATIONS}")

        response = llm.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=PHASE1_TOOLS,
            tool_choice="auto",
            temperature=0.9,
        )
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            if verbose:
                print("  [P1] Done.")
            return _parse_phase1_response(msg.content)

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            if verbose:
                args_str = ", ".join(f"{k}={v!r}" for k, v in fn_args.items())
                print(f"  [P1] → {fn_name}({args_str})")

            call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
            if call_key in seen_calls:
                result = {"note": "Duplicate call — use the previous result."}
            else:
                seen_calls.add(call_key)
                try:
                    result = dispatch_tool(db, fn_name, fn_args)
                except Exception as e:
                    result = {"error": str(e)}

            result_json = json.dumps(result, indent=2, default=str)
            if len(result_json) > 8000:
                result_json = result_json[:8000] + "\n... (truncated)"

            if verbose:
                preview = result_json[:300] + ("..." if len(result_json) > 300 else "")
                print(f"    ← {preview}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_json,
            })

    # Fallback: find last assistant message
    for m in reversed(messages):
        content = m.content if hasattr(m, "content") else m.get("content")
        role = m.role if hasattr(m, "role") else m.get("role")
        if role == "assistant" and content:
            return _parse_phase1_response(content)
    return []


def _parse_phase1_response(text: str | None) -> list[dict]:
    """Extract the relevant_concepts list from Phase 1 output."""
    if not text:
        return []
    try:
        # Strip markdown fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        data = json.loads(cleaned)
        return data.get("relevant_concepts", [])
    except (json.JSONDecodeError, AttributeError):
        # Try to find JSON block inside the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end])
                return data.get("relevant_concepts", [])
            except json.JSONDecodeError:
                pass
    return []


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 2 — Parallel Concept Exploration
# ═══════════════════════════════════════════════════════════════════════════════

PHASE2_MAX_ITERATIONS = 10

# Phase 2 uses the detailed-exploration tools
PHASE2_TOOLS = [t for t in TOOLS if t["function"]["name"] in {
    "concept_links", "node_details", "find_columns", "trace_cross_db",
    "path_between", "subgraph", "list_tables", "search",
}]


def _explore_single_concept(
    query: str,
    concept: dict,
    verbose: bool = True,
    backend: str | None = None,
) -> dict:
    """Run a ReAct loop to deeply explore one concept.  Each thread gets
    its own LLM client and GraphDB connection for thread-safety.
    """
    concept_name = concept["name"]
    concept_db = concept.get("database", "Unknown")
    tag = f"[P2:{concept_name}]"

    llm = get_llm_client()
    db = get_graph_db(backend)
    seen_calls: set[str] = set()

    system_prompt = PHASE2_SYSTEM_PROMPT_TEMPLATE.format(
        concept_name=concept_name,
        concept_database=concept_db,
        user_query=query,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]

    try:
        for iteration in range(1, PHASE2_MAX_ITERATIONS + 1):
            if verbose:
                print(f"\n  {tag} Iteration {iteration}/{PHASE2_MAX_ITERATIONS}")

            response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=PHASE2_TOOLS,
                tool_choice="auto",
                temperature=0.9,
            )
            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                if verbose:
                    print(f"  {tag} Done.")
                return _parse_phase2_response(msg.content, concept_name, concept_db)

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                if verbose:
                    args_str = ", ".join(f"{k}={v!r}" for k, v in fn_args.items())
                    print(f"  {tag} → {fn_name}({args_str})")

                call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
                if call_key in seen_calls:
                    result = {"note": "Duplicate call — use the previous result."}
                else:
                    seen_calls.add(call_key)
                    try:
                        result = dispatch_tool(db, fn_name, fn_args)
                    except Exception as e:
                        result = {"error": str(e)}

                result_json = json.dumps(result, indent=2, default=str)
                if len(result_json) > 8000:
                    result_json = result_json[:8000] + "\n... (truncated)"

                if verbose:
                    preview = result_json[:200] + ("..." if len(result_json) > 200 else "")
                    print(f"    ← {preview}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        # Max iterations — grab last assistant text
        for m in reversed(messages):
            content = m.content if hasattr(m, "content") else m.get("content")
            role = m.role if hasattr(m, "role") else m.get("role")
            if role == "assistant" and content:
                return _parse_phase2_response(content, concept_name, concept_db)
        return {"concept": concept_name, "database": concept_db, "error": "No result"}

    finally:
        db.close()


def _parse_phase2_response(text: str | None, concept_name: str, concept_db: str) -> dict:
    """Extract structured exploration result from Phase 2 output."""
    fallback = {"concept": concept_name, "database": concept_db, "raw": text}
    if not text:
        return fallback
    try:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        return json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError):
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return fallback


def _run_phase2(
    query: str,
    concepts: list[dict],
    verbose: bool = True,
    max_workers: int = 4,
    backend: str | None = None,
) -> list[dict]:
    """Explore all concepts in parallel using a thread pool."""
    if verbose:
        print(f"\n{'═'*60}")
        print(f"  PHASE 2 — Parallel Exploration ({len(concepts)} concepts)")
        print(f"{'═'*60}")

    if not concepts:
        return []

    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(concepts))) as pool:
        future_to_concept = {
            pool.submit(_explore_single_concept, query, c, verbose, backend): c
            for c in concepts
        }
        for future in as_completed(future_to_concept):
            concept = future_to_concept[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({
                    "concept": concept["name"],
                    "database": concept.get("database", "Unknown"),
                    "error": str(e),
                })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase 3 — Aggregation (single LLM call, no tools)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_phase3(
    query: str,
    concepts: list[dict],
    explorations: list[dict],
    llm,
    verbose: bool = True,
) -> str:
    """Aggregate parallel explorations into a final execution plan."""
    if verbose:
        print(f"\n{'═'*60}")
        print("  PHASE 3 — Aggregation")
        print(f"{'═'*60}")

    user_content = textwrap.dedent(f"""\
        **Original query:** {query}

        **Relevant concepts identified in Phase 1:**
        {json.dumps(concepts, indent=2, default=str)}

        **Detailed explorations from Phase 2:**
        {json.dumps(explorations, indent=2, default=str)}

        Now synthesise these findings into a single execution plan.
    """)

    response = llm.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": PHASE3_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.9,
    )

    answer = response.choices[0].message.content
    if verbose:
        print(f"\n{'═'*60}")
        print("  FINAL ANSWER")
        print(f"{'═'*60}\n")
        print(answer)
    return answer


# ═══════════════════════════════════════════════════════════════════════════════
#  Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def run_agent(query: str, verbose: bool = True, max_workers: int = 4, backend: str | None = None) -> dict:
    """Run the three-phase advanced inference agent."""
    llm = get_llm_client()
    db = get_graph_db(backend)
    
    logger.info(f"Starting tree inference for query: {query}")
    logger.info(f"Using backend: {backend or 'default'}, max_workers: {max_workers}")

    try:
        # Phase 1 — discover relevant concepts
        logger.info("Phase 1: Concept Discovery - Starting")
        concepts = _run_phase1(query, llm, db, verbose)
        if verbose:
            names = [c.get("name", "?") for c in concepts]
            print(f"\n  Discovered concepts: {names}")
        
        logger.info(f"Phase 1: Discovered {len(concepts)} concepts")
        for concept in concepts[:5]:
            logger.debug(f"  - {concept.get('name')} ({concept.get('database', '?')})")

        if not concepts:
            if verbose:
                print("  No relevant concepts found — falling back to direct answer.")
            logger.warning("No concepts discovered in Phase 1")
            return {
                "phases": {"phase1": concepts, "phase2": [], "phase3": None},
                "answer": "No relevant concepts were found for this query.",
            }

        # Phase 2 — parallel deep-dive (each thread owns its own DB + LLM)
        logger.info(f"Phase 2: Parallel Exploration - Processing {len(concepts)} concepts")
        explorations = _run_phase2(query, concepts, verbose, max_workers, backend)
        logger.info(f"Phase 2: Completed {len(explorations)} explorations")

        # Phase 3 — aggregate
        logger.info("Phase 3: Aggregation - Starting")
        answer = _run_phase3(query, concepts, explorations, llm, verbose)
        
        # Try to parse and log query plan
        try:
            plan_dict = json.loads(answer) if answer and answer.strip().startswith('{') else None
            if plan_dict and isinstance(plan_dict, dict):
                log_query_plan(logger, query, plan_dict)
        except:
            pass  # Answer is not JSON, that's okay
        
        logger.info("Tree inference complete")

        return {
            "phases": {
                "phase1": concepts,
                "phase2": explorations,
                "phase3": answer,
            },
            "answer": answer,
        }

    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Streaming generator (for Flask SSE)
# ═══════════════════════════════════════════════════════════════════════════════

def run_agent_stream(query: str, max_workers: int = 4, backend: str | None = None):
    """Generator yielding JSON event dicts as the agent works.

    Event types:
      {"type": "phase",      "phase": 1|2|3, "description": "..."}
      {"type": "iteration",  "phase": N, "concept": "...", "number": N, "max": N}
      {"type": "tool_call",  "phase": N, "concept": "...", "name": "...", "args": {...}}
      {"type": "tool_result","phase": N, "concept": "...", "name": "...", "preview": "...",
                              "duplicate": bool, "graph": {...}}
      {"type": "concepts_discovered", "concepts": [...]}
      {"type": "concept_done",  "concept": "...", "result": {...}}
      {"type": "done",       "iterations": N, "answer": "..."}
      {"type": "error",      "message": "..."}
    """
    try:
        # ── Phase 1 ──────────────────────────────────────────────────
        yield {"type": "phase", "phase": 1, "description": "Concept Discovery"}

        llm = get_llm_client()
        db = get_graph_db(backend)
        seen_calls: set[str] = set()
        messages = [
            {"role": "system", "content": PHASE1_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]

        concepts: list[dict] = []
        try:
            for iteration in range(1, PHASE1_MAX_ITERATIONS + 1):
                yield {"type": "iteration", "phase": 1, "concept": None,
                       "number": iteration, "max": PHASE1_MAX_ITERATIONS}

                response = llm.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    tools=PHASE1_TOOLS,
                    tool_choice="auto",
                    temperature=0.9,
                )
                msg = response.choices[0].message
                messages.append(msg)

                if not msg.tool_calls:
                    concepts = _parse_phase1_response(msg.content)
                    break

                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    yield {"type": "tool_call", "phase": 1, "concept": None,
                           "name": fn_name, "args": fn_args}

                    call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
                    is_dup = call_key in seen_calls
                    if is_dup:
                        result = {"note": "Duplicate call — use the previous result."}
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
                    yield {"type": "tool_result", "phase": 1, "concept": None,
                           "name": fn_name, "preview": preview,
                           "duplicate": is_dup, "graph": hints}

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_json,
                    })

            if not concepts:
                # Try to parse from last assistant message
                for m in reversed(messages):
                    content = m.content if hasattr(m, "content") else m.get("content")
                    role = m.role if hasattr(m, "role") else m.get("role")
                    if role == "assistant" and content:
                        concepts = _parse_phase1_response(content)
                        break
        finally:
            db.close()

        yield {"type": "concepts_discovered", "concepts": concepts}

        if not concepts:
            yield {"type": "done", "iterations": 0,
                   "answer": "No relevant concepts were found for this query."}
            return

        # ── Phase 2 — sequential streaming (so we can yield events) ──
        yield {"type": "phase", "phase": 2,
               "description": f"Parallel Exploration ({len(concepts)} concepts)"}

        explorations: list[dict] = []

        # We run Phase 2 threads in parallel but collect results sequentially
        # for streaming.  A queue-based approach keeps event ordering clean.
        import queue
        event_queue: queue.Queue = queue.Queue()

        def _explore_and_enqueue(concept: dict):
            concept_name = concept["name"]
            concept_db = concept.get("database", "Unknown")
            tag = f"{concept_name} [{concept_db}]"

            _llm = get_llm_client()
            _db = get_graph_db(backend)
            _seen: set[str] = set()

            system_prompt = PHASE2_SYSTEM_PROMPT_TEMPLATE.format(
                concept_name=concept_name,
                concept_database=concept_db,
                user_query=query,
            )
            msgs = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query},
            ]

            try:
                for it in range(1, PHASE2_MAX_ITERATIONS + 1):
                    event_queue.put({"type": "iteration", "phase": 2,
                                     "concept": tag, "number": it,
                                     "max": PHASE2_MAX_ITERATIONS})

                    resp = _llm.chat.completions.create(
                        model=LLM_MODEL, messages=msgs,
                        tools=PHASE2_TOOLS, tool_choice="auto",
                        temperature=0.9,
                    )
                    m = resp.choices[0].message
                    msgs.append(m)

                    if not m.tool_calls:
                        parsed = _parse_phase2_response(m.content, concept_name, concept_db)
                        event_queue.put({"type": "concept_done",
                                         "concept": tag, "result": parsed})
                        return parsed

                    for tc in m.tool_calls:
                        fn = tc.function.name
                        fa = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        event_queue.put({"type": "tool_call", "phase": 2,
                                         "concept": tag, "name": fn, "args": fa})

                        ck = json.dumps({"fn": fn, "args": fa}, sort_keys=True)
                        dup = ck in _seen
                        if dup:
                            res = {"note": "Duplicate call."}
                        else:
                            _seen.add(ck)
                            try:
                                res = dispatch_tool(_db, fn, fa)
                            except Exception as exc:
                                res = {"error": str(exc)}

                        rj = json.dumps(res, indent=2, default=str)
                        if len(rj) > 8000:
                            rj = rj[:8000] + "\n... (truncated)"

                        prev = rj[:500] + ("..." if len(rj) > 500 else "")
                        gh = extract_graph_hints(fn, res) if not dup else {"nodes": [], "edges": []}
                        event_queue.put({"type": "tool_result", "phase": 2,
                                         "concept": tag, "name": fn,
                                         "preview": prev, "duplicate": dup,
                                         "graph": gh})

                        msgs.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": rj,
                        })

                # Fallback
                for mx in reversed(msgs):
                    ct = mx.content if hasattr(mx, "content") else mx.get("content")
                    rl = mx.role if hasattr(mx, "role") else mx.get("role")
                    if rl == "assistant" and ct:
                        parsed = _parse_phase2_response(ct, concept_name, concept_db)
                        event_queue.put({"type": "concept_done",
                                         "concept": tag, "result": parsed})
                        return parsed
                return {"concept": concept_name, "database": concept_db, "error": "No result"}

            finally:
                _db.close()

        # Launch parallel explorations
        SENTINEL = object()
        with ThreadPoolExecutor(max_workers=min(max_workers, len(concepts))) as pool:
            futures = {
                pool.submit(_explore_and_enqueue, c): c for c in concepts
            }

            done_count = 0
            while done_count < len(futures):
                # Drain queued events
                while True:
                    try:
                        evt = event_queue.get_nowait()
                        yield evt
                    except queue.Empty:
                        break

                # Check for completed futures
                for f in list(futures):
                    if f.done() and futures[f] is not SENTINEL:
                        try:
                            result = f.result()
                            explorations.append(result)
                        except Exception as e:
                            c = futures[f]
                            explorations.append({
                                "concept": c["name"],
                                "database": c.get("database", "Unknown"),
                                "error": str(e),
                            })
                        futures[f] = SENTINEL
                        done_count += 1

            # Final drain
            while not event_queue.empty():
                yield event_queue.get_nowait()

        # ── Phase 3 ──────────────────────────────────────────────────
        yield {"type": "phase", "phase": 3, "description": "Aggregation"}

        answer = _run_phase3(query, concepts, explorations, llm, verbose=False)

        yield {"type": "done", "answer": answer,
               "phases": {"phase1": concepts, "phase2": explorations}}

    except Exception as e:
        yield {"type": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Advanced Inference Agent — three-phase KG exploration")
    parser.add_argument("query", nargs="*", help="Query to run (omit for interactive mode)")
    parser.add_argument("--graph-backend", choices=["neo4j", "cosmos"], default=None,
                        help="Graph DB backend (default: GRAPH_BACKEND env var or neo4j)")
    args = parser.parse_args()
    backend = args.graph_backend

    if args.query:
        run_agent(" ".join(args.query), backend=backend)
    else:
        label = f"cosmos" if backend == "cosmos" else "neo4j"
        print(f"Advanced Inference Agent [{label}] — three-phase KG exploration")
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
