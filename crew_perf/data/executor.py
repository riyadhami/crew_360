"""SQL execution abstraction: the production warehouse, or a local DuckDB.

Generated SQL always targets **Snowflake dialect** against bare table names.
Each backend closes its own gap and none of it is visible to an agent:

  DuckDB     `dialect.to_duckdb` rewrites the handful of constructs DuckDB
             spells differently.
  Snowflake  `data/snowflake.py` qualifies each table with the schema it lives
             in — the warehouse spreads these tables over three — and bounds
             every query to the tables the schema exports declared.
  Routing    `data/routing.py` sends each query to the engine that holds its
             tables, because ServiceNow is a local extract and the other three
             sources are warehouse schemas.

So swapping backends is one env var and no change to any generated query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from crew_perf import config
from crew_perf.data.dialect import to_duckdb


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    sql: str
    row_count: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)


class SqlExecutor(Protocol):
    """Minimal surface the agents are allowed to use."""

    def execute(self, sql: str, params: dict | None = None, limit: int = 500) -> QueryResult: ...
    def list_tables(self) -> list[str]: ...
    def describe(self, table: str) -> list[tuple[str, str]]: ...
    def close(self) -> None: ...


# ─── DuckDB ─────────────────────────────────────────────────────────────────


class DuckDBExecutor:
    """Local DuckDB, used with the synthetic dataset.

    Snowflake-dialect SQL in, DuckDB-dialect SQL executed. The translation is
    recorded on the result so a mismatch is debuggable rather than silent.
    """

    def __init__(self, path: str | None = None, read_only: bool = False):
        import duckdb

        db_path = str(path or config.DUCKDB_PATH)
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(db_path, read_only=read_only)
        self.path = db_path

    def execute(self, sql: str, params: dict | None = None, limit: int = 500) -> QueryResult:
        translated, notes = to_duckdb(sql)
        cur = self._con.execute(translated, params or {})
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        return QueryResult(
            columns=cols,
            rows=[tuple(r) for r in rows[:limit]],
            sql=sql,
            row_count=min(len(rows), limit),
            truncated=truncated,
            warnings=notes,
        )

    def register_df(self, name: str, df) -> None:
        """Register a pandas DataFrame as a DuckDB table. Loader-only."""
        self._con.register(f"_tmp_{name}", df)
        self._con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _tmp_{name}')
        self._con.unregister(f"_tmp_{name}")

    def list_tables(self) -> list[str]:
        rows = self._con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]

    def describe(self, table: str) -> list[tuple[str, str]]:
        rows = self._con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def close(self) -> None:
        self._con.close()


# ─── Factory ────────────────────────────────────────────────────────────────

_executor: SqlExecutor | None = None


def get_executor(fresh: bool = False) -> SqlExecutor:
    """Return the executor for the configured mode (cached).

    Opened **read-only** when the database already exists. Every consumer of this
    function — retrieval, weighting, scoring, evaluation, join verification —
    only reads; the loader builds its own writable handle. DuckDB permits many
    readers or one writer, so defaulting to write here meant a graph build and a
    scorecard could not run at the same time, which is a restriction nothing in
    the system actually needs.
    """
    global _executor
    if _executor is not None and not fresh:
        return _executor

    mode = config.SNOWFLAKE_MODE
    if mode == "snowflake":
        # Routing, not the bare Snowflake connector: three of the four sources
        # are warehouse schemas and ServiceNow is a local file extract, so
        # "snowflake mode" is one executor over two engines. See data/routing.py.
        from crew_perf.data.routing import RoutingExecutor

        _executor = RoutingExecutor()
    elif mode == "duckdb":
        _executor = DuckDBExecutor(read_only=config.DUCKDB_PATH.exists())
    else:
        raise RuntimeError(f"Unknown SNOWFLAKE_MODE={mode!r}; expected 'duckdb' or 'snowflake'")
    return _executor
