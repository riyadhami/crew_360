"""
graph_traversal.py — Interactive Neo4j knowledge-graph explorer.

Usage (interactive REPL):
    python -m src.utils.graph_traversal

Usage (as a library):
    from src.utils.graph_traversal import GraphDB
    db = GraphDB()
    print(db.schema())
    db.close()
"""

from __future__ import annotations


import json
import textwrap
from typing import Any

from src.utils.neo4j_helpers import get_neo4j_driver, run_cypher


# ═══════════════════════════════════════════════════════════════════════════════
#  GraphDB class
# ═══════════════════════════════════════════════════════════════════════════════

class GraphDB:
    """Thin wrapper around the unified Neo4j knowledge graph."""

    def __init__(self):
        self.driver = get_neo4j_driver()

    def close(self):
        self.driver.close()

    # ── helpers ────────────────────────────────────────────────────────────
    def _q(self, cypher: str, params: dict | None = None) -> list[dict]:
        return run_cypher(self.driver, cypher, params or {})

    # ── 1. schema overview ────────────────────────────────────────────────
    def schema(self) -> dict:
        """Return the full KG schema: labels, relationship types, counts."""
        labels = [r["label"] for r in self._q("CALL db.labels() YIELD label RETURN label")]
        rel_types = [
            r["relationshipType"]
            for r in self._q("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
        ]
        counts = self._q("MATCH (n) RETURN labels(n) AS labels, count(*) AS count")
        edge_count = self._q("MATCH ()-[r]->() RETURN count(r) AS count")[0]["count"]
        return {
            "node_labels": labels,
            "relationship_types": rel_types,
            "node_counts": {str(r["labels"]): r["count"] for r in counts},
            "total_edges": edge_count,
        }

    # ── 2. list concept nodes ─────────────────────────────────────────────
    def list_concepts(self) -> list[dict]:
        """Return all Concept nodes with key properties including key_columns."""
        return self._q(
            "MATCH (c:Concept) "
            "RETURN c.name AS name, c.description AS description, "
            "       c.database AS database, c.sourceTables AS source_tables, "
            "       c.keyColumns AS key_columns, c.nodeId AS node_id "
            "ORDER BY c.name"
        )

    # ── 3. concept → linked tables / databases ────────────────────────────
    def concept_links(self, concept_name: str, database: str | None = None) -> list[dict]:
        """For a concept, return which tables and databases it connects to.

        If the same concept name exists in multiple databases, results are
        returned as a list — one entry per concept node.  Pass *database*
        to narrow to a specific one.
        """
        db_filter = "WHERE c.database = $db " if database else ""
        params: dict = {"name": concept_name}
        if database:
            params["db"] = database

        # Find all matching concept nodes
        concept_nodes = self._q(
            "MATCH (c:Concept {name: $name}) " + db_filter +
            "RETURN c.name AS name, c.database AS database, "
            "       c.nodeId AS node_id, c.description AS description",
            params,
        )
        if not concept_nodes:
            return []

        results = []
        for cn in concept_nodes:
            cn_db = cn["database"]
            tables = self._q(
                "MATCH (c:Concept {name: $name, database: $cdb})-[r]-(t:Table) "
                "RETURN t.name AS table_name, t.database AS database, "
                "       type(r) AS relationship, t.nodeId AS table_node_id "
                "ORDER BY t.database, t.name",
                {"name": concept_name, "cdb": cn_db},
            )
            related = self._q(
                "MATCH (c:Concept {name: $name, database: $cdb})-[r]-(other:Concept) "
                "RETURN other.name AS concept, other.database AS database, "
                "       type(r) AS relationship "
                "ORDER BY other.name",
                {"name": concept_name, "cdb": cn_db},
            )
            results.append({
                "concept": concept_name,
                "concept_database": cn_db,
                "description": cn["description"],
                "linked_databases": sorted({r["database"] for r in tables}),
                "linked_tables": tables,
                "related_concepts": related,
            })

        return results

    # ── 4. node details (by name) ────────────────────────────────────────
    def node_details(self, name: str) -> list[dict]:
        """Fetch full properties + neighbours of any node by name."""
        nodes = self._q(
            "MATCH (n) WHERE n.name = $name "
            "RETURN labels(n) AS labels, properties(n) AS props",
            {"name": name},
        )
        if not nodes:
            return []

        neighbours = self._q(
            "MATCH (n {name: $name})-[r]-(m) "
            "RETURN type(r) AS relationship, "
            "       startNode(r) = n AS outgoing, "
            "       labels(m) AS neighbour_labels, "
            "       m.name AS neighbour_name, "
            "       m.database AS neighbour_database "
            "ORDER BY type(r), m.name",
            {"name": name},
        )
        return {
            "node": nodes[0],
            "neighbours": neighbours,
        }

    # ── 5. list tables (optionally filter by database) ────────────────────
    def list_tables(self, database: str | None = None) -> list[dict]:
        """List all Table nodes, optionally filtered by database label."""
        if database:
            return self._q(
                "MATCH (t:Table) WHERE t.database = $db "
                "RETURN t.name AS name, t.label AS label, t.database AS database, "
                "       t.description AS description "
                "ORDER BY t.name",
                {"db": database},
            )
        return self._q(
            "MATCH (t:Table) "
            "RETURN t.name AS name, t.label AS label, t.database AS database, "
            "       t.description AS description "
            "ORDER BY t.database, t.name"
        )

    # ── 6. cross-database edges ───────────────────────────────────────────
    def cross_db_edges(self) -> list[dict]:
        """Return all edges that connect nodes from different databases."""
        return self._q(
            "MATCH (a)-[r]->(b) "
            "WHERE a.database <> b.database "
            "RETURN a.name AS source, a.database AS source_db, "
            "       type(r) AS relationship, "
            "       b.name AS target, b.database AS target_db "
            "ORDER BY a.database, b.database, type(r)"
        )

    # ── 7. keyword search across all nodes ────────────────────────────────
    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """Case-insensitive keyword search across node names, labels, and
        descriptions.  This is the primary entry-point for an LLM agent —
        given a vague user query, find relevant concepts and tables."""
        return self._q(
            "MATCH (n) "
            "WHERE n.name       =~ $pat "
            "   OR n.label      =~ $pat "
            "   OR n.description =~ $pat "
            "RETURN labels(n) AS labels, n.name AS name, "
            "       n.label AS label, n.database AS database, "
            "       n.description AS description "
            "ORDER BY "
            "  CASE WHEN n.name =~ $pat THEN 0 ELSE 1 END, "
            "  n.name "
            "LIMIT $limit",
            {"pat": f"(?i).*{keyword}.*", "limit": limit},
        )

    # ── 8. subgraph (N-hop neighbourhood) ─────────────────────────────────
    def subgraph(self, name: str, depth: int = 2) -> dict:
        """Return the N-hop neighbourhood around a node.  Lets an agent
        'expand' outward from any starting point to discover context."""
        depth = min(depth, 4)  # safety cap
        nodes = self._q(
            "MATCH (start {name: $name}) "
            "MATCH path = (start)-[*1.." + str(depth) + "]-(m) "
            "UNWIND nodes(path) AS n "
            "RETURN DISTINCT labels(n) AS labels, n.name AS name, "
            "       n.database AS database "
            "ORDER BY n.name",
            {"name": name},
        )
        edges = self._q(
            "MATCH (start {name: $name}) "
            "MATCH (start)-[*0.." + str(depth) + "]-(a)-[r]-(b) "
            "WHERE (start)-[*0.." + str(depth) + "]-(b) "
            "RETURN DISTINCT a.name AS source, type(r) AS relationship, "
            "       b.name AS target "
            "ORDER BY source, target",
            {"name": name},
        )
        return {"center": name, "depth": depth, "nodes": nodes, "edges": edges}

    # ── 9. shortest path between two nodes ────────────────────────────────
    def path_between(self, name1: str, name2: str) -> list[dict]:
        """Find the shortest path between any two nodes.  Returns each hop
        as source → relationship → target."""
        return self._q(
            "MATCH (a {name: $n1}), (b {name: $n2}), "
            "      p = shortestPath((a)-[*..10]-(b)) "
            "UNWIND range(0, length(p)-1) AS i "
            "RETURN nodes(p)[i].name     AS source, "
            "       type(relationships(p)[i]) AS relationship, "
            "       nodes(p)[i+1].name   AS target",
            {"n1": name1, "n2": name2},
        )

    # ── 10. column search — find tables containing a column ───────────────
    def find_columns(self, column: str) -> list[dict]:
        """Search for tables and concepts whose columns/keyColumns list contains a keyword.
        Useful for 'where does contact info live?' type questions."""
        # Search in tables
        tables = self._q(
            "MATCH (t:Table) "
            "WHERE any(c IN t.columns WHERE c =~ $pat) "
            "RETURN 'table' AS type, t.name AS table_name, t.database AS database, "
            "       t.label AS label, t.columns AS columns, null AS description "
            "ORDER BY t.database, t.name",
            {"pat": f"(?i).*{column}.*"},
        )
        # Search in concepts
        concepts = self._q(
            "MATCH (c:Concept) "
            "WHERE any(col IN c.keyColumns WHERE col =~ $pat) "
            "RETURN 'concept' AS type, c.name AS concept_name, c.database AS database, "
            "       null AS label, c.keyColumns AS key_columns, c.description AS description "
            "ORDER BY c.database, c.name",
            {"pat": f"(?i).*{column}.*"},
        )
        return tables + concepts

    # ── 11. shared concepts — concepts bridging multiple databases ────────
    def shared_concepts(self) -> list[dict]:
        """Return concepts that bridge multiple databases.

        Uses two signals:
        1. Same-name concepts that exist in more than one database.
        2. Concepts with cross-database concept-to-concept edges
           (SAME_DOMAIN, COMPLEMENTS, RELATED, etc.) created by graph
           unification.
        """
        # Same-name concepts across DBs
        same_name = self._q(
            "MATCH (c:Concept) "
            "WITH c.name AS concept, collect(DISTINCT c.database) AS databases "
            "WHERE size(databases) > 1 "
            "RETURN concept, databases, 'same_name' AS bridge_type "
            "ORDER BY size(databases) DESC, concept"
        )
        # Concepts linked cross-DB via concept-to-concept edges
        cross_edges = self._q(
            "MATCH (c1:Concept)-[r]-(c2:Concept) "
            "WHERE c1.database <> c2.database "
            "WITH c1.name AS concept, c1.database AS database, "
            "     collect(DISTINCT c2.database) AS linked_databases, "
            "     count(DISTINCT c2) AS linked_concept_count "
            "RETURN concept, database, linked_databases, linked_concept_count, "
            "       'cross_edge' AS bridge_type "
            "ORDER BY linked_concept_count DESC, concept"
        )
        return same_name + cross_edges

    # ── 12. database summary ──────────────────────────────────────────────
    def database_summary(self, database: str) -> dict:
        """Quick overview of a single database: table count, connected
        concepts, and key tables (by neighbour count)."""
        tables = self._q(
            "MATCH (t:Table {database: $db}) "
            "OPTIONAL MATCH (t)-[r]-() "
            "RETURN t.name AS name, t.label AS label, count(r) AS connections "
            "ORDER BY connections DESC",
            {"db": database},
        )
        concepts = self._q(
            "MATCH (c:Concept)-[]-(t:Table {database: $db}) "
            "RETURN DISTINCT c.name AS concept, c.database AS concept_db "
            "ORDER BY c.name",
            {"db": database},
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
        """Starting from a node (concept or table), follow edges to find
        what it connects to in *target_database*.

        Returns bridging concepts/tables in the target DB and the
        relationships that link them.  This is the key tool for answering
        'how does X in DB-A relate to DB-B?' questions.
        """
        # Direct cross-DB edges from this node to the target DB
        direct = self._q(
            "MATCH (start {name: $name})-[r]-(target) "
            "WHERE target.database = $tdb "
            "RETURN start.name AS source, start.database AS source_db, "
            "       type(r) AS relationship, "
            "       labels(target) AS target_labels, "
            "       target.name AS target_name, "
            "       target.database AS target_db "
            "ORDER BY target.name",
            {"name": name, "tdb": target_database},
        )

        # 2-hop: start → intermediate → target-DB node
        two_hop = self._q(
            "MATCH (start {name: $name})-[r1]-(mid)-[r2]-(target) "
            "WHERE target.database = $tdb AND mid <> start AND target <> start "
            "RETURN start.name AS source, "
            "       mid.name AS via_node, labels(mid) AS via_labels, "
            "       mid.database AS via_db, "
            "       type(r1) AS rel1, type(r2) AS rel2, "
            "       target.name AS target_name, "
            "       labels(target) AS target_labels, "
            "       target.database AS target_db "
            "ORDER BY target.name "
            "LIMIT 50",
            {"name": name, "tdb": target_database},
        )

        return {
            "source": name,
            "target_database": target_database,
            "direct_connections": direct,
            "two_hop_connections": two_hop,
        }

    # ── 14. raw query ─────────────────────────────────────────────────────
    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute any read-only Cypher query and return results."""
        return self._q(cypher, params)


# ═══════════════════════════════════════════════════════════════════════════════
#  Interactive REPL
# ═══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = textwrap.dedent("""\
    Commands:
      schema                     — Show KG schema (labels, rel types, counts)
      concepts                   — List all concept nodes
      concept <name> [database]  — Show tables/databases linked to a concept
      tables [database]          — List tables (optionally filter by DB name)
      node <name>                — Full details + neighbours of any node
      cross                      — List all cross-database edges
      search <keyword>           — Keyword search across names & descriptions
      subgraph <name> [depth]    — N-hop neighbourhood (default depth=2)
      path <name1> -> <name2>    — Shortest path between two nodes
      columns <column_name>      — Find tables containing a column
      shared                     — Concepts that bridge multiple databases
      dbsummary <database>       — Quick overview of one database
      trace <name> -> <database> — Cross-DB: what does node connect to in target DB
      cypher <query>             — Run a raw Cypher query
      help                       — Show this help
      quit / exit                — Exit
""")


def _pp(obj: Any):
    """Pretty-print a result."""
    print(json.dumps(obj, indent=2, default=str))


def repl():
    db = GraphDB()
    print("Knowledge Graph Explorer  (type 'help' for commands)\n")
    try:
        while True:
            try:
                line = input("kg> ").strip()
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
                    print(f"  [{r['database'] or '?':12s}]  {r['name']}")
                print(f"\n  ({len(rows)} concepts)")
            elif cmd == "concept":
                if not arg:
                    print("Usage: concept <name> [database]")
                    continue
                cparts = arg.rsplit(maxsplit=1)
                c_name = cparts[0]
                c_db = None
                # If last word looks like a DB name, treat it as filter
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
                    print(f"  No node found with name '{arg}'")
                else:
                    _pp(result)
            elif cmd == "cross":
                rows = db.cross_db_edges()
                for r in rows:
                    print(f"  {r['source_db']:12s} {r['source']:35s} --[{r['relationship']}]--> {r['target_db']:12s} {r['target']}")
                print(f"\n  ({len(rows)} cross-database edges)")
            elif cmd == "cypher":
                if not arg:
                    print("Usage: cypher <query>")
                    continue
                _pp(db.query(arg))
            elif cmd == "search":
                if not arg:
                    print("Usage: search <keyword>")
                    continue
                rows = db.search(arg)
                for r in rows:
                    lbl = "/".join(r["labels"])
                    print(f"  [{r['database'] or '?':12s}] ({lbl:10s})  {r['name']}")
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
                    dbs = ", ".join(r["databases"])
                    print(f"  {r['concept']:35s}  tables={r['table_count']:3d}  dbs=[{dbs}]")
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
                    print(f"    --[{r['relationship']}]--> {r['target_name']} ({'/'.join(r['target_labels'])})")
                print(f"  2-hop connections: {len(result['two_hop_connections'])}")
                for r in result['two_hop_connections'][:15]:
                    print(f"    via {r['via_node']} [{r['via_db']}]  --[{r['rel2']}]--> {r['target_name']}")
                if len(result['two_hop_connections']) > 15:
                    print(f"    ... and {len(result['two_hop_connections']) - 15} more")
            else:
                print(f"Unknown command: {cmd}  (type 'help')")
    finally:
        db.close()
        print("Bye.")


if __name__ == "__main__":
    repl()
