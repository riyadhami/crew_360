"""What of the warehouse this system is allowed to see, and which engine holds it.

The production Snowflake account is far larger than this project. Pointing the
connector at it without a bound would mean `list_tables()` returning hundreds of
tables the graph never modelled, attribute discovery sampling tables nobody
scoped, and a generated query reaching something unexamined because the name
happened to resolve. So the warehouse is not browsed — it is *declared*, by the
column-level exports in `schemas/`:

    schemas/pep_schema.csv         -> PEP
    schemas/clms_schema.csv        -> CLMS
    schemas/crewportal_schema.csv  -> CREWPORTAL

Those three files are the allow-list. A table in one of them is in scope; every
other table in the account, including other tables inside those same three
schemas, is not. The CSVs carry columns too, so the bound is column-level: a
column added to a live table after the export is invisible here until the export
is refreshed, which is the right default for a system whose graph, validator and
scoring were all built from that export.

**Engines.** The registry declares one per source, and it is load-bearing here.
PEP, CLMS and CrewPortal are `snowflake` — they live in the warehouse. ServiceNow
is `servicenow`: a file extract ingested locally by `crewperf ingest-servicenow`,
which exists in DuckDB and in no Snowflake schema at all. Routing a ServiceNow
table to the warehouse would fail with "table does not exist" on data that is
sitting on disk; routing a PEP table to DuckDB would silently return the
synthetic dataset instead of production. So each table is owned by exactly one
engine, and this module is where that ownership is decided.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from crew_perf import config
from crew_perf.data.dialect import referenced_tables

# The registry's `engine` value for sources that live in the warehouse. Anything
# else is a local extract and is served from DuckDB.
WAREHOUSE_ENGINE = "snowflake"

WAREHOUSE = "warehouse"
LOCAL = "local"
MIXED = "mixed"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Scope:
    """The declared surface: which tables exist, where, and with what columns."""

    # TABLE -> SCHEMA, for warehouse tables only. This is also the qualification
    # map, so a table absent from it is one no query can be qualified against.
    layout: dict[str, str] = field(default_factory=dict)
    # TABLE -> the columns the export declared. Warehouse and local alike.
    columns: dict[str, frozenset[str]] = field(default_factory=dict)
    # Tables served by the local DuckDB file rather than the warehouse.
    local: frozenset[str] = frozenset()
    # TABLE -> the registry source that declared it, for error messages.
    owner: dict[str, str] = field(default_factory=dict)

    @property
    def schemas(self) -> list[str]:
        return sorted(set(self.layout.values()))

    @property
    def warehouse_tables(self) -> frozenset[str]:
        return frozenset(self.layout)

    def engine_of(self, table: str) -> str:
        """Which engine owns a table: WAREHOUSE, LOCAL, or UNKNOWN."""
        key = table.upper()
        if key in self.layout:
            return WAREHOUSE
        if key in self.local:
            return LOCAL
        return UNKNOWN

    def in_scope(self, table: str) -> bool:
        return self.engine_of(table) != UNKNOWN

    def columns_of(self, table: str) -> frozenset[str]:
        return self.columns.get(table.upper(), frozenset())

    def out_of_scope(self, tables) -> list[str]:
        """The names, in order, that no export declared."""
        seen, out = set(), []
        for t in tables:
            key = t.upper()
            if key not in seen and not self.in_scope(key):
                seen.add(key)
                out.append(t)
        return out

    # ── routing ────────────────────────────────────────────────────────────

    def route(self, sql: str) -> tuple[str, list[str]]:
        """`(engine, tables)` for a query — which backend can answer it.

        UNKNOWN when the query names something undeclared, MIXED when it spans
        both engines. Both are refusals rather than a best guess: a query is
        executed by exactly one connection, and picking one for a query that
        needs both returns rows from half the join condition.

        A query naming no table at all (`SELECT CURRENT_DATE()`) routes to the
        warehouse, which is where the session that should answer it lives.
        """
        tables = referenced_tables(sql)
        if not tables:
            return WAREHOUSE, tables

        engines = {self.engine_of(t) for t in tables}
        if UNKNOWN in engines:
            return UNKNOWN, tables
        if engines == {WAREHOUSE, LOCAL}:
            return MIXED, tables
        return engines.pop(), tables

    def describe_owner(self, table: str) -> str:
        """`SOURCE.SCHEMA.TABLE` where known — for messages, not for SQL."""
        key = table.upper()
        source = self.owner.get(key, "?")
        if key in self.layout:
            return f"{source} ({self.layout[key]}.{key})"
        return f"{source} (local extract)"


def build_scope(registry=None) -> Scope:
    """Read the allow-list out of the schema exports declared in the registry.

    Env overrides are applied here rather than at the connector, so everything
    downstream — qualification, catalogue listing, routing, the doctor — agrees
    on one map. They exist for the single case the export cannot describe: a
    warehouse that renamed a schema on the way in, where the tables are right and
    only the namespace moved.
    """
    if registry is None:
        from crew_perf.sources import load_registry

        registry = load_registry()

    layout: dict[str, str] = {}
    columns: dict[str, frozenset[str]] = {}
    owner: dict[str, str] = {}
    local: set[str] = set()

    for source in registry.available():
        warehouse = source.engine.lower() == WAREHOUSE_ENGINE
        override = config.SNOWFLAKE_SCHEMA_OVERRIDES.get(source.name.upper(), "")
        default_schema = (override or source.warehouse_schema).upper()

        for table in source.tables.values():
            key = table.name.upper()
            owner[key] = source.name
            if table.has_columns:
                columns[key] = frozenset(c.name.upper() for c in table.columns)
            if warehouse:
                layout[key] = override.upper() or (table.warehouse_schema or default_schema)
            else:
                local.add(key)

    return Scope(layout=layout, columns=columns, local=frozenset(local), owner=owner)


@lru_cache(maxsize=1)
def _cached_scope() -> Scope:
    return build_scope()


def get_scope(fresh: bool = False) -> Scope:
    """The process-wide scope. Cached: it is read once per run and never drifts."""
    if fresh:
        _cached_scope.cache_clear()
    return _cached_scope()
