"""Two engines behind one executor: the warehouse, and the local file extract.

PEP, CLMS and CrewPortal are Snowflake schemas. ServiceNow is not — it arrives
as a CSV export from the Crew Portal integration and is ingested into DuckDB by
`crewperf ingest-servicenow`, so its tables exist on disk and in no Snowflake
schema at all. In production both are in scope at once, and every consumer above
this line — retrieval, scoring, weighting, the join verifier — holds a single
`SqlExecutor` and does not know there are two.

So a query is routed by the tables it names, from the same declared scope that
qualifies and bounds them. The routing is total: every scoped table is owned by
exactly one engine, and a table owned by neither was already going to be refused.

**A query cannot span both engines.** One statement is executed by one
connection, and there is no federation between a Snowflake session and a local
DuckDB file — no shared catalogue, no way to put a warehouse table and a local
one either side of a join. Rather than pick an engine and return rows from half
the join condition, a mixed query is refused by name, with the split spelled out
so the agent can ask the two questions separately and the answer says which
population each half covers. Silence here would be a scorecard that reads as
complete and is missing whichever half went to the wrong engine.
"""

from __future__ import annotations

from crew_perf import config
from crew_perf.data.executor import DuckDBExecutor, QueryResult
from crew_perf.data.scope import LOCAL, MIXED, UNKNOWN, WAREHOUSE, get_scope


class CrossEngineError(RuntimeError):
    """A single query named tables held by two different engines."""


class RoutingExecutor:
    """Dispatches to Snowflake or to the local extract, by the tables named."""

    def __init__(self, warehouse=None, local=None):
        from crew_perf.data.snowflake import SnowflakeExecutor

        self.scope = get_scope()
        self._warehouse = SnowflakeExecutor() if warehouse is None else warehouse
        # Lazy: a deployment that never ingested the ServiceNow extract is a
        # valid one — the source simply has no data — and opening a DuckDB file
        # that is not there to answer a question nobody asked would fail the run
        # at startup instead.
        self._local = local
        self._local_ready = local is not None

    # ── engines ────────────────────────────────────────────────────────────

    @property
    def warehouse(self):
        return self._warehouse

    @property
    def local(self):
        """The local extract's executor, opened on first use.

        None when there is no database file: ServiceNow was never ingested, and
        that is reported as an absent source rather than raised as a failure of
        the warehouse connection, which is fine.
        """
        if not self._local_ready:
            self._local_ready = True
            if config.DUCKDB_PATH.exists():
                self._local = DuckDBExecutor(read_only=True)
        return self._local

    def _local_or_fail(self, tables: list[str]):
        ex = self.local
        if ex is None:
            raise RuntimeError(
                f"{', '.join(tables)} live in the ServiceNow extract, which has "
                f"not been ingested — no database at {config.DUCKDB_PATH}. "
                f"Run `crewperf ingest-servicenow` first"
            )
        return ex

    # ── queries ────────────────────────────────────────────────────────────

    def execute(self, sql: str, params: dict | None = None, limit: int = 500) -> QueryResult:
        engine, tables = self.scope.route(sql)

        if engine == MIXED:
            warehouse = [t for t in tables if self.scope.engine_of(t) == WAREHOUSE]
            local = [t for t in tables if self.scope.engine_of(t) == LOCAL]
            raise CrossEngineError(
                f"one query cannot span both engines: {', '.join(warehouse)} "
                f"{'is' if len(warehouse) == 1 else 'are'} in the Snowflake warehouse "
                f"and {', '.join(local)} {'is' if len(local) == 1 else 'are'} in the "
                f"local ServiceNow extract, which have no shared catalogue to join "
                f"across. Query them separately and report each population on its own "
                f"— do not join them"
            )

        if engine == UNKNOWN:
            # Deliberately delegated: the warehouse executor owns the wording of
            # what is in scope, and duplicating it here is how the two drift.
            return self._warehouse.execute(sql, params, limit)

        if engine == LOCAL:
            return self._local_or_fail(tables).execute(sql, params, limit)
        return self._warehouse.execute(sql, params, limit)

    # ── catalogue ──────────────────────────────────────────────────────────

    def list_tables(self) -> list[str]:
        """Scoped tables that exist, across both engines.

        The local side is intersected with scope too, and that is not
        redundant: the same DuckDB file also holds the full synthetic PEP, CLMS
        and CrewPortal dataset from `crewperf synth`. Listing those in production
        would offer the agents a synthetic `MENTOR_FEEDBACK` sitting alongside
        the real one under the same name, and whichever the router picked, half
        the answers would be invented.
        """
        tables = list(self._warehouse.list_tables())
        ex = self.local
        if ex is not None:
            tables += [t for t in ex.list_tables() if t.upper() in self.scope.local]
        return sorted(set(tables))

    def describe(self, table: str) -> list[tuple[str, str]]:
        if self.scope.engine_of(table) == LOCAL:
            ex = self.local
            return ex.describe(table) if ex is not None else []
        return self._warehouse.describe(table)

    def health(self) -> dict:
        h = self._warehouse.health()
        ex = self.local
        present = {t.upper() for t in ex.list_tables()} if ex is not None else set()
        h["local_extract"] = {
            "path": str(config.DUCKDB_PATH),
            "ingested": ex is not None,
            "tables_found": len(self.scope.local & present),
            "tables_expected": len(self.scope.local),
            "ok": bool(self.scope.local) and self.scope.local <= present,
        }
        return h

    def close(self) -> None:
        self._warehouse.close()
        if self._local is not None:
            self._local.close()
