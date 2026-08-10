"""Graph-grounded SQL validation.

Runs **before** execution and rejects anything the graph cannot vouch for. The
LLM never gets to name a table or column that didn't come from a graph tool
result, and never gets to join on a relationship the graph doesn't hold.

The rule that earns its keep is the SCD-2 one. Every PEP table carries
`P_IS_CURRENT`, and a query that forgets it silently double-counts superseded
history — returning a plausible number that is simply wrong. That failure is
invisible in the output, so it is enforced deterministically here rather than
asked for in a prompt (PLAN.md G7).

This is a linter over a narrow, generated dialect, not a SQL parser. It errs
toward rejection with a specific reason the agent can act on: a rejected query
costs one retry, an accepted-but-wrong query costs a wrong answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crew_perf.data.dialect import is_read_only
from crew_perf.graph.schema import PII_COLUMNS
from crew_perf.graph.store import GraphStore

# Identifiers that are SQL, not schema.
_SQL_WORDS = {
    "select", "from", "where", "group", "by", "order", "having", "limit", "offset",
    "join", "inner", "left", "right", "full", "outer", "cross", "on", "as", "and",
    "or", "not", "in", "is", "null", "true", "false", "case", "when", "then",
    "else", "end", "with", "union", "all", "distinct", "count", "sum", "avg",
    "min", "max", "round", "cast", "try_cast", "coalesce", "nullif", "asc", "desc",
    "between", "like", "ilike", "exists", "over", "partition", "qualify", "date",
    "interval", "current_date", "current_timestamp", "dateadd", "datediff",
    "stddev", "median", "percentile_cont", "row_number", "rank", "dense_rank",
    "abs", "greatest", "least", "length", "trim", "upper", "lower", "iff",
    "double", "varchar", "integer", "float", "boolean", "numeric", "decimal",
    "quantile_cont", "corr", "regr_slope", "any_value", "listagg", "string_agg",
}

_TABLE_AFTER = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_ALIAS_DECL = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_CTE_DECL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", re.IGNORECASE)
_QUALIFIED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
# Every bare identifier, so an unqualified personal-data column is caught too —
# the qualified pattern above never sees `SELECT EMPLOYEE_NAME`.
_WORD = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
# `SELECT *` and `SELECT t.*`, both of which reach columns the graph does not list.
_SELECT_STAR = re.compile(
    r"SELECT\s+(?:DISTINCT\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)?\*", re.IGNORECASE
)
_JOIN_ON = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)"
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    def reason(self) -> str:
        return "; ".join(self.errors)


def _strip(sql: str) -> str:
    """Remove comments and string literals so they can't be mistaken for identifiers."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def _route_hint(store, left: str, right: str) -> str:
    """The verified multi-hop route between two tables, if the graph holds one.

    Returned as part of the rejection so a retry has somewhere to go. Silent on
    failure by design: "there is no route" is already what the rest of the
    message says, and a hint that cannot be produced must not become an error of
    its own.
    """
    try:
        path = store.join_path(left, right)
    except Exception:  # noqa: BLE001 - a hint must never break validation
        return ""
    if not path:
        return ""
    hops = " then ".join(
        f"{j.source_table}.{j.source_column} = {j.target_table}.{j.target_column}"
        for j in path
    )
    return f". They ARE reachable in {len(path)} hops — join {hops}"


def validate(
    sql: str,
    store: GraphStore,
    *,
    require_limit: bool = True,
    max_limit: int = 5000,
    min_join_confidence: float = 0.8,
) -> ValidationResult:
    """Check generated SQL against the graph. Returns errors the agent can act on."""
    result = ValidationResult(ok=True)
    if not sql or not sql.strip():
        return ValidationResult(False, ["empty query"])

    if not is_read_only(sql):
        return ValidationResult(False, ["only read-only SELECT/WITH queries are permitted"])

    if ";" in sql.strip().rstrip(";"):
        return ValidationResult(False, ["multiple statements are not permitted"])

    body = _strip(sql)

    # ── Tables must exist in the graph ──
    ctes = {c.lower() for c in _CTE_DECL.findall(body)}
    referenced = [t for t in _TABLE_AFTER.findall(body) if t.lower() not in ctes]
    unknown = [t for t in referenced if store.table(t) is None]
    if unknown:
        known = ", ".join(sorted(store.tables)[:12])
        result.errors.append(
            f"unknown table(s): {', '.join(sorted(set(unknown)))}. "
            f"Only tables in the graph may be queried (e.g. {known}…)"
        )
    tables = [t for t in referenced if store.table(t) is not None]
    result.tables = sorted(set(tables))

    # ── Alias map, so qualified columns can be checked ──
    aliases: dict[str, str] = {}
    for table, alias in _ALIAS_DECL.findall(body):
        if alias.lower() in _SQL_WORDS or table.lower() in ctes:
            continue
        if store.table(table) is not None:
            aliases[alias.lower()] = table
    for t in tables:
        aliases.setdefault(t.lower(), t)

    # ── Personal data is not selectable, however it is spelled ──
    #
    # Dropping these columns from the schema exports already hides them from the
    # graph, every prompt and attribute discovery. Two ways past that survive and
    # are closed here, because both reach the *physical* table, which still has
    # the columns whatever the graph says:
    #
    #   `SELECT EMPLOYEE_NAME`  — the column check below only inspects qualified
    #                             references, so a bare name was never checked.
    #   `SELECT *`              — returns every column the table physically has.
    #
    # Refused rather than stripped: an agent that asked for a name should be told
    # the rule, so its retry stops asking, and silently returning fewer columns
    # than were requested is how a caller ends up reading the wrong one.
    for column in sorted({c.upper() for c in _WORD.findall(body)} & PII_COLUMNS):
        result.errors.append(
            f"{column} is personal data and cannot be selected. Crew are identified "
            f"by IGA alone — the warehouse hashes everything else behind it, so no "
            f"name, email or contact column is available on any table."
        )
    if _SELECT_STAR.search(body):
        result.errors.append(
            "SELECT * is not permitted — name the columns. A table's physical "
            "columns include personal data the graph deliberately does not list, "
            "and `*` returns them."
        )

    # ── Qualified columns must exist on their table ──
    for qualifier, column in _QUALIFIED.findall(body):
        ql, cl = qualifier.lower(), column.lower()
        if ql in ctes or ql not in aliases or cl in _SQL_WORDS:
            continue
        table = aliases[ql]
        if not store.has_column(table, column):
            cols = ", ".join((store.table(table).columns or [])[:10])
            result.errors.append(
                f"{table} has no column {column!r} (available: {cols}…)"
            )

    # ── Joins must exist in the graph ──
    for lq, lc, rq, rc in _JOIN_ON.findall(body):
        lql, rql = lq.lower(), rq.lower()
        if lql in ctes or rql in ctes or lql not in aliases or rql not in aliases:
            continue
        lt, rt = aliases[lql], aliases[rql]
        if lt == rt:
            continue
        edges = store.join_between(lt, rt, min_confidence=min_join_confidence)
        if not edges:
            any_edge = store.join_between(lt, rt)
            if any_edge:
                best = max(any_edge, key=lambda e: e.confidence)
                result.errors.append(
                    f"join {lt}.{lc} = {rt}.{rc} is below the confidence bar "
                    f"(graph holds {best.source_column}->{best.target_column} at "
                    f"{best.confidence:.2f}, needs >= {min_join_confidence})"
                )
            else:
                # A direct edge is missing, but an indirect route often is not —
                # PEP and ServiceNow both key on IGA and are joined through
                # CrewPortal's crew master, which carries both. Saying only "do
                # not invent joins" spent the whole retry budget: the agent
                # rewrote the same two-table join four ways and gave up on a
                # question the graph can answer in two hops. The route is one
                # lookup away, so the rejection hands it over.
                result.errors.append(
                    f"no direct relationship between {lt} and {rt} exists in the "
                    f"graph — do not invent one" + _route_hint(store, lt, rt)
                )
            continue
        cols = {(e.source_column.upper(), e.target_column.upper()) for e in edges}
        cols |= {(b, a) for a, b in cols}
        if (lc.upper(), rc.upper()) not in cols:
            allowed = ", ".join(f"{a}={b}" for a, b in sorted(cols))
            # Two related tables joined on the wrong columns is not a lesser
            # offence than two unrelated tables joined at all — it produces a
            # cartesian-ish result that looks like data. Anything the graph
            # verified is a hard constraint; only fully unverified edges get the
            # benefit of the doubt.
            if any(e.verified for e in edges):
                result.errors.append(
                    f"join {lt}.{lc} = {rt}.{rc} is not a relationship the graph holds. "
                    f"Verified join column(s) for these tables: {allowed}"
                )
            else:
                result.warnings.append(
                    f"join {lt}.{lc} = {rt}.{rc} uses columns the graph did not verify "
                    f"(known: {allowed})"
                )

    # ── SCD-2 currency filter (G7) ──
    # With more than one table in play the filter must be *qualified*: an
    # unqualified `P_IS_CURRENT = TRUE` filters whichever table the engine binds
    # it to and leaves the others unfiltered, which is exactly the silent
    # double-count this rule exists to prevent.
    distinct_tables = set(tables)
    predicate = r"P_IS_CURRENT\s*(?:=\s*(?:TRUE|1)|IS\s+TRUE)"
    for table in distinct_tables:
        if table not in store.scd2_tables:
            continue
        alias_names = {a for a, t in aliases.items() if t == table} | {table.lower()}
        qualified = re.compile(
            r"(?:" + "|".join(re.escape(a) for a in alias_names) + r")\s*\.\s*" + predicate,
            re.IGNORECASE,
        )
        found = bool(qualified.search(body))
        if not found and len(distinct_tables) == 1:
            found = bool(re.search(r"(?<![\w.])" + predicate, body, re.IGNORECASE))
        if not found:
            hint = (
                f" — qualify it, e.g. `{sorted(alias_names)[0]}.P_IS_CURRENT = TRUE`"
                if len(distinct_tables) > 1 else ""
            )
            result.errors.append(
                f"{table} is versioned (SCD-2) and the query has no "
                f"`P_IS_CURRENT = TRUE` filter for it — superseded history rows "
                f"would be counted twice{hint}"
            )

    # ── Result bound ──
    limit_match = re.search(r"\bLIMIT\s+(\d+)", body, re.IGNORECASE)
    if require_limit and not limit_match:
        result.errors.append("query must end with an explicit LIMIT")
    elif limit_match and int(limit_match.group(1)) > max_limit:
        result.errors.append(
            f"LIMIT {limit_match.group(1)} exceeds the maximum of {max_limit}"
        )

    # ── Advisory: casting the TEXT mark (G4) ──
    if re.search(r"\bMARK\b", body, re.IGNORECASE) and not re.search(
        r"TRY_CAST\s*\(\s*[A-Za-z_.]*MARK", body, re.IGNORECASE
    ):
        if re.search(r"(AVG|SUM|MIN|MAX|ROUND|STDDEV)\s*\(\s*[A-Za-z_.]*MARK", body, re.IGNORECASE):
            result.errors.append(
                "MENTOR_FEEDBACK.MARK is TEXT and some rows are uncastable — "
                "aggregate it as TRY_CAST(MARK AS DOUBLE), never directly"
            )

    result.ok = not result.errors
    return result
