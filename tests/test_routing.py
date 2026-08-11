"""One executor over two engines — and the one thing it must refuse.

In production PEP, CLMS and CrewPortal are Snowflake schemas while ServiceNow is
a CSV extract in a local DuckDB file. Everything above the executor holds a
single `SqlExecutor` and does not know there are two, so the router is the only
place that can get this wrong — and both ways of getting it wrong are quiet.

Send a ServiceNow query to Snowflake and it fails on a table that is sitting on
disk. Send a warehouse query to DuckDB and it *succeeds*, against the synthetic
dataset `crewperf synth` wrote into the same file — 800 invented crew, under the
real table's name, indistinguishable in the output.
"""

from __future__ import annotations

import pytest

from crew_perf.data.executor import QueryResult
from crew_perf.data.routing import CrossEngineError, RoutingExecutor


class FakeEngine:
    """Stands in for either backend; records what it was asked."""

    def __init__(self, name: str, tables: list[str]):
        self.name = name
        self.tables = tables
        self.seen: list[str] = []
        self.closed = False

    def execute(self, sql, params=None, limit=500) -> QueryResult:
        self.seen.append(sql)
        return QueryResult(columns=["ENGINE"], rows=[(self.name,)], sql=sql, row_count=1)

    def list_tables(self) -> list[str]:
        return list(self.tables)

    def describe(self, table):
        return [(f"{self.name}_COL", "TEXT")]

    def health(self) -> dict:
        return {"database": "CREW", "session_schema": "PEP", "auth": "programmatic "
                "access token", "schemas": []}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def router():
    warehouse = FakeEngine("warehouse", ["MENTOR_FEEDBACK", "M_CLMS_CREW", "M_CREW_DETAILS"])
    # The local file also holds the synthetic copies of the warehouse tables —
    # that is the whole hazard, so the fake carries them too.
    local = FakeEngine("local", ["SN_FLIGHT_REPORT", "M_SN_CREW", "MENTOR_FEEDBACK",
                                 "EMPLOYEE_INFO"])
    return RoutingExecutor(warehouse=warehouse, local=local), warehouse, local


# ─── routing ────────────────────────────────────────────────────────────────


def test_a_warehouse_query_goes_to_snowflake(router):
    ex, warehouse, local = router
    assert ex.execute("SELECT IGA FROM MENTOR_FEEDBACK LIMIT 5").rows == [("warehouse",)]
    assert len(warehouse.seen) == 1 and local.seen == []


def test_a_servicenow_query_goes_to_the_local_extract(router):
    ex, warehouse, local = router
    assert ex.execute("SELECT * FROM SN_FLIGHT_REPORT LIMIT 5").rows == [("local",)]
    assert len(local.seen) == 1 and warehouse.seen == []


def test_a_warehouse_table_is_never_answered_from_the_synthetic_copy(router):
    """MENTOR_FEEDBACK exists in both engines. It is a PEP table, so production
    is the only correct answer — the local one is 800 invented crew wearing the
    same name."""
    ex, _, local = router
    assert ex.execute("SELECT IGA FROM MENTOR_FEEDBACK LIMIT 1").rows == [("warehouse",)]
    assert local.seen == []


def test_a_query_spanning_both_engines_is_refused_by_name(router):
    """There is no shared catalogue between a Snowflake session and a DuckDB
    file. Picking one engine returns rows from half the join condition, and
    nothing downstream can tell that happened."""
    ex, warehouse, local = router
    with pytest.raises(CrossEngineError) as excinfo:
        ex.execute(
            "SELECT r.SN_REPORT_ID FROM SN_FLIGHT_REPORT r "
            "JOIN M_CREW_DETAILS d ON d.IGA = r.L1_IGA LIMIT 5"
        )
    message = str(excinfo.value)
    assert "M_CREW_DETAILS" in message and "SN_FLIGHT_REPORT" in message
    assert "separately" in message, "the refusal has to say what to do instead"
    assert warehouse.seen == [] and local.seen == []


def test_an_unscoped_query_is_refused_with_the_warehouse_wording(router):
    """Delegated rather than duplicated: two places describing what is in scope
    is how the two descriptions drift apart."""
    from crew_perf.data.snowflake import OutOfScopeError

    ex, warehouse, _ = router
    warehouse.execute = _raising(OutOfScopeError("table(s) not in the scoped schemas: X"))
    with pytest.raises(OutOfScopeError):
        ex.execute("SELECT * FROM PAYROLL_MASTER LIMIT 1")


def _raising(exc):
    def _fn(*args, **kwargs):
        raise exc
    return _fn


# ─── catalogue ──────────────────────────────────────────────────────────────


def test_the_catalogue_is_the_union_of_both_engines(router):
    ex, _, _ = router
    listed = ex.list_tables()
    assert "MENTOR_FEEDBACK" in listed        # warehouse
    assert "SN_FLIGHT_REPORT" in listed       # local extract


def test_the_local_side_is_scoped_too(router):
    """EMPLOYEE_INFO is in the local file as synthetic data, but it is a PEP
    table — the local engine does not own it, so it must not be listed from
    there. Otherwise the same name appears twice from two populations."""
    ex, _, _ = router
    assert "EMPLOYEE_INFO" not in ex.list_tables()


def test_describe_follows_the_same_routing(router):
    ex, _, _ = router
    assert ex.describe("MENTOR_FEEDBACK") == [("warehouse_COL", "TEXT")]
    assert ex.describe("SN_FLIGHT_REPORT") == [("local_COL", "TEXT")]


def test_health_reports_the_local_extract_beside_the_schemas(router):
    """From a scorecard, "no ServiceNow data" and "no CLMS grant" look
    identical — both are a signal reported as missing."""
    ex, _, _ = router
    local = ex.health()["local_extract"]
    assert local["ingested"] is True
    assert local["tables_expected"] == len(ex.scope.local)


# ─── a deployment that never ingested the extract ───────────────────────────


def test_a_missing_extract_is_not_a_startup_failure(monkeypatch, tmp_path):
    """A deployment with no ServiceNow ingest is valid — the source simply has
    no data. Opening a database that is not there to answer a question nobody
    asked would fail the whole run instead."""
    from crew_perf import config

    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "absent.duckdb")
    warehouse = FakeEngine("warehouse", ["MENTOR_FEEDBACK"])
    ex = RoutingExecutor(warehouse=warehouse)

    assert ex.list_tables() == ["MENTOR_FEEDBACK"]
    assert ex.execute("SELECT IGA FROM MENTOR_FEEDBACK LIMIT 1").rows == [("warehouse",)]
    assert ex.health()["local_extract"]["ingested"] is False


def test_querying_a_missing_extract_says_how_to_fix_it(monkeypatch, tmp_path):
    from crew_perf import config

    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "absent.duckdb")
    ex = RoutingExecutor(warehouse=FakeEngine("warehouse", []))
    with pytest.raises(RuntimeError, match="ingest-servicenow"):
        ex.execute("SELECT * FROM SN_FLIGHT_REPORT LIMIT 1")


def test_closing_closes_both_engines(router):
    ex, warehouse, local = router
    ex.close()
    assert warehouse.closed and local.closed
