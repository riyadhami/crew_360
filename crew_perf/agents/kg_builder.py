"""Agent 1 — Knowledge Graph Agent.

Turns raw schema CSVs into a semantic graph: Table vertices with business
meaning, Concept vertices grouping columns across tables, and JOINS_TO edges
carrying evidence and confidence.

Adapted from Indigo_Knowledge_Layer-main/src/agents/advanced_graph_builder_agent.py.
What carries over: deterministic enumeration first, LLM enrichment second, and a
generate->review loop for concepts. What changed:

  - sources come from a registry
  - column roles are classified deterministically before the LLM sees anything,
    so ~38% of PEP columns (audit/SCD-2 bookkeeping) never consume tokens and
    can never be mistaken for scoring attributes
  - joins are resolved and data-verified separately (joins.py) rather than being
    invented by the LLM alongside prose
  - large schemas pass through a relevance gate with recorded exclusions
  - enrichment is batched rather than a per-table ReAct loop: with roles and
    joins already established deterministically, the remaining task is naming
    and describing, which does not need a tool loop
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from crew_perf import config
from crew_perf.agents.joins import JoinReport, prune_refuted, resolve_joins
from crew_perf.graph.schema import ColumnRole, EdgeLabel, VertexLabel
from crew_perf.llm import call_llm, parse_llm_json
from crew_perf.sources import SourceSchema, TableSchema

ENRICH_BATCH = 6
CONCEPT_REVIEW_ROUNDS = 2


# ─── Prompts ────────────────────────────────────────────────────────────────

RELEVANCE_PROMPT = """\
You are triaging tables in the **{source}** database for a crew-performance \
knowledge graph.

## Source context
{context}

## The question this graph must answer
How is an individual cabin crew member performing? Scoring, ranking, assessment \
history, feedback, complaints, recognition, availability, duty and flight activity.

## Tables
{table_list}

## Task
For each table decide whether it could contribute to evaluating a crew member's \
performance, either directly (assessment results, complaints, recognition, leave, \
duty hours) or as necessary supporting context (crew identity, reference data \
needed to interpret a code, the flight an event happened on).

EXCLUDE tables that cannot contribute: document checklists, bulletins, print \
templates, UI configuration, admin notifications, email/SLA templates, and raw \
integration or API request/response logs.

Be decisive but not aggressive: when a table plausibly carries a performance \
signal, keep it. It is far worse to silently drop a real signal than to carry one \
extra table.

Return ONLY JSON:
{{"decisions": [{{"table": "NAME", "keep": true, "reason": "short reason"}}]}}
"""

ENRICH_PROMPT = """\
You are a database-schema analyst producing knowledge-graph nodes for the \
**{source}** database.

## Source context (do not invent purposes outside this)
{context}

## Tables to analyse
{tables_json}

## Task
For each table, produce a node describing what it means in business terms.

Rules:
- `label`: human-readable, 1-3 words. Never the raw table name.
  `MENTOR_FEEDBACK` -> "Mentor Feedback"; `PEP_TEMP_CAT_MAPPING` -> "Template Categories".
- `id`: snake_case of the label.
- `description`: 1-2 sentences on what the table represents and its role in crew \
  performance. Be concrete about what a row *is* (one assessment? one answer? one crew member?).
- `concepts`: 1-4 business concepts this table participates in. Use domain-level \
  names such as "Crew Identity", "Assessment Lifecycle", "Question Scoring", \
  "Grading Rules", "Flight Context", "Mentor Assignment", "Workflow & Audit".
- `grain`: what one row represents, in a few words.

The columns shown have already been classified. Audit and versioning columns are \
omitted deliberately — do not ask for them or mention them.

Where a column carries `confirmed_semantics`, that text is established fact about \
the source system. Treat it as authoritative and never contradict it — in \
particular, do not describe a column as narrative text when its confirmed \
semantics say it is a boolean answer.

Return ONLY JSON:
{{"nodes": [{{"table": "RAW_NAME", "id": "...", "label": "...", "description": "...", \
"grain": "...", "concepts": ["..."]}}]}}
"""

CONCEPT_PROMPT = """\
You are a knowledge-graph architect working on the **{source}** database.

## Source context
{context}

## Enriched table nodes
{nodes_json}

## Task
Produce normalised **Concept** nodes that group related columns across tables.

Rules:
- Merge near-duplicates: "Crew", "Crew Member", "Crew Identity" -> one "Crew Identity".
- Aim for 8-16 concepts. Each must be a meaningful business domain, not a synonym \
  for a single table.
- Every concept lists the tables contributing to it, and the specific columns that \
  make it up.
- Every table node must belong to at least one concept.
- Prefer concepts that a person asking about crew performance would actually name.

{feedback}

Return ONLY JSON:
{{"concepts": [{{"id": "snake_case", "label": "Human Readable",
  "description": "what this concept covers, naming its key columns",
  "source_tables": ["TABLE_A"],
  "key_columns": [{{"table": "TABLE_A", "column": "COL", "why": "short"}}]}}]}}
"""

CONCEPT_REVIEW_PROMPT = """\
You are reviewing concept nodes for the **{source}** knowledge graph.

## Concepts
{concepts_json}

## Tables that must each be covered by at least one concept
{table_names}

## Checklist
1. Duplicate or near-duplicate concepts that should be merged?
2. Any table not covered by any concept?
3. Concepts that are just a rename of one table and add no cross-table meaning?
4. Concepts whose `key_columns` reference tables not in their `source_tables`?
5. Missing concepts a crew-performance question would obviously need?

If everything passes, return {{"verdict": "PASS"}}.
Otherwise return {{"verdict": "FAIL", "feedback": "specific, actionable issues"}}.

Return ONLY JSON.
"""


# ─── Result container ───────────────────────────────────────────────────────


@dataclass
class BuildResult:
    source: str
    nodes: list[dict] = field(default_factory=list)
    concepts: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    joins: JoinReport | None = None
    warnings: list[str] = field(default_factory=list)
    review_history: list[str] = field(default_factory=list)

    def to_graph(self) -> dict:
        return {
            "source": self.source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "nodes": self.nodes,
            "concepts": self.concepts,
            "edges": self.edges,
            "excluded": self.excluded,
            "warnings": self.warnings,
            "review_history": self.review_history,
            "stats": {
                "tables": len(self.nodes),
                "concepts": len(self.concepts),
                "edges": len(self.edges),
                "excluded": len(self.excluded),
                "verified_joins": sum(
                    1 for e in self.edges
                    if e.get("label") == EdgeLabel.JOINS_TO and e.get("verified")
                ),
            },
        }


# ─── Steps ──────────────────────────────────────────────────────────────────


def _table_brief(table: TableSchema, max_cols: int = 28) -> dict:
    """What the LLM sees: business columns with roles, audit noise stripped."""
    from crew_perf.graph.schema import column_note

    if table.has_columns:
        cols = []
        for c in table.business_columns()[:max_cols]:
            entry = {"name": c.name, "type": c.data_type, "role": c.role.value}
            note = column_note(table.name, c.name)
            if note:
                entry["confirmed_semantics"] = note
            cols.append(entry)
        return {
            "table": table.name,
            "columns": cols,
            "total_columns": len(table.columns),
            "scd2": table.is_scd2,
        }
    return {"table": table.name, "description": table.description, "columns": []}


def capture_lookup_decodes(
    source: SourceSchema, executor, max_rows: int = 50
) -> dict[str, dict[str, dict[str, str]]]:
    """Record what a lookup table's codes actually mean, as pairs.

    `capture_enum_values` lists each column's values sorted independently, which
    for a code/label lookup is worse than saying nothing:

        = STATUS_TYPE    in ('Approved', 'Pending', 'Rejected', 'Withdrawn')
        = STATUS_TYPE_ID in ('1', '2', '3', '4')

    Read down the columns that says 2 = Pending. It does not: 2 is Rejected and 3
    is Pending. An agent filtering `STATUS_TYPE_ID = 2` for approved-ish leave
    silently counts rejections. Pairs remove the inference entirely.
    """
    if not source.has_columns:
        return {}

    available = set(executor.list_tables())
    out: dict[str, dict[str, dict[str, str]]] = {}
    for name, table in source.tables.items():
        if name not in available:
            continue
        try:
            res = executor.execute(f'SELECT COUNT(*) FROM "{name}"', limit=1)
            if not res.rows or not 0 < int(res.rows[0][0]) <= max_rows:
                continue
        except Exception:  # noqa: BLE001
            continue

        cols = [c for c in table.columns if c.role in {ColumnRole.DIMENSION, ColumnRole.IDENTITY}]
        codes = [c for c in cols if c.name.upper().endswith(("_ID", "_CODE"))]
        labels = [c for c in cols
                  if c not in codes and not c.name.upper().endswith(("_ID", "_CODE"))]
        if not codes or not labels:
            continue

        # One decode per code column, against the most label-like companion.
        label = labels[0]
        decodes: dict[str, dict[str, str]] = {}
        for code in codes:
            try:
                res = executor.execute(
                    f'SELECT "{code.name}", "{label.name}" FROM "{name}" '
                    f'WHERE "{code.name}" IS NOT NULL ORDER BY 1',
                    limit=max_rows,
                )
            except Exception:  # noqa: BLE001
                continue
            rows = [(str(a), str(b)) for a, b in res.rows if b is not None]
            pairs = dict(rows)
            # Only a column that is unique in the table decodes to one meaning.
            # DESIGNATION_CODE repeats across PEP_TEMPLATE's four templates, so
            # collapsing it produced "CA = Cabin Attendant — ATR" — the last row
            # to win, presented as though it were the definition.
            if len(pairs) != len(rows):
                continue
            if 1 < len(pairs) <= max_rows:
                decodes[code.name] = pairs
        if decodes:
            out[name] = decodes
    return out


def capture_enum_values(
    source: SourceSchema, executor, max_rows: int = 200, max_distinct: int = 20
) -> dict[str, dict[str, list]]:
    """Record the actual values of small dimension columns.

    Without this the agent filters on codes it has never seen — it wrote
    `TEMPLATE_CODE ILIKE '%LEAD%'` against a column whose values are
    `LD_A320_V4`, and got zero rows from a query that looked entirely correct.
    Four template codes cost nothing to carry and remove the guess completely.

    The distinct cap is 20 rather than 12 because ServiceNow's report categories
    are a closed list of 14. Under the old cap they were omitted, and asked which
    crew appear in the most star-performer reports the agent could not know that
    `Star Performer Of My Flight` is a category value at all — it went looking in
    CrewPortal's issue tables instead and reported the data unavailable. Six more
    strings per column is a trivial cost against a wrong "not available".

    **Table size no longer gates this, and that was the whole PEP bias.** Row
    count was standing in for "this is a lookup table", which is true of PEP's
    reference tables and false of every fact table in the other sources. The
    result was one-sided enrichment: PEP arrived with its codes spelled out and
    ServiceNow arrived with none, so `search('deviation')` returned
    PEP_DEVIATION_MATRIX and nothing else, and an agent asked about service
    deviations went to CrewPortal's compliance table rather than to
    `SN_SERVICE_CHECK.CHECK_CODE = 'DEVIATION'` sitting right there. Every graph
    lookup surfaced the source that happened to be enriched.

    What actually decides whether a column is a closed list is its cardinality,
    and the DISTINCT below already measures it — a column with more than
    `max_distinct` values is discarded whatever table it came from. On a large
    table only DIMENSION columns are probed: an identity column is high
    cardinality by definition, so scanning one buys nothing and costs a query.
    """
    if not source.has_columns:
        return {}

    available = set(executor.list_tables())
    out: dict[str, dict[str, list]] = {}
    for name, table in source.tables.items():
        if name not in available:
            continue
        try:
            res = executor.execute(f'SELECT COUNT(*) FROM "{name}"', limit=1)
            row_count = int(res.rows[0][0]) if res.rows else 0
        except Exception:  # noqa: BLE001
            continue
        if row_count <= 0:
            continue
        small = row_count <= max_rows
        probe = ({ColumnRole.DIMENSION, ColumnRole.IDENTITY} if small
                 else {ColumnRole.DIMENSION})

        values: dict[str, list] = {}
        for col in table.columns:
            if col.role not in probe:
                continue
            if col.name.upper() in {"ROW_HASH", "LOAD_DATE"}:
                continue
            try:
                res = executor.execute(
                    f'SELECT DISTINCT "{col.name}" FROM "{name}" '
                    f'WHERE "{col.name}" IS NOT NULL LIMIT {max_distinct + 1}',
                    limit=max_distinct + 1,
                )
            except Exception:  # noqa: BLE001
                continue
            vals = [r[0] for r in res.rows]
            if 0 < len(vals) <= max_distinct:
                values[col.name] = sorted(str(v) for v in vals)
        if values:
            out[name] = values
    return out


def apply_relevance_gate(source: SourceSchema) -> tuple[list[str], list[dict]]:
    """Score tables for crew-performance relevance. Returns (kept, excluded)."""
    if not source.relevance_gate:
        return list(source.tables), []

    listing = "\n".join(
        f"- {t.name}: {t.description or '(no description)'}" for t in source.tables.values()
    )
    reply = call_llm(
        RELEVANCE_PROMPT.format(source=source.name, context=source.context, table_list=listing),
        temperature=0.0,
    )
    parsed = parse_llm_json(reply) or {}
    decisions = parsed.get("decisions", []) if isinstance(parsed, dict) else []

    kept, excluded = [], []
    decided = set()
    for d in decisions:
        name = d.get("table")
        if name not in source.tables:
            continue
        decided.add(name)
        if d.get("keep"):
            kept.append(name)
        else:
            excluded.append({"table": name, "reason": d.get("reason", "not relevant")})

    # Anything the model failed to mention is kept — dropping a table by silence
    # is exactly the invisible decision the gate is meant to prevent.
    for name in source.tables:
        if name not in decided:
            kept.append(name)
    return kept, excluded


def enrich_tables(source: SourceSchema, table_names: list[str]) -> list[dict]:
    """Give each table a business label, description and grain."""
    out: list[dict] = []
    for i in range(0, len(table_names), ENRICH_BATCH):
        batch = table_names[i : i + ENRICH_BATCH]
        briefs = [_table_brief(source.tables[n]) for n in batch]
        reply = call_llm(
            ENRICH_PROMPT.format(
                source=source.name,
                context=source.context,
                tables_json=json.dumps(briefs, indent=1),
            ),
            temperature=0.2,
        )
        parsed = parse_llm_json(reply) or {}
        nodes = parsed.get("nodes", []) if isinstance(parsed, dict) else []
        got = {n.get("table") for n in nodes}
        out.extend(n for n in nodes if n.get("table") in source.tables)

        # Never let a table vanish because the model skipped it.
        for name in batch:
            if name not in got:
                out.append({
                    "table": name,
                    "id": name.lower(),
                    "label": name.replace("_", " ").title(),
                    "description": source.tables[name].description or "",
                    "grain": "",
                    "concepts": [],
                    "_fallback": True,
                })
    return out


def extract_concepts(
    source: SourceSchema, enriched: list[dict], progress=None
) -> tuple[list[dict], list[str], list[str]]:
    """Generate concepts, then review and regenerate until they pass.

    Returns (concepts, warnings, review_history).
    """
    warnings: list[str] = []
    history: list[str] = []
    feedback = ""
    concepts: list[dict] = []

    for round_no in range(CONCEPT_REVIEW_ROUNDS + 1):
        reply = call_llm(
            CONCEPT_PROMPT.format(
                source=source.name,
                context=source.context,
                nodes_json=json.dumps(enriched, indent=1)[:60_000],
                feedback=(f"## Fix these issues from the last attempt\n{feedback}"
                          if feedback else ""),
            ),
            temperature=0.3,
        )
        parsed = parse_llm_json(reply) or {}
        concepts = parsed.get("concepts", []) if isinstance(parsed, dict) else []
        if progress:
            progress("  concept round", f"{round_no + 1}: generated {len(concepts)}")
        if not concepts:
            warnings.append(f"concept generation returned nothing (round {round_no + 1})")
            break
        if round_no == CONCEPT_REVIEW_ROUNDS:
            break

        verdict_raw = call_llm(
            CONCEPT_REVIEW_PROMPT.format(
                source=source.name,
                concepts_json=json.dumps(concepts, indent=1)[:60_000],
                table_names=", ".join(n["table"] for n in enriched),
            ),
            temperature=0.0,
        )
        verdict = parse_llm_json(verdict_raw) or {}
        if progress:
            progress("  concept review", str(verdict.get("verdict", "?"))
                     if isinstance(verdict, dict) else "unparseable")
        if not isinstance(verdict, dict) or verdict.get("verdict") == "PASS":
            break
        feedback = str(verdict.get("feedback", ""))
        # Intermediate review feedback is history, not a warning. Recording it as
        # a warning makes a graph that ended up complete look broken, and buries
        # the gaps that genuinely did survive to the final state.
        history.append(f"concept review round {round_no + 1}: {feedback[:180]}")

    return concepts, warnings, history


def reconcile_coverage(
    concepts: list[dict], enriched: list[dict], kept: list[str]
) -> tuple[list[dict], list[str]]:
    """Attach tables that no concept claimed, using the enrichment hints.

    The review loop reliably *detects* coverage gaps but does not reliably fix
    them — asking for a full regeneration to repair one missing table tends to
    shuffle the other concepts instead. Enrichment already produced a per-table
    `concepts` list, so the repair is a name match against concepts we have,
    which is deterministic and cheap. Anything still unmatched stays uncovered
    and is reported rather than being force-fitted into an unrelated concept.
    """
    from difflib import SequenceMatcher

    notes: list[str] = []
    covered = {t for c in concepts for t in c.get("source_tables", [])}
    hints = {n["table"]: n.get("concepts", []) or [] for n in enriched}

    def norm(s: str) -> str:
        return "".join(ch for ch in str(s).lower() if ch.isalnum())

    for table in kept:
        if table in covered:
            continue
        best, best_score = None, 0.0
        for hint in hints.get(table, []):
            for concept in concepts:
                score = SequenceMatcher(
                    None, norm(hint), norm(concept.get("label", ""))
                ).ratio()
                if score > best_score:
                    best, best_score = concept, score
        if best is not None and best_score >= 0.65:
            best.setdefault("source_tables", []).append(table)
            notes.append(
                f"{table} attached to concept {best.get('label')!r} from its enrichment "
                f"hint (match {best_score:.0%})"
            )
    return concepts, notes


def build(source: SourceSchema, executor=None, use_llm: bool = True, progress=None) -> BuildResult:
    """Full Agent 1 pipeline for one source.

    `progress` is an optional callable(stage: str, detail: str). The LLM stages
    take tens of seconds each, so without it the build looks hung.
    """
    import time

    def step(stage: str, detail: str = "") -> None:
        if progress:
            progress(stage, detail)

    result = BuildResult(source=source.name)

    # 1. Deterministic enumeration, then relevance gating.
    t = time.time()
    kept, excluded = apply_relevance_gate(source) if use_llm else (list(source.tables), [])
    result.excluded = excluded
    step("relevance gate", f"{len(kept)} kept, {len(excluded)} excluded ({time.time() - t:.1f}s)")

    # 2. Joins — resolved and data-verified independently of the LLM.
    t = time.time()
    joins = prune_refuted(resolve_joins(source, executor))
    result.joins = joins
    step("joins", f"{len(joins.edges)} edges, {sum(e.verified for e in joins.edges)} "
                  f"data-verified ({time.time() - t:.1f}s)")

    # 3. Business enrichment.
    t = time.time()
    enriched = enrich_tables(source, kept) if use_llm else [
        {"table": n, "id": n.lower(), "label": n.replace("_", " ").title(),
         "description": source.tables[n].description, "grain": "", "concepts": []}
        for n in kept
    ]
    step("enrichment", f"{len(enriched)} tables described ({time.time() - t:.1f}s)")
    by_table = {n["table"]: n for n in enriched}

    enums = capture_enum_values(source, executor) if executor is not None else {}

    # 4. Table vertices.
    for name in kept:
        table = source.tables[name]
        node = by_table.get(name, {})
        result.nodes.append({
            "id": f"{source.name}__table__{name}",
            "label": VertexLabel.TABLE,
            "source": source.name,
            "name": name,
            "display_label": node.get("label", name),
            "description": node.get("description", ""),
            "grain": node.get("grain", ""),
            "engine": source.engine,
            "scd2": table.is_scd2,
            "in_subset": table.in_subset,
            "columns": table.column_names,
            "column_roles": {c.name: c.role.value for c in table.columns},
            "measures": [c.name for c in table.columns_by_role(ColumnRole.MEASURE)],
            "identity_columns": table.identity_columns,
            "concepts": node.get("concepts", []),
            "enum_values": enums.get(name, {}),
            "fallback_enrichment": bool(node.get("_fallback")),
        })

    # 5. Concept vertices.
    t = time.time()
    concepts, warns, history = (
        extract_concepts(source, enriched, progress=progress) if use_llm else ([], [], [])
    )
    result.warnings.extend(warns)
    result.review_history.extend(history)
    if concepts:
        concepts, notes = reconcile_coverage(concepts, enriched, kept)
        result.warnings.extend(notes)
    step("concepts", f"{len(concepts)} concepts ({time.time() - t:.1f}s)")
    for c in concepts:
        tables = [t for t in c.get("source_tables", []) if t in set(kept)]
        if not tables:
            continue
        result.concepts.append({
            "id": f"{source.name}__concept__{c.get('id', 'unknown')}",
            "label": VertexLabel.CONCEPT,
            "source": source.name,
            "name": c.get("id", ""),
            "display_label": c.get("label", ""),
            "description": c.get("description", ""),
            "source_tables": tables,
            "key_columns": c.get("key_columns", []),
        })

    # 6. Edges — joins first (they carry evidence), then table->concept membership.
    for e in joins.edges:
        if e.source_table not in set(kept) or e.target_table not in set(kept):
            continue
        result.edges.append({
            "label": EdgeLabel.JOINS_TO,
            "from": f"{source.name}__table__{e.source_table}",
            "to": f"{source.name}__table__{e.target_table}",
            "source_column": e.source_column,
            "target_column": e.target_column,
            "confidence": e.confidence,
            "join_source": e.source,
            "evidence": e.evidence,
            "verified": e.verified,
            "coverage": e.coverage,
            "cardinality": e.cardinality,
            "note": e.note,
        })

    for c in result.concepts:
        for tname in c["source_tables"]:
            result.edges.append({
                "label": EdgeLabel.BELONGS_TO,
                "from": f"{source.name}__table__{tname}",
                "to": c["id"],
                "confidence": 1.0,
            })

    # 7. Honest gaps, recorded on the graph rather than lost.
    connected = {e["from"] for e in result.edges} | {e["to"] for e in result.edges}
    isolated = [n["name"] for n in result.nodes if n["id"] not in connected]
    if isolated:
        result.warnings.append(
            f"{len(isolated)} table(s) have no resolved relationship: {', '.join(sorted(isolated))}"
        )
    uncovered = [
        n["name"] for n in result.nodes
        if not any(n["name"] in c["source_tables"] for c in result.concepts)
    ]
    if uncovered and result.concepts:
        result.warnings.append(
            f"{len(uncovered)} table(s) belong to no concept: {', '.join(sorted(uncovered))}"
        )
    return result


# ─── Persistence ────────────────────────────────────────────────────────────


def save_graph(result: BuildResult) -> tuple:
    config.GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    graph = result.to_graph()

    # Encoding is explicit because write_text otherwise takes the locale default,
    # which on Windows is cp1252 — and the rendered join notation ("A ↔ B", "A → B")
    # is not encodable there. Left implicit, a build dies after the LLM work is done.
    json_path = config.GRAPHS_DIR / f"{result.source}_concept_graph.json"
    json_path.write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")

    md_path = config.GRAPHS_DIR / f"{result.source}_concept_graph.md"
    md_path.write_text(render_markdown(graph, result), encoding="utf-8")
    return json_path, md_path


def render_markdown(graph: dict, result: BuildResult) -> str:
    s = graph["stats"]
    lines = [
        f"# Concept Graph — {graph['source']}",
        f"\n*Generated {graph['generated_at']}*\n",
        "| Metric | Count |",
        "|---|---|",
        f"| Tables | {s['tables']} |",
        f"| Concepts | {s['concepts']} |",
        f"| Edges | {s['edges']} |",
        f"| Data-verified joins | {s['verified_joins']} |",
        f"| Excluded by relevance gate | {s['excluded']} |",
    ]

    lines.append("\n## Tables\n")
    lines.append("| Table | Label | Grain | SCD-2 | Measures | Description |")
    lines.append("|---|---|---|---|---|---|")
    for n in sorted(result.nodes, key=lambda n: n["name"]):
        lines.append(
            f"| `{n['name']}` | {n['display_label']} | {n['grain']} | "
            f"{'yes' if n['scd2'] else 'no'} | {len(n['measures'])} | {n['description']} |"
        )

    if result.concepts:
        lines.append("\n## Concepts\n")
        lines.append("| Concept | Tables | Description |")
        lines.append("|---|---|---|")
        for c in result.concepts:
            lines.append(
                f"| **{c['display_label']}** | {', '.join(f'`{t}`' for t in c['source_tables'])} "
                f"| {c['description']} |"
            )

    joins = [e for e in result.edges if e["label"] == EdgeLabel.JOINS_TO]
    if joins:
        lines.append("\n## Join edges\n")
        lines.append("| Child | Parent | Column | Conf | Coverage | Source | Cardinality |")
        lines.append("|---|---|---|---|---|---|---|")
        for e in sorted(joins, key=lambda e: -e["confidence"]):
            cov = f"{e['coverage']:.0%}" if e.get("coverage") is not None else "—"
            child = e["from"].split("__table__")[-1]
            parent = e["to"].split("__table__")[-1]
            lines.append(
                f"| `{child}` | `{parent}` | `{e['source_column']}` -> `{e['target_column']}` "
                f"| {e['confidence']:.2f} | {cov} | {e['join_source']} | {e['cardinality']} |"
            )

    if result.review_history:
        lines.append("\n## Concept review history\n")
        lines.append("*Intermediate feedback from the generate/review loop. Issues listed here "
                     "were raised during review; anything that survived into the final graph "
                     "appears under Warnings below.*\n")
        for h in result.review_history:
            lines.append(f"- {h}")

    if result.excluded:
        lines.append("\n## Excluded by relevance gate\n")
        lines.append("| Table | Reason |")
        lines.append("|---|---|")
        for x in result.excluded:
            lines.append(f"| `{x['table']}` | {x['reason']} |")

    if result.joins and result.joins.skipped:
        lines.append("\n## Rejected join candidates\n")
        for sk in result.joins.skipped:
            lines.append(f"- {sk.get('reason', '')}")

    if result.warnings:
        lines.append("\n## Warnings\n")
        for w in result.warnings:
            lines.append(f"- {w}")

    return "\n".join(lines) + "\n"


def push_to_cosmos(result: BuildResult, client=None) -> dict:
    """Additively upsert the graph into Cosmos. Never drops anything."""
    from crew_perf.graph.cosmos import (
        close_cosmos_client,
        get_cosmos_client,
        upsert_edge,
        upsert_vertex,
    )

    config.assert_graph_writable()
    own_client = client is None
    client = client or get_cosmos_client()
    counts = {"vertices": 0, "edges": 0}
    try:
        for node in result.nodes:
            props = {k: v for k, v in node.items() if k not in {"id", "label"}}
            upsert_vertex(client, node["id"], node["label"], props)
            counts["vertices"] += 1
        for c in result.concepts:
            props = {k: v for k, v in c.items() if k not in {"id", "label"}}
            upsert_vertex(client, c["id"], c["label"], props)
            counts["vertices"] += 1
        for e in result.edges:
            props = {k: v for k, v in e.items() if k not in {"label", "from", "to"}}
            upsert_edge(client, e["from"], e["to"], e["label"], props)
            counts["edges"] += 1
    finally:
        if own_client:
            close_cosmos_client(client)
    return counts
