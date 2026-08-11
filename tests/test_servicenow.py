"""ServiceNow extract — parsing, normalisation, and what it must never invent."""

from __future__ import annotations

import pytest

from crew_perf.data import servicenow as sn


# ─── Parsing ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cell,iga,name",
    [
        ("IGA35231 - Aayushi Thapa", "IGA35231", "Aayushi Thapa"),
        ("IGA50488 -  vibhuti Ninad Mhatre", "IGA50488", "vibhuti Ninad Mhatre"),
        ("IGA0109 - RAHUL KISHORE KAPOOR", "IGA0109", "RAHUL KISHORE KAPOOR"),
        ("", None, ""),
        ("Not Applicable", None, "Not Applicable"),
    ],
)
def test_crew_cells_parse_to_an_iga_and_a_name(cell, iga, name):
    assert sn.parse_crew(cell) == (iga, name)


def test_internal_spacing_does_not_create_a_second_crew_member():
    """`IGA 35231` and `IGA35231` are one person. Left unnormalised they become
    two rows in the crew master and every rate computed per crew halves."""
    assert sn.parse_crew("IGA 35231 - A B")[0] == sn.parse_crew("IGA35231 - A B")[0]


@pytest.mark.parametrize(
    "text,hours",
    [
        ("19 Hours 19 Minutes", 19 + 19 / 60),
        ("3 Days 6 Hours 53 Minutes", 78 + 53 / 60),
        ("2 Days", 48.0),
        ("", None),
        ("not a duration", None),
    ],
)
def test_durations_parse_to_hours(text, hours):
    got = sn.parse_duration_hours(text)
    assert got is None if hours is None else abs(got - hours) < 0.01


def test_lead_and_l1_are_one_seat_not_two():
    """`LD, L1` names the lead twice — the extract carries them in one column."""
    assert sn.parse_positions("LD, L1") == {"L1"}


def test_na_means_nobody_was_named():
    assert sn.parse_positions("NA") == set()
    assert sn.parse_positions("N/A") == set()
    assert sn.parse_positions("L1, R1") == {"L1", "R1"}


# ─── Ingest, against the real extract ───────────────────────────────────────


@pytest.fixture(scope="module")
def extract():
    if not sn.CSV_PATH.exists():
        pytest.skip("no ServiceNow extract in the repo")
    return sn.read_extract()


def test_every_grain_is_produced(extract):
    frames, result = extract
    assert set(frames) == {"SN_FLIGHT_REPORT", "SN_REPORT_CREW", "SN_SERVICE_CHECK",
                           "M_SN_CREW", "M_SN_CATEGORY"}
    assert all(result.tables[name] > 0 for name in frames)


def test_report_ids_are_unique(extract):
    """A repeated incident would double every rate computed over it."""
    frames, _ = extract
    ids = [r["SN_REPORT_ID"] for r in frames["SN_FLIGHT_REPORT"]]
    assert len(ids) == len(set(ids))


def test_shifted_rows_are_dropped_and_counted(extract):
    """An embedded delimiter offsets every field. Realigning by guesswork would
    attribute a report to whichever crew landed in the column."""
    _, result = extract
    assert result.rows_malformed > 0
    assert any("shifted" in f for f in result.findings)
    assert result.rows_malformed < result.rows_read * 0.01


def test_crew_are_only_attributed_from_a_column_that_names_them(extract):
    """L2 and R2 are named as involved but have no crew column at all. Involvement
    must under-count rather than land on whoever else is on the report."""
    _, result = extract
    assert result.positions_unresolved > 0
    assert any("cannot be identified" in f for f in result.findings)


def test_involvement_is_a_subset_of_presence(extract):
    """Being on the flight is not being named in the report."""
    frames, _ = extract
    crew = frames["SN_REPORT_CREW"]
    involved = sum(1 for r in crew if r["IS_INVOLVED"])
    assert 0 < involved < len(crew)


def test_unanswered_service_checks_are_null_not_a_pass(extract):
    """Half the reports leave the deviation check blank. A blank read as 'no
    deviation' would score a crew member for a question nobody answered."""
    frames, _ = extract
    checks = {c["CHECK_CODE"] for c in frames["SN_SERVICE_CHECK"]}
    assert "DEVIATION" in checks
    unknown = [c for c in frames["SN_SERVICE_CHECK"] if c["IS_PASS"] is None]
    assert unknown, "no unanswered check was preserved as unknown"
    assert all(c["CHECK_VALUE"] or c["CHECK_COMMENT"] for c in frames["SN_SERVICE_CHECK"])


def test_a_deviation_is_the_one_check_where_yes_is_bad(extract):
    frames, _ = extract
    deviations = [c for c in frames["SN_SERVICE_CHECK"] if c["CHECK_CODE"] == "DEVIATION"]
    yes = [c for c in deviations if c["CHECK_VALUE"].lower().startswith("yes")]
    assert yes and all(c["IS_PASS"] is False for c in yes)


def test_category_spellings_are_collapsed(extract):
    """`Customer issues` and `Customer Issues` are one category. Two spellings
    would split every rate computed per category."""
    frames, _ = extract
    labels = {c["CATEGORY"] for c in frames["M_SN_CATEGORY"]}
    assert len({label.lower() for label in labels}) == len(labels)


def test_the_crew_master_totals_match_the_detail(extract):
    frames, _ = extract
    from collections import Counter

    counted = Counter(r["IGA"] for r in frames["SN_REPORT_CREW"])
    for master in frames["M_SN_CREW"]:
        assert master["REPORT_COUNT"] == counted[master["IGA"]]


def test_the_published_schema_matches_what_is_loaded(extract):
    """The registry reads the CSV, not the DataFrames. A column present in one
    and not the other is a table the validator will reject queries against."""
    frames, _ = extract
    for name, rows in frames.items():
        declared = set(sn.TABLES_BY_NAME[name].column_names)
        assert set(rows[0]) == declared, f"{name} row keys diverge from the declared schema"


# ─── Loaded into the working database ───────────────────────────────────────


def test_tables_are_queryable_and_versioned(executor):
    tables = set(executor.list_tables())
    if "SN_REPORT_CREW" not in tables:
        pytest.skip("extract not loaded — run `crewperf ingest-servicenow`")
    for name in sn.TABLES_BY_NAME:
        assert name in tables
        cols = {c.upper() for c, _ in executor.describe(name)}
        assert "P_IS_CURRENT" in cols, f"{name} would bypass the currency rule"


def test_every_crew_row_resolves_to_the_crew_master(executor):
    if "SN_REPORT_CREW" not in set(executor.list_tables()):
        pytest.skip("extract not loaded")
    res = executor.execute(
        "SELECT COUNT(*) FROM SN_REPORT_CREW c "
        "WHERE c.P_IS_CURRENT = TRUE AND NOT EXISTS "
        "(SELECT 1 FROM M_SN_CREW m WHERE m.IGA = c.IGA AND m.P_IS_CURRENT = TRUE) LIMIT 1",
        limit=1,
    )
    assert res.rows[0][0] == 0
