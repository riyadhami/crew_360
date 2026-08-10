"""CAC appreciation letters — the second file extract.

Like ServiceNow, this is not a warehouse schema: it arrives as a spreadsheet
export and is ingested into the same local DuckDB, so in Snowflake mode the
system reads three engines' worth of scope through two connections — PEP, CLMS
and CrewPortal from the warehouse, ServiceNow and CAC from the local file.

**Three columns of eleven are loaded.** The export carries `Name`, `Initiator`
and `Approved By` — three people per row — plus base, issue type and status. Only
the crew identifier, the flight and the words of the appreciation are taken:

    IGA        who was appreciated, and the only identity this system holds
    FLIGHT_NO  which sector it happened on
    COMMENTS   what was actually written

The rest is dropped at read, not at query time. Personal data is hashed behind
the identifier in the warehouse, and an extract that arrives with names attached
would reintroduce locally exactly what the warehouse went to the trouble of
removing — see `graph.schema.PII_COLUMNS`. `Print Time` is empty on every row of
the delivered file and `Status` is 'Approved' on every row, so neither could
separate anybody even if it were wanted.

**IGA is written as a bare integer here** (`94736`) and as `IGA94736` everywhere
else. Normalised on the way in: an identifier that does not match is not a
smaller population, it is zero rows, and it fails silently at every join.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from crew_perf import config

SOURCE_NAME = "CAC"
SCHEMA_FILE = "cac_schema.csv"
EXTRACT_DIR = config.REPO_ROOT / "cac"

# What the spreadsheet calls the three columns worth loading.
COL_IGA = "IGA"
COL_FLIGHT = "Flight No"
COL_COMMENTS = "Comments"


@dataclass
class TableSpec:
    name: str
    grain: str
    columns: list[tuple[str, str, str]]

    @property
    def column_names(self) -> list[str]:
        return [c[0] for c in self.columns]


_AUDIT_COLUMNS = [
    ("P_IS_CURRENT", "BOOLEAN", "SCD-2 currency flag. Always TRUE for a file extract; "
                                "present so this source obeys the same currency rule as "
                                "the warehouse sources."),
    ("LOAD_DATE", "TIMESTAMP_NTZ", "When this extract was ingested."),
]

TABLES: list[TableSpec] = [
    TableSpec(
        name="CAC_APPRECIATION",
        grain="one appreciation letter recorded for one crew member",
        columns=[
            ("CAC_ID", "NUMBER", "Row number within the extract. Primary key; the export "
                                 "carries no identifier of its own."),
            ("IGA", "VARCHAR", "Crew identifier of the person appreciated, as spelled in "
                               "every other source. The join to the rest of the system."),
            ("FLIGHT_NO", "VARCHAR", "Flight the appreciation relates to; null where the "
                                     "export did not record one."),
            ("COMMENTS", "VARCHAR", "What was written about the crew member. Free text — "
                                    "the substance of the appreciation."),
            *_AUDIT_COLUMNS,
        ],
    ),
    TableSpec(
        name="M_CAC_CREW",
        grain="one crew member appearing anywhere in the appreciation extract",
        columns=[
            ("IGA", "VARCHAR", "Crew identifier. Primary key, and the join to every other "
                               "source. No name is carried: this source identifies a crew "
                               "member by IGA and by nothing else."),
            ("APPRECIATION_COUNT", "NUMBER", "Appreciation letters recorded for this crew member."),
            ("FLIGHTS_NAMED", "NUMBER", "Distinct flights those appreciations name."),
            *_AUDIT_COLUMNS,
        ],
    ),
]

TABLES_BY_NAME = {t.name: t for t in TABLES}

_IGA_DIGITS = re.compile(r"(\d+)")


def default_path() -> Path | None:
    """The most recent export in `cac/`.

    Matched by glob rather than named in config: the file is dated
    (`IFSAppreciation_2026-08-06.xlsx`) and a refreshed drop should be picked up
    by putting it in the directory, not by editing a constant that then disagrees
    with what is on disk.
    """
    if not EXTRACT_DIR.is_dir():
        return None
    found = sorted(EXTRACT_DIR.glob("*.xlsx"))
    return found[-1] if found else None


def normalise_iga(value) -> str:
    """`94736`, `94736.0`, `IGA 94736` -> `IGA94736`."""
    text = str(value or "").strip().upper()
    if not text:
        return ""
    digits = _IGA_DIGITS.search(text)
    return f"IGA{digits.group(1)}" if digits else ""


def normalise_flight(value) -> str:
    """`842.0` -> `842`. Read as a float by the spreadsheet, meant as a label."""
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none"}:
        return ""
    return text[:-2] if text.endswith(".0") else text


@dataclass
class IngestResult:
    rows_read: int = 0
    crew: int = 0
    tables: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    dropped_columns: list = field(default_factory=list)


def read_extract(path: Path | None = None) -> tuple[dict, IngestResult]:
    """Parse the spreadsheet into the two tables, keeping three columns."""
    import pandas as pd

    path = path or default_path()
    if path is None or not path.exists():
        raise FileNotFoundError(f"No CAC export in {EXTRACT_DIR}")

    frame = pd.read_excel(path)
    result = IngestResult(rows_read=int(len(frame)))
    result.dropped_columns = [c for c in frame.columns
                              if c not in {COL_IGA, COL_FLIGHT, COL_COMMENTS}]

    missing = [c for c in (COL_IGA, COL_FLIGHT, COL_COMMENTS) if c not in frame.columns]
    if missing:
        raise ValueError(f"{path.name} has no {', '.join(missing)} column(s)")

    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows, master, unusable, no_flight = [], {}, 0, 0

    for n, record in enumerate(frame.to_dict("records"), start=1):
        iga = normalise_iga(record.get(COL_IGA))
        if not iga:
            unusable += 1
            continue
        flight = normalise_flight(record.get(COL_FLIGHT))
        if not flight:
            no_flight += 1
        comments = str(record.get(COL_COMMENTS) or "").strip()

        rows.append({
            "CAC_ID": n, "IGA": iga, "FLIGHT_NO": flight or None,
            "COMMENTS": comments or None,
            "P_IS_CURRENT": True, "LOAD_DATE": loaded_at,
        })
        entry = master.setdefault(iga, {
            "IGA": iga, "APPRECIATION_COUNT": 0, "FLIGHTS_NAMED": set(),
            "P_IS_CURRENT": True, "LOAD_DATE": loaded_at,
        })
        entry["APPRECIATION_COUNT"] += 1
        if flight:
            entry["FLIGHTS_NAMED"].add(flight)

    crew_rows = [{**e, "FLIGHTS_NAMED": len(e["FLIGHTS_NAMED"])} for e in master.values()]
    result.crew = len(crew_rows)
    result.tables = {"CAC_APPRECIATION": len(rows), "M_CAC_CREW": len(crew_rows)}

    if result.dropped_columns:
        result.findings.append(
            f"{len(result.dropped_columns)} column(s) in the export are not loaded "
            f"({', '.join(map(str, result.dropped_columns))}) — Name, Initiator and "
            f"Approved By identify people and are excluded on principle; the rest carry "
            f"no signal this system scores"
        )
    if unusable:
        result.findings.append(
            f"{unusable} row(s) carry no readable IGA — dropped rather than guessed, "
            f"because an appreciation attributed to the wrong crew member is worse "
            f"than one that is missing"
        )
    if no_flight:
        result.findings.append(
            f"{no_flight} appreciation(s) name no flight — loaded and countable "
            f"per crew member, but they cannot be tied to a sector"
        )
    return {"CAC_APPRECIATION": rows, "M_CAC_CREW": crew_rows}, result


def write_schema_csv(path: Path | None = None) -> Path:
    """Emit the column-level export the registry reads, as every source does."""
    path = path or config.SCHEMAS_DIR / SCHEMA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["TABLE_SCHEMA", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE",
                         "COLUMN_DEFAULT", "COMMENT"])
        for table in TABLES:
            for name, dtype, comment in table.columns:
                writer.writerow([SOURCE_NAME.upper(), table.name, name, dtype, "", comment])
    return path


def load(path: Path | None = None, executor=None) -> IngestResult:
    """Parse the export and write its tables into the working database."""
    import pandas as pd

    from crew_perf.data.executor import DuckDBExecutor

    frames, result = read_extract(path)
    own = executor is None
    executor = executor or DuckDBExecutor(read_only=False)
    try:
        for name, rows in frames.items():
            spec = TABLES_BY_NAME[name]
            df = pd.DataFrame(rows, columns=spec.column_names)
            for column, dtype, _ in spec.columns:
                if dtype.startswith("TIMESTAMP"):
                    df[column] = pd.to_datetime(df[column], errors="coerce")
                elif dtype == "NUMBER":
                    df[column] = pd.to_numeric(df[column], errors="coerce")
                elif dtype == "BOOLEAN":
                    df[column] = df[column].astype("boolean")
                else:
                    df[column] = df[column].astype("object")
            executor.register_df(name, df)
    finally:
        if own:
            executor.close()

    write_schema_csv()
    return result
