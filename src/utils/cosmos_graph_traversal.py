"""
cosmos_graph_traversal.py — Interactive Cosmos DB (Gremlin) knowledge-graph explorer.

Mirrors the API surface of graph_traversal.py (Neo4j) so both backends
expose the same set of traversal operations.

Usage (interactive REPL):
    python -m src.utils.cosmos_graph_traversal

Usage (as a library):
    from src.utils.cosmos_graph_traversal import CosmosGraphDB
    db = CosmosGraphDB()
    print(db.schema())
    db.close()
"""

from __future__ import annotations

import json
import textwrap
import time
from typing import Any

from src.utils.cosmos_helpers import (
    get_cosmos_client,
    run_gremlin,
    close_cosmos_client,
    escape_gremlin,
)

# Delay between heavy queries to avoid 429 throttling
_QUERY_DELAY = 1.0


def _prop(vertex: dict, key: str) -> str:
    """Extract a scalar property from a Gremlin valueMap result."""
    val = vertex.get(key, "")
    if isinstance(val, list):
        return val[0] if val else ""
    return val or ""


# ═══════════════════════════════════════════════════════════════════════════════
#  CosmosGraphDB class
# ═══════════════════════════════════════════════════════════════════════════════

class CosmosGraphDB:
    """Thin wrapper around the unified Cosmos DB Gremlin knowledge graph."""

    def __init__(self):
        self.client = get_cosmos_client()

    def close(self):
        close_cosmos_client(self.client)

    # ── helpers ────────────────────────────────────────────────────────────
    def _g(self, gremlin: str) -> list:
        return run_gremlin(self.client, gremlin)

    # ── 1. schema overview ────────────────────────────────────────────────
    def schema(self) -> dict:
        """Return the full KG schema: vertex labels, edge labels, counts."""
        # Vertex labels + counts
        label_counts_raw = self._g("g.V().groupCount().by(label())")
        label_counts = label_counts_raw[0] if label_counts_raw else {}
        time.sleep(_QUERY_DELAY)

        # Edge labels
        edge_labels_raw = self._g("g.E().label().dedup()")
        edge_labels = sorted(edge_labels_raw)
        time.sleep(_QUERY_DELAY)

        # Total edge count
        total_edges = self._g("g.E().count()")[0]

        return {
            "node_labels": sorted(label_counts.keys()),
            "relationship_types": edge_labels,
            "node_counts": {k: v for k, v in sorted(label_counts.items())},
            "total_edges": total_edges,
        }

    # ── 2. list concept nodes ─────────────────────────────────────────────
    def list_concepts(self) -> list[dict]:
        """Return all Concept vertices with key properties including key_columns."""
        raw = self._g(
            "g.V().hasLabel('Concept')"
            ".project('name','description','database','source_tables','key_columns','node_id')"
            ".by(values('name'))"
            ".by(coalesce(values('description'), constant('')))"
            ".by(values('database'))"
            ".by(coalesce(values('sourceTables'), constant('')))"
            ".by(coalesce(values('keyColumns'), constant('')))"
            ".by(coalesce(values('nodeId'), constant('')))"
        )
        raw.sort(key=lambda r: r.get("name", ""))
        return raw

    # ── 3. concept → linked tables / databases ────────────────────────────
    def concept_links(self, concept_name: str, database: str | None = None) -> list[dict]:
        """For a concept, return which tables and databases it connects to."""
        esc_name = escape_gremlin(concept_name)
        if database:
            esc_db = escape_gremlin(database)
            concept_q = (
                f"g.V().hasLabel('Concept')"
                f".has('name', '{esc_name}')"
                f".has('database', '{esc_db}')"
                f".project('name','database','node_id','description')"
                f".by(values('name'))"
                f".by(values('database'))"
                f".by(coalesce(values('nodeId'), constant('')))"
                f".by(coalesce(values('description'), constant('')))"
            )
        else:
            concept_q = (
                f"g.V().hasLabel('Concept')"
                f".has('name', '{esc_name}')"
                f".project('name','database','node_id','description')"
                f".by(values('name'))"
                f".by(values('database'))"
                f".by(coalesce(values('nodeId'), constant('')))"
                f".by(coalesce(values('description'), constant('')))"
            )

        concept_nodes = self._g(concept_q)
        if not concept_nodes:
            return []

        results = []
        for cn in concept_nodes:
            cn_db = cn["database"]
            esc_cn_db = escape_gremlin(cn_db)
            time.sleep(_QUERY_DELAY)

            # Tables connected to this concept
            tables = self._g(
                f"g.V().hasLabel('Concept')"
                f".has('name', '{esc_name}').has('database', '{esc_cn_db}')"
                f".bothE().otherV().hasLabel('Table')"
                f".project('table_name','database','relationship','table_node_id')"
                f".by(values('name'))"
                f".by(values('database'))"
                f".by(constant('connected'))"
                f".by(coalesce(values('nodeId'), constant('')))"
            )
            time.sleep(_QUERY_DELAY)

            # Related concepts
            related = self._g(
                f"g.V().hasLabel('Concept')"
                f".has('name', '{esc_name}').has('database', '{esc_cn_db}')"
                f".bothE().otherV().hasLabel('Concept')"
                f".project('concept','database','relationship')"
                f".by(values('name'))"
                f".by(values('database'))"
                f".by(constant('connected'))"
            )

            results.append({
                "concept": concept_name,
                "concept_database": cn_db,
                "description": cn.get("description", ""),
                "linked_databases": sorted({r["database"] for r in tables}),
                "linked_tables": tables,
                "related_concepts": related,
            })

        return results

    # ── 4. node details (by name) ────────────────────────────────────────
    def node_details(self, name: str) -> dict | list:
        """Fetch full properties + neighbours of any vertex by name."""
        esc = escape_gremlin(name)
        nodes = self._g(
            f"g.V().has('name', '{esc}').valueMap(true)"
        )
        if not nodes:
            return []

        time.sleep(_QUERY_DELAY)

        neighbours = self._g(
            f"g.V().has('name', '{esc}')"
            f".bothE()"
            f".project('relationship','outgoing','neighbour_name','neighbour_database','neighbour_label')"
            f".by(label())"
            f".by(outV().has('name', '{esc}').count())"
            f".by(otherV().values('name'))"
            f".by(otherV().values('database'))"
            f".by(otherV().label())"
        )
        # Convert outgoing flag: 1 means outgoing, 0 means incoming
        for n in neighbours:
            n["outgoing"] = n["outgoing"] == 1

        # Simplify node properties
        node = nodes[0]
        props = {}
        for k, v in node.items():
            if isinstance(v, list) and len(v) == 1:
                props[k] = v[0]
            else:
                props[k] = v
        label = props.pop("label", ["unknown"])

        return {
            "node": {"labels": [label] if isinstance(label, str) else label, "props": props},
            "neighbours": neighbours,
        }

    # ── 5. list tables (optionally filter by database) ────────────────────
    def list_tables(self, database: str | None = None) -> list[dict]:
        """List all Table vertices, optionally filtered by database."""
        if database:
            esc_db = escape_gremlin(database)
            q = (
                f"g.V().hasLabel('Table').has('database', '{esc_db}')"
                f".project('name','label','database','description')"
                f".by(values('name'))"
                f".by(coalesce(values('displayName'), constant('')))"
                f".by(values('database'))"
                f".by(coalesce(values('description'), constant('')))"
            )
        else:
            q = (
                "g.V().hasLabel('Table')"
                ".project('name','label','database','description')"
                ".by(values('name'))"
                ".by(coalesce(values('displayName'), constant('')))"
                ".by(values('database'))"
                ".by(coalesce(values('description'), constant('')))"
            )
        rows = self._g(q)
        rows.sort(key=lambda r: (r["database"], r["name"]))
        return rows

    # ── 6. cross-database edges ───────────────────────────────────────────
    def cross_db_edges(self) -> list[dict]:
        """Return all edges that connect vertices from different databases."""
        raw = self._g(
            "g.E().where(outV().values('database').as('d1')"
            ".inV().values('database').where(neq('d1')))"
            ".project('source','source_db','relationship','target','target_db')"
            ".by(outV().values('name'))"
            ".by(outV().values('database'))"
            ".by(label())"
            ".by(inV().values('name'))"
            ".by(inV().values('database'))"
        )
        raw.sort(key=lambda r: (r["source_db"], r["target_db"], r["relationship"]))
        return raw

    # ── 7. keyword search across all vertices ─────────────────────────────
    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """Case-insensitive keyword search across vertex names and descriptions.

        Note: Cosmos DB Gremlin doesn't support regex. We use TextP.containing()
        for case-sensitive substring match, or pull all and filter in Python
        for case-insensitive matching.
        """
        kw_lower = keyword.lower()
        # Pull names + descriptions for all vertices (viable at <200 nodes)
        raw = self._g(
            "g.V().project('labels','name','label','database','description')"
            ".by(label())"
            ".by(values('name'))"
            ".by(coalesce(values('displayName'), constant('')))"
            ".by(values('database'))"
            ".by(coalesce(values('description'), constant('')))"
        )
        matches = []
        for r in raw:
            name_match = kw_lower in (r.get("name") or "").lower()
            label_match = kw_lower in (r.get("label") or "").lower()
            desc_match = kw_lower in (r.get("description") or "").lower()
            if name_match or label_match or desc_match:
                # Sort priority: name match first
                r["_sort"] = 0 if name_match else 1
                r["labels"] = [r["labels"]] if isinstance(r["labels"], str) else r["labels"]
                matches.append(r)
        matches.sort(key=lambda r: (r.pop("_sort", 1), r.get("name", "")))
        return matches[:limit]

    # ── 8. subgraph (N-hop neighbourhood) ─────────────────────────────────
    def subgraph(self, name: str, depth: int = 2) -> dict:
        """Return the N-hop neighbourhood around a vertex."""
        depth = min(depth, 4)  # safety cap
        esc = escape_gremlin(name)

        nodes = self._g(
            f"g.V().has('name', '{esc}')"
            f".repeat(both().simplePath()).times({depth}).emit()"
            f".dedup()"
            f".project('labels','name','database')"
            f".by(label())"
            f".by(values('name'))"
            f".by(values('database'))"
        )
        # Include the center node
        center = self._g(
            f"g.V().has('name', '{esc}')"
            f".project('labels','name','database')"
            f".by(label())"
            f".by(values('name'))"
            f".by(values('database'))"
        )
        all_node_names = {n["name"] for n in nodes} | {n["name"] for n in center}
        nodes = center + [n for n in nodes if n["name"] not in {c["name"] for c in center}]

        time.sleep(_QUERY_DELAY)

        # Edges between discovered nodes — pull all edges and filter
        all_edges = self._g(
            "g.E().project('source','relationship','target')"
            ".by(outV().values('name'))"
            ".by(label())"
            ".by(inV().values('name'))"
        )
        edges = [
            e for e in all_edges
            if e["source"] in all_node_names and e["target"] in all_node_names
        ]
        # Deduplicate
        seen = set()
        unique_edges = []
        for e in edges:
            key = (e["source"], e["relationship"], e["target"])
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        return {"center": name, "depth": depth, "nodes": nodes, "edges": unique_edges}

    # ── 9. shortest path between two vertices ─────────────────────────────
    def path_between(self, name1: str, name2: str, max_depth: int = 10) -> list[dict]:
        """BFS shortest path between two vertices. Returns each hop as
        source → relationship → target.

        Cosmos DB Gremlin doesn't have a built-in shortestPath(), so we
        use repeat/until with emit to find the first path.
        """
        esc1 = escape_gremlin(name1)
        esc2 = escape_gremlin(name2)
        max_depth = min(max_depth, 10)

        raw = self._g(
            f"g.V().has('name', '{esc1}')"
            f".repeat(bothE().otherV().simplePath())"
            f".until(has('name', '{esc2}').or().loops().is(gte({max_depth})))"
            f".has('name', '{esc2}')"
            f".limit(1)"
            f".path()"
        )
        if not raw:
            return []

        # Parse the path object — alternating vertices and edges
        path_elements = raw[0].get("objects", raw[0]) if isinstance(raw[0], dict) else raw[0]
        if hasattr(path_elements, "objects"):
            path_elements = path_elements.objects

        hops = []
        # Path is: V, E, V, E, V, ...
        # We need to reconstruct from the raw Gremlin path
        # Fall back to a simpler approach: re-query hop by hop
        # Actually, let's try to parse the path
        if isinstance(path_elements, list) and len(path_elements) >= 3:
            for i in range(0, len(path_elements) - 2, 2):
                v1 = path_elements[i]
                edge = path_elements[i + 1]
                v2 = path_elements[i + 2]
                # Extract names — Gremlin path elements can be vertex/edge maps
                src = v1.get("name", [v1])[0] if isinstance(v1, dict) else str(v1)
                tgt = v2.get("name", [v2])[0] if isinstance(v2, dict) else str(v2)
                rel = edge.get("label", "?") if isinstance(edge, dict) else str(edge)
                hops.append({"source": src, "relationship": rel, "target": tgt})

        # If path parsing didn't work, fall back to project-based BFS
        if not hops:
            hops = self._path_fallback(name1, name2, max_depth)

        return hops

    def _path_fallback(self, name1: str, name2: str, max_depth: int) -> list[dict]:
        """Fallback path-finding: iterative BFS using edge hops."""
        esc1 = escape_gremlin(name1)
        esc2 = escape_gremlin(name2)

        for depth in range(1, max_depth + 1):
            raw = self._g(
                f"g.V().has('name', '{esc1}')"
                f".repeat(bothE().otherV().simplePath()).times({depth})"
                f".has('name', '{esc2}')"
                f".limit(1)"
                f".path()"
                f".by(coalesce(values('name'), label()))"
            )
            if raw:
                path_vals = raw[0] if isinstance(raw[0], list) else raw[0].get("objects", [])
                hops = []
                for i in range(0, len(path_vals) - 2, 2):
                    hops.append({
                        "source": str(path_vals[i]),
                        "relationship": str(path_vals[i + 1]),
                        "target": str(path_vals[i + 2]),
                    })
                if hops:
                    return hops
            time.sleep(_QUERY_DELAY)

        return []

    # ── 10. column search — find tables containing a column ───────────────
    def find_columns(self, column: str) -> list[dict]:
        """Search for tables and concepts whose columns/keyColumns property contains a keyword."""
        col_lower = column.lower()
        results = []
        
        # Search in Table columns
        table_raw = self._g(
            "g.V().hasLabel('Table')"
            ".project('table_name','database','label','columns','type')"
            ".by(values('name'))"
            ".by(values('database'))"
            ".by(coalesce(values('displayName'), constant('')))"
            ".by(coalesce(values('columns'), constant('[]')))"
            ".by(constant('table'))"
        )
        for r in table_raw:
            cols_str = r.get("columns", "[]")
            try:
                cols = json.loads(cols_str) if isinstance(cols_str, str) else cols_str
            except (json.JSONDecodeError, TypeError):
                cols = []
            if isinstance(cols, list) and any(col_lower in c.lower() for c in cols):
                r["columns"] = cols
                results.append(r)
        
        # Search in Concept keyColumns
        concept_raw = self._g(
            "g.V().hasLabel('Concept')"
            ".project('concept_name','database','description','key_columns','type')"
            ".by(values('name'))"
            ".by(values('database'))"
            ".by(coalesce(values('description'), constant('')))"
            ".by(coalesce(values('keyColumns'), constant('[]')))"
            ".by(constant('concept'))"
        )
        for r in concept_raw:
            cols_str = r.get("key_columns", "[]")
            try:
                cols = json.loads(cols_str) if isinstance(cols_str, str) else cols_str
            except (json.JSONDecodeError, TypeError):
                cols = []
            if isinstance(cols, list) and any(col_lower in c.lower() for c in cols):
                r["key_columns"] = cols
                results.append(r)
        
        results.sort(key=lambda r: (r.get("database", ""), r.get("table_name", "") or r.get("concept_name", "")))
        return results

    # ── 11. shared concepts — concepts bridging multiple databases ────────
    def shared_concepts(self) -> list[dict]:
        """Return concepts that bridge multiple databases."""
        raw = self._g(
            "g.V().hasLabel('Concept')"
            ".group().by(values('name'))"
            ".by(values('database').fold())"
        )
        if not raw:
            return []

        grouped = raw[0] if isinstance(raw[0], dict) else {}
        results = []
        for concept_name, databases in grouped.items():
            unique_dbs = sorted(set(databases))
            if len(unique_dbs) > 1:
                results.append({
                    "concept": concept_name,
                    "databases": unique_dbs,
                    "bridge_type": "same_name",
                })

        time.sleep(_QUERY_DELAY)

        # Cross-DB concept-to-concept edges
        cross = self._g(
            "g.V().hasLabel('Concept').as('c1')"
            ".bothE().otherV().hasLabel('Concept').as('c2')"
            ".where('c1', neq('c2'))"
            ".select('c1','c2')"
            ".by(project('name','db').by(values('name')).by(values('database')))"
            ".by(project('name','db').by(values('name')).by(values('database')))"
        )
        cross_map: dict[tuple[str, str], set[str]] = {}
        for row in cross:
            c1 = row["c1"]
            c2 = row["c2"]
            if c1["db"] != c2["db"]:
                key = (c1["name"], c1["db"])
                cross_map.setdefault(key, set()).add(c2["db"])

        for (cname, cdb), linked_dbs in sorted(cross_map.items()):
            results.append({
                "concept": cname,
                "database": cdb,
                "linked_databases": sorted(linked_dbs),
                "linked_concept_count": len(linked_dbs),
                "bridge_type": "cross_edge",
            })

        return results

    # ── 12. database summary ──────────────────────────────────────────────
    def database_summary(self, database: str) -> dict:
        """Quick overview of a single database partition."""
        esc_db = escape_gremlin(database)

        tables = self._g(
            f"g.V().hasLabel('Table').has('database', '{esc_db}')"
            f".project('name','label','connections')"
            f".by(values('name'))"
            f".by(coalesce(values('displayName'), constant('')))"
            f".by(bothE().count())"
        )
        tables.sort(key=lambda r: r.get("connections", 0), reverse=True)

        time.sleep(_QUERY_DELAY)

        concepts = self._g(
            f"g.V().hasLabel('Table').has('database', '{esc_db}')"
            f".bothE().otherV().hasLabel('Concept')"
            f".dedup()"
            f".project('concept','concept_db')"
            f".by(values('name'))"
            f".by(values('database'))"
        )

        return {
            "database": database,
            "table_count": len(tables),
            "concept_count": len(concepts),
            "tables": tables,
            "connected_concepts": concepts,
        }

    # ── 13. trace cross-database connections ─────────────────────────────
    def trace_cross_db(self, name: str, target_database: str) -> dict:
        """From a vertex, follow edges to find what it connects to in
        *target_database*. Returns direct and 2-hop connections."""
        esc = escape_gremlin(name)
        esc_tdb = escape_gremlin(target_database)

        # Direct
        direct = self._g(
            f"g.V().has('name', '{esc}')"
            f".bothE().otherV().has('database', '{esc_tdb}')"
            f".project('target_name','target_labels','target_db','relationship')"
            f".by(values('name'))"
            f".by(label())"
            f".by(values('database'))"
            f".by(constant('connected'))"
        )

        time.sleep(_QUERY_DELAY)

        # 2-hop: start → mid → target_db
        two_hop = self._g(
            f"g.V().has('name', '{esc}').as('start')"
            f".bothE().otherV().as('mid')"
            f".where(neq('start'))"
            f".bothE().otherV().has('database', '{esc_tdb}').as('tgt')"
            f".where(neq('start')).where(neq('mid'))"
            f".select('mid','tgt')"
            f".by(project('name','label','db')"
            f"  .by(values('name')).by(label()).by(values('database')))"
            f".by(project('name','label','db')"
            f"  .by(values('name')).by(label()).by(values('database')))"
            f".limit(50)"
        )
        two_hop_results = []
        for row in two_hop:
            mid = row["mid"]
            tgt = row["tgt"]
            two_hop_results.append({
                "via_node": mid["name"],
                "via_labels": [mid["label"]],
                "via_db": mid["db"],
                "target_name": tgt["name"],
                "target_labels": [tgt["label"]],
                "target_db": tgt["db"],
            })

        return {
            "source": name,
            "target_database": target_database,
            "direct_connections": direct,
            "two_hop_connections": two_hop_results,
        }

    # ── 14. raw query ─────────────────────────────────────────────────────
    def query(self, gremlin: str) -> list:
        """Execute any Gremlin query and return results."""
        return self._g(gremlin)


# ═══════════════════════════════════════════════════════════════════════════════
#  Interactive REPL
# ═══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = textwrap.dedent("""\
    Commands:
      schema                     — Show KG schema (labels, rel types, counts)
      concepts                   — List all concept vertices
      concept <name> [database]  — Show tables/databases linked to a concept
      tables [database]          — List tables (optionally filter by DB name)
      node <name>                — Full details + neighbours of any vertex
      cross                      — List all cross-database edges
      search <keyword>           — Keyword search across names & descriptions
      subgraph <name> [depth]    — N-hop neighbourhood (default depth=2)
      path <name1> -> <name2>    — Shortest path between two vertices
      columns <column_name>      — Find tables containing a column
      shared                     — Concepts that bridge multiple databases
      dbsummary <database>       — Quick overview of one database
      trace <name> -> <database> — Cross-DB: what does vertex connect to in target DB
      gremlin <query>            — Run a raw Gremlin query
      help                       — Show this help
      quit / exit                — Exit
""")


def _pp(obj: Any):
    """Pretty-print a result."""
    print(json.dumps(obj, indent=2, default=str))


def repl():
    db = CosmosGraphDB()
    print("Knowledge Graph Explorer (Cosmos DB)  (type 'help' for commands)\n")
    try:
        while True:
            try:
                line = input("cosmos-kg> ").strip()
            except EOFError:
                break
            if not line:
                continue

            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("quit", "exit"):
                break
            elif cmd == "help":
                print(HELP_TEXT)
            elif cmd == "schema":
                _pp(db.schema())
            elif cmd == "concepts":
                rows = db.list_concepts()
                for r in rows:
                    print(f"  [{r.get('database', '?'):12s}]  {r['name']}")
                print(f"\n  ({len(rows)} concepts)")
            elif cmd == "concept":
                if not arg:
                    print("Usage: concept <name> [database]")
                    continue
                cparts = arg.rsplit(maxsplit=1)
                c_name = cparts[0]
                c_db = None
                if len(cparts) > 1 and cparts[1] in ("CLMS", "CrewPortal", "PEP"):
                    c_name = cparts[0]
                    c_db = cparts[1]
                results = db.concept_links(c_name, c_db)
                if not results:
                    print(f"  No concept found with name '{c_name}'")
                else:
                    for entry in results:
                        print(f"\n  ── {entry['concept']} [{entry['concept_database']}] ──")
                        print(f"  Tables: {len(entry['linked_tables'])}, Related concepts: {len(entry['related_concepts'])}")
                        _pp(entry)
            elif cmd == "tables":
                rows = db.list_tables(arg or None)
                for r in rows:
                    print(f"  [{r['database']:12s}]  {r['name']:40s}  {r['label'] or ''}")
                print(f"\n  ({len(rows)} tables)")
            elif cmd == "node":
                if not arg:
                    print("Usage: node <name>")
                    continue
                result = db.node_details(arg)
                if not result:
                    print(f"  No vertex found with name '{arg}'")
                else:
                    _pp(result)
            elif cmd == "cross":
                rows = db.cross_db_edges()
                for r in rows:
                    print(f"  {r['source_db']:12s} {r['source']:35s} --[{r['relationship']}]--> {r['target_db']:12s} {r['target']}")
                print(f"\n  ({len(rows)} cross-database edges)")
            elif cmd == "gremlin":
                if not arg:
                    print("Usage: gremlin <query>")
                    continue
                _pp(db.query(arg))
            elif cmd == "search":
                if not arg:
                    print("Usage: search <keyword>")
                    continue
                rows = db.search(arg)
                for r in rows:
                    lbl = r["labels"] if isinstance(r["labels"], str) else "/".join(r["labels"])
                    print(f"  [{r.get('database', '?'):12s}] ({lbl:10s})  {r['name']}")
                print(f"\n  ({len(rows)} results)")
            elif cmd == "subgraph":
                sub_parts = arg.split()
                if not sub_parts:
                    print("Usage: subgraph <name> [depth]")
                    continue
                s_name = sub_parts[0]
                s_depth = int(sub_parts[1]) if len(sub_parts) > 1 else 2
                result = db.subgraph(s_name, s_depth)
                print(f"  Nodes: {len(result['nodes'])}, Edges: {len(result['edges'])}")
                _pp(result)
            elif cmd == "path":
                if "->" not in arg:
                    print("Usage: path <name1> -> <name2>")
                    continue
                n1, n2 = [x.strip() for x in arg.split("->", 1)]
                hops = db.path_between(n1, n2)
                if not hops:
                    print(f"  No path found between '{n1}' and '{n2}'")
                else:
                    for h in hops:
                        print(f"  {h['source']}  --[{h['relationship']}]-->  {h['target']}")
                    print(f"\n  ({len(hops)} hops)")
            elif cmd == "columns":
                if not arg:
                    print("Usage: columns <column_name>")
                    continue
                rows = db.find_columns(arg)
                for r in rows:
                    print(f"  [{r['database']:12s}]  {r['table_name']:40s}  {r['label'] or ''}")
                print(f"\n  ({len(rows)} tables contain '{arg}')")
            elif cmd == "shared":
                rows = db.shared_concepts()
                for r in rows:
                    if r.get("databases"):
                        dbs = ", ".join(r["databases"])
                        print(f"  {r['concept']:35s}  dbs=[{dbs}]")
                    else:
                        print(f"  {r['concept']:35s}  [{r.get('database','')}] → {r.get('linked_databases', [])}")
                print(f"\n  ({len(rows)} shared concepts)")
            elif cmd == "dbsummary":
                if not arg:
                    print("Usage: dbsummary <database>")
                    continue
                s = db.database_summary(arg)
                print(f"  Database: {s['database']}")
                print(f"  Tables: {s['table_count']}, Connected concepts: {s['concept_count']}")
                print(f"  Top tables by connections:")
                for t in s["tables"][:10]:
                    print(f"    {t['name']:40s}  {t['label'] or '':30s}  ({t['connections']} edges)")
            elif cmd == "trace":
                if "->" not in arg:
                    print("Usage: trace <name> -> <database>")
                    continue
                t_name, t_db = [x.strip() for x in arg.split("->", 1)]
                result = db.trace_cross_db(t_name, t_db)
                print(f"  Direct connections: {len(result['direct_connections'])}")
                for r in result['direct_connections']:
                    print(f"    --[{r['relationship']}]--> {r['target_name']} ({r['target_labels']})")
                print(f"  2-hop connections: {len(result['two_hop_connections'])}")
                for r in result['two_hop_connections'][:15]:
                    print(f"    via {r['via_node']} [{r['via_db']}]  --> {r['target_name']}")
                if len(result['two_hop_connections']) > 15:
                    print(f"    ... and {len(result['two_hop_connections']) - 15} more")
            else:
                print(f"Unknown command: {cmd}  (type 'help')")
    finally:
        db.close()
        print("Bye.")


if __name__ == "__main__":
    repl()
