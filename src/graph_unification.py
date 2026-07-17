"""
graph_unification.py — Unify two concept graphs via embedding-based concept matching.

Process:
  1. Load both concept graph JSONs
  2. Extract concept nodes from each
  3. Embed concepts (label + description), compute pairwise cosine similarity
  4. Classify pairs: ≥0.70 auto-link, 0.40–0.70 LLM-evaluate, <0.40 reject
  5. Build cross-graph concept edges
  6. Namespace all node IDs, merge graphs, write outputs
  7. Optionally load into Neo4j

Usage:
    python -m src.graph_unification
    python -m src.graph_unification --graph1 output/CLMS_concept_graph.json --graph2 output/CrewPortal_concept_graph.json
    python -m src.graph_unification --load-neo4j
    python -m src.graph_unification --load-only unified_output/unified_kg.json
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.utils.agent_logger import setup_agent_logger
from src.utils.llm import (
    call_llm,
    embed_texts,
    get_embedding_client,
    get_llm_client,
    parse_llm_json,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Loading & Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def discover_concept_graphs(data_dir: str = "output") -> list[str]:
    """Discover all concept graph JSON files in the specified directory.
    
    Args:
        data_dir: Directory to search for concept graph files
        
    Returns:
        List of paths to concept graph JSON files
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        return []
    
    # Find all *_concept_graph.json files
    graph_files = list(data_path.glob("*_concept_graph.json"))
    
    # Sort by database name for consistent ordering
    return sorted([str(f) for f in graph_files])


def load_graph(path: str) -> dict:
    """Load a concept graph JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_concepts(graph: dict) -> list[dict]:
    """Return all concept nodes from a graph."""
    return [n for n in graph.get("nodes", []) if n.get("type") == "concept"]


def extract_entities(graph: dict) -> list[dict]:
    """Return all entity (non-concept) nodes from a graph."""
    return [n for n in graph.get("nodes", []) if n.get("type") != "concept"]


def build_entity_text(entity: dict) -> str:
    """Build the text to embed for an entity (table): 'Label: Description + Columns'.
    
    Includes column-level information for accurate semantic matching of tables.
    """
    label = entity.get("label", "")
    desc = entity.get("description", "")
    
    # Extract columns from metadata for semantic matching
    metadata = entity.get("metadata", {})
    columns = metadata.get("columns", [])
    
    # Build text with label, description, and column information
    text_parts = [f"{label}: {desc}"]
    
    if columns:
        # Add column names as semantic signals (limit to 20 columns)
        columns_text = ", ".join(columns[:20])
        text_parts.append(f"Columns: {columns_text}")
    
    return " | ".join(text_parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Embedding & Similarity
# ═══════════════════════════════════════════════════════════════════════════════

def build_concept_text(concept: dict) -> str:
    """Build the text to embed for a concept: 'Label: Description + Key Columns'.
    
    Includes column-level information for more accurate semantic matching.
    """
    label = concept.get("label", "")
    desc = concept.get("description", "")
    
    # Extract key columns from metadata for semantic matching
    metadata = concept.get("metadata", {})
    key_columns = metadata.get("key_columns", [])
    
    # Build text with label, description, and column information
    text_parts = [f"{label}: {desc}"]
    
    if key_columns:
        # Handle both list of strings and list of dicts with 'column' field
        col_names = [col if isinstance(col, str) else col.get("column", str(col)) for col in key_columns]
        # Add column names as semantic signals
        columns_text = ", ".join(col_names[:15])  # Limit to first 15 columns to avoid token overflow
        text_parts.append(f"Key columns: {columns_text}")
    
    return " | ".join(text_parts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def compute_concept_similarities(
    concepts1: list[dict],
    concepts2: list[dict],
) -> list[tuple[dict, dict, float]]:
    """Embed all concepts and compute pairwise cross-graph cosine similarity.

    Returns list of (concept1, concept2, similarity_score) sorted descending.
    """
    texts1 = [build_concept_text(c) for c in concepts1]
    texts2 = [build_concept_text(c) for c in concepts2]

    print(f"    Embedding {len(texts1)} + {len(texts2)} concepts…")
    emb_client = get_embedding_client()
    all_embeddings = embed_texts(emb_client, texts1 + texts2)

    emb1 = all_embeddings[: len(texts1)]
    emb2 = all_embeddings[len(texts1) :]

    pairs = []
    for i, c1 in enumerate(concepts1):
        for j, c2 in enumerate(concepts2):
            sim = cosine_similarity(emb1[i], emb2[j])
            pairs.append((c1, c2, sim))

    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


def classify_pairs(
    pairs: list[tuple[dict, dict, float]],
    high_threshold: float = 0.70,
    low_threshold: float = 0.40,
) -> tuple[list, list, list]:
    """Classify concept pairs into auto-linked, llm-candidates, rejected."""
    auto_linked = []
    llm_candidates = []
    rejected = []

    for c1, c2, sim in pairs:
        if sim >= high_threshold:
            auto_linked.append((c1, c2, sim))
        elif sim >= low_threshold:
            llm_candidates.append((c1, c2, sim))
        else:
            rejected.append((c1, c2, sim))

    return auto_linked, llm_candidates, rejected


# ═══════════════════════════════════════════════════════════════════════════════
#  3. LLM Evaluation for Ambiguous Pairs
# ═══════════════════════════════════════════════════════════════════════════════

_LLM_PAIR_PROMPT = """\
You are an ontology expert. Two concept nodes from different airline crew databases are shown below.
Determine if they represent the SAME real-world entity or are semantically related (overlapping domain, shared purpose, or one extends the other).

## Concept A (from {db1})
- Label: {label1}
- Description: {desc1}
- Source Tables: {tables1}
- Key Columns: {columns1}

## Concept B (from {db2})
- Label: {label2}
- Description: {desc2}
- Source Tables: {tables2}
- Key Columns: {columns2}

## Embedding Cosine Similarity
{similarity:.3f} (moderate — needs expert judgement)

## Instructions
**IMPORTANT:** Consider not just the concept names and descriptions, but also analyze the KEY COLUMNS and their semantic meaning.
If the column names and their purposes strongly overlap, this indicates the concepts represent the same or closely related real-world entities.

If these concepts are related, respond with JSON:
{{"verdict": "LINK", "relationship": "<RELATIONSHIP_LABEL>", "reasoning": "<1-2 sentence explanation that mentions column overlap>"}}

Choose a relationship label that best describes the link, e.g.:
OVERLAPS_WITH, EXTENDS, SPECIALIZES, COMPLEMENTS, SAME_DOMAIN, EQUIVALENT

If they are NOT meaningfully related (different domains, no column overlap), respond with:
{{"verdict": "REJECT", "reasoning": "<1-2 sentence explanation>"}}

Respond ONLY with the JSON object, no markdown fences.
"""


def llm_evaluate_pair(
    llm_client,
    c1: dict,
    c2: dict,
    similarity: float,
) -> dict | None:
    """Ask the LLM whether two moderately-similar concepts should be linked.

    Returns edge metadata dict or None if rejected.
    
    Considers column-level metadata for more accurate semantic matching.
    """
    meta1 = c1.get("metadata", {})
    meta2 = c2.get("metadata", {})
    
    tables1 = ", ".join(meta1.get("source_tables", []))
    tables2 = ", ".join(meta2.get("source_tables", []))
    
    # Extract key columns for semantic comparison
    columns1 = meta1.get("key_columns", [])
    columns2 = meta2.get("key_columns", [])
    
    # Handle both list of strings and list of dicts with 'column' field
    col_names1 = [col if isinstance(col, str) else col.get("column", str(col)) for col in columns1]
    col_names2 = [col if isinstance(col, str) else col.get("column", str(col)) for col in columns2]
    
    # Format columns for display (limit to 20 to avoid token overflow)
    columns1_str = ", ".join(col_names1[:20]) if col_names1 else "(none)"
    columns2_str = ", ".join(col_names2[:20]) if col_names2 else "(none)"

    prompt = _LLM_PAIR_PROMPT.format(
        db1=c1.get("database", "?"),
        label1=c1.get("label", ""),
        desc1=c1.get("description", ""),
        tables1=tables1 or "(none)",
        columns1=columns1_str,
        db2=c2.get("database", "?"),
        label2=c2.get("label", ""),
        desc2=c2.get("description", ""),
        tables2=tables2 or "(none)",
        columns2=columns2_str,
        similarity=similarity,
    )

    raw = call_llm(llm_client, prompt, temperature=0.1)
    parsed = parse_llm_json(raw)

    if not parsed or not isinstance(parsed, dict):
        return None

    verdict = parsed.get("verdict", "").upper()
    if verdict == "LINK":
        return {
            "relationship": parsed.get("relationship", "RELATED"),
            "reasoning": parsed.get("reasoning", ""),
        }
    return None


def evaluate_llm_candidates(
    llm_client,
    candidates: list[tuple[dict, dict, float]],
) -> list[tuple[dict, dict, float, dict]]:
    """Run LLM evaluation on all ambiguous pairs. Returns approved pairs."""
    approved = []
    for c1, c2, sim in candidates:
        print(f"      LLM evaluating: {c1['label']} ↔ {c2['label']} (sim={sim:.3f})")
        result = llm_evaluate_pair(llm_client, c1, c2, sim)
        if result:
            print(f"        → LINKED as {result['relationship']}")
            approved.append((c1, c2, sim, result))
        else:
            print(f"        → REJECTED by LLM")
    return approved


# ═══════════════════════════════════════════════════════════════════════════════
#  4. Build Concept Mapping Edges
# ═══════════════════════════════════════════════════════════════════════════════

def build_concept_edges(
    auto_linked: list[tuple[dict, dict, float]],
    llm_approved: list[tuple[dict, dict, float, dict]],
) -> list[dict]:
    """Build cross-graph concept edge dicts from approved pairs.

    Node IDs are namespaced as {database}__{id}.  If a concept's ID is
    already namespaced (starts with ``{database}__``), it is kept as-is
    so that iterative merges don't double-prefix.
    """

    def _namespaced_id(concept: dict) -> str:
        db = concept.get("database", "unknown")
        cid = concept["id"]
        prefix = f"{db}__"
        return cid if cid.startswith(prefix) else f"{prefix}{cid}"

    edges = []

    for c1, c2, sim in auto_linked:
        edges.append({
            "source": _namespaced_id(c1),
            "target": _namespaced_id(c2),
            "relationship": "RELATED",
            "description": f"Cross-graph concept link (embedding similarity {sim:.3f})",
            "similarity_score": round(sim, 4),
            "method": "embedding",
            "llm_reasoning": None,
        })

    for c1, c2, sim, llm_result in llm_approved:
        edges.append({
            "source": _namespaced_id(c1),
            "target": _namespaced_id(c2),
            "relationship": llm_result["relationship"],
            "description": f"Cross-graph concept link (LLM-evaluated, similarity {sim:.3f})",
            "similarity_score": round(sim, 4),
            "method": "llm",
            "llm_reasoning": llm_result.get("reasoning"),
        })

    return edges


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Namespacing & Graph Unification
# ═══════════════════════════════════════════════════════════════════════════════

def namespace_graph(graph: dict) -> dict:
    """Prefix ALL node IDs (entities + concepts) with {database}__ and
    update all edge source/target references to match.

    Uses each node's own ``database`` field as the prefix.  If a node's
    ID already starts with ``{node_database}__`` it is left untouched,
    which makes iterative (N-way) merges safe — IDs from a previous
    unification pass are not double-prefixed.

    Returns a deep-copied, namespaced graph.
    """
    graph_db = graph.get("database", "unknown")

    # Build old_id → new_id mapping (per-node, using node-level database)
    id_map: dict[str, str] = {}
    for node in graph.get("nodes", []):
        old_id = node["id"]
        node_db = node.get("database", graph_db)
        prefix = f"{node_db}__"
        if old_id.startswith(prefix):
            # Already namespaced — keep as-is
            id_map[old_id] = old_id
        else:
            id_map[old_id] = f"{prefix}{old_id}"

    # Namespace nodes
    new_nodes = []
    for node in graph.get("nodes", []):
        n = {**node}
        n["id"] = id_map[node["id"]]
        new_nodes.append(n)

    # Namespace edges
    new_edges = []
    for edge in graph.get("edges", []):
        e = {**edge}
        e["source"] = id_map.get(edge["source"], edge["source"])
        e["target"] = id_map.get(edge["target"], edge["target"])
        new_edges.append(e)

    return {
        **graph,
        "nodes": new_nodes,
        "edges": new_edges,
    }


def unify_graphs(
    graph1: dict,
    graph2: dict,
    concept_edges: list[dict],
) -> dict:
    """Merge two namespaced graphs into a single unified graph."""
    ns1 = namespace_graph(graph1)
    ns2 = namespace_graph(graph2)

    all_nodes = ns1.get("nodes", []) + ns2.get("nodes", [])
    all_edges = ns1.get("edges", []) + ns2.get("edges", []) + concept_edges
    all_rejected = ns1.get("rejected", []) + ns2.get("rejected", [])

    db1 = graph1.get("database", "unknown")
    db2 = graph2.get("database", "unknown")

    entity_count = sum(1 for n in all_nodes if n.get("type") != "concept")
    concept_count = sum(1 for n in all_nodes if n.get("type") == "concept")
    cross_edges = len(concept_edges)
    intra_edges = len(all_edges) - cross_edges

    return {
        "nodes": all_nodes,
        "edges": all_edges,
        "rejected": all_rejected,
        "database": "Unified",
        "metadata": {
            "total_nodes": len(all_nodes),
            "total_entity_nodes": entity_count,
            "total_concept_nodes": concept_count,
            "total_edges": len(all_edges),
            "total_intra_graph_edges": intra_edges,
            "total_cross_graph_edges": cross_edges,
            "total_rejected": len(all_rejected),
            "source_databases": [db1, db2],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  6. Neo4j Loader
# ═══════════════════════════════════════════════════════════════════════════════

def load_to_neo4j(graph: dict, output_dir: str) -> str:
    """Load the unified KG into Neo4j. Clears existing Unified data first.

    Returns a summary string.
    """
    from src.utils.neo4j_helpers import get_neo4j_driver, run_cypher_write

    driver = get_neo4j_driver()
    cypher_log: list[str] = []

    def _run(query: str):
        cypher_log.append(query)
        run_cypher_write(driver, query)

    nodes = graph.get("nodes", [])

    # ── Clear existing data for all source databases ─────────────────────
    source_dbs = set()
    for n in nodes:
        db = n.get("database", "")
        if db:
            source_dbs.add(db)
    source_dbs.add("Unified")

    print("    Clearing existing nodes…")
    for db in sorted(source_dbs):
        _run(f'MATCH (n) WHERE n.database = "{db}" DETACH DELETE n')

    # ── Indexes ──────────────────────────────────────────────────────────
    print("    Creating indexes…")
    _run("CREATE INDEX IF NOT EXISTS FOR (t:Table) ON (t.name)")
    _run("CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.name)")
    edges = graph.get("edges", [])
    node_by_id: dict[str, dict] = {n["id"]: n for n in nodes if "id" in n}

    # Dedup nodes by ID — keep last occurrence (matches dict override semantics)
    _seen_ids: set[str] = set()
    deduped_nodes: list[dict] = []
    for n in reversed(nodes):
        if n.get("id") and n["id"] not in _seen_ids:
            _seen_ids.add(n["id"])
            deduped_nodes.append(n)
    deduped_nodes.reverse()

    table_nodes = [n for n in deduped_nodes if n.get("type", "").endswith("Entity")]
    concept_nodes = [n for n in deduped_nodes if n.get("type") == "concept"]

    # ── Create table nodes ───────────────────────────────────────────────
    print(f"    Creating {len(table_nodes)} table nodes…")
    for n in table_nodes:
        md = n.get("metadata", {})
        src_db = n.get("database", "unknown")
        orig_name = md.get("original_table_name", n["id"])
        desc = (n.get("description", "") or "").replace('"', '\\"').replace("'", "\\'")
        label_text = (n.get("label", "") or "").replace('"', '\\"')
        cols = ", ".join(f'"{c}"' for c in md.get("columns", []))
        pks = ", ".join(f'"{p}"' for p in md.get("primary_keys", []))
        concepts = ", ".join(f'"{c}"' for c in md.get("concepts", []))
        notes = (md.get("notes", "") or "").replace('"', '\\"').replace("'", "\\'")

        stmt = (
            f'MERGE (t:Table:{src_db} {{name: "{orig_name}", database: "{src_db}"}}) '
            f'SET t.label = "{label_text}", '
            f't.description = "{desc}", '
            f't.nodeId = "{n["id"]}", '
            f't.columns = [{cols}], '
            f't.primaryKeys = [{pks}], '
            f't.concepts = [{concepts}], '
            f't.notes = "{notes}"'
        )
        _run(stmt)

    # ── Create concept nodes ─────────────────────────────────────────────
    print(f"    Creating {len(concept_nodes)} concept nodes…")
    for n in concept_nodes:
        md = n.get("metadata", {})
        src_db = n.get("database", "unknown")
        desc = (n.get("description", "") or "").replace('"', '\\"').replace("'", "\\'")
        label_text = (n.get("label", "") or "").replace('"', '\\"')
        src_tables = ", ".join(f'"{t}"' for t in md.get("source_tables", []))
        notes = (md.get("notes", "") or "").replace('"', '\\"').replace("'", "\\'")

        stmt = (
            f'MERGE (c:Concept {{name: "{label_text}", database: "{src_db}"}}) '
            f'SET c.description = "{desc}", '
            f'c.nodeId = "{n["id"]}", '
            f'c.sourceTables = [{src_tables}], '
            f'c.notes = "{notes}"'
        )
        _run(stmt)

    # ── Create edges ─────────────────────────────────────────────────────
    print(f"    Creating {len(edges)} edges…")
    for e in edges:
        src_id = e.get("source", "")
        tgt_id = e.get("target", "")
        rel = e.get("relationship", "RELATED_TO").upper().replace(" ", "_").replace("-", "_")
        desc = (e.get("description", "") or "").replace('"', '\\"').replace("'", "\\'")

        src_node = node_by_id.get(src_id)
        tgt_node = node_by_id.get(tgt_id)
        if not src_node or not tgt_node:
            cypher_log.append(f"-- SKIPPED edge {src_id} → {tgt_id}: node not found")
            continue

        def _match_clause(node: dict, var: str) -> str:
            src_db = node.get("database", "unknown")
            if node.get("type") == "concept":
                lbl = (node.get("label", "") or "").replace('"', '\\"')
                return f'({var}:Concept {{name: "{lbl}", database: "{src_db}"}})'
            else:
                md = node.get("metadata", {})
                orig = md.get("original_table_name", node["id"])
                return f'({var}:Table {{name: "{orig}", database: "{src_db}"}})'

        src_match = _match_clause(src_node, "a")
        tgt_match = _match_clause(tgt_node, "b")

        rel_clean = re.sub(r'[^A-Z0-9_]', '_', rel)
        if not rel_clean:
            rel_clean = "RELATED_TO"

        # For cross-graph edges, include extra metadata properties
        extra_props = f'r.description = "{desc}"'
        if e.get("similarity_score") is not None:
            extra_props += f', r.similarity_score = {e["similarity_score"]}'
        if e.get("method"):
            extra_props += f', r.method = "{e["method"]}"'
        if e.get("llm_reasoning"):
            reason = e["llm_reasoning"].replace('"', '\\"').replace("'", "\\'")
            extra_props += f', r.llm_reasoning = "{reason}"'

        stmt = (
            f'MATCH {src_match}, {tgt_match} '
            f'MERGE (a)-[r:{rel_clean}]->(b) '
            f'SET {extra_props}'
        )
        _run(stmt)

    # ── Derived RELATES_TO from concept source_tables ────────────────────
    table_orig_to_match: dict[str, tuple[str, str]] = {}
    for n in table_nodes:
        md = n.get("metadata", {})
        orig = md.get("original_table_name", n["id"])
        src_db = n.get("database", "unknown")
        table_orig_to_match[orig] = (orig, src_db)

    derived_count = 0
    for n in concept_nodes:
        md = n.get("metadata", {})
        concept_label = (n.get("label", "") or "").replace('"', '\\"')
        concept_db = n.get("database", "unknown")
        for src_tbl in md.get("source_tables", []):
            match = table_orig_to_match.get(src_tbl)
            if not match:
                continue
            orig_name, tbl_db = match
            stmt = (
                f'MATCH (t:Table {{name: "{orig_name}", database: "{tbl_db}"}}), '
                f'(c:Concept {{name: "{concept_label}", database: "{concept_db}"}}) '
                f'MERGE (t)-[:RELATES_TO]->(c)'
            )
            _run(stmt)
            derived_count += 1

    print(f"    Created {derived_count} derived RELATES_TO edges from concept source_tables.")

    driver.close()

    # ── Save Cypher log ──────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    cypher_path = os.path.join(output_dir, "unified_cypher_queries.txt")
    with open(cypher_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(cypher_log))

    return (
        f"Neo4j loaded!\n"
        f"  Table nodes: {len(table_nodes)}\n"
        f"  Concept nodes: {len(concept_nodes)}\n"
        f"  Edges: {len(edges)}\n"
        f"  Derived RELATES_TO: {derived_count}\n"
        f"  Cypher log → {cypher_path}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  6b. Cosmos DB Loader (alternative backend)
# ═══════════════════════════════════════════════════════════════════════════════

def load_to_cosmos(graph: dict, output_dir: str) -> str:
    """Load the unified KG into Cosmos DB (Gremlin API). Clears existing data first.

    Returns a summary string.  Mirrors ``load_to_neo4j`` but emits Gremlin
    queries against the Azure Cosmos DB Gremlin endpoint.
    """
    from src.utils.cosmos_helpers import (
        get_cosmos_client, run_gremlin, close_cosmos_client,
        escape_gremlin, make_vertex_id, serialize_list,
    )

    # Use dedicated container for unified graphs
    client = get_cosmos_client(graph_container="Unified_Knowledge_graph")
    gremlin_log: list[str] = []

    def _run(query: str, ignore_conflict: bool = False, max_retries: int = 3):
        gremlin_log.append(query)
        return run_gremlin(client, query, max_retries=max_retries, ignore_conflict=ignore_conflict)

    # ── Clear existing data ──────────────────────────────────────────────
    # Unified graph may span multiple source databases.  Collect all source
    # databases present in the graph and clear their vertices, plus any
    # vertices already tagged as "Unified".
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    source_dbs = set()
    for n in nodes:
        db = n.get("database", "")
        if db:
            source_dbs.add(db)
    source_dbs.add("Unified")

    print("    Clearing existing vertices in Cosmos DB…")
    for db in sorted(source_dbs):
        # Drop is expensive; use more retries and batch by limit to stay under RU budget
        for _ in range(20):  # iterate until no vertices remain for this db
            remaining = _run(f"g.V().has('database', '{escape_gremlin(db)}').count()")
            if not remaining or remaining[0] == 0:
                break
            _run(f"g.V().has('database', '{escape_gremlin(db)}').limit(50).drop()", max_retries=10)
            import time; time.sleep(2)

    node_by_id: dict[str, dict] = {n["id"]: n for n in nodes if "id" in n}

    # Dedup nodes by ID — keep last occurrence (matches dict override semantics)
    _seen_ids: set[str] = set()
    deduped_nodes: list[dict] = []
    for n in reversed(nodes):
        if n.get("id") and n["id"] not in _seen_ids:
            _seen_ids.add(n["id"])
            deduped_nodes.append(n)
    deduped_nodes.reverse()

    table_nodes = [n for n in deduped_nodes if n.get("type", "").endswith("Entity")]
    concept_nodes = [n for n in deduped_nodes if n.get("type") == "concept"]

    # ── Mapping: JSON node id → Cosmos vertex id ─────────────────────
    cosmos_id_map: dict[str, str] = {}

    # ── Create table vertices ────────────────────────────────────────
    print(f"    Creating {len(table_nodes)} table vertices…")
    for n in table_nodes:
        md = n.get("metadata", {})
        src_db = n.get("database", "unknown")
        orig_name = md.get("original_table_name", n["id"])
        # Use the namespaced ID from the unified JSON if available,
        # otherwise build one from database + original table name.
        vid = f"table__{n['id']}" if "__" in n["id"] else make_vertex_id(src_db, "table", orig_name)
        cosmos_id_map[n["id"]] = vid

        desc = escape_gremlin(n.get("description", "") or "")
        label_text = escape_gremlin(n.get("label", "") or "")
        cols = serialize_list(md.get("columns", []))
        pks = serialize_list(md.get("primary_keys", []))
        concepts_list = serialize_list(md.get("concepts", []))
        notes = escape_gremlin((md.get("notes", "") or ""))

        stmt = (
            f"g.addV('Table')"
            f".property('id', '{escape_gremlin(vid)}')"
            f".property('database', '{escape_gremlin(src_db)}')"
            f".property('name', '{escape_gremlin(orig_name)}')"
            f".property('displayName', '{escape_gremlin(label_text)}')"
            f".property('description', '{escape_gremlin(desc)}')"
            f".property('nodeId', '{escape_gremlin(n['id'])}')"
            f".property('columns', '{escape_gremlin(cols)}')"
            f".property('primaryKeys', '{escape_gremlin(pks)}')"
            f".property('concepts', '{escape_gremlin(concepts_list)}')"
            f".property('notes', '{escape_gremlin(notes)}')"
        )
        _run(stmt, ignore_conflict=True)

    # ── Create concept vertices ──────────────────────────────────────
    print(f"    Creating {len(concept_nodes)} concept vertices…")
    for n in concept_nodes:
        md = n.get("metadata", {})
        src_db = n.get("database", "unknown")
        label_text = n.get("label", "") or ""
        vid = f"concept__{n['id']}" if "__" in n["id"] else make_vertex_id(src_db, "concept", label_text.lower().replace(" ", "_"))
        cosmos_id_map[n["id"]] = vid

        desc = escape_gremlin(n.get("description", "") or "")
        src_tables = serialize_list(md.get("source_tables", []))
        notes = escape_gremlin((md.get("notes", "") or ""))

        stmt = (
            f"g.addV('Concept')"
            f".property('id', '{escape_gremlin(vid)}')"
            f".property('database', '{escape_gremlin(src_db)}')"
            f".property('name', '{escape_gremlin(label_text)}')"
            f".property('displayName', '{escape_gremlin(label_text)}')"
            f".property('description', '{escape_gremlin(desc)}')"
            f".property('nodeId', '{escape_gremlin(n['id'])}')"
            f".property('sourceTables', '{escape_gremlin(src_tables)}')"
            f".property('notes', '{escape_gremlin(notes)}')"
        )
        _run(stmt, ignore_conflict=True)

    # ── Create edges ─────────────────────────────────────────────────
    # Track (src_cosmos_id, rel_type, tgt_cosmos_id) to dedup like Neo4j MERGE
    created_edges: set[tuple[str, str, str]] = set()
    print(f"    Creating {len(edges)} edges…")
    for e in edges:
        src_id = e.get("source", "")
        tgt_id = e.get("target", "")
        rel = e.get("relationship", "RELATED_TO").upper().replace(" ", "_").replace("-", "_")
        desc = escape_gremlin(e.get("description", "") or "")

        src_cosmos_id = cosmos_id_map.get(src_id)
        tgt_cosmos_id = cosmos_id_map.get(tgt_id)
        if not src_cosmos_id or not tgt_cosmos_id:
            gremlin_log.append(f"// SKIPPED edge {src_id} -> {tgt_id}: vertex not found")
            continue

        rel_clean = re.sub(r'[^A-Za-z0-9_]', '_', rel)
        if not rel_clean:
            rel_clean = "RELATED_TO"

        edge_key = (src_cosmos_id, rel_clean, tgt_cosmos_id)
        if edge_key in created_edges:
            gremlin_log.append(f"// DEDUP edge {src_id} -[{rel_clean}]-> {tgt_id}")
            continue
        created_edges.add(edge_key)

        # Build edge properties — include cross-graph metadata when present
        props = f".property('description', '{escape_gremlin(desc)}')"
        if e.get("similarity_score") is not None:
            props += f".property('similarity_score', {e['similarity_score']})"
        if e.get("method"):
            props += f".property('method', '{escape_gremlin(e['method'])}')"
        if e.get("llm_reasoning"):
            props += f".property('llm_reasoning', '{escape_gremlin(e['llm_reasoning'])}')"

        stmt = (
            f"g.V('{escape_gremlin(src_cosmos_id)}')"
            f".addE('{escape_gremlin(rel_clean)}')"
            f".to(g.V('{escape_gremlin(tgt_cosmos_id)}'))"
            f"{props}"
        )
        _run(stmt, ignore_conflict=True)

    # ── Derived RELATES_TO from concept source_tables ────────────────
    table_orig_to_cosmos: dict[str, str] = {}
    for n in table_nodes:
        md = n.get("metadata", {})
        orig = md.get("original_table_name", n["id"])
        table_orig_to_cosmos[orig] = cosmos_id_map.get(n["id"], "")

    derived_count = 0
    for n in concept_nodes:
        md = n.get("metadata", {})
        concept_cosmos_id = cosmos_id_map.get(n["id"], "")
        if not concept_cosmos_id:
            continue
        for src_tbl in md.get("source_tables", []):
            tbl_cosmos_id = table_orig_to_cosmos.get(src_tbl)
            if not tbl_cosmos_id:
                continue
            # Skip if a RELATES_TO already exists for this pair (mirrors Neo4j MERGE dedup)
            edge_key = (tbl_cosmos_id, "RELATES_TO", concept_cosmos_id)
            if edge_key in created_edges:
                continue
            created_edges.add(edge_key)
            stmt = (
                f"g.V('{escape_gremlin(tbl_cosmos_id)}')"
                f".addE('RELATES_TO')"
                f".to(g.V('{escape_gremlin(concept_cosmos_id)}'))"
            )
            _run(stmt, ignore_conflict=True)
            derived_count += 1

    print(f"    Created {derived_count} derived RELATES_TO edges from concept source_tables.")

    close_cosmos_client(client)

    # ── Save Gremlin log ─────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    gremlin_path = os.path.join(output_dir, "unified_gremlin_queries.txt")
    with open(gremlin_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(gremlin_log))

    return (
        f"Cosmos DB loaded!\n"
        f"  Table vertices: {len(table_nodes)}\n"
        f"  Concept vertices: {len(concept_nodes)}\n"
        f"  Edges: {len(edges)}\n"
        f"  Derived RELATES_TO: {derived_count}\n"
        f"  Gremlin log → {gremlin_path}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  7. Markdown Summary
# ═══════════════════════════════════════════════════════════════════════════════

def generate_summary_markdown(
    unified: dict,
    concept_edges: list[dict],
    auto_linked: list[tuple[dict, dict, float]],
    llm_approved: list[tuple[dict, dict, float, dict]],
    rejected_pairs: list[tuple[dict, dict, float]],
    graph1_meta: dict,
    graph2_meta: dict,
) -> str:
    """Generate a comprehensive Markdown summary of the unified graph."""
    meta = unified.get("metadata", {})
    nodes = unified.get("nodes", [])
    edges = unified.get("edges", [])

    # Counts by source database
    db_counts: dict[str, dict] = {}
    for n in nodes:
        db = n.get("database", "unknown")
        ntype = "concept" if n.get("type") == "concept" else "entity"
        db_counts.setdefault(db, {"entity": 0, "concept": 0})
        db_counts[db][ntype] += 1

    # Relationship type distribution
    rel_counts: dict[str, int] = {}
    for e in edges:
        rel = e.get("relationship", "UNKNOWN")
        rel_counts[rel] = rel_counts.get(rel, 0) + 1

    # Build markdown
    lines = [
        "# Unified Knowledge Graph — Summary",
        "",
        f"**Generated at:** {meta.get('generated_at', 'N/A')}  ",
        f"**Source databases:** {', '.join(meta.get('source_databases', []))}",
        "",
        "---",
        "",
        "## Overall Statistics",
        "",
        f"| Metric | Count |",
        f"|--------|-------|",
        f"| Total Nodes | {meta.get('total_nodes', 0)} |",
        f"| Entity Nodes | {meta.get('total_entity_nodes', 0)} |",
        f"| Concept Nodes | {meta.get('total_concept_nodes', 0)} |",
        f"| Total Edges | {meta.get('total_edges', 0)} |",
        f"| Intra-graph Edges | {meta.get('total_intra_graph_edges', 0)} |",
        f"| Cross-graph Edges | {meta.get('total_cross_graph_edges', 0)} |",
        f"| Rejected Tables | {meta.get('total_rejected', 0)} |",
        "",
        "## Nodes by Source Database",
        "",
        "| Database | Entity Nodes | Concept Nodes | Total |",
        "|----------|-------------|---------------|-------|",
    ]

    for db in sorted(db_counts):
        ec = db_counts[db]["entity"]
        cc = db_counts[db]["concept"]
        lines.append(f"| {db} | {ec} | {cc} | {ec + cc} |")

    lines += [
        "",
        "## Source Graph Metadata",
        "",
        f"| Property | {graph1_meta.get('database', 'Graph 1')} | {graph2_meta.get('database', 'Graph 2')} |",
        f"|----------|------|------|",
        f"| Nodes | {graph1_meta.get('total_nodes', '?')} | {graph2_meta.get('total_nodes', '?')} |",
        f"| Edges | {graph1_meta.get('total_edges', '?')} | {graph2_meta.get('total_edges', '?')} |",
        f"| Rejected | {graph1_meta.get('total_rejected', '?')} | {graph2_meta.get('total_rejected', '?')} |",
        "",
        "---",
        "",
        "## Cross-Graph Concept Matching",
        "",
        f"- **Pairs evaluated:** {len(auto_linked) + len(llm_approved) + len(rejected_pairs)}",
        f"- **Auto-linked (similarity ≥ 0.70):** {len(auto_linked)}",
        f"- **LLM-approved (similarity 0.40–0.70):** {len(llm_approved)}",
        f"- **Rejected (similarity < 0.40 or LLM rejected):** {len(rejected_pairs)}",
        "",
    ]

    if auto_linked:
        lines += [
            "### Auto-Linked Concept Pairs (Embedding Similarity ≥ 0.70)",
            "",
            "| # | Concept A | Database A | Concept B | Database B | Similarity |",
            "|---|-----------|------------|-----------|------------|------------|",
        ]
        for i, (c1, c2, sim) in enumerate(auto_linked, 1):
            lines.append(
                f"| {i} | {c1['label']} | {c1.get('database', '?')} "
                f"| {c2['label']} | {c2.get('database', '?')} | {sim:.4f} |"
            )
        lines.append("")

    if llm_approved:
        lines += [
            "### LLM-Approved Concept Pairs (Similarity 0.40–0.70)",
            "",
            "| # | Concept A | Database A | Concept B | Database B | Similarity | Relationship | Reasoning |",
            "|---|-----------|------------|-----------|------------|------------|--------------|-----------|",
        ]
        for i, (c1, c2, sim, result) in enumerate(llm_approved, 1):
            lines.append(
                f"| {i} | {c1['label']} | {c1.get('database', '?')} "
                f"| {c2['label']} | {c2.get('database', '?')} | {sim:.4f} "
                f"| {result['relationship']} | {result.get('reasoning', '')} |"
            )
        lines.append("")

    lines += [
        "---",
        "",
        "## Relationship Type Distribution",
        "",
        "| Relationship | Count |",
        "|-------------|-------|",
    ]
    for rel in sorted(rel_counts, key=rel_counts.get, reverse=True):
        lines.append(f"| {rel} | {rel_counts[rel]} |")

    # List all concept nodes in unified graph
    concept_nodes = [n for n in nodes if n.get("type") == "concept"]
    lines += [
        "",
        "---",
        "",
        "## All Concept Nodes in Unified Graph",
        "",
        "| # | ID | Label | Database | Source Tables |",
        "|---|----|-------|----------|---------------|",
    ]
    for i, c in enumerate(concept_nodes, 1):
        st = ", ".join(c.get("metadata", {}).get("source_tables", []))
        lines.append(f"| {i} | `{c['id']}` | {c['label']} | {c.get('database', '?')} | {st} |")

    lines += ["", "---", ""]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  8. Save Outputs
# ═══════════════════════════════════════════════════════════════════════════════

def save_outputs(
    unified: dict,
    concept_edges: list[dict],
    markdown: str,
    output_dir: str,
):
    """Write all output files."""
    os.makedirs(output_dir, exist_ok=True)

    # Unified KG JSON
    kg_path = os.path.join(output_dir, "unified_kg.json")
    with open(kg_path, "w", encoding="utf-8") as f:
        json.dump(unified, f, indent=2, ensure_ascii=False)
    print(f"    Unified KG → {kg_path}")

    # Concept mapping JSON (for inspection/debugging)
    mapping_path = os.path.join(output_dir, "unified_concept_mapping.json")
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(concept_edges, f, indent=2, ensure_ascii=False)
    print(f"    Concept mapping → {mapping_path}")

    # Markdown summary
    md_path = os.path.join(output_dir, "unified_kg.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    print(f"    Summary → {md_path}")


# ═══════════════════════════════════════════════════════════════════════════════
#  9. Main Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def run_unification(
    graph1_path: str,
    graph2_path: str,
    output_dir: str,
    load_neo4j: bool = False,
    high_threshold: float = 0.70,
    low_threshold: float = 0.40,
    graph_backend: str = "neo4j",
):
    """Run the full graph unification pipeline."""
    # Setup logger with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = setup_agent_logger(f"Unifier_COT_{timestamp}")
    
    logger.info("="*60)
    logger.info("GRAPH UNIFICATION PIPELINE STARTED")
    logger.info(f"Graph 1: {graph1_path}")
    logger.info(f"Graph 2: {graph2_path}")
    logger.info(f"High threshold (auto-link): {high_threshold}")
    logger.info(f"Low threshold (reject): {low_threshold}")
    logger.info(f"Graph backend: {graph_backend}")
    logger.info(f"Load to database: {load_neo4j}")
    logger.info("="*60)
    
    print(f"\n{'═' * 60}")
    print("  Graph Unification via Concept Similarity")
    print(f"{'═' * 60}")

    # ── Step 1: Load graphs ──────────────────────────────────────────────
    logger.info("\n[STEP 1] Loading graphs...")
    print(f"\n  Step 1: Loading graphs…")
    graph1 = load_graph(graph1_path)
    graph2 = load_graph(graph2_path)
    db1 = graph1.get("database", "Graph1")
    db2 = graph2.get("database", "Graph2")
    logger.info(f"Loaded {db1}: {len(graph1.get('nodes', []))} nodes, {len(graph1.get('edges', []))} edges")
    logger.info(f"Loaded {db2}: {len(graph2.get('nodes', []))} nodes, {len(graph2.get('edges', []))} edges")
    logger.debug(f"Graph 1 structure: {list(graph1.keys())}")
    logger.debug(f"Graph 2 structure: {list(graph2.keys())}")
    print(f"    {db1}: {len(graph1.get('nodes', []))} nodes, {len(graph1.get('edges', []))} edges")
    print(f"    {db2}: {len(graph2.get('nodes', []))} nodes, {len(graph2.get('edges', []))} edges")

    # ── Step 2: Extract concepts ─────────────────────────────────────────
    logger.info("\n[STEP 2] Extracting concept nodes...")
    print(f"\n  Step 2: Extracting concepts…")
    concepts1 = extract_concepts(graph1)
    concepts2 = extract_concepts(graph2)
    logger.info(f"Extracted {len(concepts1)} concepts from {db1}")
    logger.info(f"Extracted {len(concepts2)} concepts from {db2}")
    logger.info(f"Total concept pairs to evaluate: {len(concepts1) * len(concepts2)}")
    print(f"    {db1} concepts: {len(concepts1)}")
    print(f"    {db2} concepts: {len(concepts2)}")

    for c in concepts1:
        key_cols = c.get("metadata", {}).get("key_columns", [])
        # Handle both list of strings and list of dicts with 'column' field
        col_names = [col if isinstance(col, str) else col.get("column", str(col)) for col in key_cols]
        cols_preview = ", ".join(col_names[:5]) + ("..." if len(col_names) > 5 else "")
        logger.debug(f"  {db1} concept: {c['label']} (ID: {c['id']}, Columns: [{cols_preview}])")
        print(f"      - {c['label']} ({c['id']})")
    for c in concepts2:
        key_cols = c.get("metadata", {}).get("key_columns", [])
        # Handle both list of strings and list of dicts with 'column' field
        col_names = [col if isinstance(col, str) else col.get("column", str(col)) for col in key_cols]
        cols_preview = ", ".join(col_names[:5]) + ("..." if len(col_names) > 5 else "")
        logger.debug(f"  {db2} concept: {c['label']} (ID: {c['id']}, Columns: [{cols_preview}])")
        print(f"      - {c['label']} ({c['id']})")

    # ── Step 3: Embedding & similarity ───────────────────────────────────
    logger.info("\n[STEP 3] Computing concept similarities via embeddings...")
    logger.info("Chain of Thought: Using semantic embeddings that include concept descriptions AND key column names")
    logger.info("This ensures matching considers both high-level semantics and column-level details")
    logger.info(f"Embedding {len(concepts1)} concepts from {db1}")
    logger.info(f"Embedding {len(concepts2)} concepts from {db2}")
    print(f"\n  Step 3: Computing concept similarities…")
    all_pairs = compute_concept_similarities(concepts1, concepts2)
    logger.info(f"Computed {len(all_pairs)} pairwise similarities")
    logger.debug(f"Top 5 similarities: {[(c1['label'], c2['label'], sim) for c1, c2, sim in all_pairs[:5]]}")
    
    auto_linked, llm_candidates, rejected_pairs = classify_pairs(
        all_pairs, high_threshold, low_threshold
    )
    logger.info(f"Classification complete:")
    logger.info(f"  Auto-linked (similarity >= {high_threshold}): {len(auto_linked)} pairs")
    logger.info(f"  LLM candidates (similarity {low_threshold}–{high_threshold}): {len(llm_candidates)} pairs")
    logger.info(f"  Rejected (similarity < {low_threshold}): {len(rejected_pairs)} pairs")
    print(f"    Auto-linked (≥ {high_threshold}): {len(auto_linked)}")
    print(f"    LLM candidates ({low_threshold}–{high_threshold}): {len(llm_candidates)}")
    print(f"    Rejected (< {low_threshold}): {len(rejected_pairs)}")

    if auto_linked:
        logger.info("Auto-linked concept pairs:")
        print(f"\n    Auto-linked pairs:")
        for c1, c2, sim in auto_linked:
            logger.info(f"  LINK: {c1['label']} ({db1}) ↔ {c2['label']} ({db2}) [similarity={sim:.4f}]")
            print(f"      {c1['label']} ↔ {c2['label']}  (sim={sim:.4f})")

    # ── Step 4: LLM evaluation of ambiguous pairs ────────────────────────
    llm_approved = []
    if llm_candidates:
        logger.info(f"\n[STEP 4] LLM evaluation of {len(llm_candidates)} ambiguous pairs...")
        logger.info("Chain of Thought: Pairs with moderate similarity require expert judgement")
        logger.info("LLM will evaluate semantic relatedness, domain overlap, specialization, AND column overlap")
        logger.info("Column-level analysis helps determine if concepts represent the same real-world entity")
        print(f"\n  Step 4: LLM evaluation of {len(llm_candidates)} ambiguous pairs…")
        llm_client = get_llm_client()
        llm_approved = evaluate_llm_candidates(llm_client, llm_candidates)
        logger.info(f"LLM evaluation complete: {len(llm_approved)} approved, {len(llm_candidates) - len(llm_approved)} rejected")
        for c1, c2, sim, result in llm_approved:
            logger.info(f"  APPROVED: {c1['label']} ↔ {c2['label']} [{result['relationship']}]")
            logger.info(f"    Reasoning: {result.get('reasoning', 'N/A')}")
        print(f"    LLM approved: {len(llm_approved)}")
        # Count LLM-rejected pairs and add to rejected list
        llm_rejected_count = len(llm_candidates) - len(llm_approved)
        logger.info(f"LLM rejected {llm_rejected_count} pairs as not meaningfully related")
        print(f"    LLM rejected: {llm_rejected_count}")
    else:
        logger.info("\n[STEP 4] No LLM candidates - all pairs either auto-linked or rejected")
        print(f"\n  Step 4: No LLM candidates — skipping.")

    # ── Step 5: Build concept edges ──────────────────────────────────────
    logger.info("\n[STEP 5] Building cross-graph concept edges...")
    logger.info(f"Chain of Thought: Creating edges to link related concepts across {db1} and {db2}")
    logger.info(f"  Embedding-based edges: {len(auto_linked)}")
    logger.info(f"  LLM-evaluated edges: {len(llm_approved)}")
    print(f"\n  Step 5: Building cross-graph concept edges…")
    concept_edges = build_concept_edges(auto_linked, llm_approved)
    logger.info(f"Created {len(concept_edges)} cross-graph edges")
    for edge in concept_edges:
        logger.debug(f"  Edge: {edge['source']} -[{edge['relationship']}]-> {edge['target']} (method={edge.get('method')})")
    print(f"    Cross-graph edges created: {len(concept_edges)}")

    # ── Step 6: Unify graphs ─────────────────────────────────────────────
    logger.info("\n[STEP 6] Unifying graphs...")
    logger.info("Chain of Thought: Namespacing node IDs to prevent collisions, merging nodes/edges")
    print(f"\n  Step 6: Unifying graphs…")
    unified = unify_graphs(graph1, graph2, concept_edges)
    meta = unified["metadata"]
    logger.info(f"Unified graph statistics:")
    logger.info(f"  Total nodes: {meta['total_nodes']} ({meta['total_entity_nodes']} entities + {meta['total_concept_nodes']} concepts)")
    logger.info(f"  Total edges: {meta['total_edges']} ({meta['total_intra_graph_edges']} intra-graph + {meta['total_cross_graph_edges']} cross-graph)")
    logger.info(f"  Source databases: {meta['source_databases']}")
    print(f"    Total nodes: {meta['total_nodes']}")
    print(f"    Total edges: {meta['total_edges']}")
    print(f"    Cross-graph edges: {meta['total_cross_graph_edges']}")

    # ── Step 7: Generate markdown summary ────────────────────────────────
    print(f"\n  Step 7: Generating summary…")
    # Gather rejected pairs including LLM-rejected ones
    all_rejected_pairs = list(rejected_pairs)
    llm_approved_set = {(c1["id"], c2["id"]) for c1, c2, _, _ in llm_approved}
    for c1, c2, sim in llm_candidates:
        if (c1["id"], c2["id"]) not in llm_approved_set:
            all_rejected_pairs.append((c1, c2, sim))

    markdown = generate_summary_markdown(
        unified=unified,
        concept_edges=concept_edges,
        auto_linked=auto_linked,
        llm_approved=llm_approved,
        rejected_pairs=all_rejected_pairs,
        graph1_meta={**graph1.get("metadata", {}), "database": db1},
        graph2_meta={**graph2.get("metadata", {}), "database": db2},
    )

    # ── Step 8: Save outputs ─────────────────────────────────────────────
    print(f"\n  Step 8: Saving outputs…")
    save_outputs(unified, concept_edges, markdown, output_dir)

    # ── Step 9: Graph DB loading ──────────────────────────────────────────
    if load_neo4j:
        if graph_backend == "cosmos":
            logger.info("\n[STEP 9] Loading unified graph into Cosmos DB (Gremlin API)...")
            print(f"\n  Step 9: Loading into Cosmos DB (Gremlin)…")
            try:
                cosmos_result = load_to_cosmos(unified, output_dir)
                logger.info(f"Cosmos DB load successful: {cosmos_result}")
                print(f"    {cosmos_result}")
            except Exception as e:
                logger.error(f"Cosmos DB loading failed: {type(e).__name__}: {e}")
                print(f"    ⚠️  Cosmos DB loading failed: {type(e).__name__}: {e}")
                print("    (Run without --load-neo4j / --load-cosmos to skip this step.)")
        else:
            logger.info("\n[STEP 9] Loading unified graph into Neo4j...")
            print(f"\n  Step 9: Loading into Neo4j…")
            try:
                neo4j_result = load_to_neo4j(unified, output_dir)
                logger.info(f"Neo4j load successful: {neo4j_result}")
                print(f"    {neo4j_result}")
            except Exception as e:
                logger.error(f"Neo4j loading failed: {type(e).__name__}: {e}")
                print(f"    ⚠️  Neo4j loading failed: {type(e).__name__}: {e}")
                print("    (Run without --load-neo4j to skip this step.)")
    else:
        logger.info("\n[STEP 9] Skipping graph DB loading (not requested)")
        print(f"\n  Step 9: Skipping graph DB loading (use --load-neo4j or --load-cosmos to enable).")

    logger.info("\n" + "="*60)
    logger.info("GRAPH UNIFICATION PIPELINE COMPLETE")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Final statistics:")
    logger.info(f"  - Total nodes: {meta['total_nodes']}")
    logger.info(f"  - Total edges: {meta['total_edges']}")
    logger.info(f"  - Cross-graph concept links: {meta['total_cross_graph_edges']}")
    logger.info(f"  - Source databases unified: {', '.join(meta['source_databases'])}")
    logger.info("="*60)
    
    print(f"\n{'═' * 60}")
    print(f"  Unification complete!")
    print(f"  Output → {output_dir}")
    print(f"{'═' * 60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Unify concept graphs via embedding-based concept matching. By default, auto-discovers and unifies all graphs in output folder."
    )
    parser.add_argument(
        "--graph1",
        default=None,
        help="Path to first concept graph JSON (optional, auto-discovers if not provided)",
    )
    parser.add_argument(
        "--graph2",
        default=None,
        help="Path to second concept graph JSON (optional, auto-discovers if not provided)",
    )
    parser.add_argument(
        "--data-dir",
        default="output",
        help="Directory to auto-discover concept graph JSON files (default: output)",
    )
    parser.add_argument(
        "--output-dir",
        default="unified_output",
        help="Output directory (default: unified_output)",
    )
    parser.add_argument(
        "--load-neo4j",
        action="store_true",
        default=False,
        help="Load the unified graph into Neo4j after unification",
    )
    parser.add_argument(
        "--load-cosmos",
        action="store_true",
        default=False,
        help="Load the unified graph into Cosmos DB (Gremlin) after unification",
    )
    parser.add_argument(
        "--graph-backend",
        choices=["neo4j", "cosmos"],
        default=None,
        help="Which graph database backend to load into (overrides --load-neo4j/--load-cosmos)",
    )
    parser.add_argument(
        "--load-only",
        default=None,
        metavar="JSON_PATH",
        help="Skip unification: load an existing unified JSON directly into the graph DB "
             "(e.g. --load-only unified_output/unified_kg.json)",
    )
    parser.add_argument(
        "--high-threshold",
        type=float,
        default=0.70,
        help="Cosine similarity threshold for auto-linking (default: 0.70)",
    )
    parser.add_argument(
        "--low-threshold",
        type=float,
        default=0.40,
        help="Cosine similarity threshold below which pairs are rejected (default: 0.40)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt and proceed automatically",
    )
    args = parser.parse_args()

    # Resolve graph backend: --graph-backend takes priority, then --load-cosmos / --load-neo4j
    if args.graph_backend:
        backend = args.graph_backend
        should_load = args.load_neo4j or args.load_cosmos or (args.load_only is not None)
    elif args.load_cosmos:
        backend = "cosmos"
        should_load = True
    else:
        backend = "neo4j"
        should_load = args.load_neo4j

    if args.load_only:
        # Load-only mode: read existing JSON and push to graph DB
        print(f"\n{'═' * 60}")
        print(f"  Graph Unification — Load-Only Mode ({backend})")
        print(f"  JSON: {args.load_only}")
        print(f"{'═' * 60}")
        with open(args.load_only, encoding="utf-8") as f:
            kg_dict = json.load(f)
        nodes = kg_dict.get("nodes", [])
        edges = kg_dict.get("edges", [])
        print(f"  Loaded: {len(nodes)} nodes, {len(edges)} edges")
        try:
            if backend == "cosmos":
                result = load_to_cosmos(kg_dict, args.output_dir)
            else:
                result = load_to_neo4j(kg_dict, args.output_dir)
            print(f"  {result}")
        except Exception as e:
            print(f"  ⚠️  {backend} loading failed: {type(e).__name__}: {e}")
        print(f"\n{'═' * 60}")
        print(f"  Load complete!")
        print(f"{'═' * 60}\n")
        return

    # Auto-discovery mode: discover all graphs in data_dir
    if args.graph1 is None or args.graph2 is None:
        print(f"\n{'═' * 60}")
        print("  AUTO-DISCOVERY MODE")
        print(f"{'═' * 60}")
        print(f"  Searching for concept graphs in: {args.data_dir}")
        print()
        
        discovered_graphs = discover_concept_graphs(args.data_dir)
        
        if len(discovered_graphs) == 0:
            print(f"  ❌ Error: No concept graph files found in '{args.data_dir}'")
            print(f"     Expected files matching pattern: *_concept_graph.json")
            print()
            print(f"  To generate concept graphs, run:")
            print(f"     python -m src.agents.advanced_graph_builder_agent --database all --skip-neo4j")
            print()
            sys.exit(1)
        
        if len(discovered_graphs) == 1:
            print(f"  ⚠️  Only 1 concept graph found: {Path(discovered_graphs[0]).name}")
            print(f"     Need at least 2 graphs to unify.")
            print()
            sys.exit(1)
        
        # Display discovered graphs
        print(f"  📊 Discovered {len(discovered_graphs)} concept graphs:")
        print()
        for i, graph_path in enumerate(discovered_graphs, 1):
            graph_name = Path(graph_path).stem.replace("_concept_graph", "")
            try:
                with open(graph_path, 'r', encoding='utf-8') as f:
                    graph_data = json.load(f)
                    node_count = len(graph_data.get('nodes', []))
                    edge_count = len(graph_data.get('edges', []))
                    print(f"     {i}. {graph_name:20s} ({node_count:4d} nodes, {edge_count:4d} edges)")
            except Exception:
                print(f"     {i}. {graph_name:20s} (error reading file)")
        print()
        
        # Confirmation prompt
        if not args.yes:
            print(f"  These graphs will be unified pairwise into a single unified graph.")
            print(f"  Output directory: {args.output_dir}")
            if should_load:
                print(f"  Graph will be loaded to: {backend.upper()}")
            print()
            response = input("  Proceed with unification? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                print("  ❌ Unification cancelled.")
                print()
                sys.exit(0)
            print()
        
        # Unify all graphs pairwise
        print(f"{'═' * 60}")
        print("  UNIFYING GRAPHS")
        print(f"{'═' * 60}")
        print()
        
        # Start with first two graphs
        current_unified = None
        for i in range(len(discovered_graphs)):
            if i == 0:
                # First pair
                print(f"  [{i+1}/{len(discovered_graphs)-1}] Unifying: {Path(discovered_graphs[0]).stem.replace('_concept_graph', '')} + {Path(discovered_graphs[1]).stem.replace('_concept_graph', '')}")
                print()
                
                # For first iteration, save to a temp location
                temp_output = os.path.join(args.output_dir, "temp_unified_kg.json")
                os.makedirs(args.output_dir, exist_ok=True)
                
                run_unification(
                    graph1_path=discovered_graphs[0],
                    graph2_path=discovered_graphs[1],
                    output_dir=args.output_dir,
                    load_neo4j=False,  # Don't load yet
                    high_threshold=args.high_threshold,
                    low_threshold=args.low_threshold,
                    graph_backend=backend,
                )
                
                # Move the result to temp location for next iteration
                unified_kg_path = os.path.join(args.output_dir, "unified_kg.json")
                if os.path.exists(unified_kg_path):
                    os.rename(unified_kg_path, temp_output)
                    current_unified = temp_output
                
            elif i < len(discovered_graphs) - 1:
                # Subsequent pairs: unified + next graph
                next_graph_name = Path(discovered_graphs[i+1]).stem.replace('_concept_graph', '')
                print(f"\n  [{i+1}/{len(discovered_graphs)-1}] Adding: {next_graph_name}")
                print()
                
                run_unification(
                    graph1_path=current_unified,
                    graph2_path=discovered_graphs[i+1],
                    output_dir=args.output_dir,
                    load_neo4j=False,  # Don't load yet
                    high_threshold=args.high_threshold,
                    low_threshold=args.low_threshold,
                    graph_backend=backend,
                )
                
                # Update temp file
                unified_kg_path = os.path.join(args.output_dir, "unified_kg.json")
                temp_output = os.path.join(args.output_dir, "temp_unified_kg.json")
                if os.path.exists(unified_kg_path):
                    if os.path.exists(temp_output):
                        os.remove(temp_output)
                    os.rename(unified_kg_path, temp_output)
                    current_unified = temp_output
        
        # Restore final unified graph
        temp_output = os.path.join(args.output_dir, "temp_unified_kg.json")
        unified_kg_path = os.path.join(args.output_dir, "unified_kg.json")
        if os.path.exists(temp_output):
            if os.path.exists(unified_kg_path):
                os.remove(unified_kg_path)
            os.rename(temp_output, unified_kg_path)
        
        # Load to database if requested
        if should_load and os.path.exists(unified_kg_path):
            print(f"\n{'═' * 60}")
            print(f"  LOADING TO {backend.upper()}")
            print(f"{'═' * 60}")
            print()
            
            with open(unified_kg_path, 'r', encoding='utf-8') as f:
                unified_kg = json.load(f)
            
            try:
                if backend == "cosmos":
                    result = load_to_cosmos(unified_kg, args.output_dir)
                else:
                    result = load_to_neo4j(unified_kg, args.output_dir)
                print(f"  {result}")
            except Exception as e:
                print(f"  ⚠️  {backend} loading failed: {type(e).__name__}: {e}")
        
        print(f"\n{'═' * 60}")
        print("  ALL GRAPHS UNIFIED SUCCESSFULLY")
        print(f"{'═' * 60}")
        print(f"  Output: {args.output_dir}/unified_kg.json")
        print(f"{'═' * 60}\n")
        
    else:
        # Manual mode: use specified graph1 and graph2
        run_unification(
            graph1_path=args.graph1,
            graph2_path=args.graph2,
            output_dir=args.output_dir,
            load_neo4j=should_load,
            high_threshold=args.high_threshold,
            low_threshold=args.low_threshold,
            graph_backend=backend,
        )


if __name__ == "__main__":
    main()
