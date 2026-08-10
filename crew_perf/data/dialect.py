"""SQL rewrites between the dialect the agents write and the one that runs.

Agents always emit Snowflake dialect against bare table names. Two rewrites
follow from that, in opposite directions:

  `to_duckdb`  the handful of constructs DuckDB spells differently. DuckDB
               natively supports most of what we generate (QUALIFY, ILIKE,
               window functions, TRY_CAST, CTEs), so this stays small by design.

  `qualify`    the table→schema prefixing real Snowflake needs and DuckDB does
               not, because locally every source lands in one namespace and in
               the warehouse they are spread across four schemas.

Every rewrite is recorded and returned so a translation can be shown in the
trace rather than silently changing what runs.

Deliberately NOT a SQL parser. These are conservative regex rewrites over the
narrow set of constructs our generator is allowed to produce; anything more
exotic should fail loudly in the engine rather than be quietly mangled here.
"""

from __future__ import annotations

import re

# Snowflake date parts -> DuckDB interval units
_DATE_PARTS = {
    "year": "year", "yy": "year", "yyyy": "year",
    "quarter": "quarter", "qq": "quarter",
    "month": "month", "mm": "month", "mon": "month",
    "week": "week", "wk": "week",
    "day": "day", "dd": "day", "dayofmonth": "day",
    "hour": "hour", "hh": "hour",
    "minute": "minute", "mi": "minute",
    "second": "second", "ss": "second",
}


def _split_args(text: str) -> list[str]:
    """Split a function argument list on top-level commas only."""
    args, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current).strip())
    return args


def _rewrite_call(sql: str, func: str, render) -> tuple[str, int]:
    """Find `func(...)` calls, balancing parens, and replace via `render(args)`.

    `render` returns None when it can't translate that occurrence; the call is
    then left intact and scanning continues past it (DuckDB will reject it
    loudly, which is the intent — better than a silent mangle).
    """
    pattern = re.compile(rf"\b{func}\s*\(", re.IGNORECASE)
    count = 0
    pos = 0
    while True:
        m = pattern.search(sql, pos)
        if not m:
            return sql, count

        # Walk forward to the matching close paren.
        i, depth = m.end() - 1, 0
        while i < len(sql):
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if i >= len(sql):
            return sql, count  # unbalanced; let the engine reject it

        replacement = render(_split_args(sql[m.end() : i]))
        if replacement is None:
            pos = i + 1  # skip this occurrence, keep scanning
            continue

        sql = sql[: m.start()] + replacement + sql[i + 1 :]
        pos = m.start() + len(replacement)
        count += 1


def to_duckdb(sql: str) -> tuple[str, list[str]]:
    """Translate Snowflake SQL to DuckDB. Returns (sql, notes)."""
    notes: list[str] = []
    out = sql

    # DATEADD(part, n, date) -> (date + INTERVAL (n) part)
    def _dateadd(args):
        if len(args) != 3:
            return None
        part = _DATE_PARTS.get(args[0].strip().strip("'\"").lower())
        if not part:
            return None
        return f"(({args[2]}) + INTERVAL ({args[1]}) {part})"

    out, n = _rewrite_call(out, "DATEADD", _dateadd)
    if n:
        notes.append(f"DATEADD -> INTERVAL arithmetic ({n}x)")

    # DATEDIFF(part, start, end) -> DATE_DIFF('part', start, end)
    def _datediff(args):
        if len(args) != 3:
            return None
        part = _DATE_PARTS.get(args[0].strip().strip("'\"").lower())
        if not part:
            return None
        return f"DATE_DIFF('{part}', {args[1]}, {args[2]})"

    out, n = _rewrite_call(out, "DATEDIFF", _datediff)
    if n:
        notes.append(f"DATEDIFF -> DATE_DIFF ({n}x)")

    # IFF(cond, a, b) -> CASE WHEN cond THEN a ELSE b END
    def _iff(args):
        if len(args) != 3:
            return None
        return f"(CASE WHEN {args[0]} THEN {args[1]} ELSE {args[2]} END)"

    out, n = _rewrite_call(out, "IFF", _iff)
    if n:
        notes.append(f"IFF -> CASE ({n}x)")

    # NVL -> COALESCE (DuckDB has COALESCE; NVL is Snowflake/Oracle spelling)
    out, n = re.subn(r"\bNVL\s*\(", "COALESCE(", out, flags=re.IGNORECASE)
    if n:
        notes.append(f"NVL -> COALESCE ({n}x)")

    # CURRENT_TIMESTAMP() / CURRENT_DATE() -> bare keywords
    out, n = re.subn(r"\bCURRENT_TIMESTAMP\s*\(\s*\)", "CURRENT_TIMESTAMP", out, flags=re.IGNORECASE)
    out, n2 = re.subn(r"\bCURRENT_DATE\s*\(\s*\)", "CURRENT_DATE", out, flags=re.IGNORECASE)
    if n or n2:
        notes.append("CURRENT_* () -> keyword form")

    # TO_VARCHAR(x) -> CAST(x AS VARCHAR). Only the single-arg form; the two-arg
    # format-string variant has no clean DuckDB equivalent, so leave it to fail.
    def _to_varchar(args):
        return f"CAST({args[0]} AS VARCHAR)" if len(args) == 1 else None

    out, n = _rewrite_call(out, "TO_VARCHAR", _to_varchar)
    if n:
        notes.append(f"TO_VARCHAR -> CAST AS VARCHAR ({n}x)")

    def _to_number(args):
        return f"CAST({args[0]} AS DOUBLE)" if len(args) == 1 else None

    out, n = _rewrite_call(out, "TO_NUMBER", _to_number)
    if n:
        notes.append(f"TO_NUMBER -> CAST AS DOUBLE ({n}x)")

    return out, notes


# ─── Schema qualification (Snowflake only) ──────────────────────────────────

# `FROM x`, `JOIN x`, and the quoted forms the agents also emit.
_FROM_OR_JOIN = re.compile(
    r"\b(FROM|JOIN)(\s+)([\"`]?)([A-Za-z_][A-Za-z0-9_]*)\3(?![\w.\"`])",
    re.IGNORECASE,
)
_CTE_NAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", re.IGNORECASE)


def referenced_tables(sql: str) -> list[str]:
    """The base tables a query reads, upper-cased, CTEs and literals excluded.

    The same extraction `qualify` does, exposed on its own because routing and
    scope enforcement need to know *which* tables a query touches before it is
    rewritten or sent anywhere. Sharing the regexes matters: a scope check that
    saw a different set of tables from the one qualification rewrites would pass
    a query and then send an unapproved name to the warehouse.
    """
    ctes = {c.upper() for c in _CTE_NAME.findall(_blank_literals(sql))}
    names: list[str] = []
    for part in _split_on_literals(sql)[::2]:
        for _, _, _, table in _FROM_OR_JOIN.findall(part):
            key = table.upper()
            if key not in ctes and key not in names:
                names.append(key)
    return names


def qualify(sql: str, layout: dict[str, str], database: str = "") -> tuple[str, list[str]]:
    """Prefix bare table names with the schema (and database) they live in.

    Every agent writes `FROM MENTOR_FEEDBACK`, because locally every table is in
    one DuckDB namespace. Snowflake spreads the same tables across PEP, CLMS,
    CREWPORTAL and SERVICENOW, and a connection defaults to exactly one of them —
    so the moment a query reaches across sources, which the interesting ones all
    do, it fails on the first table outside the session schema.

    Rewriting here rather than teaching the agents to qualify is deliberate. The
    graph, the validator and the golden queries all speak in bare table names;
    making qualification a generation concern would mean the same table has two
    spellings depending on the backend, and the validator would have to
    understand both. This runs after validation and changes only the resolution
    of a name, never which table is named.

    Names already qualified are left alone, and so are CTEs — a CTE is a name
    the query defined itself, and prefixing it with a schema turns a valid query
    into a reference to a table that does not exist.
    """
    if not layout:
        return sql, []

    ctes = {c.upper() for c in _CTE_NAME.findall(_blank_literals(sql))}
    prefix = f"{database}." if database else ""
    qualified: set[str] = set()

    def replace(m: re.Match) -> str:
        keyword, gap, quote, table = m.group(1), m.group(2), m.group(3), m.group(4)
        key = table.upper()
        if key in ctes:
            return m.group(0)
        schema = layout.get(key)
        if not schema:
            return m.group(0)
        qualified.add(table)
        return f"{keyword}{gap}{prefix}{schema}.{quote}{table}{quote}"

    # Rewrite only outside string literals and comments. `GRADE = 'FROM X'` is a
    # value, and qualifying the table name inside it changes what the query
    # compares against — a silent wrong answer rather than an error.
    out = "".join(
        part if i % 2 else _FROM_OR_JOIN.sub(replace, part)
        for i, part in enumerate(_split_on_literals(sql))
    )
    notes = ([f"qualified {len(qualified)} table(s) with their schema: "
              f"{', '.join(sorted(qualified))}"] if qualified else [])
    return out, notes


# Literals and comments, captured so `re.split` keeps them: with one capturing
# group the split alternates code, literal, code, literal — so odd indices are
# exactly the spans that must survive untouched.
_LITERAL_OR_COMMENT = re.compile(
    r"('(?:[^']|'')*'|--[^\n]*|/\*.*?\*/)",
    re.DOTALL,
)


def _split_on_literals(sql: str) -> list[str]:
    return _LITERAL_OR_COMMENT.split(sql)


def _blank_literals(sql: str) -> str:
    """Blank out comments and strings so they cannot look like identifiers."""
    return "".join(
        "" if i % 2 else part for i, part in enumerate(_split_on_literals(sql))
    )


def is_read_only(sql: str) -> bool:
    """True when the statement only reads. Used by the validator, not here."""
    stripped = re.sub(r"--[^\n]*", " ", sql)
    stripped = re.sub(r"/\*.*?\*/", " ", stripped, flags=re.DOTALL).strip()
    if not stripped:
        return False
    forbidden = r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|MERGE|GRANT|REVOKE|COPY|CALL)\b"
    if re.search(forbidden, stripped, flags=re.IGNORECASE):
        return False
    return bool(re.match(r"^\s*(WITH|SELECT)\b", stripped, flags=re.IGNORECASE))
