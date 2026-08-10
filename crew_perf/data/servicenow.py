"""ServiceNow crew-feedback extract: parse, normalise, load, and publish a schema.

The extract is one wide CSV row per ServiceNow incident raised through the Crew
Portal integration — a flight report with the operating crew named by seat
position, a category and sub-category, free-text situation/action/outcome prose,
resolution state, and a service checklist filled in by the lead. It is the only
source in the system that arrives as a file rather than as a warehouse schema, so
this module does what the warehouse would otherwise have done:

  1. splits the wide row into the four grains it actually contains — report,
     report x crew position, report x service check, and a crew master;
  2. writes them into the same DuckDB the rest of the pipeline reads;
  3. emits `schemas/servicenow_schema.csv` in the same column-level shape as
     every other source, so the registry, the KG builder, the join resolver and
     the SQL validator all treat ServiceNow exactly like PEP or CLMS.

**The schema is declared here, not inferred from the file.** Types and column
comments are what the KG builder reasons over, and a type inferred per-batch from
a sample would silently change the graph between loads.

Two things this deliberately does NOT do:

  It does not repair shifted rows. A handful of rows in the extract have an
  embedded delimiter that pushes every field one column right, so `Number` holds
  something that is not an incident id. Guessing the intended alignment would
  invent data; they are counted, reported, and dropped.

  It does not zero-fill crew who are absent from the extract. A crew member with
  no row here has an *unknown* feedback profile, not a clean one, and the
  difference decides whether a scorecard is allowed to rank them.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from crew_perf import config

CSV_PATH = config.REPO_ROOT / "servicenow" / "servicenow_response.csv"
SCHEMA_FILE = "servicenow_schema.csv"
SOURCE_NAME = "ServiceNow"

# Seat positions the extract carries a crew column for. L2 and R2 are referenced
# by `Crew Involved` and `customerExperienceChampion` but have NO column of their
# own — so those crew cannot be resolved to an IGA at all. That gap is counted
# and reported rather than being papered over by attributing the report to
# whoever happens to be named.
POSITION_COLUMNS = {
    "L1": "L1", "L2": "L2", "L3": "L3", "L4": "L4",
    "R1": "R1", "R2": "R2", "R3": "R3", "R4": "R4",
    "FO": "FO", "CAPTAIN": "Captain",
}

# The lead's service checklist: (check code, value column, comment column).
SERVICE_CHECKS = [
    ("THANK_YOU_SERVICE", "Thank you Service", "ThankyouServiceComments"),
    ("SECOND_ROUND_FB_SERVICE", "SecondroundofFBService", "SecondroundofFBServiceComments"),
    ("CABIN_SERVICE", "Cabin Service", "Cabin Service Comments"),
    ("SERVICE_COMPLETED", "ServiceCompleted", "ServiceCompletedComments"),
    ("DEVIATION", "Deviation", "DeviationComments"),
    ("COCKPIT_MEAL_REQUESTED", "LeadAskedForCockpitMeal", "LeadAskedForCockpitMealComments"),
    ("COCKPIT_MEAL_ON_GROUND", "CockpitMealOrderGivenOnGround", "CockpitMealOrderMsg"),
]

# A deviation from the service standard is the one check where "Yes" is the bad
# answer. Everything else passes on Yes.
INVERTED_CHECKS = {"DEVIATION"}

# What a category says about the crew on the flight. This classifies the *report*,
# not the crew — it is a reading of what the category names, and nothing here
# decides how much any of it counts towards a score.
#
#   APPRECIATION  the report exists because the crew did something well
#   SERVICE       a customer- or cabin-facing shortfall the crew were part of
#   OPERATIONAL   an upstream failure (catering, engineering, airport, systems)
#                 recorded on the crew's flight but not attributed to them
#   REPORTING     the crew filing a report, which is the process working
POLARITY = {
    "star performer of my flight": "APPRECIATION",
    "customer issues": "SERVICE",
    "cabin events": "SERVICE",
    "services impacted": "SERVICE",
    "security issues": "SERVICE",
    "catering issues": "OPERATIONAL",
    "airport issues": "OPERATIONAL",
    "engineering issues": "OPERATIONAL",
    "mpos serviceability": "OPERATIONAL",
    "delay on flight": "OPERATIONAL",
    "crew feedback": "REPORTING",
}

_INC = re.compile(r"^INC\d+$")
_IGA = re.compile(r"\b(IGA\s*\d+)\b", re.IGNORECASE)
_DURATION = re.compile(
    r"(?:(\d+)\s*Days?)?\s*(?:(\d+)\s*Hours?)?\s*(?:(\d+)\s*Minutes?)?", re.IGNORECASE
)


# ─── Declared schema ────────────────────────────────────────────────────────
# (column, Snowflake type, comment). The comments are the only semantics the KG
# builder gets for this source, so they say what a value means, not what it is
# called.


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
        name="SN_FLIGHT_REPORT",
        grain="one ServiceNow incident raised from the Crew Portal about one flight",
        columns=[
            ("SN_REPORT_ID", "VARCHAR", "ServiceNow incident number, e.g. INC3083734. Primary key."),
            ("FLIGHT_DATE_TIME", "TIMESTAMP_NTZ", "Scheduled departure of the flight the report is about."),
            ("CREATED_AT", "TIMESTAMP_NTZ", "When the report was raised."),
            ("RESOLVED_AT", "TIMESTAMP_NTZ", "When the incident was resolved; null while open."),
            ("REPORT_LAG_HOURS", "FLOAT", "Hours between the flight and the report being raised, "
                                          "as stated by the extract's own 'Delay in days' text."),
            ("RESOLUTION_HOURS", "FLOAT", "Hours between raising and resolving; null while open."),
            ("FLEET_TYPE", "VARCHAR", "Aircraft family: 320, 321 or ATR."),
            ("LEAD_BASE", "VARCHAR", "Home base of the operating lead."),
            ("STATION", "VARCHAR", "Station the report was filed at."),
            ("SECTOR", "VARCHAR", "Route as flown, e.g. DEL-KTM."),
            ("ORIGIN", "VARCHAR", "Departure airport, split from the sector."),
            ("DESTINATION", "VARCHAR", "Arrival airport, split from the sector."),
            ("FLIGHT_NUMBER", "VARCHAR", "Commercial flight number, e.g. 6E-1153."),
            ("FLIGHT_REG", "VARCHAR", "Aircraft registration."),
            ("CATEGORY", "VARCHAR", "Reported category, spelling normalised across the extract."),
            ("CATEGORY_CODE", "VARCHAR", "Slug of CATEGORY; joins M_SN_CATEGORY."),
            ("SUB_CATEGORY", "VARCHAR", "Reported sub-category."),
            ("SUB_CATEGORY_CODE", "VARCHAR", "Slug of CATEGORY + SUB_CATEGORY; joins M_SN_CATEGORY."),
            ("POLARITY", "VARCHAR", "What the category says about the crew: APPRECIATION, "
                                    "SERVICE, OPERATIONAL, REPORTING or UNCLASSIFIED. A reading "
                                    "of the category only — it carries no weight and no severity."),
            ("STATE", "VARCHAR", "ServiceNow workflow state: Resolved, Assigned, In Progress, Canceled."),
            ("IS_RESOLVED", "BOOLEAN", "TRUE when the incident reached a resolved state."),
            ("RESOLUTION_CODE", "VARCHAR", "How it was closed; blank while open."),
            ("RESOLVED_BY", "VARCHAR", "Resolver. 'Crew Portal Integration' means auto-acknowledged "
                                       "rather than worked by a person."),
            ("IS_AUTO_RESOLVED", "BOOLEAN", "TRUE when closed by the Crew Portal integration with no "
                                            "human handling — an acknowledgement, not an investigation."),
            ("HAS_BREACHED", "BOOLEAN", "SLA breach flag as recorded."),
            ("CDLB_ENTRY_MADE", "VARCHAR", "Whether a Cabin Defect Log Book entry was made."),
            ("CABIN_TYPE", "VARCHAR", "Cabin the event occurred in, where recorded."),
            ("DESCRIPTION", "VARCHAR", "Crew's situation/action/outcome narrative. Free text; "
                                       "quoted, never summarised into a score."),
            ("IFS_COMMENT_1", "VARCHAR", "In-flight services follow-up note."),
            ("IFS_COMMENT_2", "VARCHAR", "Second in-flight services follow-up note."),
            ("RESOLUTION_SHORT_DESCRIPTION", "VARCHAR", "Closing note sent back to the reporter."),
            ("CREW_INVOLVED_RAW", "VARCHAR", "Seat positions the report names as involved, verbatim."),
            ("CX_CHAMPION_RAW", "VARCHAR", "Seat positions nominated customer-experience champion, verbatim."),
            ("CREW_POSITIONS_NAMED", "NUMBER", "How many seat positions carry an IGA on this report."),
            ("UNRESOLVED_INVOLVED_POSITIONS", "VARCHAR", "Positions named as involved that the extract "
                                                         "carries no crew column for (L2/R2 have none), "
                                                         "so they cannot be attributed to a person."),
            *_AUDIT_COLUMNS,
        ],
    ),
    TableSpec(
        name="SN_REPORT_CREW",
        grain="one crew member in one seat position on one reported flight",
        columns=[
            ("SN_REPORT_ID", "VARCHAR", "The report this crew member operated. Joins SN_FLIGHT_REPORT."),
            ("IGA", "VARCHAR", "Crew identifier as spelled in every other source."),
            ("POSITION", "VARCHAR", "Seat position: L1-L4, R1-R4, FO or CAPTAIN."),
            ("IS_INVOLVED", "BOOLEAN", "TRUE when the report names this seat position under "
                                       "'Crew Involved'. Everyone else was on the flight but not "
                                       "named — being rostered is not being implicated."),
            ("IS_CX_CHAMPION", "BOOLEAN", "TRUE when this seat was nominated customer-experience champion."),
            ("IS_CABIN_CREW", "BOOLEAN", "FALSE for flight-deck positions (FO, CAPTAIN), which are "
                                         "not assessed by the cabin-crew process."),
            *_AUDIT_COLUMNS,
        ],
    ),
    TableSpec(
        name="SN_SERVICE_CHECK",
        grain="one service-standard check answered on one report",
        columns=[
            ("SN_REPORT_ID", "VARCHAR", "The report this answer belongs to. Joins SN_FLIGHT_REPORT."),
            ("CHECK_CODE", "VARCHAR", "Which check: THANK_YOU_SERVICE, SECOND_ROUND_FB_SERVICE, "
                                      "CABIN_SERVICE, SERVICE_COMPLETED, DEVIATION, "
                                      "COCKPIT_MEAL_REQUESTED, COCKPIT_MEAL_ON_GROUND."),
            ("CHECK_VALUE", "VARCHAR", "The lead's answer, verbatim."),
            ("IS_PASS", "BOOLEAN", "TRUE when the answer meets the standard. DEVIATION is inverted: "
                                   "'No' passes. Null when the answer is neither yes nor no."),
            ("CHECK_COMMENT", "VARCHAR", "Free-text explanation, usually present only when the "
                                         "answer was not the expected one."),
            *_AUDIT_COLUMNS,
        ],
    ),
    TableSpec(
        name="M_SN_CREW",
        grain="one crew member appearing anywhere in the extract",
        columns=[
            ("IGA", "VARCHAR", "Crew identifier. Primary key, and the join to every other source."),
            ("REPORT_COUNT", "NUMBER", "Reported flights this crew member operated."),
            ("INVOLVED_COUNT", "NUMBER", "Reports naming this crew member's position as involved."),
            ("FIRST_SEEN", "TIMESTAMP_NTZ", "Earliest reported flight."),
            ("LAST_SEEN", "TIMESTAMP_NTZ", "Latest reported flight."),
            ("IS_CABIN_CREW", "BOOLEAN", "FALSE when only ever seen in a flight-deck seat."),
            *_AUDIT_COLUMNS,
        ],
    ),
    TableSpec(
        name="M_SN_CATEGORY",
        grain="one category/sub-category pair used in the extract",
        columns=[
            ("SUB_CATEGORY_CODE", "VARCHAR", "Primary key: slug of category + sub-category."),
            ("CATEGORY_CODE", "VARCHAR", "Slug of the parent category."),
            ("CATEGORY", "VARCHAR", "Category label, spelling normalised."),
            ("SUB_CATEGORY", "VARCHAR", "Sub-category label."),
            ("POLARITY", "VARCHAR", "APPRECIATION, SERVICE, OPERATIONAL, REPORTING or UNCLASSIFIED."),
            ("REPORT_COUNT", "NUMBER", "Reports in this extract carrying this pair."),
            *_AUDIT_COLUMNS,
        ],
    ),
]

TABLES_BY_NAME = {t.name: t for t in TABLES}


# ─── Parsing helpers ────────────────────────────────────────────────────────


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _slug(text: str) -> str:
    out = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_").upper()
    return out[:60] or "UNKNOWN"


def parse_crew(cell: str) -> tuple[str | None, str]:
    """`IGA35231 - Aayushi Thapa` -> ('IGA35231', 'Aayushi Thapa').

    The extract writes the identifier with inconsistent internal spacing and
    occasionally a doubled space before the name. Normalising here rather than at
    query time keeps `IGA35231` and `IGA 35231` from becoming two crew members.
    """
    text = _clean(cell)
    if not text:
        return None, ""
    match = _IGA.search(text)
    if not match:
        return None, text
    iga = re.sub(r"\s+", "", match.group(1)).upper()
    name = text[match.end():].lstrip(" -–").strip()
    return iga, re.sub(r"\s+", " ", name)


def parse_timestamp(cell: str) -> datetime | None:
    text = _clean(cell)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_duration_hours(cell: str) -> float | None:
    """`3 Days 6 Hours 53 Minutes` -> 78.88."""
    text = _clean(cell)
    if not text:
        return None
    m = _DURATION.match(text)
    if not m or not any(m.groups()):
        return None
    days, hours, minutes = (int(g) if g else 0 for g in m.groups())
    return round(days * 24 + hours + minutes / 60.0, 3)


def parse_positions(cell: str) -> set[str]:
    """`L1, R1` / `R1,R2` / `LD, L1` -> {'L1','R1'} / {'R1','R2'} / {'L1'}.

    `LD` is the lead, which the extract carries in the L1 column, so the two
    spellings name one seat. `NA` and `N/A` mean nobody was named — distinct from
    an empty cell, which means the question was not answered.
    """
    text = _clean(cell).upper()
    if not text or text in {"NA", "N/A", "NONE", "NOT APPLICABLE"}:
        return set()
    out: set[str] = set()
    for token in re.split(r"[,/;&]+", text):
        token = token.strip()
        if not token:
            continue
        if token == "LD":
            token = "L1"
        if token in POSITION_COLUMNS:
            out.add(token)
    return out


def _yes_no(value: str) -> bool | None:
    text = _clean(value).lower()
    if text.startswith("yes"):
        return True
    if text.startswith("no") and not text.startswith("not "):
        return False
    return None


# ─── Ingest ─────────────────────────────────────────────────────────────────


@dataclass
class IngestResult:
    rows_read: int = 0
    rows_malformed: int = 0
    tables: dict[str, int] = field(default_factory=dict)
    crew: int = 0
    crew_cabin: int = 0
    positions_unresolved: int = 0
    check_fill: dict[str, int] = field(default_factory=dict)
    polarity: dict[str, int] = field(default_factory=dict)
    date_range: tuple[str, str] | None = None
    findings: list[str] = field(default_factory=list)


def read_extract(path: Path | None = None) -> tuple[dict[str, list[dict]], IngestResult]:
    """Parse the CSV into the four grains it contains, plus a data-quality report."""
    path = path or CSV_PATH
    result = IngestResult()
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)

    reports: list[dict] = []
    crew_rows: list[dict] = []
    checks: list[dict] = []
    crew_master: dict[str, dict] = {}
    categories: dict[str, dict] = {}
    seen_ids: set[str] = set()
    unresolved_positions: dict[str, int] = {}
    category_spellings: dict[str, dict[str, int]] = {}
    flight_dates: list[datetime] = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    # Category labels differ only in case between rows ('Customer issues' vs
    # 'Customer Issues'). Collapsing them by case-folded key — and keeping the
    # most frequent spelling as the label — stops one category becoming two.
    for row in rows:
        for col in ("Category", "Sub-category"):
            text = _clean(row.get(col))
            if text:
                bucket = category_spellings.setdefault(text.lower(), {})
                bucket[text] = bucket.get(text, 0) + 1
    canonical = {
        key: max(spellings.items(), key=lambda kv: kv[1])[0]
        for key, spellings in category_spellings.items()
    }

    for row in rows:
        result.rows_read += 1
        report_id = _clean(row.get("Number"))
        if not _INC.match(report_id):
            result.rows_malformed += 1
            continue
        if report_id in seen_ids:
            continue                      # the extract repeats a handful of incidents
        seen_ids.add(report_id)

        category = canonical.get(_clean(row.get("Category")).lower(), _clean(row.get("Category")))
        sub_category = canonical.get(
            _clean(row.get("Sub-category")).lower(), _clean(row.get("Sub-category"))
        )
        category_code = _slug(category) if category else "UNKNOWN"
        sub_code = f"{category_code}__{_slug(sub_category)}" if sub_category else category_code
        polarity = POLARITY.get(category.lower(), "UNCLASSIFIED")
        result.polarity[polarity] = result.polarity.get(polarity, 0) + 1

        created = parse_timestamp(row.get("Created"))
        resolved = parse_timestamp(row.get("Resolved"))
        flight_dt = parse_timestamp(row.get("Flight Date Time"))
        if flight_dt:
            flight_dates.append(flight_dt)
        resolution_hours = (
            round((resolved - created).total_seconds() / 3600.0, 3)
            if created and resolved and resolved >= created else None
        )

        sector = _clean(row.get("Sector"))
        origin, _, destination = sector.partition("-")
        state = _clean(row.get("State"))
        resolved_by = _clean(row.get("Resolved by"))

        involved = parse_positions(row.get("Crew Involved"))
        champions = parse_positions(row.get("customerExperienceChampion"))

        # Which of the named positions the extract can actually resolve to a
        # person. L2 and R2 are named regularly and have no crew column at all.
        named: dict[str, tuple[str, str]] = {}
        for position, column in POSITION_COLUMNS.items():
            iga, name = parse_crew(row.get(column))
            if iga:
                named[position] = (iga, name)
        missing = sorted(p for p in involved if p not in named)
        for position in missing:
            unresolved_positions[position] = unresolved_positions.get(position, 0) + 1
        result.positions_unresolved += len(missing)

        reports.append({
            "SN_REPORT_ID": report_id,
            "FLIGHT_DATE_TIME": flight_dt,
            "CREATED_AT": created,
            "RESOLVED_AT": resolved,
            "REPORT_LAG_HOURS": parse_duration_hours(row.get("Delay in days")),
            "RESOLUTION_HOURS": resolution_hours,
            "FLEET_TYPE": _clean(row.get("Fleet Type")),
            "LEAD_BASE": _clean(row.get("LEAD BASE")),
            "STATION": _clean(row.get("Station")),
            "SECTOR": sector,
            "ORIGIN": origin.strip(),
            "DESTINATION": destination.strip(),
            "FLIGHT_NUMBER": _clean(row.get("Flight Number")),
            "FLIGHT_REG": _clean(row.get("Flight Reg")),
            "CATEGORY": category,
            "CATEGORY_CODE": category_code,
            "SUB_CATEGORY": sub_category,
            "SUB_CATEGORY_CODE": sub_code,
            "POLARITY": polarity,
            "STATE": state,
            "IS_RESOLVED": state.lower() in {"resolved", "closed"},
            "RESOLUTION_CODE": _clean(row.get("Resolution code")),
            "RESOLVED_BY": resolved_by,
            "IS_AUTO_RESOLVED": resolved_by.lower() == "crew portal integration",
            "HAS_BREACHED": _clean(row.get("Has breached")).lower() == "true",
            "CDLB_ENTRY_MADE": _clean(row.get("CDLB Entry Made")),
            "CABIN_TYPE": _clean(row.get("Cabin Type")),
            "DESCRIPTION": _clean(row.get("Description")),
            "IFS_COMMENT_1": _clean(row.get("IFS comment 1")),
            "IFS_COMMENT_2": _clean(row.get("IFS comment 2")),
            "RESOLUTION_SHORT_DESCRIPTION": _clean(row.get("Resolution Short Description")),
            "CREW_INVOLVED_RAW": _clean(row.get("Crew Involved")),
            "CX_CHAMPION_RAW": _clean(row.get("customerExperienceChampion")),
            "CREW_POSITIONS_NAMED": len(named),
            "UNRESOLVED_INVOLVED_POSITIONS": ",".join(missing),
            "P_IS_CURRENT": True,
            "LOAD_DATE": loaded_at,
        })

        entry = categories.setdefault(sub_code, {
            "SUB_CATEGORY_CODE": sub_code, "CATEGORY_CODE": category_code,
            "CATEGORY": category, "SUB_CATEGORY": sub_category,
            "POLARITY": polarity, "REPORT_COUNT": 0,
            "P_IS_CURRENT": True, "LOAD_DATE": loaded_at,
        })
        entry["REPORT_COUNT"] += 1

        # The name is parsed off the cell only to isolate the identifier, and is
        # deliberately not carried into any table: the extract is the one source
        # that arrives with names attached, and loading them would reintroduce
        # locally exactly what the warehouse hashes.
        for position, (iga, _name) in named.items():
            is_cabin = position not in {"FO", "CAPTAIN"}
            crew_rows.append({
                "SN_REPORT_ID": report_id,
                "IGA": iga,
                "POSITION": position,
                "IS_INVOLVED": position in involved,
                "IS_CX_CHAMPION": position in champions,
                "IS_CABIN_CREW": is_cabin,
                "P_IS_CURRENT": True,
                "LOAD_DATE": loaded_at,
            })
            master = crew_master.setdefault(iga, {
                "IGA": iga, "REPORT_COUNT": 0, "INVOLVED_COUNT": 0,
                "FIRST_SEEN": flight_dt, "LAST_SEEN": flight_dt, "IS_CABIN_CREW": False,
                "P_IS_CURRENT": True, "LOAD_DATE": loaded_at,
            })
            master["REPORT_COUNT"] += 1
            master["INVOLVED_COUNT"] += int(position in involved)
            master["IS_CABIN_CREW"] = master["IS_CABIN_CREW"] or is_cabin
            if flight_dt:
                master["FIRST_SEEN"] = min(master["FIRST_SEEN"] or flight_dt, flight_dt)
                master["LAST_SEEN"] = max(master["LAST_SEEN"] or flight_dt, flight_dt)

        for code, value_col, comment_col in SERVICE_CHECKS:
            value = _clean(row.get(value_col))
            comment = _clean(row.get(comment_col))
            if not value and not comment:
                continue
            answer = _yes_no(value)
            is_pass = None if answer is None else (not answer if code in INVERTED_CHECKS else answer)
            checks.append({
                "SN_REPORT_ID": report_id,
                "CHECK_CODE": code,
                "CHECK_VALUE": value,
                "IS_PASS": is_pass,
                "CHECK_COMMENT": comment,
                "P_IS_CURRENT": True,
                "LOAD_DATE": loaded_at,
            })
            result.check_fill[code] = result.check_fill.get(code, 0) + 1

    frames = {
        "SN_FLIGHT_REPORT": reports,
        "SN_REPORT_CREW": crew_rows,
        "SN_SERVICE_CHECK": checks,
        "M_SN_CREW": list(crew_master.values()),
        "M_SN_CATEGORY": list(categories.values()),
    }
    result.tables = {name: len(rows) for name, rows in frames.items()}
    result.crew = len(crew_master)
    result.crew_cabin = sum(1 for c in crew_master.values() if c["IS_CABIN_CREW"])
    if flight_dates:
        result.date_range = (min(flight_dates).date().isoformat(),
                             max(flight_dates).date().isoformat())

    # ── Data-quality findings, recorded rather than silently absorbed ──
    if result.rows_malformed:
        result.findings.append(
            f"{result.rows_malformed} row(s) have no incident number in the Number column — "
            f"an embedded delimiter shifted every field. Dropped rather than realigned, "
            f"because guessing the intended alignment would invent crew attributions."
        )
    if unresolved_positions:
        detail = ", ".join(f"{p} x{n}" for p, n in sorted(unresolved_positions.items(),
                                                          key=lambda kv: -kv[1]))
        result.findings.append(
            f"{result.positions_unresolved} 'crew involved' mentions name a seat the extract "
            f"carries no crew column for ({detail}) — those crew cannot be identified, so "
            f"involvement is under-counted, never mis-attributed."
        )
    empty_checks = [code for code, _, _ in SERVICE_CHECKS if not result.check_fill.get(code)]
    if empty_checks:
        result.findings.append(
            f"service check(s) {', '.join(empty_checks)} are empty on every row in this "
            f"extract — the columns exist but were never filled, so no crew member can be "
            f"scored on them"
        )
    if result.polarity.get("UNCLASSIFIED"):
        result.findings.append(
            f"{result.polarity['UNCLASSIFIED']} report(s) carry a category with no polarity "
            f"reading — they are loaded and queryable, but excluded from polarity-based signals"
        )
    return frames, result


# ─── Schema publication ─────────────────────────────────────────────────────


def write_schema_csv(path: Path | None = None) -> Path:
    """Emit the column-level export the registry reads.

    Every other source arrives as a Snowflake information-schema dump; this makes
    ServiceNow indistinguishable from one, which is what lets the KG builder, the
    join resolver and the validator treat it identically.
    """
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


# ─── Load ───────────────────────────────────────────────────────────────────


def load(path: Path | None = None, executor=None) -> IngestResult:
    """Parse the extract and write its tables into the working database."""
    import pandas as pd

    from crew_perf.data.executor import DuckDBExecutor

    frames, result = read_extract(path)
    own = executor is None
    executor = executor or DuckDBExecutor(read_only=False)
    try:
        for name, rows in frames.items():
            spec = TABLES_BY_NAME[name]
            frame = pd.DataFrame(rows, columns=spec.column_names)
            for column, dtype, _ in spec.columns:
                if dtype.startswith("TIMESTAMP"):
                    frame[column] = pd.to_datetime(frame[column], errors="coerce")
                elif dtype in {"FLOAT", "NUMBER"}:
                    frame[column] = pd.to_numeric(frame[column], errors="coerce")
                elif dtype == "BOOLEAN":
                    frame[column] = frame[column].astype("boolean")
            executor.register_df(name, frame)
    finally:
        if own:
            executor.close()

    write_schema_csv()
    return result
