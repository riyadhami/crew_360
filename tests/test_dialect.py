"""Snowflake -> DuckDB translation. Agents emit Snowflake dialect always."""

import duckdb
import pandas as pd
import pytest

from crew_perf.data.dialect import is_read_only, to_duckdb


def _runs(sql: str):
    translated, _ = to_duckdb(sql)
    return duckdb.sql(translated).fetchall()


def test_dateadd_translates_and_executes():
    # Value must match. Type intentionally does not: DuckDB's DATE + INTERVAL
    # widens to TIMESTAMP where Snowflake's DATEADD on a DATE stays a DATE.
    # Harmless in practice (DuckDB compares the two freely) but it is a real
    # difference, so assert the instant rather than the type.
    (result,), = _runs("SELECT DATEADD(day, 7, DATE '2026-01-01') AS d")
    assert pd.Timestamp(result) == pd.Timestamp("2026-01-08")


def test_datediff_translates_and_executes():
    assert _runs("SELECT DATEDIFF(day, DATE '2026-01-01', DATE '2026-01-08') AS d") == [(7,)]


def test_iff_translates_and_executes():
    assert _runs("SELECT IFF(1 = 1, 'yes', 'no') AS r") == [("yes",)]


def test_nested_calls_translate():
    sql = "SELECT IFF(DATEDIFF(day, DATE '2026-01-01', DATE '2026-02-01') > 20, 'far', 'near') AS r"
    assert _runs(sql) == [("far",)]


def test_untranslatable_call_is_left_intact_not_mangled():
    """An unknown date part must be passed through, not silently corrupted."""
    out, notes = to_duckdb("SELECT DATEADD(fortnight, 1, x) FROM t")
    assert "DATEADD(fortnight, 1, x)" in out
    assert not notes


def test_multiple_occurrences_all_translate():
    out, _ = to_duckdb("SELECT IFF(a, 1, 2), IFF(b, 3, 4), IFF(c, 5, 6)")
    assert "IFF(" not in out.upper()
    assert out.upper().count("CASE WHEN") == 3


def test_to_varchar_does_not_corrupt_existing_casts():
    out, _ = to_duckdb("SELECT CAST(a AS INTEGER), TO_VARCHAR(b) FROM t")
    assert "CAST(a AS INTEGER)" in out
    assert "CAST(b AS VARCHAR)" in out


def test_translation_is_idempotent():
    once, _ = to_duckdb("SELECT DATEADD(day, 7, d), IFF(x, 1, 2) FROM t")
    twice, _ = to_duckdb(once)
    assert once == twice


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "  WITH a AS (SELECT 1) SELECT * FROM a",
    "-- comment\nSELECT * FROM t",
])
def test_read_only_accepts_queries(sql):
    assert is_read_only(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE t",
    "SELECT 1; DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "CREATE TABLE t (a INT)",
    "",
])
def test_read_only_rejects_mutations(sql):
    assert not is_read_only(sql)
