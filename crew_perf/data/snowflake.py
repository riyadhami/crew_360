"""The Snowflake connector — the production warehouse, bounded to what is scoped.

Live, the crew performance data is not one schema. PEP holds the assessment
chain, CLMS the leave and recognition domain, CREWPORTAL the flight reports and
the identity bridge — three schemas in one database, and the questions worth
asking cross them. A connection defaults to exactly one schema, so a query that
reaches past it fails on the first unqualified name.

Rather than teach every agent to qualify, this connector resolves names on the
way out, from the same column-level exports that built the graph:

    FROM MENTOR_FEEDBACK JOIN M_CLMS_CREW
    -> FROM CREW.PEP.MENTOR_FEEDBACK JOIN CREW.CLMS.M_CLMS_CREW

The map is `TABLE_SCHEMA` off the live export, so it cannot drift from the
warehouse it describes.

Two bounds hold everywhere in here, both from `data/scope.py`:

  **Only scoped tables exist.** The production account is much larger than this
  project. The catalogue is the intersection of what the warehouse reports and
  what the exports declared, not everything the role can see — otherwise
  attribute discovery samples tables nobody scoped and the retrieval agent is
  offered a vocabulary the graph cannot vouch for.

  **Nothing unscoped is executed.** A query naming a table outside the exports
  is refused here, before the warehouse sees it, with the name that was out of
  scope. That is a second line behind the validator, not a replacement for it:
  the validator checks against the graph, this checks against the delivered
  extract, and a table can pass one and fail the other.

Nothing here changes which table a query names. Qualification runs *after*
validation, and only resolves a name the validator has already approved.
"""

from __future__ import annotations

from crew_perf import config
from crew_perf.data.dialect import qualify
from crew_perf.data.executor import QueryResult
from crew_perf.data.scope import UNKNOWN, get_scope


class OutOfScopeError(RuntimeError):
    """A query named a table no schema export declared."""


def warehouse_layout() -> dict[str, str]:
    """`TABLE_NAME -> SCHEMA`, from the schema exports, with env overrides applied.

    The exports are the default because they are a statement of fact about the
    delivered extract. The overrides exist for the one case they cannot cover: a
    warehouse whose schemas were renamed between the export and the deployment,
    where the tables are right and only the namespace moved.
    """
    return dict(get_scope().layout)


class SnowflakeExecutor:
    """Real Snowflake. Activated by SNOWFLAKE_MODE=snowflake."""

    def __init__(self, layout: dict[str, str] | None = None, connect=None):
        cfg = config.SNOWFLAKE
        missing = [k for k in ("account", "user", "database") if not cfg[k]]
        if missing:
            raise RuntimeError(
                "Snowflake mode requires: "
                + ", ".join("SNOWFLAKE_" + m.upper() for m in missing)
            )

        auth = config.snowflake_auth()
        self.auth_method = auth.pop("_method")
        if not self.auth_method:
            raise RuntimeError(
                "Snowflake mode requires a credential: set SNOWFLAKE_PAT to a "
                "programmatic access token (preferred), or one of "
                "SNOWFLAKE_PRIVATE_KEY_FILE / SNOWFLAKE_AUTHENTICATOR / "
                "SNOWFLAKE_PASSWORD"
            )

        self.database = cfg["database"]
        # The session schema still matters: it resolves anything the layout does
        # not know, and it is what `USE SCHEMA`-style tooling expects to find.
        self.schema = cfg["schema"]
        self.scope = get_scope()
        self.layout = warehouse_layout() if layout is None else layout
        self.schemas = sorted(set(self.layout.values()))

        if connect is None:
            import snowflake.connector  # imported lazily: optional dependency

            connect = snowflake.connector.connect

        self._con = connect(
            account=cfg["account"],
            user=cfg["user"],
            role=cfg["role"] or None,
            warehouse=cfg["warehouse"] or None,
            database=self.database,
            schema=self.schema,
            login_timeout=cfg["login_timeout"],
            network_timeout=cfg["query_timeout"],
            client_session_keep_alive=True,
            **auth,
        )

    # ── scope ──────────────────────────────────────────────────────────────

    def _guard(self, sql: str) -> None:
        """Refuse anything naming a table outside the delivered exports.

        Reported with what *is* available rather than a bare rejection: this
        fires on a retryable agent query as often as on a genuine mistake, and a
        rejection the agent cannot act on costs the whole retry budget.
        """
        engine, tables = self.scope.route(sql)
        if engine != UNKNOWN:
            return
        unknown = self.scope.out_of_scope(tables)
        raise OutOfScopeError(
            f"table(s) not in the scoped schemas: {', '.join(unknown)}. "
            f"Only tables declared in schemas/*_schema.csv are queryable "
            f"({len(self.scope.warehouse_tables)} in {', '.join(self.schemas)})"
        )

    # ── queries ────────────────────────────────────────────────────────────

    def execute(self, sql: str, params: dict | None = None, limit: int = 500) -> QueryResult:
        self._guard(sql)
        resolved, notes = qualify(sql, self.layout, database=self.database)
        cur = self._con.cursor()
        try:
            cur.execute(resolved, params or None)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit + 1)
            truncated = len(rows) > limit
            return QueryResult(
                columns=cols,
                rows=[tuple(r) for r in rows[:limit]],
                # The SQL as written, not as resolved — the trace, the golden
                # queries and the validator all speak in bare table names, and a
                # rewritten query shown back would not match any of them.
                sql=sql,
                row_count=min(len(rows), limit),
                truncated=truncated,
                warnings=notes,
            )
        finally:
            cur.close()

    def _raw(self, sql: str, limit: int = 10_000) -> QueryResult:
        """Run without qualification or the scope guard. For catalogue queries,
        which name no source table and would be mangled by a rewrite of
        `information_schema`."""
        cur = self._con.cursor()
        try:
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit)
            return QueryResult(columns=cols, rows=[tuple(r) for r in rows], sql=sql,
                               row_count=len(rows))
        finally:
            cur.close()

    # ── catalogue ──────────────────────────────────────────────────────────

    def list_tables(self) -> list[str]:
        """Every *scoped* table that actually exists in the warehouse, bare-named.

        The intersection, in both directions, and each direction matters. A table
        the account holds but no export declared is out of scope and must not
        appear — the production database carries hundreds, and offering them to
        attribute discovery or to the retrieval agent means sampling and querying
        tables the graph never modelled. A table the export declares but the
        warehouse does not hold is missing data, and listing it would have the
        join verifier report a broken relationship instead of an absent table.

        Bare, because that is the vocabulary of everything upstream: the graph
        keys on `MENTOR_FEEDBACK`, and a list of `PEP.MENTOR_FEEDBACK` would
        match nothing the join verifier or the attribute discovery looks up.
        """
        schemas = ", ".join(f"'{s}'" for s in self.schemas) or f"'{self.schema}'"
        r = self._raw(
            "SELECT DISTINCT table_name FROM information_schema.tables "
            f"WHERE table_schema IN ({schemas}) ORDER BY 1"
        )
        present = {str(row[0]).upper() for row in r.rows}
        return sorted(t for t in self.layout if t in present)

    def describe(self, table: str) -> list[tuple[str, str]]:
        """Columns of a scoped table, bounded to the ones the export declared.

        Unscoped tables describe as empty rather than raising: `describe` is
        called speculatively while a graph is being built, and an exception there
        aborts a build over a table that was never going to be used.
        """
        key = table.upper()
        if key not in self.layout:
            return []
        r = self._raw(
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = '{self.layout[key]}' AND table_name = '{key}' "
            "ORDER BY ordinal_position"
        )
        declared = self.scope.columns_of(key)
        return [(row[0], row[1]) for row in r.rows
                if not declared or str(row[0]).upper() in declared]

    def health(self) -> dict:
        """What `crewperf doctor` needs: is every scoped table actually visible?

        Counted against the export rather than against the schema's own size,
        and reported per schema rather than as one verdict, because the failure
        this catches is partial — a role granted on PEP and not on CLMS connects,
        answers PEP questions, and reports every CLMS signal as missing data. A
        count of "tables in the schema" would hide that behind a big number from
        the schemas that did work.
        """
        expected: dict[str, set[str]] = {s: set() for s in self.schemas}
        for table, schema in self.layout.items():
            expected[schema].add(table)

        found: dict[str, set[str]] = {s: set() for s in self.schemas}
        r = self._raw(
            "SELECT table_schema, table_name FROM information_schema.tables "
            f"WHERE table_schema IN ({', '.join(repr(s) for s in self.schemas)})"
        )
        for schema, name in r.rows:
            if schema in found:
                found[schema].add(str(name).upper())

        return {
            "database": self.database,
            "session_schema": self.schema,
            "auth": self.auth_method,
            "schemas": [
                {
                    "schema": s,
                    "tables_found": len(expected[s] & found[s]),
                    "tables_expected": len(expected[s]),
                    "missing": sorted(expected[s] - found[s])[:8],
                    "ok": expected[s] <= found[s],
                }
                for s in self.schemas
            ],
        }

    def close(self) -> None:
        self._con.close()
