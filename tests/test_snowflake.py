"""The Snowflake connector — the part that can be tested without a warehouse.

Everything here is about ONE failure: the live data is three schemas, every agent
writes bare table names, and a connection defaults to exactly one schema. A query
crossing sources — which is most of the interesting ones — then fails on the
first table outside that default, or worse resolves to a same-named table in it.

The connector itself needs credentials; the resolution it depends on does not,
and that is what is pinned here.
"""

from __future__ import annotations

import pytest

from crew_perf.data.dialect import qualify
from crew_perf.sources import load_registry


@pytest.fixture(scope="module")
def layout():
    return load_registry().warehouse_layout()


# ─── the map ────────────────────────────────────────────────────────────────


def test_every_warehouse_table_knows_its_schema(layout):
    """A table missing from the map is left bare and resolves against whatever
    the session schema happens to be — silently, and against a different table
    if a name collides."""
    registry = load_registry()
    for source in registry.available():
        if not source.is_warehouse:
            continue
        for name in source.tables:
            assert layout.get(name.upper()), f"{source.name}.{name} has no schema"


def test_the_schemas_come_from_the_export_not_from_the_source_name(layout):
    """CrewPortal's schema is CREWPORTAL — the registry's own name is not.
    Deriving one from the other would work for PEP and CLMS and quietly break
    the one that matters most, since CrewPortal holds the identity bridge."""
    assert layout["MENTOR_FEEDBACK"] == "PEP"
    assert layout["M_CLMS_CREW"] == "CLMS"
    assert layout["M_CREW_DETAILS"] == "CREWPORTAL"


def test_only_the_three_warehouse_schemas_are_covered(layout):
    """The exports in schemas/ are the allow-list, and they name three schemas.
    A fourth appearing here would mean something is being qualified into a
    namespace no export described."""
    assert set(layout.values()) == {"PEP", "CLMS", "CREWPORTAL"}


def test_the_local_extract_is_not_in_the_warehouse_layout(layout):
    """ServiceNow is ingested from a CSV into DuckDB and exists in no Snowflake
    schema. Qualifying SN_FLIGHT_REPORT as SERVICENOW.SN_FLIGHT_REPORT sends it
    to a warehouse that has never heard of it, and the failure arrives from
    Snowflake long after the validator approved the query."""
    assert "SN_FLIGHT_REPORT" not in layout
    assert "M_SN_CREW" not in layout


def test_a_source_reports_the_schema_its_tables_live_in():
    reg = load_registry()
    assert reg["CrewPortal"].warehouse_schema == "CREWPORTAL"
    assert reg["CrewPortal"].is_warehouse
    assert not reg["ServiceNow"].is_warehouse


# ─── qualification ──────────────────────────────────────────────────────────


def test_a_cross_source_query_resolves_every_table_in_its_own_schema(layout):
    """The whole point. The identity bridge lives in CREWPORTAL, the assessment
    in PEP and the leave record in CLMS — one query, three schemas, and no
    session default can cover more than one of them."""
    sql = (
        "SELECT mf.IGA FROM MENTOR_FEEDBACK mf "
        "JOIN M_CREW_DETAILS cd ON cd.IGA = mf.IGA "
        "JOIN M_CLMS_CREW c ON c.CREW_ID = cd.CREW_ID "
        "WHERE mf.P_IS_CURRENT = TRUE LIMIT 10"
    )
    out, notes = qualify(sql, layout, database="CREW")
    assert "FROM CREW.PEP.MENTOR_FEEDBACK" in out
    assert "JOIN CREW.CREWPORTAL.M_CREW_DETAILS" in out
    assert "JOIN CREW.CLMS.M_CLMS_CREW" in out
    assert notes and "3 table" in notes[0]


def test_a_cte_is_never_given_a_schema(layout):
    """A CTE is a name the query defined itself. Prefixing it turns a valid
    query into a reference to a table that does not exist — and the failure
    arrives from Snowflake, long after the validator approved the query."""
    sql = ("WITH recent AS (SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE) "
           "SELECT * FROM recent LIMIT 5")
    out, _ = qualify(sql, layout, database="CREW")
    assert "FROM recent" in out
    assert "CREW.PEP.recent" not in out and ".recent" not in out
    assert "FROM CREW.PEP.MENTOR_FEEDBACK" in out


def test_an_already_qualified_name_is_left_alone(layout):
    sql = "SELECT * FROM CREW.PEP.MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 5"
    out, notes = qualify(sql, layout, database="CREW")
    assert out == sql and not notes


def test_an_unmapped_table_is_left_bare_rather_than_guessed(layout):
    """Guessing a schema for a table the graph never modelled would turn a query
    the validator was always going to reject into one that runs against
    something unexamined."""
    out, _ = qualify("SELECT * FROM SOME_OTHER_TABLE LIMIT 1", layout, database="CREW")
    assert out == "SELECT * FROM SOME_OTHER_TABLE LIMIT 1"


def test_aliases_and_column_references_are_untouched(layout):
    """Only the name after FROM or JOIN is a table. Rewriting `mf.IGA` would
    corrupt every qualified column in the query."""
    sql = ("SELECT mf.IGA, mf.MARK FROM MENTOR_FEEDBACK mf "
           "WHERE mf.P_IS_CURRENT = TRUE LIMIT 5")
    out, _ = qualify(sql, layout, database="CREW")
    assert "mf.IGA" in out and "mf.MARK" in out and "mf.P_IS_CURRENT" in out
    assert out.count("CREW.PEP.") == 1


def test_qualification_without_a_database_still_names_the_schema(layout):
    """A connection already bound to the database needs the schema and nothing
    more; forcing a database name in would break a session using a different one."""
    out, _ = qualify("SELECT * FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 1",
                     layout)
    assert "FROM PEP.MENTOR_FEEDBACK" in out


def test_an_empty_layout_changes_nothing(layout):
    """DuckDB shares this code path's input. A missing registry must degrade to
    the query as written, not to a half-qualified one."""
    sql = "SELECT * FROM MENTOR_FEEDBACK LIMIT 1"
    assert qualify(sql, {}) == (sql, [])


def test_a_table_named_in_a_string_literal_is_not_qualified(layout):
    """The comment and literal stripping exists so a value that happens to read
    like SQL cannot steer the rewrite."""
    sql = ("SELECT * FROM MENTOR_FEEDBACK "
           "WHERE P_IS_CURRENT = TRUE AND GRADE = 'FROM M_CLMS_CREW' LIMIT 1")
    out, _ = qualify(sql, layout, database="CREW")
    assert "'FROM M_CLMS_CREW'" in out, "a literal must survive verbatim"
    assert out.count("CREW.") == 1


# ─── the connector, driven without a warehouse ──────────────────────────────
#
# Schema overrides are pinned in test_scope.py, against the scope the connector
# reads its layout from.


class FakeCursor:
    """Records what actually reached the warehouse."""

    def __init__(self, con):
        self.con = con
        self.description = [("ONE",)]
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.con.executed.append(sql)
        self._rows = self.con.responses.get(_kind(sql), [(1,)])
        return self

    def fetchmany(self, n):
        return self._rows[:n]

    def close(self):
        pass


def _kind(sql: str) -> str:
    lowered = sql.lower()
    if "information_schema.tables" in lowered:
        return "tables"
    if "information_schema.columns" in lowered:
        return "columns"
    return "query"


class FakeConnection:
    def __init__(self, **responses):
        self.executed: list[str] = []
        self.kwargs: dict = {}
        self.responses = responses
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def warehouse(monkeypatch):
    """A SnowflakeExecutor over a fake connection, with credentials stubbed."""
    from crew_perf import config
    from crew_perf.data.snowflake import SnowflakeExecutor

    monkeypatch.setitem(config.SNOWFLAKE, "account", "acct")
    monkeypatch.setitem(config.SNOWFLAKE, "user", "svc_crewperf")
    monkeypatch.setitem(config.SNOWFLAKE, "database", "CREW")
    monkeypatch.setitem(config.SNOWFLAKE, "pat", "pat-abc123")

    con = FakeConnection(
        tables=[("MENTOR_FEEDBACK",), ("M_CLMS_CREW",), ("PAYROLL_MASTER",),
                ("T_UNSCOPED_AUDIT",)],
        columns=[("IGA", "TEXT"), ("MARK", "TEXT"), ("COLUMN_ADDED_LATER", "TEXT")],
    )

    def connect(**kwargs):
        con.kwargs = kwargs
        return con

    return SnowflakeExecutor(connect=connect), con


# ─── authentication ─────────────────────────────────────────────────────────


def test_a_pat_is_sent_as_a_token_not_as_a_password(warehouse):
    """They are different login flows. A PAT put in `password` fails against an
    account with MFA enforced, and the error names the password policy rather
    than the token, which is a long way from the actual mistake."""
    ex, con = warehouse
    assert con.kwargs["authenticator"] == "PROGRAMMATIC_ACCESS_TOKEN"
    assert con.kwargs["token"] == "pat-abc123"
    assert "password" not in con.kwargs
    assert ex.auth_method == "programmatic access token"


def test_exactly_one_credential_is_sent(monkeypatch):
    """A stale password left in the environment must not quietly beat the PAT
    that was just issued — the connector picks by its own precedence, and which
    one won is invisible from the failure."""
    from crew_perf import config

    monkeypatch.setitem(config.SNOWFLAKE, "pat", "pat-abc123")
    monkeypatch.setitem(config.SNOWFLAKE, "password", "hunter2")
    monkeypatch.setitem(config.SNOWFLAKE, "private_key_file", "/tmp/rsa_key.p8")

    auth = config.snowflake_auth()
    assert auth == {"authenticator": "PROGRAMMATIC_ACCESS_TOKEN", "token": "pat-abc123",
                    "_method": "programmatic access token"}


def test_the_other_methods_still_work_when_no_pat_is_set(monkeypatch):
    from crew_perf import config

    monkeypatch.setitem(config.SNOWFLAKE, "pat", "")
    monkeypatch.setitem(config.SNOWFLAKE, "private_key_file", "/tmp/rsa_key.p8")
    monkeypatch.setitem(config.SNOWFLAKE, "private_key_passphrase", "s3cret")
    auth = config.snowflake_auth()
    assert auth["_method"] == "key pair"
    assert auth["private_key_file"] == "/tmp/rsa_key.p8"
    assert auth["private_key_file_pwd"] == b"s3cret"

    monkeypatch.setitem(config.SNOWFLAKE, "private_key_file", "")
    monkeypatch.setitem(config.SNOWFLAKE, "password", "hunter2")
    assert config.snowflake_auth() == {"password": "hunter2", "_method": "password"}


def test_no_credential_at_all_fails_before_connecting(monkeypatch):
    """Rather than at the connector, whose message for a credential-less connect
    is about the account, not about the thing that was never set."""
    from crew_perf import config
    from crew_perf.data.snowflake import SnowflakeExecutor

    for key in ("pat", "password", "private_key_file", "authenticator"):
        monkeypatch.setitem(config.SNOWFLAKE, key, "")
    for key, value in (("account", "acct"), ("user", "u"), ("database", "CREW")):
        monkeypatch.setitem(config.SNOWFLAKE, key, value)

    with pytest.raises(RuntimeError, match="SNOWFLAKE_PAT"):
        SnowflakeExecutor(connect=lambda **kw: None)


# ─── the scope bound, at the connector ──────────────────────────────────────


def test_the_catalogue_is_the_intersection_not_what_the_account_holds(warehouse):
    """The account reports four tables; two of them no export declared. Listing
    those hands attribute discovery tables to sample and the retrieval agent a
    vocabulary the graph cannot vouch for."""
    ex, _ = warehouse
    listed = ex.list_tables()
    assert "MENTOR_FEEDBACK" in listed and "M_CLMS_CREW" in listed
    assert "PAYROLL_MASTER" not in listed
    assert "T_UNSCOPED_AUDIT" not in listed


def test_a_scoped_table_the_warehouse_does_not_hold_is_not_listed(warehouse):
    """The other direction. Listing it would have the join verifier report a
    broken relationship instead of an absent table."""
    ex, _ = warehouse
    assert "T_SPL_APPRECIATION" in ex.scope.warehouse_tables
    assert "T_SPL_APPRECIATION" not in ex.list_tables()


def test_describe_is_bounded_to_the_declared_columns(warehouse):
    ex, _ = warehouse
    cols = dict(ex.describe("MENTOR_FEEDBACK"))
    assert "IGA" in cols and "MARK" in cols
    assert "COLUMN_ADDED_LATER" not in cols


def test_describing_an_unscoped_table_is_empty_rather_than_fatal(warehouse):
    """`describe` is called speculatively while a graph is built; raising there
    aborts the build over a table that was never going to be used."""
    ex, con = warehouse
    assert ex.describe("PAYROLL_MASTER") == []
    assert not any("PAYROLL_MASTER" in s for s in con.executed)


def test_an_unscoped_query_never_reaches_the_warehouse(warehouse):
    """Refused here, before execution — a second line behind the validator,
    checking the delivered extract rather than the graph."""
    from crew_perf.data.snowflake import OutOfScopeError

    ex, con = warehouse
    with pytest.raises(OutOfScopeError, match="PAYROLL_MASTER"):
        ex.execute("SELECT * FROM PAYROLL_MASTER LIMIT 1")
    assert con.executed == [], "nothing may be sent to the warehouse"


def test_a_scoped_query_is_qualified_and_executed(warehouse):
    ex, con = warehouse
    result = ex.execute(
        "SELECT mf.IGA FROM MENTOR_FEEDBACK mf JOIN M_CLMS_CREW c "
        "ON c.CREW_ID = mf.IGA LIMIT 5"
    )
    sent = con.executed[-1]
    assert "CREW.PEP.MENTOR_FEEDBACK" in sent and "CREW.CLMS.M_CLMS_CREW" in sent
    # What comes back is the SQL as written: the trace, the golden queries and
    # the validator all speak in bare table names.
    assert "CREW.PEP." not in result.sql
