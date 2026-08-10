"""In-memory view of the knowledge graph, for agents to query.

Reads the JSON artifacts that `build-graph` and `build-bridge` produce. Those are the
source of truth — Cosmos is a load target, not the origin — so reading them
directly is both correct and roughly three orders of magnitude faster than a
Gremlin round trip per tool call. A ReAct loop makes tens of graph calls per
question; at ~200ms each against Cosmos that alone would dominate latency.

`CosmosGraphStore` exists for the case where the live graph has been edited
outside this repo (a join corrected in place, say) and the local artifacts are
stale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from crew_perf import config
from crew_perf.graph.schema import EdgeLabel


@dataclass
class TableNode:
    id: str
    name: str
    source: str
    display_label: str
    description: str
    grain: str
    scd2: bool
    columns: list[str]
    column_roles: dict[str, str]
    measures: list[str]
    identity_columns: list[str]
    in_subset: bool = True
    engine: str = "snowflake"
    enum_values: dict = field(default_factory=dict)
    decodes: dict = field(default_factory=dict)   # code column -> {value: meaning}

    def role_of(self, column: str) -> str | None:
        return self.column_roles.get(column.upper()) or self.column_roles.get(column)


@dataclass
class ConceptNode:
    id: str
    name: str
    display_label: str
    description: str
    source_tables: list[str]
    key_columns: list[dict] = field(default_factory=list)


CROSS_SOURCE_FILE = "cross_source_joins.json"


@dataclass
class JoinEdgeView:
    source_table: str
    target_table: str
    source_column: str
    target_column: str
    confidence: float
    verified: bool
    evidence: str
    cardinality: str | None = None
    coverage: float | None = None
    cross_source: bool = False
    # The curated export's reason for this relationship, when it stated one.
    note: str = ""



class GraphStore:
    """The merged concept graph: every built source, plus the cross-source bridge.

    Scoring rules are not held here — they are plain configuration in
    `crew_perf/policy.py`, and `scoring_parameters()` reads them from there.
    """

    def __init__(self, graph_path: Path | None = None, sources: list[str] | None = None):
        """Load one source graph, or every source that has been built.

        The default is every source. Loading PEP alone left the retrieval agent
        able to see ten of the thirty-eight onboarded tables — it would answer a
        question about leave or check-in by reporting the data unavailable, which
        is a false answer rather than a missing one.
        """
        paths = self._resolve_paths(graph_path, sources)
        if not paths:
            raise FileNotFoundError(
                f"No concept graph in {config.GRAPHS_DIR}. Run `crewperf build-graph PEP` first."
            )

        self.sources: list[str] = []
        self.warnings: list[str] = []
        self.tables: dict[str, TableNode] = {}
        self.concepts: dict[str, ConceptNode] = {}
        self.joins: list[JoinEdgeView] = []
        self._membership: dict[str, list[str]] = {}

        for path in paths:
            self._absorb(json.loads(path.read_text()))

        self._absorb_bridge()

    @staticmethod
    def _resolve_paths(graph_path: Path | None, sources: list[str] | None) -> list[Path]:
        if graph_path is not None:
            return [graph_path] if graph_path.exists() else []
        if sources is None:
            from crew_perf.sources import load_registry

            try:
                sources = [s.name for s in load_registry().available()]
            except Exception:  # noqa: BLE001 - a missing registry is not fatal here
                sources = []
        found = [config.GRAPHS_DIR / f"{s}_concept_graph.json" for s in sources]
        found = [p for p in found if p.exists()]
        if found:
            return found
        # Registry unreadable or nothing declared: fall back to whatever is on disk.
        return sorted(config.GRAPHS_DIR.glob("*_concept_graph.json"))

    def _absorb(self, raw: dict) -> None:
        source = raw.get("source", "?")
        self.sources.append(source)
        self.warnings.extend(raw.get("warnings", []))

        for n in raw.get("nodes", []):
            self.tables[n["name"]] = TableNode(
                id=n["id"], name=n["name"], source=n["source"],
                display_label=n.get("display_label", n["name"]),
                description=n.get("description", ""), grain=n.get("grain", ""),
                scd2=bool(n.get("scd2")), columns=n.get("columns", []),
                column_roles=n.get("column_roles", {}), measures=n.get("measures", []),
                identity_columns=n.get("identity_columns", []),
                in_subset=bool(n.get("in_subset", True)),
                engine=n.get("engine", "snowflake"),
                enum_values=n.get("enum_values", {}) or {},
                decodes=n.get("decodes", {}) or {},
            )

        for c in raw.get("concepts", []):
            existing = self.concepts.get(c["name"])
            if existing:
                # `crew_identity` is named by all three sources and means the same
                # thing in each. Overwriting would leave the concept pointing only
                # at the last source's tables.
                for t in c.get("source_tables", []):
                    if t not in existing.source_tables:
                        existing.source_tables.append(t)
                existing.key_columns.extend(c.get("key_columns", []))
                continue
            self.concepts[c["name"]] = ConceptNode(
                id=c["id"], name=c["name"],
                display_label=c.get("display_label", c["name"]),
                description=c.get("description", ""),
                source_tables=list(c.get("source_tables", [])),
                key_columns=list(c.get("key_columns", [])),
            )

        for e in raw.get("edges", []):
            if e["label"] == EdgeLabel.JOINS_TO:
                self.joins.append(JoinEdgeView(
                    source_table=e["from"].split("__table__")[-1],
                    target_table=e["to"].split("__table__")[-1],
                    source_column=e["source_column"], target_column=e["target_column"],
                    confidence=float(e.get("confidence", 0)), verified=bool(e.get("verified")),
                    evidence=e.get("evidence", ""), cardinality=e.get("cardinality"),
                    coverage=e.get("coverage"), cross_source=bool(e.get("cross_source")),
                    note=e.get("note", ""),
                ))
            elif e["label"] == EdgeLabel.BELONGS_TO:
                table = e["from"].split("__table__")[-1]
                concept = e["to"].split("__concept__")[-1]
                self._membership.setdefault(table, []).append(concept)

    def _absorb_bridge(self) -> None:
        """The identity edges that span sources.

        Per-source graphs are built in isolation, so nothing in them can join PEP
        to CLMS. Without this file the merged graph is three islands: more tables
        visible, still no question answerable across them.
        """
        path = config.GRAPHS_DIR / CROSS_SOURCE_FILE
        if not path.exists():
            if len(set(self.sources)) > 1:
                self.warnings.append(
                    f"{len(set(self.sources))} sources loaded but no cross-source joins "
                    f"({CROSS_SOURCE_FILE} missing) — run `crewperf build-bridge`; until then "
                    f"no question can span sources"
                )
            return
        raw = json.loads(path.read_text())
        known = set(self.tables)
        for e in raw.get("edges", []):
            src = e["from"].split("__table__")[-1]
            tgt = e["to"].split("__table__")[-1]
            if src not in known or tgt not in known:
                continue      # a source that is not loaded; not an error
            self.joins.append(JoinEdgeView(
                source_table=src, target_table=tgt,
                source_column=e["source_column"], target_column=e["target_column"],
                confidence=float(e.get("confidence", 0)), verified=bool(e.get("verified")),
                evidence=e.get("evidence", ""), cardinality=e.get("cardinality"),
                coverage=e.get("coverage"), cross_source=True,
            ))
        self.warnings.extend(raw.get("warnings", []))

    @property
    def source(self) -> str:
        """Back-compat label for the loaded scope."""
        return "+".join(dict.fromkeys(self.sources)) or "?"


    # ── lookups ────────────────────────────────────────────────────────────

    @cached_property
    def column_index(self) -> dict[str, list[tuple[str, str]]]:
        """upper(column) -> [(table, role)] across every table."""
        idx: dict[str, list[tuple[str, str]]] = {}
        for t in self.tables.values():
            for col in t.columns:
                idx.setdefault(col.upper(), []).append((t.name, t.role_of(col) or ""))
        return idx

    @cached_property
    def scd2_tables(self) -> set[str]:
        return {t.name for t in self.tables.values() if t.scd2}

    def table(self, name: str) -> TableNode | None:
        return self.tables.get(name) or self.tables.get(name.upper())

    def has_column(self, table: str, column: str) -> bool:
        t = self.table(table)
        return bool(t and column.upper() in {c.upper() for c in t.columns})

    def concepts_of(self, table: str) -> list[str]:
        return self._membership.get(table, [])

    def joins_for(self, table: str, min_confidence: float = 0.0) -> list[JoinEdgeView]:
        """Every join touching a table, in either direction."""
        return [
            j for j in self.joins
            if (j.source_table == table or j.target_table == table)
            and j.confidence >= min_confidence
        ]

    def join_between(self, a: str, b: str, min_confidence: float = 0.0) -> list[JoinEdgeView]:
        return [
            j for j in self.joins
            if {j.source_table, j.target_table} == {a, b} and j.confidence >= min_confidence
        ]

    def join_path(self, start: str, end: str, min_confidence: float = 0.8,
                  max_hops: int = 4) -> list[JoinEdgeView] | None:
        """Shortest high-confidence join path between two tables.

        Only high-confidence edges are traversable: a path routed through a
        0.4-confidence guess produces SQL that runs and returns the wrong rows,
        which is worse than admitting no path exists.
        """
        if start == end:
            return []
        frontier: list[tuple[str, list[JoinEdgeView]]] = [(start, [])]
        seen = {start}
        for _ in range(max_hops):
            nxt: list[tuple[str, list[JoinEdgeView]]] = []
            for node, path in frontier:
                for j in self.joins_for(node, min_confidence):
                    other = j.target_table if j.source_table == node else j.source_table
                    if other in seen:
                        continue
                    if other == end:
                        return [*path, j]
                    seen.add(other)
                    nxt.append((other, [*path, j]))
            if not nxt:
                break
            frontier = nxt
        return None

    def scoring_parameters(self) -> dict:
        """Computable rules. Plain configuration now, not graph vertices."""
        from crew_perf import policy

        return policy.PARAMETERS

    def search(self, term: str, limit: int = 12) -> list[dict]:
        """Substring search across tables, columns, concepts and stored values.

        Values matter as much as names here, and used not to be searched at all.
        A business user's word is very often a *value* rather than an identifier:
        "deviation" is `SN_SERVICE_CHECK.CHECK_CODE = 'DEVIATION'`, "crew
        feedback" is `SN_FLIGHT_REPORT.CATEGORY`, "star performer" is a
        sub-category. Matching only names returned PEP_DEVIATION_MATRIX for
        "deviation" and nothing from ServiceNow, so the agent concluded the
        service-deviation data lived in PEP's grading tables — a confident wrong
        turn produced entirely by what the index covered.
        """
        t = term.lower().strip()
        hits: list[dict] = []
        for tbl in self.tables.values():
            if t in tbl.name.lower() or t in tbl.display_label.lower() or t in tbl.description.lower():
                hits.append({"kind": "table", "name": tbl.name,
                             "label": tbl.display_label, "description": tbl.description[:160]})
        for col, holders in self.column_index.items():
            if t in col.lower():
                hits.append({"kind": "column", "name": col,
                             "tables": [h[0] for h in holders][:6]})
        for c in self.concepts.values():
            if t in c.name.lower() or t in c.display_label.lower() or t in c.description.lower():
                hits.append({"kind": "concept", "name": c.name, "label": c.display_label,
                             "tables": c.source_tables})
        hits.extend(self._value_hits(t))
        return hits[:limit]

    def _value_hits(self, term: str) -> list[dict]:
        """Stored enum values and code decodes containing the term.

        Returned last so a table or column named for the term still leads, and
        deduplicated per (table, column) so one match does not spend the whole
        result budget on a column with twenty near-identical values.
        """
        out: list[dict] = []
        for tbl in self.tables.values():
            for col, vals in tbl.enum_values.items():
                matched = [v for v in vals if term in str(v).lower()]
                if matched:
                    out.append({"kind": "value", "table": tbl.name, "column": col,
                                "matching_values": matched[:6],
                                "usage": f"filter {tbl.name}.{col} on one of these"})
            for col, mapping in tbl.decodes.items():
                matched = {k: v for k, v in mapping.items() if term in str(v).lower()}
                if matched:
                    out.append({"kind": "value", "table": tbl.name, "column": col,
                                "matching_codes": dict(list(matched.items())[:6]),
                                "usage": f"filter {tbl.name}.{col} on the CODE, not the label"})
        return out

    def foreign_decodes(self, table: str) -> list[tuple[str, str, dict]]:
        """Code meanings reachable from this table's foreign keys.

        Returns (column on `table`, lookup table, {code: meaning}).
        """
        out: list[tuple[str, str, dict]] = []
        seen: set[str] = set()
        for j in self.joins_for(table, min_confidence=0.8):
            if j.source_table == table:
                col, other, other_col = j.source_column, j.target_table, j.target_column
            else:
                col, other, other_col = j.target_column, j.source_table, j.source_column
            node = self.tables.get(other)
            if node is None or col in seen:
                continue
            mapping = node.decodes.get(other_col)
            if mapping:
                out.append((col, other, mapping))
                seen.add(col)
        return sorted(out)

    def source_brief(self) -> str:
        """What each onboarded source holds, and what nothing holds.

        Generated from `schemas/registry.yaml` rather than written into a prompt.
        The retrieval prompt used to name three sources in prose — PEP, CLMS and
        CrewPortal — while the store had loaded four, so every ServiceNow table
        was visible in the schema digest and invisible in the agent's own account
        of what it could reach. That asymmetry is exactly what produces a
        PEP-shaped answer to a cross-source question: the model orients on the
        prose, not on the table list, and a source it was never told it had is a
        source it does not think to use.
        """
        try:
            from crew_perf.sources import load_registry

            registry = load_registry()
        except Exception:  # noqa: BLE001 - a missing registry must not kill a query
            return ""

        loaded = set(self.sources)
        lines: list[str] = ["### The sources you can reach"]
        for source in registry.available():
            if source.name not in loaded:
                continue
            n = sum(1 for t in self.tables.values() if t.source == source.name)
            lines.append(f"- **{source.name}** ({n} tables, keyed on {source.join_key}) — "
                         f"{source.context}")

        missing = registry.out_of_scope()
        if missing:
            lines += [
                "",
                "No onboarded source supplies " + ", ".join(missing) + ". That is a "
                "decision, not a delivery that is pending. If a question needs any of "
                "them, say so and stop — do not answer from a related-but-different "
                "table. Counting flight rows is not duty hours; counting assessments "
                "is not sectors flown.",
            ]
        return "\n".join(lines)

    def identity_brief(self) -> str:
        """How a crew member is identified in each source, and how to translate.

        Without this the agent sees `IGA60406`, sees CLMS keyed on `CREW_ID`, and
        writes `CREW_ID = '60406'` — stripping the prefix as though the two were
        the same number in different clothes. That query is valid, runs, and
        returns nothing, so the agent reports the crew member has no leave. A
        silent wrong answer is the worst outcome available here, and no amount of
        join validation catches it: the join was fine, the literal was invented.
        """
        try:
            from crew_perf.sources import load_registry

            registry = load_registry()
        except Exception:  # noqa: BLE001
            return ""

        keys = {s.name: (s.join_key, s.crew_master) for s in registry.available()
                if s.name in set(self.sources)}
        if len(keys) < 2:
            return ""

        lines = [f"- {name}: keyed on {key} (crew master {master})"
                 for name, (key, master) in keys.items()]
        bridge = (registry.identity or {}).get("bridge") or {}
        canonical = (registry.identity or {}).get("canonical_key", "IGA")
        distinct = sorted({k for k, _ in keys.values()})

        out = ["\n\n### Crew identity across sources", *lines]
        if len(distinct) > 1 and bridge.get("table"):
            out += [
                f"\n{' and '.join(distinct)} are DIFFERENT identifiers for the same person. "
                f"One is NEVER the other with a prefix added or removed — do not derive "
                f"{distinct[0]} from {distinct[-1]} by editing the string.",
                f"To carry a {canonical} into a source keyed on something else, join through "
                f"{bridge['table']}, which carries both. `find_join_path` returns that route; "
                f"use it rather than inventing a literal.",
            ]
        return "\n".join(out)

    def digest(self, max_tables: int | None = None, max_cols: int = 12) -> str:
        """Compact orientation for a system prompt.

        Without this the agent spends most of its turn budget rediscovering that
        BASE lives on EMPLOYEE_INFO and STATUS on PEP_SCHEDULER — facts that fit
        in a few hundred tokens. Exploration tools then get used for the things
        that genuinely need looking up, rather than for orientation.

        Tables are grouped by source, and the budget defaults to *all* of them.
        A fixed cap silently truncated the tail once three sources were merged,
        which is the failure this whole digest exists to prevent — an agent
        orientated on a schema it cannot see all of reports data unavailable
        that is sitting right there.
        """
        from crew_perf.graph.schema import CONFIRMED_COLUMN_NOTES

        skip_roles = {"audit", "temporal_control"}
        budget = max_tables if max_tables is not None else len(self.tables)
        by_source: dict[str, list[TableNode]] = {}
        for t in sorted(self.tables.values(), key=lambda t: (not t.in_subset, t.name)):
            by_source.setdefault(t.source, []).append(t)

        lines: list[str] = []
        shown_tables = 0
        truncated = False
        for source in dict.fromkeys(self.sources) or by_source:
            group = by_source.get(source, [])
            if not group:
                continue
            lines.append(f"\n-- {source} ({len(group)} tables) --")
            for t in group:
                if shown_tables >= budget:
                    truncated = True
                    break
                cols = [c for c in t.columns if (t.role_of(c) or "") not in skip_roles]
                shown = ", ".join(cols[:max_cols])
                if len(cols) > max_cols:
                    shown += ", …"
                lines.append(f"- {t.name} — {t.grain or t.display_label}\n    {shown}")
                # Traps that produce a plausible-but-empty result if not stated up
                # front, e.g. a denormalised column that silently matches nothing.
                for col in cols:
                    note = CONFIRMED_COLUMN_NOTES.get((t.name.upper(), col.upper()), "")
                    if note.startswith(("DENORMALISED", "BOOLEAN", "Assessment score",
                                        "DISAMBIGUATION")):
                        lines.append(f"    ! {col}: {note.splitlines()[0]}")
                # Code -> meaning, as pairs. Printed first and in place of the
                # bare value list, which sorted each column independently and so
                # implied a pairing that was wrong.
                for col, mapping in sorted(t.decodes.items()):
                    shown_pairs = ", ".join(f"{k}={v}" for k, v in list(mapping.items())[:12])
                    lines.append(f"    = {col}: {shown_pairs}")
                # Real values for small dimensions, so filters are never guessed.
                # A code's meaning has to appear where the filter gets written.
                # Knowing that M_LMS_STATUS_TYPE decodes 2 to "Rejected" is no
                # use while writing a predicate on T_CLMS_LEAVE_REQ_MASTER —
                # the agent wrote STATUS_TYPE_ID IN (1,2) and counted rejected
                # leave as taken.
                for col, target, mapping in self.foreign_decodes(t.name):
                    pairs = ", ".join(f"{k}={v}" for k, v in list(mapping.items())[:12])
                    lines.append(f"    = {col} -> {target}: {pairs}")

                decoded_values = {frozenset(m.values()) for m in t.decodes.values()}
                for col, vals in sorted(t.enum_values.items()):
                    # Skip the code column itself, and the label column whose
                    # values the decode already spells out against their codes.
                    if col in t.decodes or frozenset(vals) in decoded_values:
                        continue
                    lines.append(f"    = {col} in ({', '.join(repr(v) for v in vals)})")
                shown_tables += 1
        if truncated:
            lines.append(f"… {len(self.tables) - shown_tables} further tables — use list_tables")

        usable = sorted(
            (j for j in self.joins if j.confidence >= 0.9 and j.verified),
            key=lambda j: (j.cross_source, j.source_table, j.target_table),
        )
        join_lines = [
            f"- {j.source_table}.{j.source_column} = {j.target_table}.{j.target_column}"
            + ("   << crosses sources" if j.cross_source else "")
            for j in usable
        ]
        out = (
            "### Tables (audit/versioning columns omitted)"
            + "\n".join(lines)
            + "\n\n### Verified joins (the only ones you may use)\n"
            + "\n".join(join_lines)
        )
        if not any(j.cross_source for j in usable) and len(set(self.sources)) > 1:
            out += ("\n\n! No verified join spans sources. Do not invent one — a question "
                    "needing data from two sources cannot be answered.")
        return out + self.identity_brief()

    def summary(self) -> dict:
        by_source: dict[str, int] = {}
        for t in self.tables.values():
            by_source[t.source] = by_source.get(t.source, 0) + 1
        return {
            "source": self.source,
            "sources": list(dict.fromkeys(self.sources)),
            "tables": len(self.tables),
            "tables_by_source": by_source,
            "concepts": len(self.concepts),
            "join_edges": len(self.joins),
            "verified_joins": sum(1 for j in self.joins if j.verified),
            "cross_source_joins": sum(1 for j in self.joins if j.cross_source),
            "rules": len(self.scoring_parameters()),
        }


_store: GraphStore | None = None


def get_store(fresh: bool = False) -> GraphStore:
    global _store
    if _store is None or fresh:
        _store = GraphStore()
    return _store
