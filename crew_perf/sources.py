"""Schema registry loader.

The donor hardcoded one `_load_*` method per database (six of them), which is why
adding a source there meant editing the agent. Here every source is declared in
`schemas/registry.yaml` and read through one path.

Two schema shapes exist and the difference is load-bearing:
  `columns` — full column-level detail; supports role tagging, SCD-2 detection,
              and join-key inference.
  `tables`  — table name + description only; joins must be asserted in the
              registry, and column roles are simply unknown.

Code that treats a `tables` source as if it had columns will silently produce an
empty column list rather than an error, so `TableSchema.has_columns` exists to
make the distinction explicit at every call site that cares.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from crew_perf import config
from crew_perf.graph.schema import (
    ColumnRole, classify_column, is_pii_column, is_scd2_table,
)


@dataclass
class ColumnSchema:
    name: str
    data_type: str
    role: ColumnRole
    comment: str = ""

    @property
    def is_scorable(self) -> bool:
        """Only measures are eligible to become scoring attributes."""
        return self.role == ColumnRole.MEASURE


@dataclass
class TableSchema:
    name: str
    source: str
    description: str = ""
    columns: list[ColumnSchema] = field(default_factory=list)
    in_subset: bool = True   # present in the narrower confirmed-in-Snowflake view
    # The Snowflake schema this table lives in, straight off the export's
    # TABLE_SCHEMA column. Locally everything lands in one DuckDB namespace, so
    # this is unused until SNOWFLAKE_MODE=snowflake — at which point it is the
    # only thing that can tell PEP.EMPLOYEE_INFO from CREWPORTAL.M_CREW_DETAILS.
    warehouse_schema: str = ""

    @property
    def has_columns(self) -> bool:
        return bool(self.columns)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def is_scd2(self) -> bool:
        return is_scd2_table(self.column_names) if self.has_columns else False

    def columns_by_role(self, role: ColumnRole) -> list[ColumnSchema]:
        return [c for c in self.columns if c.role == role]

    @property
    def identity_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.role == ColumnRole.IDENTITY]

    def business_columns(self) -> list[ColumnSchema]:
        """Columns worth showing an LLM — audit and SCD-2 control noise removed.

        These are ~40% of every PEP table and are mechanically identifiable, so
        spending tokens (and risking drift) on them is waste.
        """
        skip = {ColumnRole.AUDIT, ColumnRole.TEMPORAL_CONTROL}
        return [c for c in self.columns if c.role not in skip]


@dataclass
class SourceSchema:
    name: str
    engine: str
    detail: str
    join_key: str
    context: str
    tables: dict[str, TableSchema] = field(default_factory=dict)
    relevance_gate: bool = False
    onboarded: bool = False
    available: bool = True
    crew_master: str = ""          # the table whose row IS a crew member
    key_aliases: list = field(default_factory=list)  # other spellings of join_key

    @property
    def has_columns(self) -> bool:
        return self.detail == "columns"

    @property
    def is_warehouse(self) -> bool:
        """True when this source's tables live in Snowflake.

        ServiceNow is the one that does not: it arrives as a file extract and is
        ingested locally, so its tables exist in DuckDB and in no Snowflake
        schema. Sending one of them to the warehouse would fail on a table that
        is sitting on disk — see `data/scope.py`, which routes on this.
        """
        return self.engine.lower() == "snowflake"

    @property
    def warehouse_schema(self) -> str:
        """The Snowflake schema this source's tables live in.

        Read off the export rather than declared, so it cannot drift from the
        live system. An env override exists for the case the registry cannot
        cover — a warehouse whose schemas were renamed on the way in — and is
        applied at the connector, not here, so this stays a statement of fact
        about the extract.
        """
        seen = [t.warehouse_schema for t in self.tables.values() if t.warehouse_schema]
        return seen[0] if seen else self.name.upper()

    def __len__(self) -> int:
        return len(self.tables)


@dataclass
class Registry:
    sources: dict[str, SourceSchema]
    identity: dict
    # What was decided to be out of scope, and what it would have supplied.
    # Declared here rather than written into each agent's prompt: the retrieval
    # prompt used to carry "there are NO duty hours, NO flight hours..." as
    # literal text, so onboarding a roster source would have left four agents
    # confidently refusing questions the warehouse could now answer.
    descoped: list[dict] = field(default_factory=list)
    # Relationships stated by the curated export, keyed by source name, and what
    # in it could not be resolved. Both are carried: the notes are the only place
    # a curated join that failed to load becomes visible.
    declared_joins: dict[str, list["DeclaredJoin"]] = field(default_factory=dict)
    declared_join_notes: list[dict] = field(default_factory=list)

    def available(self) -> list[SourceSchema]:
        return [s for s in self.sources.values() if s.available]

    def out_of_scope(self) -> list[str]:
        """What no onboarded source supplies, phrased for a prompt."""
        return sorted({
            item
            for entry in self.descoped
            for item in (entry.get("would_have_supplied") or [])
        })

    def __getitem__(self, name: str) -> SourceSchema:
        return self.sources[name]

    def warehouse_layout(self) -> dict[str, str]:
        """`TABLE_NAME -> SCHEMA` across every onboarded source in the warehouse.

        Generated SQL names tables bare, because locally they all sit in one
        DuckDB namespace. Snowflake spreads the same tables over three schemas,
        and a session can only default to one — so without this map every
        cross-source query, which is most of the interesting ones, fails on the
        first table outside the session schema. Keys are upper-cased: Snowflake
        folds unquoted identifiers, and the exports do not agree on case.

        Warehouse sources only. A local extract has no Snowflake schema to be
        qualified with, and inventing one for it produces a query that resolves
        to nothing — which is worse than the bare name, because the bare name at
        least reaches the engine that does hold the table.
        """
        layout: dict[str, str] = {}
        for source in self.available():
            if not source.is_warehouse:
                continue
            schema = source.warehouse_schema
            for table in source.tables.values():
                layout[table.name.upper()] = table.warehouse_schema or schema
        return layout


# ─── Parsing ────────────────────────────────────────────────────────────────


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _load_column_schema(path: Path, source: str) -> dict[str, TableSchema]:
    """Column-level CSV: TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, [COMMENT].

    Personal data is dropped here, at the one place every consumer reads the
    schema through. The warehouse hashes it behind the crew identifier, so a name
    or an email is not the crew member — IGA is — and a column nobody can select
    is the only version of that rule that cannot be forgotten by a prompt.
    """
    tables: dict[str, TableSchema] = {}
    for row in _read_csv(path):
        tname = (row.get("TABLE_NAME") or "").strip()
        cname = (row.get("COLUMN_NAME") or "").strip()
        if not tname or not cname or is_pii_column(cname):
            continue
        dtype = (row.get("DATA_TYPE") or "").strip()
        table = tables.setdefault(
            tname,
            TableSchema(name=tname, source=source,
                        warehouse_schema=(row.get("TABLE_SCHEMA") or "").strip().upper()),
        )
        table.columns.append(
            ColumnSchema(
                name=cname,
                data_type=dtype,
                role=classify_column(cname, dtype, table=tname),
                comment=(row.get("COMMENT") or "").strip(),
            )
        )
    return tables


def _load_table_schema(path: Path, source: str) -> dict[str, TableSchema]:
    """Table-level CSV: TABLE_NAME, Description/DESCRIPTION. No columns."""
    tables: dict[str, TableSchema] = {}
    for row in _read_csv(path):
        tname = (row.get("TABLE_NAME") or "").strip()
        if not tname:
            continue
        desc = (row.get("Description") or row.get("DESCRIPTION") or "").strip()
        if tname in tables:
            # CrewPortal lists a couple of tables twice with different wording;
            # keep both descriptions rather than letting one silently win.
            existing = tables[tname].description
            if desc and desc not in existing:
                tables[tname].description = f"{existing} {desc}".strip()
            continue
        tables[tname] = TableSchema(name=tname, source=source, description=desc)
    return tables


# ─── The curated relationship export ────────────────────────────────────────
#
# `CrewPerformance_Metadata.csv` is the one thing the source systems never
# shipped: a human statement of which columns actually join, per report, with the
# reason each table was selected. Everything else here is a column dump, which is
# why `agents/joins.py` has to infer almost every edge from column naming.
#
# It is a *report* export, not a schema export, and the difference shows: table
# and column names are written in the report author's casing (`MentorFeedBack`,
# `T_FlightIssueGeneralInfo`) rather than the warehouse's (`MENTOR_FEEDBACK`,
# `T_FLIGHT_ISSUE_GENERAL_INFO`), several rows describe a table in prose instead
# of naming a join ("Parent Transaction Table"), and it covers tables that are
# not in the scoped exports at all. So every name is resolved against the exports
# rather than trusted, and anything that does not resolve is recorded in
# `Registry.declared_join_notes` instead of being dropped silently — a curated
# join that quietly failed to load is worse than one that was never written,
# because nobody goes looking for it.


@dataclass
class DeclaredJoin:
    """One relationship the export states, resolved onto real table/column names."""

    source: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    report: str                    # which report this join was documented for
    why: str                       # the export's "Why Selected" for the row
    raw: str                       # the relationship text, verbatim


def _norm(name: str) -> str:
    """Fold a name to compare across casing conventions: `M_CrewDetails` -> MCREWDETAILS."""
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


# `A.B = C.D`, then `A = C.D`, then `A = B`. Tried in that order: the first is
# unambiguous, and each later form needs more context to place its columns.
_QUALIFIED = re.compile(r"(\w+)\s*\.\s*(\w+)\s*[=→↔]\s*(\w+)\s*\.\s*(\w+)")
_LEFT_BARE = re.compile(r"(?:^|[.;]\s|\b)(\w+)\s*[=→↔]\s*(\w+)\s*\.\s*(\w+)")
_BOTH_BARE = re.compile(r"^\s*(\w+)\s*[=→↔]\s*(\w+)\s*$")


def _load_declared_joins(
    path: Path, sources: dict[str, SourceSchema], db_names: dict[str, str],
) -> tuple[dict[str, list[DeclaredJoin]], list[dict]]:
    """Parse the relationship export, resolving every name against the exports."""
    out: dict[str, list[DeclaredJoin]] = {}
    notes: list[dict] = []
    if not path.exists():
        return out, notes

    # norm(table) -> (source, real name), and real table -> norm(col) -> real col
    table_by_norm: dict[str, tuple[str, str]] = {}
    col_by_norm: dict[str, dict[str, str]] = {}
    for src in sources.values():
        for table in src.tables.values():
            table_by_norm[_norm(table.name)] = (src.name, table.name)
            col_by_norm[table.name] = {_norm(c.name): c.name for c in table.columns}

    rows = _read_csv(path)
    # The first row of each (db, report) block is its parent entity — "ReportId
    # drives all downstream joins". Used only to place a bare column that the
    # row's own table cannot claim.
    parent_of: dict[tuple, str] = {}
    for row in rows:
        key = ((row.get("DB Name") or "").strip(), (row.get("Report Name") or "").strip())
        resolved = table_by_norm.get(_norm((row.get("Table Name") or "").strip()))
        if resolved and key not in parent_of:
            parent_of[key] = resolved[1]

    grouped: dict[tuple, list[str]] = {}
    for row in rows:
        key = ((row.get("DB Name") or "").strip(), (row.get("Report Name") or "").strip())
        resolved = table_by_norm.get(_norm((row.get("Table Name") or "").strip()))
        if resolved:
            grouped.setdefault(key, []).append(resolved[1])

    for row in rows:
        db = (row.get("DB Name") or "").strip()
        report = (row.get("Report Name") or "").strip()
        raw_table = (row.get("Table Name") or "").strip()
        rel = " ".join((row.get("Relationship / Join") or "").split())
        why = " ".join((row.get("Why Selected") or "").split())
        key = (db, report)

        source_name = db_names.get(_norm(db))
        resolved = table_by_norm.get(_norm(raw_table))
        if not source_name or not resolved:
            notes.append({"table": raw_table, "report": report,
                          "reason": "not in the scoped column exports, so nothing can "
                                    "query it — the relationship is not loadable"})
            continue
        row_table = resolved[1]
        peers = grouped.get(key, [])

        def place(col: str, prefer: list[str], exclude: str = "") -> tuple[str, str] | None:
            """Which table in this report block owns a bare column name."""
            for table in [*prefer, *peers]:
                if table == exclude or table not in col_by_norm:
                    continue
                real = col_by_norm[table].get(_norm(col))
                if real:
                    return table, real
            return None

        pair = None
        m = _QUALIFIED.search(rel)
        if m:
            lt, rt = table_by_norm.get(_norm(m.group(1))), table_by_norm.get(_norm(m.group(3)))
            if lt and rt:
                lc = col_by_norm[lt[1]].get(_norm(m.group(2)))
                rc = col_by_norm[rt[1]].get(_norm(m.group(4)))
                if lc and rc:
                    pair = (lt[1], lc, rt[1], rc)
        elif (m := _LEFT_BARE.search(rel)):
            rt = table_by_norm.get(_norm(m.group(2)))
            left = place(m.group(1), [row_table])
            if left and rt:
                rc = col_by_norm[rt[1]].get(_norm(m.group(3)))
                if rc:
                    pair = (left[0], left[1], rt[1], rc)
        elif (m := _BOTH_BARE.match(rel)):
            # Neither side is qualified, and the export is not consistent about
            # which one belongs to the row's table — `CrewIGA = IGA` reads the
            # other way round from `ReportID = ReportId`. So the left is placed
            # on whichever table actually has the column, and the right is placed
            # on a *different* table, preferring the row's own and then the
            # block's parent. Without the exclusion this resolves to a self-join.
            left = place(m.group(1), [row_table])
            if left:
                right = place(m.group(2), [row_table, parent_of.get(key, "")],
                              exclude=left[0])
                if right:
                    pair = (left[0], left[1], right[0], right[1])

        if not pair:
            notes.append({"table": row_table, "report": report,
                          "reason": f"no column pair could be resolved from {rel!r}"})
            continue

        lt, lc, rt, rc = pair
        out.setdefault(source_name, []).append(DeclaredJoin(
            source=source_name, left_table=lt, left_column=lc,
            right_table=rt, right_column=rc, report=report, why=why, raw=rel,
        ))
    return out, notes


def _key_aliases(identity: dict, join_key: str) -> list[str]:
    """Every column spelling that means "this crew member" for a source.

    CrewPortal writes the same identity as `IGA`, `CREW_IGA` and `IGA_CODE`, and
    attributes flight issues to seven crew by seat position (`L1_IGA` ...).
    Treating those as unrelated columns leaves the source unjoinable.
    """
    cols = identity.get("alias_columns", {}) or {}
    out = list(cols.get(join_key.upper(), [join_key])) if join_key else []
    out += list(cols.get("positional", []))
    return [c.upper() for c in dict.fromkeys(out)]


def load_registry(path: Path | None = None) -> Registry:
    """Load registry.yaml and every available source schema."""
    path = path or config.SCHEMAS_DIR / "registry.yaml"
    spec = yaml.safe_load(path.read_text())

    sources: dict[str, SourceSchema] = {}
    for entry in spec["sources"]:
        name = entry["name"]
        schema_path = config.SCHEMAS_DIR / entry["schema_file"]
        available = entry.get("available", True) and schema_path.exists()

        source = SourceSchema(
            name=name,
            engine=entry.get("engine", "snowflake"),
            detail=entry.get("detail", "columns"),
            join_key=entry.get("join_key", ""),
            context=" ".join((entry.get("context") or "").split()),
            relevance_gate=entry.get("relevance_gate", False),
            onboarded=entry.get("onboarded", False),
            available=available,
            crew_master=entry.get("crew_master", ""),
            key_aliases=_key_aliases(spec.get("identity", {}), entry.get("join_key", "")),
        )

        if available:
            if source.detail == "columns":
                source.tables = _load_column_schema(schema_path, name)
            else:
                source.tables = _load_table_schema(schema_path, name)

        sources[name] = source

    # Declared in the registry rather than discovered by scanning the directory,
    # for the same reason every other schema input is: what the system reads has
    # to be visible in one file.
    rel = spec.get("relationships") or {}
    declared, notes = ({}, [])
    if rel.get("file"):
        declared, notes = _load_declared_joins(
            config.SCHEMAS_DIR / rel["file"], sources,
            {_norm(k): v for k, v in (rel.get("db_names") or {}).items()},
        )

    return Registry(sources=sources, identity=spec.get("identity", {}),
                    descoped=list(spec.get("descoped_sources") or []),
                    declared_joins=declared, declared_join_notes=notes)
