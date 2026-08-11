"""The bound on the production warehouse, and the routing between two engines.

Two failures are pinned here, and both are silent ones.

The first is reach. The production account is far larger than this project, and
a connector with no bound will happily list, describe and query tables no export
ever declared — so attribute discovery samples them, the retrieval agent is
offered them as vocabulary, and an answer comes back from a table nobody scoped.

The second is engine. PEP, CLMS and CrewPortal are warehouse schemas; ServiceNow
is a CSV extract living in a local DuckDB file. Sending a query to the wrong one
does not error in the interesting cases — the same DuckDB also holds a full
synthetic copy of the warehouse tables, so a misrouted `MENTOR_FEEDBACK` returns
800 invented crew that look exactly like production.

None of this needs a warehouse: the scope is read from `schemas/*.csv`, and the
connector is driven through an injected connection.
"""

from __future__ import annotations

import pytest

from crew_perf.data import scope as scope_mod
from crew_perf.data.scope import LOCAL, MIXED, UNKNOWN, WAREHOUSE, build_scope


@pytest.fixture(scope="module")
def scope():
    return build_scope()


# ─── what is in scope ───────────────────────────────────────────────────────


def test_the_allow_list_is_the_three_schema_exports(scope):
    assert scope.schemas == ["CLMS", "CREWPORTAL", "PEP"]


def test_a_table_from_each_export_is_in_scope(scope):
    assert scope.engine_of("MENTOR_FEEDBACK") == WAREHOUSE       # pep_schema.csv
    assert scope.engine_of("M_CLMS_CREW") == WAREHOUSE           # clms_schema.csv
    assert scope.engine_of("M_CREW_DETAILS") == WAREHOUSE        # crewportal_schema.csv


def test_a_table_the_account_holds_but_no_export_declares_is_out_of_scope(scope):
    """The whole point of the bound. These are plausible names in an airline
    warehouse; none of them is in the three CSVs, so none of them is reachable
    however well-formed the query naming it is."""
    for table in ("COPS_ROSTER", "PAYROLL_MASTER", "T_CLMS_SOMETHING_NEW"):
        assert scope.engine_of(table) == UNKNOWN
        assert not scope.in_scope(table)


def test_scope_is_column_level_not_just_table_level(scope):
    """The exports carry columns, so the bound does too — a column added to a
    live table after the export is invisible until the export is refreshed,
    which is the same cut the graph and the validator were built from."""
    cols = scope.columns_of("MENTOR_FEEDBACK")
    assert {"IGA", "MARK", "GRADE", "P_IS_CURRENT"} <= cols
    assert "COLUMN_ADDED_LAST_TUESDAY" not in cols


def test_out_of_scope_reports_the_offending_names_in_order(scope):
    assert scope.out_of_scope(["MENTOR_FEEDBACK", "PAYROLL", "M_CLMS_CREW", "ROSTER"]) == [
        "PAYROLL", "ROSTER",
    ]


def test_an_env_override_moves_a_whole_source(monkeypatch):
    """For a warehouse that renamed the schemas on the way in: the tables are
    right and only the namespace moved."""
    from crew_perf import config

    monkeypatch.setattr(config, "SNOWFLAKE_SCHEMA_OVERRIDES", {"CLMS": "LEAVE_MGMT"})
    moved = build_scope()
    assert moved.layout["M_CLMS_CREW"] == "LEAVE_MGMT"
    assert moved.layout["MENTOR_FEEDBACK"] == "PEP", "an override must not leak across sources"


# ─── which engine owns what ─────────────────────────────────────────────────


def test_the_servicenow_extract_is_local_not_warehouse(scope):
    """It is ingested from a CSV into DuckDB. It exists in no Snowflake schema,
    so routing it to the warehouse asks for a table that is sitting on disk."""
    assert scope.engine_of("SN_FLIGHT_REPORT") == LOCAL
    assert scope.engine_of("M_SN_CREW") == LOCAL
    assert "SN_FLIGHT_REPORT" not in scope.layout


def test_a_warehouse_query_routes_to_the_warehouse(scope):
    engine, tables = scope.route(
        "SELECT mf.IGA FROM MENTOR_FEEDBACK mf JOIN M_CLMS_CREW c ON c.CREW_ID = mf.IGA LIMIT 5"
    )
    assert engine == WAREHOUSE
    assert tables == ["MENTOR_FEEDBACK", "M_CLMS_CREW"]


def test_a_servicenow_query_routes_to_the_local_extract(scope):
    engine, _ = scope.route(
        "SELECT r.SN_REPORT_ID FROM SN_FLIGHT_REPORT r JOIN SN_REPORT_CREW c "
        "ON c.SN_REPORT_ID = r.SN_REPORT_ID LIMIT 5"
    )
    assert engine == LOCAL


def test_a_query_spanning_both_engines_is_flagged_not_guessed(scope):
    """One statement runs on one connection. Picking an engine here would return
    rows from half the join condition and no error to say so."""
    engine, _ = scope.route(
        "SELECT * FROM SN_FLIGHT_REPORT r JOIN M_CREW_DETAILS d ON d.IGA = r.L1_IGA LIMIT 5"
    )
    assert engine == MIXED


def test_a_query_naming_an_undeclared_table_routes_nowhere(scope):
    engine, _ = scope.route("SELECT * FROM PAYROLL_MASTER LIMIT 1")
    assert engine == UNKNOWN


def test_a_cte_is_not_mistaken_for_an_out_of_scope_table(scope):
    """A CTE is a name the query defined itself. Treating it as an unknown table
    rejects a valid query, and the agent has no way to act on the rejection."""
    engine, tables = scope.route(
        "WITH recent AS (SELECT IGA FROM MENTOR_FEEDBACK LIMIT 10) "
        "SELECT * FROM recent LIMIT 5"
    )
    assert engine == WAREHOUSE
    assert tables == ["MENTOR_FEEDBACK"]


def test_a_table_named_in_a_string_literal_does_not_change_the_route(scope):
    engine, tables = scope.route(
        "SELECT * FROM MENTOR_FEEDBACK WHERE GRADE = 'FROM PAYROLL_MASTER' LIMIT 1"
    )
    assert engine == WAREHOUSE
    assert tables == ["MENTOR_FEEDBACK"]


def test_a_query_naming_no_table_at_all_still_routes(scope):
    engine, tables = scope.route("SELECT CURRENT_DATE()")
    assert engine == WAREHOUSE and tables == []


# ─── the cached accessor ────────────────────────────────────────────────────


def test_get_scope_is_cached_and_refreshable():
    first = scope_mod.get_scope()
    assert scope_mod.get_scope() is first
    assert scope_mod.get_scope(fresh=True) is not first
