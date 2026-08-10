"""One crew member, and where in the graph they actually appear.

The concept graph is metadata: tables, concepts, joins. There is no vertex for a
person in it, and there is deliberately no vertex for a person in Cosmos either —
`VertexLabel.IDENTITY` and `EdgeLabel.IDENTIFIES` are in the vocabulary and
nothing has ever built them, because a graph with 800 crew vertices in it is a
graph nobody can draw and every agent has to filter past.

So the crew node is built *per request*, against the live data, and thrown away.
It answers one question: **which tables actually hold rows about this person, and
which hold none.** The second half is the valuable half. A score reading "computed
from 59% of the weight" is a footnote nobody acts on; the same fact drawn as four
lit tables and nine grey ones is the reason the number is what it is.

── Two constraints that shape the whole module ────────────────────────────────

**One query per table, never a join.** In Snowflake mode PEP, CLMS and CrewPortal
come from the warehouse while ServiceNow and CAC are served from the local DuckDB
extract, and one query cannot span both engines. Counting each table on its own is
the only shape that works in both modes, and it is also the shape that survives a
table being unreadable — a permission gap on one schema greys one row rather than
failing the whole view.

**CLMS is keyed on CREW_ID, everything else on IGA**, and one is never the other
with a prefix edited on. The translation goes through `M_CREW_DETAILS`, which
carries both. The synthetic data happens to make `IGA60406` -> `C60406` look like
string surgery; the real warehouse does not promise that, and deriving it that way
returns a clean, confident, empty answer.
"""

from __future__ import annotations

import re

# The identifier arrives from a URL and is interpolated into SQL. Parameter
# placeholders are not portable here — DuckDB binds `$name`, the Snowflake
# connector binds `%(name)s` — so the query is built by hand and the value is
# constrained instead. Nothing outside this class can reach the SQL: no quote,
# no semicolon, no whitespace, no comment marker.
#
# The lookahead is not decoration. `-` and `_` are allowed so a warehouse with
# hyphenated crew codes still works, and without the lookahead that admits `--`
# — which is inert inside the quotes it lands in, but is a SQL comment marker
# arriving at a hand-built query, and a value made only of punctuation is not an
# identifier in the first place. Refuse it at the door rather than reason about
# why it happens to be harmless.
SAFE_IDENTIFIER = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9_-]{1,32}$")

# The bridge table: the one place both identifiers sit on the same row.
BRIDGE_TABLE = "M_CREW_DETAILS"


class UnsafeIdentifier(ValueError):
    """The identifier is not a crew identifier, so it is refused rather than run."""


def build_ego(identifier: str, executor=None, store=None) -> dict:
    """Where `identifier` appears across every onboarded source.

    Returns the resolved identity, one row per crew-bearing table with its count,
    and the per-source totals the coverage summary is drawn from.
    """
    ident = (identifier or "").strip()
    if not SAFE_IDENTIFIER.match(ident):
        raise UnsafeIdentifier(
            f"{identifier!r} is not a crew identifier. Expected something like "
            f"IGA60406 or C60406."
        )

    if executor is None:
        from crew_perf.data.executor import get_executor

        executor = get_executor()
    if store is None:
        from crew_perf.graph.store import get_store

        store = get_store()

    keys, warnings = _resolve(ident, executor, store)
    probes = _probe_plan(store, keys)
    presence = [_count(executor, store, p) for p in probes]

    by_source: dict[str, int] = {}
    probed_sources: set[str] = set()
    for row in presence:
        probed_sources.add(row["source"])
        if isinstance(row["rows"], int):
            by_source[row["source"]] = by_source.get(row["source"], 0) + row["rows"]

    holding = [r for r in presence if isinstance(r["rows"], int) and r["rows"] > 0]
    absent = sorted(s for s in probed_sources if not by_source.get(s))

    if not holding:
        warnings.append(
            f"No source holds a row for {ident}. Either the identifier is not a "
            f"crew member, or nothing has been recorded against them."
        )
    elif absent:
        warnings.append(
            "No rows in " + ", ".join(absent) + " — every signal those sources "
            "carry is missing data for this crew member, not a low score."
        )

    return {
        "identifier": ident,
        "keys": keys,
        "found": bool(holding),
        "presence": presence,
        "rows_by_source": by_source,
        "absent_sources": absent,
        "stats": {
            "tables_probed": len(presence),
            "tables_holding": len(holding),
            "total_rows": sum(r["rows"] for r in presence if isinstance(r["rows"], int)),
            "sources_holding": len([s for s in by_source if by_source[s]]),
            "sources_probed": len(probed_sources),
        },
        "warnings": warnings,
    }


def _resolve(ident: str, executor, store) -> tuple[dict[str, str], list[str]]:
    """`{IGA: ..., CREW_ID: ...}` — both spellings of the same person.

    Read off `M_CREW_DETAILS`, the only table carrying both. Whichever key the
    caller typed is matched, and the other comes back with it; a source keyed on
    the one we could not resolve is simply not probed, rather than probed with a
    derived literal that would return zero rows and read as "no leave recorded".
    """
    keys: dict[str, str] = {}
    warnings: list[str] = []
    bridge = store.table(BRIDGE_TABLE)

    if bridge is not None:
        cols = {c.upper() for c in bridge.columns}
        both = [c for c in ("IGA", "CREW_ID") if c in cols]
        for typed in both:
            where = _current(bridge, f"{typed} = '{ident}'")
            sql = f"SELECT {', '.join(both)} FROM {BRIDGE_TABLE} WHERE {where}"
            try:
                result = executor.execute(sql, limit=1)
            except Exception as exc:  # noqa: BLE001 - one unreadable table is not fatal
                warnings.append(f"Could not read {BRIDGE_TABLE}: {exc}")
                break
            if result.rows:
                keys = {c: v for c, v in zip(both, result.rows[0]) if v}
                break

    if not keys:
        # No bridge row. The identifier may still be a source's own key — probe
        # on it directly rather than reporting the crew member does not exist.
        keys = {"IGA": ident} if ident.upper().startswith("IGA") else {"CREW_ID": ident}
        warnings.append(
            f"{ident} is not in {BRIDGE_TABLE}, so it could not be translated "
            f"across sources. Only sources keyed on "
            f"{', '.join(keys)} were checked."
        )
    return keys, warnings


def _probe_plan(store, keys: dict[str, str]) -> list[dict]:
    """Every table that carries a crew key we hold a value for.

    The key is the *source's* declared join key, not whatever identity-looking
    column the table happens to have. `EMPLOYEE_INFO` carries `EMPLOYEE_ID` as
    well as `IGA`, and counting on the wrong one is a different question.
    """
    try:
        from crew_perf.sources import load_registry

        join_keys = {s.name: s.join_key.upper() for s in load_registry().available()}
    except Exception:  # noqa: BLE001
        join_keys = {}

    plan = []
    for table in store.tables.values():
        key = join_keys.get(table.source, "")
        value = keys.get(key)
        if not key or not value:
            continue
        if key not in {c.upper() for c in table.columns}:
            continue
        plan.append({
            "table": table.name,
            "node_id": table.id,
            "source": table.source,
            "engine": table.engine,
            "column": key,
            "value": value,
            "scd2": bool(table.scd2),
        })
    return sorted(plan, key=lambda p: (p["source"], p["table"]))


def _count(executor, store, probe: dict) -> dict:
    """How many rows this table holds for this crew member.

    An unreadable table returns its error rather than a zero. The difference
    matters more here than anywhere: zero means *nothing was recorded*, and a
    permission gap rendered as zero would say the crew member has no leave when
    what happened is that nobody could look.
    """
    node = store.table(probe["table"])
    where = _current(node, f"{probe['column']} = '{probe['value']}'")
    sql = f"SELECT COUNT(*) FROM {probe['table']} WHERE {where}"
    out = {**probe, "sql": sql, "rows": None, "error": None}
    try:
        result = executor.execute(sql, limit=1)
        out["rows"] = int(result.rows[0][0]) if result.rows else 0
    except Exception as exc:  # noqa: BLE001 - reported per table, never fatal
        out["error"] = str(exc)[:200]
    return out


def _current(node, predicate: str) -> str:
    """Add the SCD-2 currency filter where the table versions its rows.

    Without it a crew member who has changed base three times counts as three
    people's worth of rows on `M_CREW_DETAILS`, and the picture reports history
    as volume.
    """
    if node is not None and getattr(node, "scd2", False):
        return f"{predicate} AND P_IS_CURRENT = TRUE"
    return predicate
