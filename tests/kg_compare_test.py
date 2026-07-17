"""
kg_compare_test.py — Compare the Knowledge Graph loaded in Neo4j vs Cosmos DB (Gremlin).

Runs a battery of structural checks on both backends and reports mismatches.

Usage:
    python kg_compare_test.py
"""

import json
import sys
import time
from collections import Counter

from src.utils.neo4j_helpers import get_neo4j_driver, run_cypher
from src.utils.cosmos_helpers import get_cosmos_client, run_gremlin, close_cosmos_client

# Delay (seconds) between heavy Cosmos queries to stay under 400 RU/s
COSMOS_QUERY_DELAY = 2


# ═══════════════════════════════════════════════════════════════════════════════
#  Neo4j queries
# ═══════════════════════════════════════════════════════════════════════════════

def neo4j_table_nodes(driver) -> list[dict]:
    """Return all Table nodes sorted by (database, name)."""
    rows = run_cypher(driver, (
        "MATCH (t:Table) "
        "RETURN t.name AS name, t.database AS database, t.label AS label, "
        "       t.nodeId AS nodeId, t.description AS description "
        "ORDER BY t.database, t.name"
    ))
    return rows


def neo4j_concept_nodes(driver) -> list[dict]:
    """Return all Concept nodes sorted by (database, name)."""
    rows = run_cypher(driver, (
        "MATCH (c:Concept) "
        "RETURN c.name AS name, c.database AS database, "
        "       c.nodeId AS nodeId, c.description AS description "
        "ORDER BY c.database, c.name"
    ))
    return rows


def neo4j_edges(driver) -> list[dict]:
    """Return all edges as (src_name, src_db, rel_type, tgt_name, tgt_db)."""
    rows = run_cypher(driver, (
        "MATCH (a)-[r]->(b) "
        "RETURN coalesce(a.name, a.nodeId) AS src_name, a.database AS src_db, "
        "       type(r) AS rel_type, "
        "       coalesce(b.name, b.nodeId) AS tgt_name, b.database AS tgt_db "
        "ORDER BY src_db, src_name, rel_type, tgt_db, tgt_name"
    ))
    return rows


def neo4j_counts(driver) -> dict:
    """Return basic counts."""
    total_nodes = run_cypher(driver, "MATCH (n) RETURN count(n) AS cnt")[0]["cnt"]
    table_count = run_cypher(driver, "MATCH (t:Table) RETURN count(t) AS cnt")[0]["cnt"]
    concept_count = run_cypher(driver, "MATCH (c:Concept) RETURN count(c) AS cnt")[0]["cnt"]
    edge_count = run_cypher(driver, "MATCH ()-[r]->() RETURN count(r) AS cnt")[0]["cnt"]
    return {
        "total_nodes": total_nodes,
        "table_nodes": table_count,
        "concept_nodes": concept_count,
        "total_edges": edge_count,
    }


def neo4j_databases(driver) -> list[str]:
    """Return distinct database values across all nodes."""
    rows = run_cypher(driver, "MATCH (n) RETURN DISTINCT n.database AS db ORDER BY db")
    return [r["db"] for r in rows if r["db"]]


def neo4j_rel_type_counts(driver) -> dict[str, int]:
    """Return edge counts grouped by relationship type."""
    rows = run_cypher(driver, (
        "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt ORDER BY rel"
    ))
    return {r["rel"]: r["cnt"] for r in rows}


def neo4j_node_degree(driver) -> dict[str, int]:
    """Return total degree (inE + outE) per node, keyed by 'database::name'.
    Uses directed counts so self-loops are counted the same way as Gremlin bothE()."""
    rows = run_cypher(driver, (
        "MATCH (n) "
        "OPTIONAL MATCH (n)-[r_out]->() "
        "WITH n, count(r_out) AS out_deg "
        "OPTIONAL MATCH (n)<-[r_in]-() "
        "WITH n, out_deg, count(r_in) AS in_deg "
        "RETURN n.database AS db, coalesce(n.name, n.nodeId) AS name, "
        "       out_deg + in_deg AS degree "
        "ORDER BY db, name"
    ))
    return {f"{r['db']}::{r['name']}": r["degree"] for r in rows}


# ═══════════════════════════════════════════════════════════════════════════════
#  Cosmos DB (Gremlin) queries
# ═══════════════════════════════════════════════════════════════════════════════

def _gremlin_prop(vertex: dict, key: str) -> str:
    """Extract a scalar property from a Gremlin valueMap vertex."""
    val = vertex.get(key, "")
    if isinstance(val, list):
        return val[0] if val else ""
    return val or ""


def cosmos_table_nodes(client) -> list[dict]:
    """Return all Table vertices sorted by (database, name)."""
    raw = run_gremlin(client, "g.V().hasLabel('Table').valueMap(true)")
    rows = []
    for v in raw:
        rows.append({
            "name": _gremlin_prop(v, "name"),
            "database": _gremlin_prop(v, "database"),
            "label": _gremlin_prop(v, "displayName"),  # Cosmos stores it as displayName
            "nodeId": _gremlin_prop(v, "nodeId"),
            "description": _gremlin_prop(v, "description"),
        })
    rows.sort(key=lambda r: (r["database"], r["name"]))
    return rows


def cosmos_concept_nodes(client) -> list[dict]:
    """Return all Concept vertices sorted by (database, name)."""
    raw = run_gremlin(client, "g.V().hasLabel('Concept').valueMap(true)")
    rows = []
    for v in raw:
        rows.append({
            "name": _gremlin_prop(v, "name"),
            "database": _gremlin_prop(v, "database"),
            "nodeId": _gremlin_prop(v, "nodeId"),
            "description": _gremlin_prop(v, "description"),
        })
    rows.sort(key=lambda r: (r["database"], r["name"]))
    return rows


def cosmos_edges(client) -> list[dict]:
    """Return all edges as (src_name, src_db, rel_type, tgt_name, tgt_db)."""
    raw = run_gremlin(client, (
        "g.E().project('src_id','rel','tgt_id')"
        ".by(outV().id())"
        ".by(label())"
        ".by(inV().id())"
    ))
    time.sleep(COSMOS_QUERY_DELAY)
    # We need vertex name/db lookup — build from all vertices
    vtx_raw = run_gremlin(client, "g.V().project('vid','name','db').by(id()).by(values('name')).by(values('database'))")
    time.sleep(COSMOS_QUERY_DELAY)
    vtx_map = {v["vid"]: v for v in vtx_raw}

    rows = []
    for e in raw:
        src_v = vtx_map.get(e["src_id"], {})
        tgt_v = vtx_map.get(e["tgt_id"], {})
        rows.append({
            "src_name": src_v.get("name", ""),
            "src_db": src_v.get("db", ""),
            "rel_type": e["rel"],
            "tgt_name": tgt_v.get("name", ""),
            "tgt_db": tgt_v.get("db", ""),
        })
    rows.sort(key=lambda r: (r["src_db"], r["src_name"], r["rel_type"], r["tgt_db"], r["tgt_name"]))
    return rows


def cosmos_counts(client) -> dict:
    """Return basic counts."""
    total_nodes = run_gremlin(client, "g.V().count()")[0]
    time.sleep(COSMOS_QUERY_DELAY)
    table_count = run_gremlin(client, "g.V().hasLabel('Table').count()")[0]
    time.sleep(COSMOS_QUERY_DELAY)
    concept_count = run_gremlin(client, "g.V().hasLabel('Concept').count()")[0]
    time.sleep(COSMOS_QUERY_DELAY)
    edge_count = run_gremlin(client, "g.E().count()")[0]
    time.sleep(COSMOS_QUERY_DELAY)
    return {
        "total_nodes": total_nodes,
        "table_nodes": table_count,
        "concept_nodes": concept_count,
        "total_edges": edge_count,
    }


def cosmos_databases(client) -> list[str]:
    """Return distinct database values across all vertices."""
    raw = run_gremlin(client, "g.V().values('database').dedup().order()")
    return [str(d) for d in raw if d]


def cosmos_rel_type_counts(client) -> dict[str, int]:
    """Return edge counts grouped by relationship label."""
    raw = run_gremlin(client, "g.E().groupCount().by(label())")
    if raw and isinstance(raw[0], dict):
        return {k: v for k, v in sorted(raw[0].items())}
    return {}


def cosmos_node_degree(client) -> dict[str, int]:
    """Return total degree per vertex, keyed by 'database::name'."""
    raw = run_gremlin(client, (
        "g.V().project('db','name','degree')"
        ".by(values('database'))"
        ".by(values('name'))"
        ".by(bothE().count())"
    ))
    return {f"{r['db']}::{r['name']}": r["degree"] for r in raw}


# ═══════════════════════════════════════════════════════════════════════════════
#  Comparison helpers
# ═══════════════════════════════════════════════════════════════════════════════

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

results: list[tuple[str, str, str]] = []  # (test_name, status, detail)


def check(name: str, passed: bool, detail: str = ""):
    status = PASS if passed else FAIL
    results.append((name, "PASS" if passed else "FAIL", detail))
    tag = status
    print(f"  [{tag}] {name}" + (f"  — {detail}" if detail else ""))


def compare_sets(name: str, neo4j_set: set, cosmos_set: set):
    """Compare two sets and report differences."""
    only_neo4j = neo4j_set - cosmos_set
    only_cosmos = cosmos_set - neo4j_set
    if not only_neo4j and not only_cosmos:
        check(name, True, f"{len(neo4j_set)} items match")
    else:
        parts = []
        if only_neo4j:
            items = sorted(only_neo4j)[:10]
            parts.append(f"only in Neo4j ({len(only_neo4j)}): {items}")
        if only_cosmos:
            items = sorted(only_cosmos)[:10]
            parts.append(f"only in Cosmos ({len(only_cosmos)}): {items}")
        check(name, False, "; ".join(parts))


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═" * 70)
    print("  KG Compare Test — Neo4j vs Cosmos DB")
    print("═" * 70)

    # ── Connect ──────────────────────────────────────────────────────────
    print("\n  Connecting to Neo4j…")
    driver = get_neo4j_driver()
    print("  Connecting to Cosmos DB…")
    client = get_cosmos_client()

    # ══════════════════════════════════════════════════════════════════════
    #  1. Count checks
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 1. Count Checks ─────────────────────────────────────────")
    n4j_counts = neo4j_counts(driver)
    cos_counts = cosmos_counts(client)

    print(f"\n  {'Metric':<20} {'Neo4j':>8} {'Cosmos':>8} {'Match':>8}")
    print(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*8}")
    for key in ["total_nodes", "table_nodes", "concept_nodes", "total_edges"]:
        n = n4j_counts[key]
        c = cos_counts[key]
        match = "✓" if n == c else "✗"
        print(f"  {key:<20} {n:>8} {c:>8} {match:>8}")

    check("Total node count", n4j_counts["total_nodes"] == cos_counts["total_nodes"],
          f"Neo4j={n4j_counts['total_nodes']}, Cosmos={cos_counts['total_nodes']}")
    check("Table node count", n4j_counts["table_nodes"] == cos_counts["table_nodes"],
          f"Neo4j={n4j_counts['table_nodes']}, Cosmos={cos_counts['table_nodes']}")
    check("Concept node count", n4j_counts["concept_nodes"] == cos_counts["concept_nodes"],
          f"Neo4j={n4j_counts['concept_nodes']}, Cosmos={cos_counts['concept_nodes']}")
    check("Total edge count", n4j_counts["total_edges"] == cos_counts["total_edges"],
          f"Neo4j={n4j_counts['total_edges']}, Cosmos={cos_counts['total_edges']}")

    # ══════════════════════════════════════════════════════════════════════
    #  2. Database partition check
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 2. Database Partitions ──────────────────────────────────")
    n4j_dbs = neo4j_databases(driver)
    time.sleep(COSMOS_QUERY_DELAY)
    cos_dbs = cosmos_databases(client)
    print(f"  Neo4j databases:  {n4j_dbs}")
    print(f"  Cosmos databases: {cos_dbs}")
    compare_sets("Database partitions", set(n4j_dbs), set(cos_dbs))

    # ══════════════════════════════════════════════════════════════════════
    #  3. Table node identity check
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 3. Table Node Identity ──────────────────────────────────")
    n4j_tables = neo4j_table_nodes(driver)
    time.sleep(COSMOS_QUERY_DELAY)
    cos_tables = cosmos_table_nodes(client)

    n4j_table_keys = {(r["database"], r["name"]) for r in n4j_tables}
    cos_table_keys = {(r["database"], r["name"]) for r in cos_tables}
    compare_sets("Table nodes (database, name)", n4j_table_keys, cos_table_keys)

    # Also check labels match
    n4j_table_labels = {(r["database"], r["name"]): r["label"] for r in n4j_tables}
    cos_table_labels = {(r["database"], r["name"]): r["label"] for r in cos_tables}
    label_mismatches = []
    for key in n4j_table_keys & cos_table_keys:
        n_label = (n4j_table_labels.get(key) or "").strip()
        c_label = (cos_table_labels.get(key) or "").strip()
        if n_label != c_label:
            label_mismatches.append((key, n_label, c_label))
    check("Table node labels match", len(label_mismatches) == 0,
          f"{len(label_mismatches)} mismatches" + (f": {label_mismatches[:5]}" if label_mismatches else ""))

    # ══════════════════════════════════════════════════════════════════════
    #  4. Concept node identity check
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 4. Concept Node Identity ────────────────────────────────")
    n4j_concepts = neo4j_concept_nodes(driver)
    time.sleep(COSMOS_QUERY_DELAY)
    cos_concepts = cosmos_concept_nodes(client)

    n4j_concept_keys = {(r["database"], r["name"]) for r in n4j_concepts}
    cos_concept_keys = {(r["database"], r["name"]) for r in cos_concepts}
    compare_sets("Concept nodes (database, name)", n4j_concept_keys, cos_concept_keys)

    # ══════════════════════════════════════════════════════════════════════
    #  5. Relationship type distribution
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 5. Relationship Type Distribution ──────────────────────")
    n4j_rel_counts = neo4j_rel_type_counts(driver)
    time.sleep(COSMOS_QUERY_DELAY)
    cos_rel_counts = cosmos_rel_type_counts(client)

    all_rels = sorted(set(n4j_rel_counts) | set(cos_rel_counts))
    print(f"\n  {'Relationship':<30} {'Neo4j':>8} {'Cosmos':>8} {'Match':>8}")
    print(f"  {'─'*30} {'─'*8} {'─'*8} {'─'*8}")
    for rel in all_rels:
        n = n4j_rel_counts.get(rel, 0)
        c = cos_rel_counts.get(rel, 0)
        match = "✓" if n == c else "✗"
        print(f"  {rel:<30} {n:>8} {c:>8} {match:>8}")

    check("Relationship types same", set(n4j_rel_counts) == set(cos_rel_counts),
          f"Neo4j has {len(n4j_rel_counts)} types, Cosmos has {len(cos_rel_counts)} types")
    check("Relationship counts match", n4j_rel_counts == cos_rel_counts,
          f"{sum(1 for r in all_rels if n4j_rel_counts.get(r, 0) != cos_rel_counts.get(r, 0))} types differ")

    # ══════════════════════════════════════════════════════════════════════
    #  6. Edge-level comparison (src, rel, tgt)
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 6. Edge-Level Comparison ────────────────────────────────")
    n4j_edges_list = neo4j_edges(driver)
    time.sleep(COSMOS_QUERY_DELAY)
    cos_edges_list = cosmos_edges(client)

    def edge_key(e):
        return (e["src_db"], e["src_name"], e["rel_type"], e["tgt_db"], e["tgt_name"])

    n4j_edge_set = set(edge_key(e) for e in n4j_edges_list)
    cos_edge_set = set(edge_key(e) for e in cos_edges_list)
    compare_sets("Edges (src, rel, tgt)", n4j_edge_set, cos_edge_set)

    # ══════════════════════════════════════════════════════════════════════
    #  7. Node degree comparison
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 7. Node Degree (Connectivity) ──────────────────────────")
    n4j_degrees = neo4j_node_degree(driver)
    time.sleep(COSMOS_QUERY_DELAY * 2)  # extra pause before heavy query
    try:
        cos_degrees = cosmos_node_degree(client)
    except Exception as exc:
        print(f"  [{WARN}] Cosmos degree query failed (likely 429 throttling): {type(exc).__name__}")
        cos_degrees = None

    if cos_degrees is None:
        check("Node degree match", False, "Cosmos degree query failed (429 throttling)")
    else:
        degree_mismatches = []
        all_node_keys = set(n4j_degrees) | set(cos_degrees)
        for nk in sorted(all_node_keys):
            nd = n4j_degrees.get(nk, -1)
            cd = cos_degrees.get(nk, -1)
            if nd != cd:
                degree_mismatches.append((nk, nd, cd))

        check("Node degree match", len(degree_mismatches) == 0,
              f"{len(degree_mismatches)} nodes differ" +
              (f" — first 5: {[(m[0], f'n4j={m[1]}', f'cos={m[2]}') for m in degree_mismatches[:5]]}" if degree_mismatches else ""))

    # ══════════════════════════════════════════════════════════════════════
    #  8. Isolated nodes (nodes with zero edges) — data quality check
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 8. Isolated Nodes (degree=0) ────────────────────────────")
    n4j_isolated = {k for k, d in n4j_degrees.items() if d == 0}
    if cos_degrees is not None:
        cos_isolated = {k for k, d in cos_degrees.items() if d == 0}
        compare_sets("Isolated nodes same in both", n4j_isolated, cos_isolated)
        if n4j_isolated:
            print(f"  [{WARN}] {len(n4j_isolated)} isolated nodes in both DBs (data quality): {sorted(n4j_isolated)[:10]}")
    else:
        check("Isolated nodes comparison", False, "Skipped — degree query failed")

    # ══════════════════════════════════════════════════════════════════════
    #  9. Self-loops check — data quality check
    # ══════════════════════════════════════════════════════════════════════
    print("\n── 9. Self-Loops ───────────────────────────────────────────")
    n4j_self_loops = {edge_key(e) for e in n4j_edges_list
                      if e["src_name"] == e["tgt_name"] and e["src_db"] == e["tgt_db"]}
    cos_self_loops = {edge_key(e) for e in cos_edges_list
                      if e["src_name"] == e["tgt_name"] and e["src_db"] == e["tgt_db"]}
    compare_sets("Self-loops same in both", n4j_self_loops, cos_self_loops)
    if n4j_self_loops:
        print(f"  [{WARN}] {len(n4j_self_loops)} self-loops in both DBs (data quality): {sorted(n4j_self_loops)[:5]}")

    # ══════════════════════════════════════════════════════════════════════
    #  Summary
    # ══════════════════════════════════════════════════════════════════════
    driver.close()
    close_cosmos_client(client)

    total = len(results)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")

    print(f"\n{'═' * 70}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'═' * 70}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
