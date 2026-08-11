"""Join-key resolution: declared first, inferred second, data-verified third.

Agent 3's SQL is only ever as good as these edges. PEP ships zero foreign keys,
zero primary keys and zero column comments (PLAN.md G2), so almost every join in
the system is inferred — which makes it essential that inference is (a) evidenced,
(b) scored, and (c) checkable against real data rather than asserted.

Four tiers, in descending trust:

  declared  confidence 1.00  stated by the curated relationship export
  asserted  confidence 0.90  the identity spine, declared in the registry
  verified  confidence 0.95  inferred, then confirmed by overlap in actual data
  inferred  confidence <1.0  name/type/role evidence only — needs review

The verification step matters more than the inference heuristics. A name match
between `PEP_SCHEDULER.PEP_SCHDULER_ID` and `MENTOR_FEEDBACK.PEP_SCHDULER_ID` is
a hypothesis; running it against the data and finding 100% of children resolve to
a parent is evidence. Where data is available we always prefer the latter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from crew_perf.graph.schema import ColumnRole, JoinSource
from crew_perf.sources import SourceSchema, TableSchema

# Columns that appear everywhere and join nothing. Without this an inferred-join
# pass "discovers" that all 26 PEP tables join to each other on ROW_HASH.
NON_JOINING = {
    "ROW_HASH", "LOAD_DATE", "P_IS_CURRENT", "P_CREATED_BY", "P_CREATED_DT",
    "P_MODIFIED_BY", "P_MODIFIED_DT", "CREATED_BY", "CREATED_ON", "MODIFIED_BY",
    "MODIFIED_ON", "ACTIVE", "IS_ACTIVE", "STATUS", "DESCRIPTION", "POSITION_AT",
    "ID",  # bare "ID" is a local surrogate key, never a shared join key
}

# Lookups into small reference tables need a much narrower blocklist. `STATUS`
# and `ACTIVE` are excluded above because they would explode `_ID`-style
# inference across every table — but `PEP_SCHEDULER.STATUS -> PEP_STATUS` is a
# real and necessary join, and a 4-row reference table cannot explode anything.
# Only structural bookkeeping is excluded here.
NON_JOINING_LOOKUP = {
    "ROW_HASH", "LOAD_DATE", "P_IS_CURRENT", "P_CREATED_BY", "P_CREATED_DT",
    "P_MODIFIED_BY", "P_MODIFIED_DT", "CREATED_BY", "CREATED_ON", "MODIFIED_BY",
    "MODIFIED_ON", "CREATED_DATE", "UPDATED_DATE", "UPDATED_BY", "UPDATED_ON",
}


@dataclass
class JoinEdge:
    source_table: str
    target_table: str
    source_column: str
    target_column: str
    confidence: float
    source: str                      # JoinSource value
    evidence: str
    verified: bool = False
    coverage: float | None = None    # fraction of child rows resolving to a parent
    cardinality: str | None = None   # e.g. "many-to-one"
    # Why the curated export selected this table, in its own words. Kept apart
    # from `evidence`, which accumulates provenance and verification prose: this
    # is the one sentence worth showing an agent choosing tables to query.
    note: str = ""

    def key(self) -> tuple:
        return (self.source_table, self.source_column, self.target_table, self.target_column)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JoinReport:
    edges: list[JoinEdge] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def inferred(self) -> list[JoinEdge]:
        return [e for e in self.edges if e.source == JoinSource.INFERRED]


# ─── Declared ───────────────────────────────────────────────────────────────


def identity_edges(source: SourceSchema) -> list[JoinEdge]:
    """Assert the crew-identity spine from the registry, not from guesswork.

    Two problems this solves that inference cannot:

    1. **An identity key rarely names its own table.** `IGA` does not resemble
       `EMPLOYEE_INFO`, nor `CREW_ID` `M_CLMS_CREW`. Name-similarity scoring finds
       nothing, so the single most important relationship in each source is the
       one inference is worst at.
    2. **Without data there is nothing to verify against.** CrewPortal ships no
       declared FKs, so every proposed edge stayed below the confidence bar and
       the source was onboarded but unjoinable — visible to the agent and useless
       to it.

    The registry names the crew master and every alias of the identity key. That
    is curated human knowledge, so these edges are ASSERTED rather than inferred:
    trusted above inference, still below a source-declared FK, and still
    demotable if data later refutes them.
    """
    master_name = source.crew_master
    if not master_name or master_name not in source.tables:
        return []

    master = source.tables[master_name]
    aliases = {a.upper() for a in (source.key_aliases or [source.join_key.upper()])}
    master_key = next(
        (c.name for c in master.columns if c.name.upper() in aliases), None
    )
    if master_key is None:
        return []

    out: list[JoinEdge] = []
    for table in source.tables.values():
        if table.name == master_name:
            continue
        for col in table.columns:
            if col.name.upper() not in aliases:
                continue
            out.append(JoinEdge(
                source_table=table.name,
                target_table=master_name,
                source_column=col.name,
                target_column=master_key,
                confidence=0.90,
                source=JoinSource.ASSERTED,
                evidence=(
                    f"registry-asserted identity spine: {col.name} is an alias of "
                    f"{source.join_key} for source {source.name}, and {master_name} is "
                    f"its crew master"
                ),
                cardinality="many-to-one",
            ))
    return out


def declared_edges(source: SourceSchema, registry) -> list[JoinEdge]:
    """Relationships the curated export states outright.

    The top of the ladder, and the tier this module was written expecting and
    never had: the source systems ship no foreign keys, so until now everything
    below the identity spine was a hypothesis formed from column naming. Naming
    finds the joins whose two sides happen to agree and misses the ones that do
    not — `T_FLIGHT_ISSUE_GENERAL_INFO.FLIGHT_TYPE_ID -> M_AIRCRAFT.AIRCRAFT_ID`
    is a real relationship that no name heuristic can reach, and the whole
    flight-report spine (compliance answers and issue details back to the report
    header) was missing for the same reason.

    Stated is not the same as true, so these are still verified against the data
    like everything else. What the tier buys is that a curated edge starts from
    "this is the relationship" rather than "these two columns are spelled alike",
    which is the difference between a partial overlap reading as a sparse
    population and reading as a name coincidence.
    """
    declared = (getattr(registry, "declared_joins", None) or {}).get(source.name) or []
    out: list[JoinEdge] = []
    for j in declared:
        if j.left_table not in source.tables or j.right_table not in source.tables:
            continue
        out.append(JoinEdge(
            source_table=j.left_table,
            target_table=j.right_table,
            source_column=j.left_column,
            target_column=j.right_column,
            confidence=1.0,
            source=JoinSource.DECLARED,
            evidence=(
                f"declared by the curated relationship export for the "
                f"{j.report!r} report ({j.raw!r})"
                + (f"; {j.why}" if j.why else "")
            ),
            cardinality="many-to-one",
            note=j.why,
        ))
    return out


def orient_declared(edges: list[JoinEdge], executor, available_tables: set[str]) -> None:
    """Point each declared edge from child to parent, deciding by measured uniqueness.

    The export documents a relationship, not a direction: it writes
    `M_CrewPosition.CrewPositionID = T_FlightReportWorkPositionDetails.CrewPositionID`
    for what is a many-to-one from the transaction table into the position master,
    and the other way round two rows later. Everything downstream reads
    `source_table` as the child — verification measures how many child values
    find a parent, and `M_` masters read as 0-2% coverage with a non-unique
    "parent" when they are entered from the wrong end, which is exactly what a
    refuted edge looks like.

    So the direction is measured rather than taken from the text: the side whose
    key is unique is the parent. This is the same rule verification already uses
    to decide cardinality — applied earlier, because by then the damage is done.
    Ties and unreadable probes keep the export's order.
    """
    for edge in edges:
        if edge.source != JoinSource.DECLARED:
            continue
        if edge.source_table not in available_tables or edge.target_table not in available_tables:
            continue
        try:
            res = executor.execute(f'''
                SELECT
                    (SELECT COUNT(DISTINCT s."{edge.source_column}") FROM "{edge.source_table}" s
                       WHERE s."{edge.source_column}" IS NOT NULL)                    AS l_distinct,
                    (SELECT COUNT(*) FROM "{edge.source_table}" s
                       WHERE s."{edge.source_column}" IS NOT NULL)                    AS l_rows,
                    (SELECT COUNT(DISTINCT t."{edge.target_column}") FROM "{edge.target_table}" t
                       WHERE t."{edge.target_column}" IS NOT NULL)                    AS r_distinct,
                    (SELECT COUNT(*) FROM "{edge.target_table}" t
                       WHERE t."{edge.target_column}" IS NOT NULL)                    AS r_rows
            ''', limit=1)
        except Exception as exc:  # noqa: BLE001 - a failed probe must not stop the build
            edge.evidence += f"; orientation probe failed: {type(exc).__name__}"
            continue
        if not res.rows:
            continue
        l_distinct, l_rows, r_distinct, r_rows = (x or 0 for x in res.rows[0])
        if not l_rows or not r_rows:
            continue
        left_unique = l_distinct / l_rows > 0.99
        right_unique = r_distinct / r_rows > 0.99
        if left_unique and not right_unique:
            edge.source_table, edge.target_table = edge.target_table, edge.source_table
            edge.source_column, edge.target_column = edge.target_column, edge.source_column
            edge.evidence += ("; oriented from the data — the export's left side is the "
                              "unique key, so it is the parent")


def bridge_edges(registry) -> list[JoinEdge]:
    """The identity edges that span sources.

    Every source graph is built in isolation, so no edge inside one can ever
    reach another. Merged, they are three islands: the agent sees all 38 tables
    and still cannot answer "how much leave has this crew member taken", because
    leave is in CLMS and the crew member was named by an IGA that only PEP and
    CrewPortal use.

    The registry declares the bridge — one table carrying both keys. Everything
    hangs off it:

        PEP.EMPLOYEE_INFO.IGA  <-  M_CREW_DETAILS.IGA
                                   M_CREW_DETAILS.CREW_ID  ->  CLMS.M_CLMS_CREW.CREW_ID

    Asserted, not inferred: no name-similarity score would ever propose joining
    a column called CREW_ID to one called IGA. `verify_edges` still has to
    confirm them against data, and a bridge that does not resolve is worse than
    none — it silently produces empty results for every cross-source question.
    """
    bridge = (registry.identity or {}).get("bridge") or {}
    table = bridge.get("table")
    if not table:
        return []

    hub_source = bridge.get("source")
    hub = None
    for s in registry.available():
        if s.name == hub_source and table in s.tables:
            hub = s
            break
    if hub is None:
        return []

    hub_cols = {c.name.upper(): c.name for c in hub.tables[table].columns}
    out: list[JoinEdge] = []
    for other in registry.available():
        if other.name == hub.name:
            continue
        master = other.crew_master
        if not master or master not in other.tables:
            continue
        aliases = {a.upper() for a in (other.key_aliases or [other.join_key.upper()])}
        # The column the other source's crew master is keyed on, and the column
        # on the hub that carries the same identity.
        master_key = next(
            (c.name for c in other.tables[master].columns if c.name.upper() in aliases), None
        )
        hub_key = next((hub_cols[a] for a in aliases if a in hub_cols), None)
        if not master_key or not hub_key:
            continue
        out.append(JoinEdge(
            source_table=table,
            target_table=master,
            source_column=hub_key,
            target_column=master_key,
            confidence=0.90,
            source=JoinSource.ASSERTED,
            evidence=(
                f"registry-declared identity bridge: {table} ({hub.name}) carries "
                f"{hub_key}, which is {other.name}'s identity key on {master}"
            ),
            cardinality="one-to-one",
        ))
    return out


# ─── Inferred ───────────────────────────────────────────────────────────────


def _is_denormalised(table: str, column: str) -> bool:
    """A denormalised copy is not a foreign key, however much it looks like one.

    `PEP_CATEGORY.TEMPLATE_ID` records only the originating template, so every
    row carries the same value. It joins `PEP_TEMPLATE` at 100% coverage and is
    completely wrong to filter on — a query for the Lead template through it
    returns zero rows while looking entirely reasonable. Advertising it as a
    usable join is how the retrieval agent ended up writing exactly that query.
    """
    from crew_perf.graph.schema import CONFIRMED_COLUMN_NOTES

    note = CONFIRMED_COLUMN_NOTES.get((table.upper(), column.upper()), "")
    return note.startswith("DENORMALISED") or note.startswith("Denormalised")


def _is_key_like(name: str, role: ColumnRole, table: str | None = None) -> bool:
    upper = name.upper()
    if upper in NON_JOINING:
        return False
    if table and _is_denormalised(table, name):
        return False
    return role == ColumnRole.IDENTITY or upper.endswith("_ID") or upper.endswith("_CODE")


def _stem(name: str) -> str:
    """Reduce a name to a comparable stem: drop table prefixes and key suffixes.

    `PEP_SCHDULER_ID` -> `SCHDULER`, `PEP_SCHEDULER` -> `SCHEDULER`. The two are
    then close enough for fuzzy matching to relate them despite the source
    schema's misspelling, which no exact-match rule would ever catch.
    """
    up = name.upper().strip()
    for suffix in ("_ID", "_CODE", "ID"):
        if up.endswith(suffix) and len(up) > len(suffix) + 2:
            up = up[: -len(suffix)]
            break
    parts = [p for p in up.split("_") if p]
    # Drop conventional prefixes that carry no identity (M_ master, T_ transaction,
    # and the source's own namespace like PEP_).
    while len(parts) > 1 and parts[0] in {"M", "T", "L", "WF", "PEP"}:
        parts = parts[1:]
    return "".join(parts)


def _similar(a: str, a2: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, a2).ratio()


def _head_noun(table: str) -> str:
    """What a table is a table OF: the last word of its name, prefixes dropped.

    `MENTOR_FEEDBACK` -> FEEDBACK, `PEP_QUESTION_FEEDBACK` -> FEEDBACK,
    `T_CLMS_LEAVE_REQ_MASTER` -> MASTER. Qualifiers narrow the head noun; they
    never replace it, which is what makes this the right thing to match a
    foreign key's name against.
    """
    parts = [p for p in table.upper().split("_") if p]
    while len(parts) > 1 and parts[0] in {"M", "T", "L", "WF", "PEP"}:
        parts = parts[1:]
    return parts[-1] if parts else ""


def infer_edges(source: SourceSchema) -> tuple[list[JoinEdge], list[dict]]:
    """Propose JOINS_TO edges from column-name, type and role evidence.

    Where the column name identifies its owning table, a directed child->parent
    edge is proposed with real confidence. Where it does not, *both* directions
    are proposed at low confidence and left for data verification to settle —
    guessing a direction from names alone is how inferred graphs acquire
    confidently-wrong edges.

    Only runs on `columns`-detail sources; a table-only schema has nothing to
    infer from and must rely on declared FKs.
    """
    if not source.has_columns:
        return [], [{"reason": "source has no column detail; inference not possible",
                     "source": source.name}]

    tables = source.tables
    edges: list[JoinEdge] = []
    skipped: list[dict] = []

    holders: dict[str, list[TableSchema]] = {}
    for table in tables.values():
        for col in table.columns:
            if _is_key_like(col.name, col.role, table.name):
                holders.setdefault(col.name.upper(), []).append(table)

    def add(child: TableSchema, parent: TableSchema, col_name: str,
            confidence: float, why: list[str]) -> None:
        src_col = next(c for c in child.columns if c.name.upper() == col_name)
        tgt_col = next(c for c in parent.columns if c.name.upper() == col_name)
        conf, reasons = confidence, list(why)
        if src_col.data_type == tgt_col.data_type:
            conf += 0.10
            reasons.append(f"matching data type ({src_col.data_type})")
        else:
            conf -= 0.15
            reasons.append(f"TYPE MISMATCH ({src_col.data_type} vs {tgt_col.data_type})")
        edges.append(
            JoinEdge(
                source_table=child.name,
                target_table=parent.name,
                source_column=src_col.name,
                target_column=tgt_col.name,
                confidence=round(max(0.05, min(conf, 0.95)), 2),
                source=JoinSource.INFERRED,
                evidence="; ".join(reasons),
                cardinality="many-to-one",
            )
        )

    join_key = (source.join_key or "").upper()

    for col_name, carriers in holders.items():
        if len(carriers) < 2:
            continue

        # The registry-declared identity key is the backbone of the whole graph
        # and must never be dropped for being widespread — being widespread is
        # precisely what makes it the identity key. Its owning table rarely
        # name-matches it either (IGA lives in EMPLOYEE_INFO), so propose every
        # direction and let the uniqueness test in verification pick the master.
        if col_name == join_key and len(carriers) <= 12:
            for child in carriers:
                for parent in carriers:
                    if child.name == parent.name:
                        continue
                    add(child, parent, col_name, 0.35,
                        [f"registry-declared identity key {col_name} for source {source.name}",
                         "master table resolved by uniqueness during data verification"])
            continue

        if len(carriers) > max(4, 0.6 * len(tables)):
            skipped.append({
                "column": col_name,
                "reason": f"present in {len(carriers)}/{len(tables)} tables — too "
                          f"widespread to be a join key",
            })
            continue

        col_stem = _stem(col_name)
        scored = [(t, _similar(col_stem, _stem(t.name))) for t in carriers]
        best_table, best_score = max(scored, key=lambda p: p[1])

        if best_score >= 0.80:
            # The column names its owner; everything else referencing it is a child.
            for table in carriers:
                if table.name == best_table.name:
                    continue
                why = [f"shared key-like column {col_name}",
                       f"column stem {col_stem!r} matches target table stem "
                       f"{_stem(best_table.name)!r} ({best_score:.0%})"]
                add(table, best_table, col_name, 0.65, why)

            # Name similarity picks a *plausible* owner, not a measured one, and
            # when it picks wrong there is no edge left for verification to
            # promote. `SN_REPORT_ID` reads as belonging to SN_REPORT_CREW rather
            # than to SN_FLIGHT_REPORT — a closer string match — which left the
            # source's primary join sitting at 0.45 "likely the wrong direction"
            # with no correct edge anywhere in the graph, so the validator
            # refused every query joining a report to the crew who operated it.
            # With few enough carriers the remaining directions cost one probe
            # each and let measured uniqueness settle the parent, exactly as the
            # identity key above already does.
            if len(carriers) <= 4:
                proposed = {(t.name, best_table.name) for t in carriers}
                for child in carriers:
                    for parent in carriers:
                        if child.name == parent.name:
                            continue
                        if (child.name, parent.name) in proposed:
                            continue
                        add(child, parent, col_name, 0.25,
                            [f"shared key-like column {col_name}",
                             f"name evidence favoured {best_table.name} as the owner; this "
                             f"direction is proposed so data verification can settle it"])
        elif len(carriers) <= 4:
            # Direction unknown. Propose both ways cheaply; verification decides.
            for child in carriers:
                for parent in carriers:
                    if child.name == parent.name:
                        continue
                    add(child, parent, col_name, 0.30,
                        [f"shared key-like column {col_name}",
                         "direction unresolved from names — pending data verification"])
        else:
            skipped.append({
                "column": col_name,
                "reason": f"shared by {len(carriers)} tables with no name-identifiable "
                          f"owner; too ambiguous to propose",
            })

    edges.extend(_surrogate_key_edges(tables, {e.key() for e in edges}))
    return edges, skipped


def _surrogate_key_edges(tables: dict[str, TableSchema], seen: set[tuple]) -> list[JoinEdge]:
    """`PEP_QUESTION_FEEDBACK.FEEDBACK_ID -> MENTOR_FEEDBACK.ID`.

    A foreign key that names its parent table and points at that table's bare
    surrogate key. Every name-equality rule above is blind to this shape twice
    over: the two columns are not spelled the same, and bare `ID` sits in
    NON_JOINING because every table has one and matching on it would relate all
    of them.

    The cost of the blind spot was concrete. This is the join the scoring agent
    itself writes to reach a question answer's assessment, and with it missing
    from the graph the retrieval agent could not answer "which crew failed a
    safety question" at all — the validator refused the only correct query, and
    the agent reported the data unavailable when it was sitting right there.

    Proposed at low confidence and settled by data verification, like everything
    else: the name evidence here is real but weak, and `ID` being unique in the
    parent is what actually establishes the direction.
    """
    out: list[JoinEdge] = []
    for child in tables.values():
        for col in child.columns:
            upper = col.name.upper()
            if not upper.endswith("_ID") or upper in NON_JOINING:
                continue
            stem = _stem(col.name)
            if len(stem) < 5:
                continue                      # too short for containment to mean anything
            for parent in tables.values():
                if parent.name == child.name:
                    continue
                pk = next((c for c in parent.columns if c.name.upper() == "ID"), None)
                if pk is None:
                    continue
                # Match the parent's HEAD NOUN, not merely a substring of its
                # name. Substring matching proposed `PEP_QUESTIONS.QUESTION_ID ->
                # PEP_QUESTION_FEEDBACK.ID`, which verified at 100% coverage
                # purely because 150 question ids all fall inside 186,709 feedback
                # ids — a coincidence of integer ranges that would have joined
                # every question to an unrelated answer row and looked like data.
                # `PEP_QUESTION_FEEDBACK` is a table of feedback, not of
                # questions, and its head noun says so.
                parent_stem = _stem(parent.name)
                head = _head_noun(parent.name)
                score = max(_similar(stem, head), 0.0)
                if score < 0.85 and _similar(stem, parent_stem) < 0.80:
                    continue
                edge = JoinEdge(
                    source_table=child.name, target_table=parent.name,
                    source_column=col.name, target_column=pk.name,
                    confidence=0.50,
                    source=JoinSource.INFERRED,
                    evidence=(
                        f"{col.name} names what {parent.name} is a table OF (stem "
                        f"{stem!r} vs head noun {head!r}, {score:.0%} match) and that "
                        f"table's key is a bare ID"
                    ),
                    cardinality="many-to-one",
                )
                if edge.key() not in seen:
                    seen.add(edge.key())
                    out.append(edge)
    return out


# ─── Reference/lookup joins ─────────────────────────────────────────────────


def infer_lookup_edges(source: SourceSchema, executor, max_ref_rows: int = 500) -> list[JoinEdge]:
    """Propose joins into small reference tables, which key off *values*.

    The `_ID`-based inference above never finds these: `MENTOR_FEEDBACK.GRADE`
    joins `PEP_GRADE.GRADE`, and `PEP_SCHEDULER.STATUS` (a NUMBER) joins
    `PEP_STATUS.STATUS_ID` — neither pair looks like a foreign key by name. Yet
    these tables carry the domain semantics (what a grade means, what a status is),
    so leaving them isolated strands exactly the vocabulary the scoring agents
    need.

    Candidates are proposed on type compatibility plus weak name evidence, then
    settled by the same value-overlap verification as everything else. Only small
    tables are treated as reference tables, which keeps the probe count bounded.
    """
    if not source.has_columns:
        return []

    available = set(executor.list_tables())
    row_counts: dict[str, int] = {}
    for name in source.tables:
        if name not in available:
            continue
        try:
            res = executor.execute(f'SELECT COUNT(*) FROM "{name}"', limit=1)
            row_counts[name] = int(res.rows[0][0]) if res.rows else 0
        except Exception:  # noqa: BLE001
            continue

    ref_tables = [n for n, c in row_counts.items() if 0 < c <= max_ref_rows]
    fact_tables = [n for n, c in row_counts.items() if c > max_ref_rows]
    edges: list[JoinEdge] = []
    skip_roles = {ColumnRole.AUDIT, ColumnRole.TEMPORAL_CONTROL, ColumnRole.TEMPORAL,
                  ColumnRole.FREE_TEXT}

    def joinable(col, table_name: str) -> bool:
        """A join key must discriminate. Booleans and flags never do.

        `EMPLOYEE_INFO.ACTIVE = PEP_TEMPLATE.ACTIVE` passes every name and
        coverage test — 100% name match, ~95% of values "resolve" — and is
        completely meaningless: every `true` matches every `true`, so the join
        multiplies rows instead of relating them. Coverage cannot catch this
        because coverage is trivially near-perfect. Exclude by type up front,
        and by measured cardinality during verification.
        """
        if col.role in skip_roles or col.name.upper() in NON_JOINING_LOOKUP:
            return False
        if _is_denormalised(table_name, col.name):
            return False
        return col.data_type.upper() != "BOOLEAN"

    for ref_name in ref_tables:
        ref = source.tables[ref_name]
        ref_stem = _stem(ref_name)
        for ref_col in ref.columns:
            if not joinable(ref_col, ref_name):
                continue
            for fact_name in fact_tables:
                fact = source.tables[fact_name]
                for fact_col in fact.columns:
                    if not joinable(fact_col, fact_name):
                        continue
                    if fact_col.data_type != ref_col.data_type:
                        continue

                    fname, rname = fact_col.name.upper(), ref_col.name.upper()
                    name_sim = _similar(_stem(fname), _stem(rname))
                    stem_hit = ref_stem and ref_stem in fname.replace("_", "")
                    if name_sim < 0.75 and not stem_hit:
                        continue

                    why = [f"reference lookup into {ref_name} ({row_counts[ref_name]} rows)"]
                    if name_sim >= 0.75:
                        why.append(f"column names align ({fname} ~ {rname}, {name_sim:.0%})")
                    if stem_hit:
                        why.append(f"column name carries reference table stem {ref_stem!r}")
                    edges.append(
                        JoinEdge(
                            source_table=fact_name,
                            target_table=ref_name,
                            source_column=fact_col.name,
                            target_column=ref_col.name,
                            confidence=0.35,
                            source=JoinSource.INFERRED,
                            evidence="; ".join(why),
                            cardinality="many-to-one",
                        )
                    )
    return edges


# ─── Data verification ──────────────────────────────────────────────────────


def verify_edges(
    edges: list[JoinEdge],
    executor,
    available_tables: set[str],
    scd2_tables: set[str] | None = None,
) -> list[JoinEdge]:
    """Check each proposed join against real data and record coverage.

    Coverage is the fraction of non-null child values that find a parent. A join
    that resolves ~100% is almost certainly real; one that resolves 0% is almost
    certainly a name coincidence, and is demoted rather than deleted so the
    reasoning stays visible.

    `scd2_tables` must be supplied for any versioned source. Without it the
    uniqueness test counts superseded history rows and concludes that a perfectly
    good primary key is non-unique — the same P_IS_CURRENT trap (G7) that this
    verification exists to keep out of Agent 3's queries.
    """
    scd2_tables = scd2_tables or set()

    for edge in edges:
        if edge.source_table not in available_tables or edge.target_table not in available_tables:
            continue
        def current(alias: str, table: str) -> str:
            return f" AND {alias}.P_IS_CURRENT = TRUE" if table in scd2_tables else ""

        child_where = (f'WHERE s."{edge.source_column}" IS NOT NULL'
                       + current("s", edge.source_table))
        parent_where = (f'WHERE t."{edge.target_column}" IS NOT NULL'
                        + current("t", edge.target_table))
        # Coverage settles whether the join is real; target uniqueness settles
        # which side is the parent. A "parent" whose key repeats is not a parent.
        sql = f'''
            SELECT
                (SELECT COUNT(*) FROM "{edge.source_table}" s {child_where})    AS child_rows,
                (SELECT COUNT(*) FROM "{edge.source_table}" s {child_where}
                    AND EXISTS (SELECT 1 FROM "{edge.target_table}" t {parent_where}
                                  AND t."{edge.target_column}" = s."{edge.source_column}"))
                                                                                AS matched,
                (SELECT COUNT(*) FROM "{edge.target_table}" t {parent_where})    AS parent_rows,
                (SELECT COUNT(DISTINCT t."{edge.target_column}")
                   FROM "{edge.target_table}" t {parent_where})                  AS parent_distinct
        '''
        try:
            res = executor.execute(sql, limit=1)
        except Exception as exc:  # noqa: BLE001 - a failed probe must not stop the build
            edge.evidence += f"; verification failed: {type(exc).__name__}"
            continue
        if not res.rows:
            continue

        child_rows, matched, parent_rows, parent_distinct = (x or 0 for x in res.rows[0])
        if not child_rows:
            edge.evidence += "; no non-null child values to verify against"
            continue

        coverage = float(matched) / float(child_rows)
        uniqueness = (float(parent_distinct) / float(parent_rows)) if parent_rows else 0.0
        edge.coverage = round(coverage, 4)
        edge.cardinality = "many-to-one" if uniqueness > 0.99 else "many-to-many"

        # A low-cardinality column that is *not* the parent's key relates
        # nothing — it multiplies rows. Coverage is blind to this (a flag
        # resolves ~100% of the time by construction), so cardinality has to be
        # measured. Checked before the coverage tiers, or such an edge gets
        # promoted to "verified" on the strength of that meaningless coverage.
        #
        # Uniqueness is what separates the two cases: PEP_DESIGNATION has just
        # two rows, but DESIGNATION_CODE is unique within it and is a perfectly
        # good join key. PEP_TEMPLATE.ACTIVE has one distinct value across four
        # rows and is a flag. Low cardinality alone would reject both.
        degenerate = parent_distinct <= 2 and uniqueness < 0.99
        if degenerate:
            edge.verified = False
            edge.confidence = 0.05
            edge.evidence += (
                f"; DEGENERATE — the key has only {parent_distinct} distinct value(s), "
                f"so this join multiplies rows rather than relating them"
            )
            continue

        # An ASSERTED identity edge that resolves only partly is a statement about
        # the two POPULATIONS, not about the join. ServiceNow's extract names
        # 13,996 crew and the modelled fleet has 800, of whom ~18% appear in it;
        # IGA still means IGA on both sides, and the target key is unique, so the
        # join returns exactly the right rows for the crew it covers. Treating
        # that as refutation would delete the only route to the source and every
        # question about crew feedback would answer "no data".
        #
        # ZERO overlap is a different thing entirely and stays refuted below: it
        # is what a genuinely wrong assertion looks like — CLMS.CREW_ID joined
        # straight to PEP.IGA matches nothing, and an edge like that produces
        # silent empty answers, which is worse than having no edge.
        # Bounded to the range the tiers below would REFUTE. At 97.8% —
        # M_CREW_DETAILS.CREW_ID against CLMS — "partially verified" is the right
        # label and this branch must not steal it; the case that needs rescuing
        # is the one that would otherwise be thrown out as a name coincidence.
        if (edge.source in (JoinSource.ASSERTED, JoinSource.DECLARED)
                and uniqueness > 0.99 and 0.01 < coverage < 0.80):
            edge.verified = False
            edge.confidence = max(edge.confidence, 0.85)
            edge.evidence += (
                f"; identity assertion holds (target key is unique) but the populations "
                f"differ — only {coverage:.1%} of child rows have a counterpart. Joins "
                f"through this edge are correct for those rows and SILENTLY EXCLUDE the "
                f"remaining {1 - coverage:.1%}"
            )
            continue

        if coverage >= 0.99 and uniqueness > 0.99:
            edge.verified = True
            edge.confidence = max(edge.confidence, 0.95)
            edge.evidence += (
                f"; VERIFIED against data ({coverage:.1%} of child rows resolve; "
                f"target key is unique)"
            )
        elif coverage >= 0.99:
            # Real overlap, but the target repeats its key — this is the child
            # side of the relationship, not the parent. Keep it, flag it clearly.
            edge.confidence = round(min(edge.confidence, 0.45), 2)
            edge.evidence += (
                f"; overlap confirmed ({coverage:.1%}) but target key is NOT unique "
                f"({parent_distinct}/{parent_rows}) — likely the wrong direction"
            )
        elif coverage >= 0.80:
            edge.verified = True
            edge.confidence = max(edge.confidence, 0.80)
            edge.evidence += f"; partially verified ({coverage:.1%} resolve)"
        else:
            edge.confidence = round(min(edge.confidence, 0.20), 2)
            edge.evidence += (
                f"; REFUTED by data — only {coverage:.1%} of child rows resolve to a parent"
            )
    return edges


def prune_refuted(report: "JoinReport", threshold: float = 0.30) -> "JoinReport":
    """Drop edges that data refuted, keeping the reasoning in `skipped`.

    Refuted edges are removed rather than merely down-weighted because Agent 3
    is allowed to join on anything present in the graph — leaving a disproved
    edge in place is an invitation to generate a query that returns nothing.
    """
    kept, dropped = [], []
    for edge in report.edges:
        if edge.coverage is not None and edge.confidence < threshold:
            dropped.append({
                "column": edge.source_column,
                "reason": f"{edge.source_table} -> {edge.target_table}: {edge.evidence}",
            })
        else:
            kept.append(edge)
    report.edges = kept
    report.skipped.extend(dropped)
    return report


def resolve_joins(source: SourceSchema, executor=None, registry=None) -> JoinReport:
    """Full pipeline: declared, then the asserted identity spine, then inference.

    The declared tier reads the curated relationship export. It goes first so its
    edges own the dedup: where the export and inference describe the same join,
    the stated one wins and inference does not get to overwrite it with a weaker
    reading of the same two columns.

    Everything is still checked against real rows afterwards. Being declared
    raises where an edge starts, not whether it has to survive the data.
    """
    report = JoinReport()
    seen: set[tuple] = set()

    # Loaded here when not supplied so every existing caller gets the declared
    # tier without threading a registry through. Passing one explicitly is for
    # tests, which build a SourceSchema by hand — `declared_edges` filters to the
    # source's own tables, so a fixture that does not contain them gets nothing.
    if registry is None:
        from crew_perf.sources import load_registry

        registry = load_registry()

    # Oriented before anything is deduped, because `key()` carries the direction:
    # a declared edge entered from the wrong end and the inferred edge for the
    # same relationship are two different keys, so both would survive and the
    # graph would hold one relationship twice, pointing opposite ways.
    declared = declared_edges(source, registry) if registry is not None else []
    if declared and executor is not None:
        orient_declared(declared, executor, set(executor.list_tables()))
    for edge in declared:
        if edge.key() not in seen:
            seen.add(edge.key())
            report.edges.append(edge)

    for edge in identity_edges(source):
        if edge.key() not in seen:
            seen.add(edge.key())
            report.edges.append(edge)

    inferred, skipped = infer_edges(source)
    report.skipped.extend(skipped)
    for edge in inferred:
        if edge.key() not in seen:
            seen.add(edge.key())
            report.edges.append(edge)

    if executor is not None:
        for edge in infer_lookup_edges(source, executor):
            if edge.key() not in seen:
                seen.add(edge.key())
                report.edges.append(edge)

        available = set(executor.list_tables())
        scd2 = {t.name for t in source.tables.values() if t.is_scd2}
        verify_edges(report.edges, executor, available, scd2_tables=scd2)

    return report
