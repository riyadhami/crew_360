"""What population a file extract is allowed to describe.

ServiceNow and CAC arrive whole: 13,996 and 7,756 crew, of whom the synthetic
fleet is a sample. Loaded unfiltered, the database ends up describing two
populations at once — 800 crew with assessments, leave and check-ins, and
thousands more who exist only as an inflight report or an appreciation letter.

Nothing breaks, but every count is then ambiguous. Asked how many crew are in
the fleet, the agent read `M_SN_CREW` and answered 9,083, which is true of the
extract and false of the system: those crew have no assessment, no leave record
and no scorecard, and cannot be ranked against anybody.

So the extracts are cut to the fleet on the way in. What the database holds is
one population, described five ways.
"""

from __future__ import annotations

# The crew master to read the fleet from. `EMPLOYEE_INFO` rather than
# `M_CREW_DETAILS`: the generator deliberately omits a few crew from CrewPortal
# so join-coverage reporting has something real to measure, and those crew are
# still part of the fleet.
FLEET_TABLE = "EMPLOYEE_INFO"
FLEET_COLUMN = "IGA"


def fleet_igas(executor=None) -> frozenset[str]:
    """Every crew member the generated fleet contains, or empty if there is none.

    Empty is the honest answer for a database with no synthetic data in it yet —
    `ingest-servicenow` before `synth` is a valid order — and callers treat it as
    "do not filter" rather than "filter everything away".
    """
    from crew_perf.data.executor import DuckDBExecutor

    own = executor is None
    executor = executor or DuckDBExecutor(read_only=False)
    try:
        if FLEET_TABLE not in set(executor.list_tables()):
            return frozenset()
        rows = executor.execute(
            f"SELECT DISTINCT {FLEET_COLUMN} FROM {FLEET_TABLE} "
            f"WHERE {FLEET_COLUMN} IS NOT NULL",
            limit=1_000_000,
        ).rows
        return frozenset(str(r[0]) for r in rows if r[0])
    except Exception:  # noqa: BLE001 - a database without the table is not an error
        return frozenset()
    finally:
        if own:
            executor.close()


def restrict(frames: dict, fleet: frozenset[str], column: str = "IGA") -> dict:
    """Drop rows naming a crew member outside the fleet.

    Rows carrying no identifier at all are kept: a flight report with no crew
    named is still a fact about the flight, and dropping it would silently shrink
    the denominator of every rate computed per report.
    """
    if not fleet:
        return {"kept": 0, "dropped": 0, "crew_before": 0, "crew_after": 0}

    before, after, dropped, kept = set(), set(), 0, 0
    for name, rows in frames.items():
        if not rows or column not in rows[0]:
            continue
        surviving = []
        for row in rows:
            iga = row.get(column)
            if iga:
                before.add(iga)
            if iga and iga not in fleet:
                dropped += 1
                continue
            if iga:
                after.add(iga)
            surviving.append(row)
            kept += 1
        frames[name] = surviving
    return {"kept": kept, "dropped": dropped,
            "crew_before": len(before), "crew_after": len(after)}


def cascade(frames: dict, parent_tables, child_table: str, key: str) -> int:
    """Drop parent rows whose children were all cut away.

    A ServiceNow report is flight-grain and carries no crew identifier, so the
    crew filter cannot see it. Left alone, two thirds of the reports survive
    naming nobody in the fleet — and every rate computed per report is then
    divided by a population the fleet has no part in.
    """
    surviving = {row.get(key) for row in frames.get(child_table, []) if row.get(key)}
    if not surviving:
        return 0
    dropped = 0
    for table in parent_tables:
        rows = frames.get(table) or []
        keep = [r for r in rows if r.get(key) in surviving]
        dropped += len(rows) - len(keep)
        frames[table] = keep
    return dropped
