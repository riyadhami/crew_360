"""
advanced_graph_builder_agent.py — 4-step pipeline for ontology concept-graph extraction.

No agent frameworks. Uses call_llm() from utils/llm.py for all LLM calls.

Pipeline:
  Step 1: Deterministically fetch all tables from the chosen database.
  Step 2: Per-table ReAct agent loop (max 10 steps) → enriched node per table.
  Step 3: Generator → Reviewer loop to assemble the full KG JSON with concept
          normalisation, edge construction, and rejection of useless tables.
  Step 4: Load the KG into Neo4j via Cypher queries.

Outputs:
  - {database}_concept_graph.json   — final KG JSON
  - {database}_concept_graph.md     — Markdown overview
  - {database}_agent_log.txt        — full scratchpad of all ReAct + gen/review rounds
  - {database}_cypher_queries.txt   — all Cypher statements executed against Neo4j

Usage:
    python -m src.agents.advanced_graph_builder_agent --database CLMS
    python -m src.agents.advanced_graph_builder_agent --database CrewPortal
    python -m src.agents.advanced_graph_builder_agent --database all
    python -m src.agents.advanced_graph_builder_agent --database CLMS --skip-neo4j
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── project imports ──────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.utils.llm import get_llm_client, call_llm, parse_llm_json
from src.utils.agent_logger import (
    setup_agent_logger,
    log_entity_generation,
    log_entity_unification,
    log_subgraph_extraction,
)

# Initialize logger
logger = setup_agent_logger("advanced_graph_builder")


# ═══════════════════════════════════════════════════════════════════════════════
#  Database Contexts — Prevents hallucination about database purposes
# ═══════════════════════════════════════════════════════════════════════════════

DATABASE_CONTEXTS = {
    "CLMS": {
        "purpose": "Crew Leave Management System",
        "description": "Manages all aspects of crew leave processes including leave requests, approvals, balances, types, and year configurations. Tracks sick leave (SL), privilege leave (PL), URTI balances, and leave allocations for crew members.",
        "graph_name": "crew_leave_management"
    },
    "PEP": {
        "purpose": "Crew Performance Evaluation and Planning",
        "description": "Tracks crew performance assessments, evaluations, appraisals, training records, competency tracking, and career development planning. Manages performance metrics, goals, and review cycles.",
        "graph_name": "crew_performance_evaluation"
    },
    "CREWPORTAL": {
        "purpose": "Crew Portal and General Information",
        "description": "Central crew member portal providing access to general information, announcements, resources, and self-service functions for crew members.",
        "graph_name": "crew_portal_system"
    },
    "HRDATA": {
        "purpose": "HR Master Data and Recruitment",
        "description": "Contains HR master data including employee personal information, contact details, job applications, internal job postings (IJP), recruitment tracking, and employee preferences.",
        "graph_name": "HR_KnowledgeGraph"
    },
    "IJP": {
        "purpose": "Internal Job Posting Employee Scores",
        "description": "Tracks employee performance scores and metrics for internal job posting eligibility. Includes fleet qualifications, attendance (LWP days), BMI data, appreciation letters, caution letters, coaching sessions, and recognition awards.",
        "graph_name": "IJP_Employee_Scores_Graph"
    },
    "NPS": {
        "purpose": "Passenger Net Promoter Score Survey",
        "description": "Tracks passenger satisfaction survey responses across the journey (booking, pre-travel, check-in, boarding, on-board, arrival), including the overall NPS score and per-stage sub-ratings, plus the operating crew members attributed to each flight for crew-to-NPS correlation.",
        "graph_name": "IndigoNPS_Graph"
    }
}


def get_database_context(database: str) -> str:
    """Return the business context description for a database to prevent hallucination."""
    db_upper = database.upper()
    if db_upper in DATABASE_CONTEXTS:
        ctx = DATABASE_CONTEXTS[db_upper]
        return f"{ctx['purpose']} — {ctx['description']}"
    return "General database schema"


def get_graph_container_name(database: str) -> str:
    """Return the Cosmos DB graph container name for a database."""
    db_upper = database.upper()
    if db_upper in DATABASE_CONTEXTS:
        return DATABASE_CONTEXTS[db_upper]["graph_name"]
    # Fallback: sanitize database name
    return database.lower().replace(" ", "_").replace("-", "_")


def _call_llm_retry(client, prompt: str, temperature: float = 0.9,
                    max_retries: int = 3, base_delay: float = 5.0) -> str:
    """Wrapper around call_llm with exponential-backoff retry for transient errors."""
    for attempt in range(1, max_retries + 1):
        try:
            return call_llm(client, prompt, temperature=temperature)
        except Exception as e:
            err_name = type(e).__name__
            is_transient = any(kw in str(e).lower() for kw in [
                "timeout", "connect", "reset", "502", "503", "504",
                "internal", "upstream", "rate",
            ])
            if not is_transient or attempt == max_retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            print(f"      ⏳ LLM call failed ({err_name}), retrying in {delay:.0f}s "
                   f"(attempt {attempt}/{max_retries})…")
            time.sleep(delay)
    raise RuntimeError("Unreachable")


def _is_content_policy_error(e: Exception) -> bool:
    """True if an LLM call failed because the content filter flagged the prompt.

    These are non-transient (retrying is pointless), so callers should skip the
    offending item and continue rather than crash the whole build.
    """
    msg = str(e).lower()
    return any(kw in msg for kw in [
        "invalid_prompt", "usage policy", "content_filter", "content management policy",
        "responsibleaipolicyviolation", "jailbreak",
    ])


# ═══════════════════════════════════════════════════════════════════════════════
#  1. DBSchemaTool — reads CSV metadata for CrewPortal / CLMS
# ═══════════════════════════════════════════════════════════════════════════════

class DBSchemaTool:
    """Loads database-schema CSVs into memory and exposes query methods."""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        # ── CrewPortal (single CSV) ──
        self.crewportal_tables: dict[str, str] = {}          # table_name → description
        self._load_crewportal()
        # ── CLMS (three CSVs) ──
        self.clms_details: dict[str, str] = {}               # table_name → description
        self.clms_data: dict[str, dict] = {}                  # table_name → {columns, pk, audit, rows}
        self.clms_relationships: dict[str, str] = {}          # table_name → relationship text
        self._load_clms()
        # ── PEP (single schema-definition CSV) ──
        self.pep_tables: dict[str, dict] = {}                 # table_name → {columns, data_types, nullable, description}
        self._load_pep()
        # ── HRData (single schema CSV) ──
        self.hrdata_tables: dict[str, dict] = {}              # table_name → {columns, data_types, description}
        self._load_hrdata()
        # ── IJP Employee Scores (single schema CSV) ──
        self.ijp_tables: dict[str, dict] = {}                 # table_name → {columns, data_types, description}
        self._load_ijp()
        # ── NPS (single schema CSV) ──
        self.nps_tables: dict[str, dict] = {}                 # table_name → {columns, data_types, description}
        self._load_nps()

    # ── loaders ──────────────────────────────────────────────────────────────

    def _read_csv(self, filename: str) -> list[dict]:
        path = self.data_dir / filename
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def _load_crewportal(self):
        for row in self._read_csv("CrewPortal DB details.csv"):
            name = row.get("TABLE_NAME", "").strip()
            desc = row.get("DESCRIPTION", "").strip()
            if name:
                self.crewportal_tables[name] = desc

    def _load_clms(self):
        for row in self._read_csv("CLMS Table Details.csv"):
            name = row.get("TABLE_NAME", "").strip()
            desc = row.get("Description", "").strip()
            if name:
                self.clms_details[name] = desc

        for row in self._read_csv("CLMS Table Data .csv"):
            name = row.get("TABLE_NAME", "").strip()
            if name:
                self.clms_data[name] = {
                    "columns": row.get("COLUMN_LIST", "").strip(),
                    "primary_key": row.get("PRIMARY_KEY_COLUMNS", "").strip(),
                    "audit_columns": row.get("AUDIT_DATE_COLUMNS", "").strip(),
                    "row_count": row.get("Total No. of Rows", "").strip(),
                }

        for row in self._read_csv("CLMS Table RelationShip.csv"):
            name = row.get("TABLE NAME", "").strip()
            rel = row.get("RELATIONSHIP", "").strip()
            if name:
                self.clms_relationships[name] = rel

    def _load_pep(self):
        for row in self._read_csv("PEP_Schema_Defn_2026-04-15.csv"):
            table_name = row.get("TABLE_NAME", "").strip()
            col_name = row.get("COLUMN_NAME", "").strip()
            if not table_name or not col_name:
                continue
            if table_name not in self.pep_tables:
                self.pep_tables[table_name] = {
                    "columns": [],
                    "data_types": {},
                    "nullable": {},
                    "description": "",
                }
            entry = self.pep_tables[table_name]
            entry["columns"].append(col_name)
            entry["data_types"][col_name] = row.get("DATA_TYPE", "").strip()
            entry["nullable"][col_name] = row.get("IS_NULLABLE", "").strip()

    def _load_hrdata(self):
        try:
            for row in self._read_csv("HRData_Schema.csv"):
                table_name = row.get("TABLE_NAME", "").strip()
                col_name = row.get("COLUMN_NAME", "").strip()
                if not table_name or not col_name:
                    continue
                if table_name not in self.hrdata_tables:
                    self.hrdata_tables[table_name] = {
                        "columns": [],
                        "data_types": {},
                        "descriptions": {},
                        "table_description": "Indigo HR employee data with job applications and preferences",
                    }
                entry = self.hrdata_tables[table_name]
                entry["columns"].append(col_name)
                entry["data_types"][col_name] = row.get("DATA_TYPE", "").strip()
                entry["descriptions"][col_name] = row.get("DESCRIPTION", "").strip()
        except FileNotFoundError:
            pass  # HRData_Schema.csv not present, skip

    def _load_ijp(self):
        try:
            for row in self._read_csv("IJP_Employee_scores_Schema.csv"):
                table_name = row.get("TABLE_NAME", "").strip()
                col_name = row.get("COLUMN_NAME", "").strip()
                if not table_name or not col_name:
                    continue
                if table_name not in self.ijp_tables:
                    self.ijp_tables[table_name] = {
                        "columns": [],
                        "data_types": {},
                        "descriptions": {},
                        "table_description": "Employee performance scores and metrics for internal job posting eligibility",
                    }
                entry = self.ijp_tables[table_name]
                entry["columns"].append(col_name)
                entry["data_types"][col_name] = row.get("DATA_TYPE", "").strip()
                entry["descriptions"][col_name] = row.get("DESCRIPTION", "").strip()
        except FileNotFoundError:
            pass  # IJP_Employee_scores_Schema.csv not present, skip

    def _load_nps(self):
        try:
            for row in self._read_csv("IndigoNPS_Summary_Schema.csv"):
                table_name = row.get("TABLE_NAME", "").strip()
                col_name = row.get("COLUMN_NAME", "").strip()
                if not table_name or not col_name:
                    continue
                if table_name not in self.nps_tables:
                    self.nps_tables[table_name] = {
                        "columns": [],
                        "data_types": {},
                        "descriptions": {},
                        "table_description": "Passenger NPS satisfaction survey responses with per-journey-stage ratings and operating crew attribution",
                    }
                entry = self.nps_tables[table_name]
                entry["columns"].append(col_name)
                entry["data_types"][col_name] = row.get("DATA_TYPE", "").strip()
                entry["descriptions"][col_name] = row.get("DESCRIPTION", "").strip()
        except FileNotFoundError:
            pass  # IndigoNPS_Summary_Schema.csv not present, skip

    # ── deterministic table fetchers (Step 1) ────────────────────────────

    def get_all_tables(self, database: str) -> list[dict]:
        """Return a list of table-info dicts for every table in the database.

        For CLMS each dict has:
            name, description, columns, primary_key, audit_columns, row_count, relationships
        For CrewPortal each dict has:
            name, description  (columns / relationships are not in the CSV)
        """
        if database.lower() == "clms":
            tables = []
            for name, desc in self.clms_details.items():
                data = self.clms_data.get(name, {})
                rel = self.clms_relationships.get(name, "")
                tables.append({
                    "name": name,
                    "description": desc,
                    "columns": data.get("columns", ""),
                    "primary_key": data.get("primary_key", ""),
                    "audit_columns": data.get("audit_columns", ""),
                    "row_count": data.get("row_count", ""),
                    "relationships": rel,
                })
            return tables

        if database.lower() == "crewportal":
            return [
                {"name": name, "description": desc}
                for name, desc in self.crewportal_tables.items()
            ]

        if database.lower() == "pep":
            tables = []
            for name, info in self.pep_tables.items():
                col_details = ", ".join(
                    f"{c} ({info['data_types'].get(c, '')})" for c in info["columns"]
                )
                tables.append({
                    "name": name,
                    "description": info.get("description", ""),
                    "columns": ", ".join(info["columns"]),
                    "column_details": col_details,
                })
            return tables

        if database.lower() == "hrdata":
            tables = []
            for name, info in self.hrdata_tables.items():
                col_details = ", ".join(
                    f"{c} ({info['data_types'].get(c, '')}): {info['descriptions'].get(c, '')}"
                    for c in info["columns"]
                )
                tables.append({
                    "name": name,
                    "description": info.get("table_description", ""),
                    "columns": ", ".join(info["columns"]),
                    "column_details": col_details,
                })
            return tables

        if database.lower() == "ijp":
            tables = []
            for name, info in self.ijp_tables.items():
                col_details = ", ".join(
                    f"{c} ({info['data_types'].get(c, '')}): {info['descriptions'].get(c, '')}"
                    for c in info["columns"]
                )
                tables.append({
                    "name": name,
                    "description": info.get("table_description", ""),
                    "columns": ", ".join(info["columns"]),
                    "column_details": col_details,
                })
            return tables

        if database.lower() == "nps":
            tables = []
            for name, info in self.nps_tables.items():
                col_details = ", ".join(
                    f"{c} ({info['data_types'].get(c, '')}): {info['descriptions'].get(c, '')}"
                    for c in info["columns"]
                )
                tables.append({
                    "name": name,
                    "description": info.get("table_description", ""),
                    "columns": ", ".join(info["columns"]),
                    "column_details": col_details,
                })
            return tables

        raise ValueError(f"Unknown database: {database}")

    def get_table_info_text(self, database: str, table_name: str) -> str:
        """Return a human-readable text block with all available info for a table."""
        if database.lower() == "crewportal":
            desc = self.crewportal_tables.get(table_name, "No description.")
            return (
                f"Table: {table_name} (CrewPortal)\n"
                f"Description: {desc}\n"
                f"Note: CrewPortal CSV only provides table names and descriptions "
                f"(no column details or relationships)."
            )
        if database.lower() == "clms":
            desc = self.clms_details.get(table_name, "No description available.")
            data = self.clms_data.get(table_name, {})
            rel = self.clms_relationships.get(table_name, "No relationship data.")
            return "\n".join([
                f"Table: {table_name} (CLMS)",
                f"Description: {desc}",
                f"Columns: {data.get('columns', 'N/A')}",
                f"Primary Key: {data.get('primary_key', 'N/A')}",
                f"Audit Columns: {data.get('audit_columns', 'N/A')}",
                f"Row Count: {data.get('row_count', 'N/A')}",
                f"Relationships: {rel}",
            ])
        if database.lower() == "pep":
            info = self.pep_tables.get(table_name)
            if not info:
                return f"Table {table_name} not found in PEP schema."
            col_lines = []
            for c in info["columns"]:
                dtype = info["data_types"].get(c, "")
                nullable = info["nullable"].get(c, "")
                col_lines.append(f"  - {c} ({dtype}, nullable={nullable})")
            return "\n".join([
                f"Table: {table_name} (PEP)",
                f"Description: {info.get('description') or 'No description available (schema-only CSV).'}",
                f"Columns ({len(info['columns'])}):",
                *col_lines,
                f"Note: PEP schema CSV provides column definitions only "
                f"(no primary keys, relationships, or row counts).",
            ])
        if database.lower() == "hrdata":
            info = self.hrdata_tables.get(table_name)
            if not info:
                return f"Table {table_name} not found in HRData schema."
            col_lines = []
            for c in info["columns"]:
                dtype = info["data_types"].get(c, "")
                desc = info["descriptions"].get(c, "")
                col_lines.append(f"  - {c} ({dtype}): {desc}")
            return "\n".join([
                f"Table: {table_name} (HRData)",
                f"Description: {info.get('table_description', 'No description available.')}",
                f"Columns ({len(info['columns'])}):",
                *col_lines,
                f"Note: HRData schema represents employee information, job applications, "
                f"and location preferences from Excel source.",
            ])
        if database.lower() == "ijp":
            info = self.ijp_tables.get(table_name)
            if not info:
                return f"Table {table_name} not found in IJP schema."
            col_lines = []
            for c in info["columns"]:
                dtype = info["data_types"].get(c, "")
                desc = info["descriptions"].get(c, "")
                col_lines.append(f"  - {c} ({dtype}): {desc}")
            return "\n".join([
                f"Table: {table_name} (IJP)",
                f"Description: {info.get('table_description', 'No description available.')}",
                f"Columns ({len(info['columns'])}):",
                *col_lines,
                f"Note: IJP Employee Scores tracks performance metrics for internal job posting eligibility, "
                f"including fleet qualifications, attendance, health metrics, and recognition.",
            ])
        if database.lower() == "nps":
            info = self.nps_tables.get(table_name)
            if not info:
                return f"Table {table_name} not found in NPS schema."
            col_lines = []
            for c in info["columns"]:
                dtype = info["data_types"].get(c, "")
                desc = info["descriptions"].get(c, "")
                col_lines.append(f"  - {c} ({dtype}): {desc}")
            return "\n".join([
                f"Table: {table_name} (NPS)",
                f"Description: {info.get('table_description', 'No description available.')}",
                f"Columns ({len(info['columns'])}):",
                *col_lines,
                f"Note: NPS tracks passenger satisfaction survey responses per journey stage "
                f"(booking, pre-travel, check-in, boarding, on-board, arrival), plus the operating "
                f"crew (by IGA) attributed to each flight. IGA is the join key back to "
                f"Indigo_HR_Raw_Data.IGA and CLMS_Raw_Data.IGA.",
            ])
        return f"Unknown database: {database}"


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Per-table ReAct agent (Step 2)
# ═══════════════════════════════════════════════════════════════════════════════

TABLE_REACT_SYSTEM_PROMPT = """\
You are a database-schema analyst. You are analysing a SINGLE table from the \
**{database}** database to produce an enriched knowledge-graph node.

## Database Context (DO NOT HALLUCINATE)
{database_context}

This database's purpose and scope are clearly defined above. Do NOT invent or \
assume business purposes outside this context. All concepts, relationships, and \
descriptions MUST align with the stated purpose.

## Table under analysis
{table_info}

## All tables in this database (for relationship context)
{all_table_names}

## Available tools
{tool_descriptions}

## Your task
Analyse this table and either **submit an enriched node** or **reject it**.

### When to REJECT a table
If the table is NOT useful for a business knowledge graph — e.g. pure internal \
config, staging/temp tables, raw ESB/API request-response logs with no business \
semantics, scheduler job-tracking tables, or generic system logging tables — call \
`reject_table` with a clear reason. These tables add noise, not value.

### When to SUBMIT an enriched node
For tables that carry meaningful business semantics, call `submit_enriched_node` \
with a JSON object containing:

```
{{
  "id": "snake_case_id",
  "label": "Human Readable Label",
  "type": "{database}Entity",
  "description": "1-2 sentences of business context about what this table represents.",
  "original_table_name": "exact DB table name",
  "database": "{database}",
  "columns": ["col1", "col2", ...],
  "primary_keys": ["pk_col"],
  "concepts": ["Concept A", "Concept B"],
  "edges": [
    {{
      "target_table": "OtherTableName",
      "relationship": "verb_phrase",
      "description": "1-2 sentences"
    }}
  ],
  "notes": "any extra observations (audit columns, row count, etc.)"
}}
```

### Rules for the enriched node
- **label**: A human-readable name — NOT the raw table name. For example:
  - `M_CLMS_Crew` → "Crew Master"
  - `T_CLMS_LeaveReqMaster` → "Leave Request"
  - `M_Aircraft` → "Aircraft"
  - `T_FlightIssueGeneralInfo` → "Flight Issue Report"
  Prefer short names (1-3 words). Never use the raw `M_`, `T_`, `L_` prefixed name.
- **id**: snake_case of the label (e.g. "crew_master", "leave_request").
- **type**: always "{database}Entity".
- **concepts**: list of business concepts this table relates to. Examples:
  "Crew Identity", "Leave Management", "Flight Operations", "Compliance & Safety",
  "Transfer & Relocation", "Role & Access Control", "Notification", etc.
  Be specific but not too granular — think domain-level concepts.
- **column_groups** (IMPORTANT NEW FIELD): Map business concepts to specific column groups.
  This allows creating concept nodes from related columns, not just entire tables.
  Format: {{
    "Concept Name": [
      {{"column": "ColumnName", "data_type": "type", "description": "what it represents"}},
      ...
    ]
  }}
  **Example for IJP_Employee_scores table:**
  {{
    "Employee Performance": [
      {{"column": "AppreciationLetters", "data_type": "integer", "description": "Count of appreciation letters"}},
      {{"column": "CautionLetters", "data_type": "string", "description": "Caution letters or counselling forms"}},
      {{"column": "Recognition", "data_type": "string", "description": "Employee recognition awards"}}
    ],
    "Employee Health": [
      {{"column": "BMI", "data_type": "float", "description": "Body Mass Index"}}
    ]
  }}
  Group columns that belong to the same business concept. This enables fine-grained
  concept nodes that represent specific data domains within a table.
- **edges**: relationships to OTHER tables in the database. Only include edges you \
  can justify from the schema (FK relationships, shared columns, or semantic links \
  from the description). Each edge needs a `target_table` (exact table name from the \
  list above), a `relationship` verb phrase, and a `description`.
- **columns**: list of key column names (from the schema). Omit if not available.
- **primary_keys**: PK columns if known. Omit or use empty list if not available.
- **notes**: extra context — e.g. row count, audit columns, special observations.

## Output format
Always respond using EXACTLY this format:

Thought: <your reasoning>
Action: <tool_name>
Action Input: <JSON arguments>

After receiving an Observation, continue with the next Thought/Action cycle.
"""


def _parse_react_output(text: str) -> tuple[str, str, str]:
    """Extract Thought, Action, and Action Input from LLM output."""
    thought = ""
    action = ""
    action_input = ""

    thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|\Z)", text, re.DOTALL)
    if thought_match:
        thought = thought_match.group(1).strip()

    action_match = re.search(r"Action:\s*(\S+)", text)
    if action_match:
        action = action_match.group(1).strip()

    input_match = re.search(r"Action Input:\s*(.*)", text, re.DOTALL)
    if input_match:
        action_input = input_match.group(1).strip()

    return thought, action, action_input


def _build_table_tools(
    table_info_text: str,
    all_tables: list[dict],
    schema_tool: DBSchemaTool,
    database: str,
) -> dict:
    """Build the per-table tool registry."""
    all_names = [t["name"] for t in all_tables]
    tools: dict[str, dict] = {}

    tools["get_table_info"] = {
        "fn": lambda **_kw: table_info_text,
        "description": "Get the full schema info for the table currently being analysed.",
        "params": "No parameters.",
    }
    tools["get_all_table_names"] = {
        "fn": lambda **_kw: "Tables in this database:\n" + "\n".join(f"  - {n}" for n in all_names),
        "description": "List all table names in this database (for relationship context).",
        "params": "No parameters.",
    }
    tools["get_other_table_info"] = {
        "fn": lambda table_name, **_kw: schema_tool.get_table_info_text(database, table_name),
        "description": "Get schema info for another table (to understand a relationship).",
        "params": '{"table_name": "exact table name"}',
    }
    tools["submit_enriched_node"] = {
        "fn": lambda **_kw: "SUBMIT",  # sentinel — handled in the loop
        "description": "Submit the final enriched node for this table. This ends the loop.",
        "params": '{"node_json": "<JSON object as described above>"}',
    }
    tools["reject_table"] = {
        "fn": lambda **_kw: "REJECT",  # sentinel — handled in the loop
        "description": "Reject this table as not useful for the KG. Provide a reason.",
        "params": '{"reason": "why this table should be excluded"}',
    }

    return tools


def _format_tool_descriptions(tools: dict) -> str:
    lines = []
    for name, info in tools.items():
        lines.append(f"- **{name}**: {info['description']}")
        lines.append(f"  Parameters: {info['params']}")
    return "\n".join(lines)


FORCE_NODE_PROMPT = """\
You were analysing the table below but ran out of steps. Based on your reasoning \
so far, produce the enriched node JSON (or reject it) NOW.

## Table info
{table_info}

## Your reasoning so far
{scratchpad}

## Instructions
If the table is useful for a business knowledge graph, return a JSON object with:
  id, label, type, description, original_table_name, database, columns, primary_keys,
  concepts, edges, notes.
If the table should be rejected, return: {{"rejected": true, "reason": "..."}}

The `type` MUST be "{database}Entity".
The `label` must be human-readable — NOT the raw table name.

Return ONLY the JSON. No markdown fences, no explanation.
"""


def run_table_react(
    table_info: dict,
    all_tables: list[dict],
    schema_tool: DBSchemaTool,
    database: str,
    llm_client,
    max_steps: int = 10,
) -> tuple[str, dict | None, list[str]]:
    """Run the per-table ReAct loop.

    Returns:
        (outcome, result, scratchpad)
        outcome: "enriched" | "rejected" | "force_enriched" | "force_rejected"
        result: enriched node dict, or {"table": ..., "reason": ...} for rejected
        scratchpad: list of log lines
    """
    table_name = table_info["name"]
    table_info_text = schema_tool.get_table_info_text(database, table_name)
    all_table_names = "\n".join(f"  - {t['name']}" for t in all_tables)

    tools = _build_table_tools(table_info_text, all_tables, schema_tool, database)
    tool_desc = _format_tool_descriptions(tools)

    system_prompt = TABLE_REACT_SYSTEM_PROMPT.format(
        database=database,
        database_context=get_database_context(database),
        table_info=table_info_text,
        all_table_names=all_table_names,
        tool_descriptions=tool_desc,
    )

    scratchpad: list[str] = []
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Analyse the table **{table_name}** and either submit an enriched node "
            f"or reject it. Start by examining the table info."
        )},
    ]

    for step in range(1, max_steps + 1):
        print(f"      ── step {step}/{max_steps} ──")
        prompt_text = "\n\n".join(
            f"{'[System]' if m['role']=='system' else '[User]' if m['role']=='user' else '[Assistant]'}\n{m['content']}"
            for m in messages
        )
        try:
            response = _call_llm_retry(llm_client, prompt_text, temperature=0.9)
        except Exception as e:
            if _is_content_policy_error(e):
                print(f"      🚫 Content filter flagged {table_name} — auto-rejecting and continuing.")
                scratchpad.append(f"  CONTENT-FILTER on {table_name} — auto-rejected: {str(e)[:150]}")
                return "force_rejected", {"table": table_name, "reason": f"Content filter flagged prompt: {str(e)[:150]}"}, scratchpad
            raise
        thought, action, action_input = _parse_react_output(response)

        if thought:
            print(f"      💭 {thought[:150]}{'…' if len(thought)>150 else ''}")
        if action:
            print(f"      🔧 {action}")

        scratchpad.append(f"  Step {step}: Thought: {thought}")
        scratchpad.append(f"  Step {step}: Action: {action}")
        scratchpad.append(f"  Step {step}: Input: {action_input[:300]}")

        if not action:
            obs = "No action detected. Use the exact format: Thought: ... Action: ... Action Input: ..."
            scratchpad.append(f"  Step {step}: Observation: {obs}")
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Observation: {obs}"})
            continue

        # ── Terminal actions ─────────────────────────────────────────────
        if action == "submit_enriched_node":
            parsed = parse_llm_json(action_input) if action_input else None
            if isinstance(parsed, dict):
                parsed.setdefault("type", f"{database}Entity")
                parsed.setdefault("database", database)
                parsed.setdefault("original_table_name", table_name)
                scratchpad.append(f"  SUBMITTED enriched node for {table_name}")
                return "enriched", parsed, scratchpad
            obs = "ERROR: Could not parse node JSON. Please provide valid JSON."
            scratchpad.append(f"  Step {step}: Observation: {obs}")
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Observation: {obs}"})
            continue

        if action == "reject_table":
            parsed = parse_llm_json(action_input) if action_input else None
            reason = ""
            if isinstance(parsed, dict):
                reason = parsed.get("reason", action_input)
            else:
                reason = action_input or "No reason provided."
            scratchpad.append(f"  REJECTED {table_name}: {reason}")
            return "rejected", {"table": table_name, "reason": reason}, scratchpad

        # ── Non-terminal tool execution ──────────────────────────────────
        if action not in tools:
            obs = f"Unknown tool '{action}'. Available: {', '.join(tools.keys())}"
        else:
            try:
                kwargs = {}
                if action_input:
                    parsed = parse_llm_json(action_input)
                    if isinstance(parsed, dict):
                        kwargs = parsed
                    elif action == "get_other_table_info":
                        kwargs = {"table_name": action_input.strip().strip('"')}
                obs = tools[action]["fn"](**kwargs)
            except Exception as e:
                obs = f"Tool error: {type(e).__name__}: {e}"

        obs_preview = str(obs)[:400]
        print(f"      👁️  {obs_preview[:120]}{'…' if len(str(obs))>120 else ''}")
        scratchpad.append(f"  Step {step}: Observation: {obs_preview}")
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Observation: {obs}"})

    # ── Max steps reached — force generate ───────────────────────────────
    print(f"      ⚠️  Max steps reached for {table_name} — forcing answer…")
    scratchpad.append(f"  MAX STEPS — forcing answer for {table_name}")

    force_prompt = FORCE_NODE_PROMPT.format(
        table_info=table_info_text,
        scratchpad="\n".join(scratchpad[-20:]),  # last 20 lines of context
        database=database,
    )
    try:
        force_response = _call_llm_retry(llm_client, force_prompt, temperature=0.9)
    except Exception as e:
        if _is_content_policy_error(e):
            print(f"      🚫 Content filter flagged {table_name} (force step) — auto-rejecting.")
            scratchpad.append(f"  CONTENT-FILTER on {table_name} (force) — auto-rejected.")
            return "force_rejected", {"table": table_name, "reason": f"Content filter flagged prompt: {str(e)[:150]}"}, scratchpad
        raise
    parsed = parse_llm_json(force_response)

    if isinstance(parsed, dict):
        if parsed.get("rejected"):
            reason = parsed.get("reason", "Forced rejection — max steps reached.")
            scratchpad.append(f"  FORCE-REJECTED {table_name}: {reason}")
            return "force_rejected", {"table": table_name, "reason": reason}, scratchpad
        else:
            parsed.setdefault("type", f"{database}Entity")
            parsed.setdefault("database", database)
            parsed.setdefault("original_table_name", table_name)
            scratchpad.append(f"  FORCE-SUBMITTED enriched node for {table_name}")
            return "force_enriched", parsed, scratchpad

    # Absolute fallback — create a minimal node
    scratchpad.append(f"  FALLBACK minimal node for {table_name}")
    minimal = {
        "id": table_name.lower().replace(" ", "_"),
        "label": table_name,
        "type": f"{database}Entity",
        "description": table_info.get("description", ""),
        "original_table_name": table_name,
        "database": database,
        "columns": [c.strip() for c in table_info.get("columns", "").split(",") if c.strip()],
        "primary_keys": [p.strip() for p in table_info.get("primary_key", "").split(",") if p.strip()],
        "concepts": [],
        "edges": [],
        "notes": "Auto-generated minimal node (agent could not produce a full enriched node).",
    }
    return "force_enriched", minimal, scratchpad


# ═══════════════════════════════════════════════════════════════════════════════
#  Step 2 orchestrator — process all tables
# ═══════════════════════════════════════════════════════════════════════════════

def process_all_tables(
    database: str,
    schema_tool: DBSchemaTool,
    llm_client,
    max_steps_per_table: int = 10,
    table_filter: list[str] | None = None,
) -> tuple[dict, list[dict], list[str]]:
    """Run per-table ReAct for every table.

    Args:
        database: Database name (CLMS, HRData, etc.)
        schema_tool: Schema tool for table metadata
        llm_client: LLM client for enrichment
        max_steps_per_table: Max ReAct steps per table
        table_filter: Optional list of table names to process (if None, process all)

    Returns:
        enriched_nodes: dict[table_name → enriched node dict]
        rejected_tables: list of {table, reason}
        full_scratchpad: combined log lines
    """
    all_tables = schema_tool.get_all_tables(database)
    
    # Apply table filter if provided
    if table_filter:
        filtered_tables = [t for t in all_tables if t["name"] in table_filter]
        logger.info(f"Table filter applied: {len(filtered_tables)}/{len(all_tables)} tables selected")
        logger.info(f"Filtered table names: {[t['name'] for t in filtered_tables]}")
        all_tables = filtered_tables
    
    total = len(all_tables)

    print(f"\n  Step 1 complete: {total} tables loaded from {database}.")
    print(f"  Starting Step 2: per-table ReAct enrichment…\n")

    enriched_nodes: dict[str, dict] = {}
    rejected_tables: list[dict] = []
    full_scratchpad: list[str] = []

    for idx, table_info in enumerate(all_tables, 1):
        table_name = table_info["name"]
        print(f"  ┌─ Table {idx}/{total}: {table_name}")

        full_scratchpad.append(f"\n{'═'*50}")
        full_scratchpad.append(f"TABLE {idx}/{total}: {table_name}")
        full_scratchpad.append(f"{'═'*50}")

        outcome, result, scratchpad = run_table_react(
            table_info, all_tables, schema_tool, database,
            llm_client, max_steps=max_steps_per_table,
        )

        full_scratchpad.extend(scratchpad)

        if outcome in ("enriched", "force_enriched"):
            enriched_nodes[table_name] = result
            label = result.get("label", table_name)
            n_concepts = len(result.get("concepts", []))
            n_edges = len(result.get("edges", []))
            tag = "✅" if outcome == "enriched" else "⚡"
            print(f"  └─ {tag} → \"{label}\"  ({n_concepts} concepts, {n_edges} edges)")
            
            # Log entity generation
            log_entity_generation(logger, "Enriched Table Node", result)
        else:
            rejected_tables.append(result)
            reason = result.get("reason", "?")[:80]
            print(f"  └─ ❌ Rejected: {reason}")
            logger.info(f"Table rejected: {table_name} - {reason}")

    print(f"\n  Step 2 complete: {len(enriched_nodes)} enriched nodes, "
          f"{len(rejected_tables)} rejected tables.")
    
    logger.info(f"Step 2 Summary: {len(enriched_nodes)} nodes enriched, {len(rejected_tables)} tables rejected")

    return enriched_nodes, rejected_tables, full_scratchpad


# ═══════════════════════════════════════════════════════════════════════════════
#  3. KG Construction — Generator → Reviewer loop (Step 3)
# ═══════════════════════════════════════════════════════════════════════════════

GENERATOR_PROMPT = """\
You are a knowledge-graph architect. Below are enriched node definitions for every \
table in the **{database}** database, plus a list of tables that were rejected.

## Database Context (DO NOT HALLUCINATE)
{database_context}

This database's purpose and scope are clearly defined above. All concepts, relationships, \
and descriptions MUST align with the stated purpose. Do NOT invent concepts outside this scope.

Your job is to produce a **final Knowledge Graph JSON** with nodes, edges, and a \
rejected list.

## Enriched nodes (from per-table analysis)
{enriched_nodes_json}

## Already rejected tables
{rejected_json}

## Instructions

### Node construction
There are TWO kinds of nodes in the KG:

1. **Table nodes** — one per enriched table. These represent database entities.
   - `type`: "{database}Entity"
   - `id`: snake_case (derived from the human-readable label, NOT the raw table name)
   - `label`: the human-readable label from the enriched node
   - `description`: from the enriched node
   - `database`: "{database}"
   - `metadata`: {{
       "original_table_name": "exact DB table name",
       "columns": [...],
       "primary_keys": [...],
       "concepts": [...],
       "notes": "..."
     }}

2. **Concept nodes** — normalized business concepts that bridge multiple tables OR represent
   a group of related columns within a single table.
   - `type`: "concept"
   - `id`: snake_case (e.g. "leave_management", "crew_identity", "employee_performance")
   - `label`: human-readable concept name (e.g. "Leave Management", "Crew Identity", "Employee Performance")
   - `description`: what this concept represents across the domain. **MUST explicitly mention the key columns/data fields that this concept covers** (e.g., "Tracks employee performance indicators including appreciation letters, caution letters, coaching sessions, and recognition awards")
   - `database`: "{database}"
   - `metadata`: {{
       "source_tables": ["TableA", "TableB", ...],
       "key_columns": [  // **REQUIRED**: List specific columns this concept represents
         {{"table": "TableA", "column": "col1", "data_type": "type", "description": "..."}},
         {{"table": "TableB", "column": "col2", "data_type": "type", "description": "..."}}
       ],
       "concept_source": "column_group" or "cross_table",  // NEW: Indicates if concept comes from column grouping or spans tables
       "notes": "..."
     }}

### Concept normalisation (CRITICAL)
Many enriched nodes list similar concepts. You MUST normalise:
- Merge near-duplicate concepts: "Crew", "Crew Member", "Crew Identity" → one concept "Crew Identity".
- Merge related sub-concepts into broader domains: "Leave Request", "Leave Approval", \
  "Leave Balance", "Leave Allocation" → one concept "Leave Management".
- **Create concept nodes from column groups**: When enriched nodes have `column_groups`,
  create dedicated concept nodes for each grouped concept. These represent fine-grained
  business domains within tables. For example:
  - "Employee Performance" concept from columns: AppreciationLetters, CautionLetters, 
    Recognition, CoachingSessions in IJP_Employee_scores
  - "Employee Health" concept from columns: BMI, health-related fields
- Aim for ~10-30 concept nodes total. Each concept should be a meaningful business \
  domain, representing either:
  1. Cross-table concepts (traditional approach)
  2. Column-group concepts (new: groups of related columns within a table)
- Track which tables contribute to each concept in `metadata.source_tables`.
- **For column-group concepts**: Populate `metadata.key_columns` with the full column details
  from the `column_groups` field, and set `metadata.concept_source` to "column_group".
- **For cross-table concepts**: Set `metadata.concept_source` to "cross_table".

### Edge construction
Edges connect nodes. Types:

1. **Table → Table** edges: from the `edges` field of enriched nodes. Use the \
   `relationship` and `description` from the enriched node. Map `target_table` to the \
   correct table node `id`.
2. **Table → Concept** edges:
   a. For cross-table concepts: table nodes connect to concepts via `RELATES_TO`.
   b. **For column-group concepts**: table nodes connect to their column-group concepts
      via `CONTAINS_CONCEPT` (since these concepts are derived from columns within the table).
3. **Concept → Concept** edges: if two concepts are semantically related (e.g. \
   "Crew Identity" → "Leave Management" because crew members take leave), add an edge.
   Also consider relationships between column-group concepts and cross-table concepts.

Every edge must have: `source`, `target`, `relationship`, `description`, `source_tables`.

### Rejected tables
Include ALL rejected tables (from the pre-rejected list AND any additional tables you \
determine are not useful). Each entry: {{"table": "TableName", "reason": "..."}}.

### Additional rejection in this step
Review the enriched nodes one more time. If any table that was initially enriched is \
actually not useful (e.g. pure mapping/junction table with no business value, or a \
duplicate of another table), move it to rejected and remove its node. Be aggressive \
about keeping the graph clean.

{feedback_section}

## Output format
Return ONLY a valid JSON object with this structure:
{{
  "nodes": [...],
  "edges": [...],
  "rejected": [...]
}}

No markdown fences, no explanation outside the JSON. Strictly valid JSON — no comments, \
no trailing commas, no placeholders.
"""

REVIEWER_PROMPT = """\
You are a knowledge-graph quality reviewer. Below is a KG JSON produced for the \
**{database}** database. Review it and either PASS or provide specific feedback.

## KG JSON
{kg_json}

## Review checklist
1. **Concept normalisation**: Are there duplicate or near-duplicate concept nodes?  \
   (e.g. "Crew" and "Crew Identity" both existing as separate nodes). Flag any that \
   should be merged.
2. **Edge validity**: Do all edges reference valid node IDs? Are there missing edges \
   between obviously related tables?
3. **Node labels**: Are all labels human-readable? None should be raw table names like \
   "M_CLMS_Crew" or "T_FlightIssueDetails".
4. **Rejected tables**: Are there tables in the nodes list that should be rejected \
   (pure config, logs, staging)? Are there useful tables in the rejected list that \
   should be nodes?
5. **Type correctness**: Table nodes should have type "{database}Entity", concept \
   nodes should have type "concept".
6. **Completeness**: Are there enriched tables missing from the nodes list?
7. **Concept coverage**: Does every table node connect to at least one concept node?

## Output
If the KG passes all checks, return:
{{"verdict": "PASS"}}

If improvements are needed, return:
{{"verdict": "FAIL", "feedback": "Specific list of issues to fix..."}}

Return ONLY the JSON. No markdown fences.
"""


def build_kg_json(
    enriched_nodes: dict[str, dict],
    rejected_tables: list[dict],
    database: str,
    llm_client,
    max_rounds: int = 3,
) -> tuple[dict, list[str]]:
    """Generator → Reviewer loop to build the final KG JSON.

    Returns (kg_dict, scratchpad_lines).
    """
    scratchpad: list[str] = []
    enriched_json = json.dumps(enriched_nodes, indent=2, ensure_ascii=False)
    rejected_json = json.dumps(rejected_tables, indent=2, ensure_ascii=False)

    kg_dict = None
    feedback_text = ""

    for rnd in range(1, max_rounds + 1):
        print(f"\n  ── Generator round {rnd}/{max_rounds} ──")
        scratchpad.append(f"\n--- GENERATOR ROUND {rnd} ---")

        feedback_section = ""
        if feedback_text:
            feedback_section = (
                f"## Reviewer feedback from the previous round\n"
                f"Address ALL of these issues:\n{feedback_text}"
            )

        gen_prompt = GENERATOR_PROMPT.format(
            database=database,
            database_context=get_database_context(database),
            enriched_nodes_json=enriched_json,
            rejected_json=rejected_json,
            feedback_section=feedback_section,
        )

        try:
            gen_response = _call_llm_retry(llm_client, gen_prompt, temperature=0.9)
        except Exception as e:
            if _is_content_policy_error(e):
                print("    🚫 Content filter flagged generator prompt — building minimal KG from enriched nodes.")
                scratchpad.append("  CONTENT-FILTER on generator — falling back to tables-only KG.")
                kg_dict = {
                    "database": database,
                    "nodes": list(enriched_nodes.values()),
                    "edges": [],
                    "rejected": rejected_tables,
                }
                break
            raise
        kg_dict = parse_llm_json(gen_response)

        if not isinstance(kg_dict, dict) or "nodes" not in kg_dict or "edges" not in kg_dict:
            scratchpad.append("  Generator produced invalid JSON — retrying…")
            print("    ⚠️  Invalid JSON from generator — retrying…")
            feedback_text = (
                "Your previous output was not valid JSON with 'nodes' and 'edges'. "
                "Please produce strictly valid JSON."
            )
            continue

        n_nodes = len(kg_dict.get("nodes", []))
        n_edges = len(kg_dict.get("edges", []))
        n_rej = len(kg_dict.get("rejected", []))
        scratchpad.append(f"  Generated: {n_nodes} nodes, {n_edges} edges, {n_rej} rejected")
        print(f"    Generated: {n_nodes} nodes, {n_edges} edges, {n_rej} rejected")
        
        logger.info(f"KG Generation Round {rnd}: {n_nodes} nodes, {n_edges} edges, {n_rej} rejected")
        
        # Log concept nodes for entity unification tracking
        concept_nodes = [n for n in kg_dict.get("nodes", []) if n.get("type") == "concept"]
        for concept in concept_nodes[:5]:  # Log first 5 concepts
            log_entity_generation(logger, "Concept Node", concept)

        # ── Reviewer ─────────────────────────────────────────────────────
        print(f"  ── Reviewer round {rnd}/{max_rounds} ──")
        scratchpad.append(f"\n--- REVIEWER ROUND {rnd} ---")

        review_prompt = REVIEWER_PROMPT.format(
            database=database,
            kg_json=json.dumps(kg_dict, indent=2, ensure_ascii=False),
        )
        try:
            review_response = _call_llm_retry(llm_client, review_prompt, temperature=0.9)
        except Exception as e:
            if _is_content_policy_error(e):
                print("    🚫 Content filter flagged reviewer prompt — accepting current graph.")
                scratchpad.append("  CONTENT-FILTER on reviewer — accepting current generation.")
                break
            raise
        review_result = parse_llm_json(review_response)
        scratchpad.append(f"  Reviewer response: {str(review_result)[:300]}")

        if isinstance(review_result, dict) and review_result.get("verdict") == "PASS":
            scratchpad.append("  Reviewer: PASS ✅")
            print("    Reviewer: PASS ✅")
            logger.info(f"Reviewer approved KG in round {rnd}")
            break
        elif isinstance(review_result, dict) and review_result.get("verdict") == "FAIL":
            feedback_text = review_result.get("feedback", "")
            scratchpad.append(f"  Reviewer: FAIL — {feedback_text[:300]}")
            print(f"    Reviewer: FAIL — {feedback_text[:150]}…")
            logger.warning(f"Reviewer rejected KG in round {rnd}: {feedback_text[:200]}")
        else:
            scratchpad.append("  Reviewer produced unexpected output — accepting current graph.")
            print("    Reviewer produced unexpected output — accepting current graph.")
            break
    else:
        scratchpad.append("  Max review rounds reached — accepting last generation.")
        print("    Max review rounds reached — accepting last generation.")
        logger.warning("Max review rounds reached - accepting current generation")
    
    # Log final entity unification summary
    if kg_dict:
        nodes = kg_dict.get("nodes", [])
        concept_nodes = [n for n in nodes if n.get("type") == "concept"]
        table_nodes = [n for n in nodes if n.get("type", "").endswith("Entity")]
        
        unification_summary = {
            "concepts": [c.get("label", c.get("id", "")) for c in concept_nodes],
            "total_concepts": len(concept_nodes),
            "total_tables": len(table_nodes),
            "merged": [],
        }
        
        # Track which concepts merge multiple source tables
        for concept in concept_nodes:
            src_tables = concept.get("metadata", {}).get("source_tables", [])
            if len(src_tables) > 1:
                unification_summary["merged"].append(
                    f"{concept.get('label', '?')} <- {', '.join(src_tables[:3])}"
                )
        
        log_entity_unification(
            logger, 
            "Concept Normalization Complete",
            {"concepts": enriched_nodes, "nodes": []},
            {"concepts": concept_nodes, "nodes": nodes, "merged": unification_summary["merged"]}
        )

    return kg_dict, scratchpad


# ═══════════════════════════════════════════════════════════════════════════════
#  4. Output helpers — JSON, Markdown, agent log
# ═══════════════════════════════════════════════════════════════════════════════

def save_graph_outputs(graph: dict, database: str, output_dir: str) -> str:
    """Write the final KG JSON and Markdown overview. Returns summary string."""
    # Stamp metadata
    graph.setdefault("database", database)
    graph.setdefault("metadata", {})
    meta = graph["metadata"]
    meta["total_nodes"] = len(graph.get("nodes", []))
    meta["total_edges"] = len(graph.get("edges", []))
    meta["total_rejected"] = len(graph.get("rejected", []))
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Count source tables (only real tables that have original_table_name)
    all_tables: set[str] = {
        n["metadata"]["original_table_name"]
        for n in graph.get("nodes", [])
        if n.get("metadata", {}).get("original_table_name")
    }
    meta["source_tables_count"] = len(all_tables)

    os.makedirs(output_dir, exist_ok=True)
    safe_db = database.replace(" ", "_")

    # ── JSON ─────────────────────────────────────────────────────────────
    json_path = os.path.join(output_dir, f"{safe_db}_concept_graph.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)

    # ── Markdown ─────────────────────────────────────────────────────────
    md_path = os.path.join(output_dir, f"{safe_db}_concept_graph.md")
    md = _render_markdown(graph)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    return (
        f"Graph saved!\n"
        f"  JSON → {json_path}\n"
        f"  Markdown → {md_path}\n"
        f"  Nodes: {meta['total_nodes']}, Edges: {meta['total_edges']}, "
        f"Rejected: {meta['total_rejected']}"
    )


def _render_markdown(graph: dict) -> str:
    """Generate a Markdown overview from the concept graph JSON."""
    meta = graph.get("metadata", {})
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    rejected = graph.get("rejected", [])
    db = graph.get("database", "Unknown")

    type_counts: dict[str, int] = {}
    for n in nodes:
        t = n.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    def _src_tables(node_or_edge: dict) -> list[str]:
        md = node_or_edge.get("metadata", {})
        tables = md.get("source_tables", [])
        orig = md.get("original_table_name")
        if orig and orig not in tables:
            tables = [orig] + tables
        if not tables:
            tables = node_or_edge.get("source_tables", [])
        return tables

    shared = [n for n in nodes if len(_src_tables(n)) > 1]

    # Separate table nodes and concept nodes
    table_nodes = [n for n in nodes if n.get("type", "").endswith("Entity")]
    concept_nodes = [n for n in nodes if n.get("type") == "concept"]

    lines: list[str] = []
    lines.append(f"# Concept Knowledge Graph — {db}\n")
    lines.append(f"*Generated: {meta.get('generated_at', 'N/A')}*\n")

    lines.append("## Summary\n")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Nodes | {meta.get('total_nodes', len(nodes))} |")
    lines.append(f"| — Table Nodes | {len(table_nodes)} |")
    lines.append(f"| — Concept Nodes | {len(concept_nodes)} |")
    lines.append(f"| Total Edges | {meta.get('total_edges', len(edges))} |")
    lines.append(f"| Source Tables Referenced | {meta.get('source_tables_count', '—')} |")
    lines.append(f"| Shared Concepts (multi-table) | {len(shared)} |")
    lines.append(f"| Rejected / Unidentified Tables | {len(rejected)} |")
    lines.append("")

    lines.append("### Node Type Breakdown\n")
    lines.append("| Type | Count |")
    lines.append("|------|-------|")
    for t, c in sorted(type_counts.items()):
        lines.append(f"| {t} | {c} |")
    lines.append("")

    # ── Table Nodes ──────────────────────────────────────────────────────
    if table_nodes:
        lines.append("## Table Nodes\n")
        lines.append("| ID | Label | Type | Original Table | Description | Key Columns |")
        lines.append("|----|-------|------|----------------|-------------|-------------|")
        for n in table_nodes:
            md = n.get("metadata", {})
            orig = md.get("original_table_name", "")
            desc = (n.get("description", "") or "")[:120]
            cols = ", ".join(md.get("columns", []))[:80] if md.get("columns") else "—"
            lines.append(
                f"| {n.get('id','')} | {n.get('label','')} | {n.get('type','')} "
                f"| {orig} | {desc} | {cols} |"
            )
        lines.append("")

    # ── Concept Nodes ────────────────────────────────────────────────────
    if concept_nodes:
        lines.append("## Concept Nodes\n")
        lines.append("| ID | Label | Description | Source Tables |")
        lines.append("|----|-------|-------------|---------------|")
        for n in concept_nodes:
            desc = (n.get("description", "") or "")[:120]
            md = n.get("metadata", {})
            src = ", ".join(md.get("source_tables", []))
            lines.append(f"| {n.get('id','')} | {n.get('label','')} | {desc} | {src} |")
        lines.append("")

    # ── Edges ────────────────────────────────────────────────────────────
    lines.append("## Edges\n")
    lines.append("| Source | Target | Relationship | Description |")
    lines.append("|--------|--------|--------------|-------------|")
    for e in edges:
        desc = (e.get("description", "") or "")[:120]
        lines.append(
            f"| {e.get('source','')} | {e.get('target','')} "
            f"| {e.get('relationship','')} | {desc} |"
        )
    lines.append("")

    # ── Shared concepts ──────────────────────────────────────────────────
    if shared:
        lines.append("## Shared Concepts (appear in multiple tables)\n")
        for n in shared:
            tables = ", ".join(_src_tables(n))
            lines.append(f"- **{n.get('label', n.get('id',''))}** — {tables}")
        lines.append("")

    # ── Rejected ─────────────────────────────────────────────────────────
    if rejected:
        lines.append("## Rejected / Excluded Tables\n")
        lines.append("| Table | Reason |")
        lines.append("|-------|--------|")
        for r in rejected:
            lines.append(f"| {r.get('table', '')} | {r.get('reason', '')} |")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Neo4j loader (Step 4)
# ═══════════════════════════════════════════════════════════════════════════════

def load_to_neo4j(graph: dict, database: str, output_dir: str) -> str:
    """Load the KG into Neo4j. Clears existing data first.

    Returns a summary string.
    """
    from src.utils.neo4j_helpers import get_neo4j_driver, run_cypher_write

    driver = get_neo4j_driver()
    cypher_log: list[str] = []

    def _run(query: str):
        cypher_log.append(query)
        run_cypher_write(driver, query)

    # ── Clear only nodes/edges for THIS database ──────────────────────
    print(f"    Clearing existing {database} nodes and their edges…")
    logger.info(f"Clearing existing {database} nodes from Neo4j")
    _run(f'MATCH (t:Table:{database}) DETACH DELETE t')
    _run(f'MATCH (c:Concept) WHERE c.database = "{database}" DETACH DELETE c')

    # ── Indexes (no global uniqueness — multiple databases may share names) ──
    print("    Creating indexes…")
    _run("CREATE INDEX IF NOT EXISTS FOR (t:Table) ON (t.name)")
    _run("CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.name)")

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Build a lookup: node id → node (for edge resolution)
    node_by_id: dict[str, dict] = {n["id"]: n for n in nodes if "id" in n}

    # ── Create table nodes ───────────────────────────────────────────────
    table_nodes = [n for n in nodes if n.get("type", "").endswith("Entity")]
    concept_nodes = [n for n in nodes if n.get("type") == "concept"]

    print(f"    Creating {len(table_nodes)} table nodes…")
    logger.info(f"Loading {len(table_nodes)} table nodes into Neo4j")
    for n in table_nodes:
        md = n.get("metadata", {})
        orig_name = md.get("original_table_name", n.get("original_table_name", n["id"]))
        desc = (n.get("description", "") or "").replace('"', '\\"').replace("'", "\\'")
        label_text = (n.get("label", "") or "").replace('"', '\\"')
        cols = ", ".join(f'"{c}"' for c in md.get("columns", []))
        pks = ", ".join(f'"{p}"' for p in md.get("primary_keys", []))
        concepts = ", ".join(f'"{c}"' for c in md.get("concepts", n.get("concepts", [])))
        notes = (md.get("notes", n.get("notes", "")) or "").replace('"', '\\"').replace("'", "\\'")

        # Dual label: :Table:{Database}
        stmt = (
            f'MERGE (t:Table:{database} {{name: "{orig_name}"}}) '
            f'SET t.label = "{label_text}", '
            f't.description = "{desc}", '
            f't.database = "{database}", '
            f't.nodeId = "{n["id"]}", '
            f't.columns = [{cols}], '
            f't.primaryKeys = [{pks}], '
            f't.concepts = [{concepts}], '
            f't.notes = "{notes}"'
        )
        _run(stmt)

    # ── Create concept nodes ─────────────────────────────────────────────
    print(f"    Creating {len(concept_nodes)} concept nodes…")
    logger.info(f"Loading {len(concept_nodes)} concept nodes into Neo4j")
    for n in concept_nodes:
        md = n.get("metadata", {})
        desc = (n.get("description", "") or "").replace('"', '\\"').replace("'", "\\'")
        label_text = (n.get("label", "") or "").replace('"', '\\"')
        src_tables = ", ".join(f'"{t}"' for t in md.get("source_tables", []))
        key_columns = ", ".join(f'"{c}"' for c in md.get("key_columns", []))
        notes = (md.get("notes", "") or "").replace('"', '\\"').replace("'", "\\'")

        stmt = (
            f'MERGE (c:Concept {{name: "{label_text}", database: "{database}"}}) '
            f'SET c.description = "{desc}", '
            f'c.nodeId = "{n["id"]}", '
            f'c.sourceTables = [{src_tables}], '
            f'c.keyColumns = [{key_columns}], '
            f'c.notes = "{notes}"'
        )
        _run(stmt)

    # ── Create edges ─────────────────────────────────────────────────────
    print(f"    Creating {len(edges)} edges…")
    logger.info(f"Creating {len(edges)} edges in Neo4j")
    for e in edges:
        src_id = e.get("source", "")
        tgt_id = e.get("target", "")
        rel = e.get("relationship", "RELATED_TO").upper().replace(" ", "_").replace("-", "_")
        desc = (e.get("description", "") or "").replace('"', '\\"').replace("'", "\\'")

        # Resolve source/target to Neo4j match patterns
        src_node = node_by_id.get(src_id)
        tgt_node = node_by_id.get(tgt_id)
        if not src_node or not tgt_node:
            cypher_log.append(f"-- SKIPPED edge {src_id} → {tgt_id}: node not found")
            continue

        # Determine match pattern based on node type
        def _match_clause(node: dict, var: str) -> str:
            if node.get("type") == "concept":
                lbl = (node.get("label", "") or "").replace('"', '\\"')
                return f'({var}:Concept {{name: "{lbl}", database: "{database}"}})'
            else:
                md = node.get("metadata", {})
                orig = md.get("original_table_name", node.get("original_table_name", node["id"]))
                return f'({var}:Table {{name: "{orig}"}})'

        src_match = _match_clause(src_node, "a")
        tgt_match = _match_clause(tgt_node, "b")

        # Sanitise relationship label for Cypher
        rel_clean = re.sub(r'[^A-Z0-9_]', '_', rel)
        if not rel_clean:
            rel_clean = "RELATED_TO"

        stmt = (
            f'MATCH {src_match}, {tgt_match} '
            f'MERGE (a)-[:{rel_clean} {{description: "{desc}"}}]->(b)'
        )
        _run(stmt)

    # ── Create RELATES_TO edges from concept source_tables metadata ──────
    # The LLM sometimes omits explicit edges between tables and concepts
    # even though the concept's source_tables list references those tables.
    # Derive these edges deterministically from the metadata.
    table_id_to_name: dict[str, str] = {}
    for n in table_nodes:
        md = n.get("metadata", {})
        orig = md.get("original_table_name", n["id"])
        table_id_to_name[n["id"]] = orig

    derived_count = 0
    for n in concept_nodes:
        md = n.get("metadata", {})
        concept_label = (n.get("label", "") or "").replace('"', '\\"')
        for src_tbl_id in md.get("source_tables", []):
            orig_name = table_id_to_name.get(src_tbl_id)
            if not orig_name:
                continue
            stmt = (
                f'MATCH (t:Table {{name: "{orig_name}"}}), '
                f'(c:Concept {{name: "{concept_label}", database: "{database}"}}) '
                f'MERGE (t)-[:RELATES_TO]->(c)'
            )
            _run(stmt)
            derived_count += 1

    logger.info(f"Created {derived_count} derived RELATES_TO edges")

    driver.close()

    # ── Save Cypher log ──────────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    safe_db = database.replace(" ", "_")
    cypher_path = os.path.join(output_dir, f"{safe_db}_cypher_queries.txt")
    with open(cypher_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(cypher_log))
    
    logger.info(f"Neo4j load complete: {len(table_nodes)} tables, {len(concept_nodes)} concepts, {len(edges)} edges")

    return (
        f"Neo4j loaded!\n"
        f"  Table nodes: {len(table_nodes)}\n"
        f"  Concept nodes: {len(concept_nodes)}\n"
        f"  Edges: {len(edges)}\n"
        f"  Cypher log → {cypher_path}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  5b. Cosmos DB loader (Step 4 — alternative backend)
# ═══════════════════════════════════════════════════════════════════════════════

def load_to_cosmos(graph: dict, database: str, output_dir: str) -> str:
    """Load the KG into Cosmos DB (Gremlin API). Uses a dedicated graph container per schema.

    Returns a summary string.  Mirrors ``load_to_neo4j`` but emits Gremlin
    queries against the Azure Cosmos DB Gremlin endpoint.
    
    Each database gets its own graph container (e.g., crew_leave_management, crew_performance_evaluation).
    """
    from src.utils.cosmos_helpers import (
        get_cosmos_client, run_gremlin, close_cosmos_client,
        escape_gremlin, make_vertex_id, serialize_list,
    )

    # Get the dedicated graph container name for this database
    graph_container = get_graph_container_name(database)
    print(f"    Using graph container: {graph_container}")
    
    client = get_cosmos_client(graph_container=graph_container)
    gremlin_log: list[str] = []

    def _run(query: str, ignore_conflict: bool = False):
        gremlin_log.append(query)
        run_gremlin(client, query, ignore_conflict=ignore_conflict)

    # ── Clear existing data for this database only ───
    if graph_container == "Unified_Knowledge_graph":
        print(f"    Clearing existing {database} vertices in unified graph…")
        _run(f"g.V().has('database', '{database}').drop()")
    else:
        print(f"    Clearing all data in {graph_container} graph container…")
        _run(f"g.V().drop()")

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    node_by_id: dict[str, dict] = {n["id"]: n for n in nodes if "id" in n}

    table_nodes = [n for n in nodes if n.get("type", "").endswith("Entity")]
    concept_nodes = [n for n in nodes if n.get("type") == "concept"]

    # ── Mapping: JSON node id → Cosmos vertex id ─────────────────────
    cosmos_id_map: dict[str, str] = {}

    # ── Create table vertices ────────────────────────────────────────
    print(f"    Creating {len(table_nodes)} table vertices…")
    for n in table_nodes:
        md = n.get("metadata", {})
        orig_name = md.get("original_table_name", n.get("original_table_name", n["id"]))
        vid = make_vertex_id(database, "table", orig_name)
        cosmos_id_map[n["id"]] = vid

        desc = escape_gremlin(n.get("description", "") or "")
        label_text = escape_gremlin(n.get("label", "") or "")
        cols = serialize_list(md.get("columns", []))
        pks = serialize_list(md.get("primary_keys", []))
        concepts_list = serialize_list(md.get("concepts", n.get("concepts", [])))
        notes = escape_gremlin((md.get("notes", n.get("notes", "")) or ""))

        stmt = (
            f"g.addV('Table')"
            f".property('id', '{escape_gremlin(vid)}')"
            f".property('database', '{escape_gremlin(database)}')"
            f".property('name', '{escape_gremlin(orig_name)}')"
            f".property('displayName', '{escape_gremlin(label_text)}')"
            f".property('description', '{escape_gremlin(desc)}')"
            f".property('nodeId', '{escape_gremlin(n['id'])}')"
            f".property('columns', '{escape_gremlin(cols)}')"
            f".property('primaryKeys', '{escape_gremlin(pks)}')"
            f".property('concepts', '{escape_gremlin(concepts_list)}')"
            f".property('notes', '{escape_gremlin(notes)}')"
        )
        _run(stmt)

    # ── Create concept vertices ──────────────────────────────────────
    print(f"    Creating {len(concept_nodes)} concept vertices…")
    for n in concept_nodes:
        md = n.get("metadata", {})
        label_text = n.get("label", "") or ""
        vid = make_vertex_id(database, "concept", label_text.lower().replace(" ", "_"))
        cosmos_id_map[n["id"]] = vid

        desc = escape_gremlin(n.get("description", "") or "")
        src_tables = serialize_list(md.get("source_tables", []))
        key_columns = serialize_list(md.get("key_columns", []))
        notes = escape_gremlin((md.get("notes", "") or ""))

        stmt = (
            f"g.addV('Concept')"
            f".property('id', '{escape_gremlin(vid)}')"
            f".property('database', '{escape_gremlin(database)}')"
            f".property('name', '{escape_gremlin(label_text)}')"
            f".property('displayName', '{escape_gremlin(label_text)}')"
            f".property('description', '{escape_gremlin(desc)}')"
            f".property('nodeId', '{escape_gremlin(n['id'])}')"
            f".property('sourceTables', '{escape_gremlin(src_tables)}')"
            f".property('keyColumns', '{escape_gremlin(key_columns)}')"
            f".property('notes', '{escape_gremlin(notes)}')"
        )
        _run(stmt, ignore_conflict=True)

    # ── Create edges ─────────────────────────────────────────────────
    print(f"    Creating {len(edges)} edges…")
    for e in edges:
        src_id = e.get("source", "")
        tgt_id = e.get("target", "")
        rel = e.get("relationship", "RELATED_TO").upper().replace(" ", "_").replace("-", "_")
        desc = escape_gremlin(e.get("description", "") or "")

        src_cosmos_id = cosmos_id_map.get(src_id)
        tgt_cosmos_id = cosmos_id_map.get(tgt_id)
        if not src_cosmos_id or not tgt_cosmos_id:
            gremlin_log.append(f"// SKIPPED edge {src_id} -> {tgt_id}: vertex not found")
            continue

        rel_clean = re.sub(r'[^A-Za-z0-9_]', '_', rel)
        if not rel_clean:
            rel_clean = "RELATED_TO"

        stmt = (
            f"g.V('{escape_gremlin(src_cosmos_id)}')"
            f".addE('{escape_gremlin(rel_clean)}')"
            f".to(g.V('{escape_gremlin(tgt_cosmos_id)}'))"
            f".property('description', '{escape_gremlin(desc)}')"
        )
        _run(stmt, ignore_conflict=True)

    # ── Derived RELATES_TO from concept source_tables ────────────────
    table_id_to_cosmos_id: dict[str, str] = {}
    for n in table_nodes:
        table_id_to_cosmos_id[n["id"]] = cosmos_id_map.get(n["id"], "")

    derived_count = 0
    for n in concept_nodes:
        md = n.get("metadata", {})
        concept_cosmos_id = cosmos_id_map.get(n["id"], "")
        if not concept_cosmos_id:
            continue
        for src_tbl_id in md.get("source_tables", []):
            tbl_cosmos_id = table_id_to_cosmos_id.get(src_tbl_id)
            if not tbl_cosmos_id:
                continue
            stmt = (
                f"g.V('{escape_gremlin(tbl_cosmos_id)}')"
                f".addE('RELATES_TO')"
                f".to(g.V('{escape_gremlin(concept_cosmos_id)}'))"
            )
            _run(stmt, ignore_conflict=True)
            derived_count += 1

    print(f"    Created {derived_count} derived RELATES_TO edges from concept source_tables.")

    close_cosmos_client(client)

    # ── Save Gremlin log ─────────────────────────────────────────────
    os.makedirs(output_dir, exist_ok=True)
    safe_db = database.replace(" ", "_")
    gremlin_path = os.path.join(output_dir, f"{safe_db}_gremlin_queries.txt")
    with open(gremlin_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(gremlin_log))

    return (
        f"Cosmos DB loaded!\n"
        f"  Table vertices: {len(table_nodes)}\n"
        f"  Concept vertices: {len(concept_nodes)}\n"
        f"  Edges: {len(edges)}\n"
        f"  Derived RELATES_TO: {derived_count}\n"
        f"  Gremlin log → {gremlin_path}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  6. Main orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    database: str,
    data_dir: str,
    output_dir: str,
    skip_neo4j: bool = False,
    user_query: str | None = None,
    graph_backend: str = "cosmos",
    table_filter: list[str] | None = None,
):
    """Run the full 4-step pipeline for a single database.
    
    Args:
        database: Database name (CLMS, HRData, etc.)
        data_dir: Directory with schema CSV files
        output_dir: Output directory for results
        skip_neo4j: Skip loading to graph database
        user_query: Optional user query for context
        graph_backend: Graph backend (neo4j or cosmos)
        table_filter: Optional list of table names to process (if None, process all)
    """
    print(f"\n{'═'*60}")
    print(f"  Advanced Graph Builder — {database}")
    if table_filter:
        print(f"  Table Filter: {len(table_filter)} tables selected")
    print(f"{'═'*60}")

    schema_tool = DBSchemaTool(data_dir)
    llm_client = get_llm_client()

    # ── Step 1 + 2: Deterministic fetch + per-table ReAct ────────────────
    enriched_nodes, rejected_tables, step2_scratchpad = process_all_tables(
        database, schema_tool, llm_client, max_steps_per_table=10,
        table_filter=table_filter,
    )

    if not enriched_nodes:
        print("  ⚠️  No enriched nodes produced — nothing to build a graph from.")
        return

    # ── Step 3: Generator → Reviewer loop ────────────────────────────────
    print(f"\n  Starting Step 3: KG construction (generator → reviewer)…")
    kg_dict, step3_scratchpad = build_kg_json(
        enriched_nodes, rejected_tables, database, llm_client, max_rounds=3,
    )

    if not kg_dict:
        print("  ⚠️  KG construction failed — no valid graph produced.")
        return

    # ── Save outputs ─────────────────────────────────────────────────────
    save_result = save_graph_outputs(kg_dict, database, output_dir)
    print(f"\n  {save_result}")

    # ── Save agent log ───────────────────────────────────────────────────
    full_log = step2_scratchpad + ["\n" + "═" * 50, "STEP 3: KG CONSTRUCTION", "═" * 50] + step3_scratchpad
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, f"{database}_agent_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(full_log))
    print(f"  Agent log → {log_path}")

    # ── Step 4: Graph DB loading ──────────────────────────────────────────
    if skip_neo4j:
        print("\n  Skipping graph DB loading (--skip-neo4j).")
    elif graph_backend == "cosmos":
        print(f"\n  Starting Step 4: Loading into Cosmos DB (Gremlin)…")
        try:
            cosmos_result = load_to_cosmos(kg_dict, database, output_dir)
            print(f"  {cosmos_result}")
        except Exception as e:
            print(f"\n  ❌ Cosmos DB loading failed!")
            print(f"  Error: {type(e).__name__}: {e}")
            print(f"\n  Full stack trace:")
            import traceback
            traceback.print_exc()
            print("\n  (Run with --skip-neo4j to skip this step.)")
            sys.exit(1)
    else:
        print(f"\n  Starting Step 4: Loading into Neo4j…")
        try:
            neo4j_result = load_to_neo4j(kg_dict, database, output_dir)
            print(f"  {neo4j_result}")
        except Exception as e:
            print(f"\n  ❌ Neo4j loading failed!")
            print(f"  Error: {type(e).__name__}: {e}")
            print(f"\n  Full stack trace:")
            import traceback
            traceback.print_exc()
            print("\n  (Run with --skip-neo4j to skip this step.)")
            sys.exit(1)

    print(f"\n{'═'*60}")
    print(f"  Pipeline complete for {database}!")
    print(f"{'═'*60}")

    # ── Sample queries ───────────────────────────────────────────────────
    if not skip_neo4j and graph_backend == "neo4j":
        _print_sample_queries(database)
    elif not skip_neo4j and graph_backend == "cosmos":
        _print_sample_gremlin_queries(database)


def _print_sample_queries(database: str):
    """Print sample Cypher queries the user can run in Neo4j Browser."""
    db = database
    print(f"""
{'─'*60}
  Sample Cypher queries for {db}
  Open Neo4j Browser → http://localhost:7474/browser/
{'─'*60}

  1. View the entire graph:
     MATCH (n) RETURN n

  2. All {db} table nodes with labels:
     MATCH (t:Table:{db}) RETURN t.name AS table_name, t.label AS label, t.description AS description ORDER BY t.label

  3. Concept nodes and which tables relate to them:
     MATCH (t:Table:{db})-[:RELATES_TO]->(c:Concept) RETURN c.name AS concept, collect(t.label) AS tables, count(t) AS table_count ORDER BY table_count DESC

  4. All edges between table nodes (FK relationships):
     MATCH (a:Table:{db})-[r]->(b:Table:{db}) RETURN a.label AS from_table, type(r) AS relationship, b.label AS to_table

  5. Most connected tables (hub tables):
     MATCH (t:Table:{db})-[r]-() RETURN t.label AS table, t.name AS raw_name, count(r) AS connections ORDER BY connections DESC LIMIT 10

  6. Concept-to-concept relationships:
     MATCH (c1:Concept)-[r]->(c2:Concept) RETURN c1.name AS from_concept, type(r) AS relationship, c2.name AS to_concept

  7. Explore a specific concept's neighborhood:
     MATCH (c:Concept)<-[:RELATES_TO]-(t:Table:{db}) RETURN c, t

  8. Full graph with relationships (for visualization):
     MATCH p=()-[]-() RETURN p LIMIT 300
""")


def _print_sample_gremlin_queries(database: str):
    """Print sample Gremlin queries the user can run in Azure Portal Data Explorer."""
    db = database
    graph_name = get_graph_container_name(database)
    print(f"""
{'─'*60}
  Sample Gremlin queries for {db}
  Graph Container: {graph_name}
  Open Azure Portal → Cosmos DB → Data Explorer → {graph_name}
{'─'*60}

  1. Count all vertices:
     g.V().count()

  2. All table vertices with labels:
     g.V().hasLabel('Table').valueMap('name', 'displayName', 'description')

  3. Concept vertices:
     g.V().hasLabel('Concept').valueMap('name', 'description', 'keyColumns')

  4. Edges from a specific table (outgoing):
     g.V().hasLabel('Table').has('name', '<TABLE_NAME>').outE().inV().valueMap('name', 'displayName')

  5. Tables that RELATES_TO a concept:
     g.V().hasLabel('Concept').inE('RELATES_TO').outV().valueMap('name', 'displayName')

  6. Count vertices by label:
     g.V().groupCount().by(label)

  7. All edges:
     g.E().valueMap(true)

  8. Full graph overview (limit 100):
     g.V().limit(100).path()
""")


def clear_entire_neo4j_graph():
    """Clear all vertices and edges from Neo4j."""
    from src.utils.neo4j_helpers import get_neo4j_driver, run_cypher_write, close_neo4j_driver
    
    print("    Clearing entire Neo4j graph...")
    logger.info("Clearing entire Neo4j graph")
    driver = get_neo4j_driver()
    try:
        run_cypher_write(driver, "MATCH (n) DETACH DELETE n")
        print("    ✓ Neo4j graph cleared")
        logger.info("Neo4j graph cleared successfully")
    finally:
        close_neo4j_driver(driver)


def clear_entire_cosmos_graph():
    """Clear all vertices and edges from Cosmos DB."""
    from src.utils.cosmos_helpers import get_cosmos_client, run_gremlin, close_cosmos_client
    
    print("    Clearing entire Cosmos DB graph...")
    logger.info("Clearing entire Cosmos DB graph")
    client = get_cosmos_client()
    try:
        run_gremlin(client, "g.V().drop()")
        print("    ✓ Cosmos DB graph cleared")
        logger.info("Cosmos DB graph cleared successfully")
    finally:
        close_cosmos_client(client)


def load_table_filter(filter_file: str) -> list[str] | None:
    """Load table names from a filter file.
    
    Args:
        filter_file: Path to file containing table names (one per line)
        
    Returns:
        List of table names, or None if file doesn't exist
    """
    filter_path = Path(filter_file)
    if not filter_path.exists():
        return None
    
    table_names = []
    with open(filter_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if line and not line.startswith("#") and not line.startswith("-"):
                table_names.append(line)
    
    return table_names if table_names else None


def main():
    parser = argparse.ArgumentParser(
        description="Advanced graph builder: 4-step pipeline for ontology KG extraction."
    )
    parser.add_argument(
        "--database",
        choices=["CrewPortal", "CLMS", "PEP", "HRData", "IJP", "NPS", "all"],
        default="all",
        help="Which database to process (default: all)",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to the folder containing schema CSVs (default: auto-detect)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory for JSON, Markdown, and log files (default: output)",
    )
    parser.add_argument(
        "--table-filter",
        default=None,
        metavar="FILE",
        help="Path to file containing table names to process (one per line, filters tables within selected database)",
    )
    parser.add_argument(
        "--skip-neo4j",
        action="store_true",
        help="Skip loading the graph into any graph database",
    )
    parser.add_argument(
        "--graph-backend",
        choices=["neo4j", "cosmos"],
        default="cosmos",
        help="Which graph database backend to load into (default: cosmos)",
    )
    parser.add_argument(
        "--load-only",
        default=None,
        metavar="JSON_PATH",
        help="Skip Steps 1-3: load an existing concept-graph JSON directly into the graph DB "
             "(e.g. --load-only output/CLMS_concept_graph.json)",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Optional user query / focus area for the agent",
    )
    args = parser.parse_args()

    if args.load_only:
        # Load-only mode: read JSON and push to graph DB
        backend = args.graph_backend
        print(f"\n{'═'*60}")
        print(f"  Advanced Graph Builder — Load-Only Mode ({backend})")
        print(f"  JSON: {args.load_only}")
        print(f"{'═'*60}")
        with open(args.load_only, encoding="utf-8") as f:
            kg_dict = json.load(f)
        database = kg_dict.get("database", "Unknown")
        nodes = kg_dict.get("nodes", [])
        edges = kg_dict.get("edges", [])
        print(f"  Loaded: {len(nodes)} nodes, {len(edges)} edges (database: {database})")
        try:
            if backend == "cosmos":
                result = load_to_cosmos(kg_dict, database, args.output_dir)
            else:
                result = load_to_neo4j(kg_dict, database, args.output_dir)
            print(f"  {result}")
        except Exception as e:
            print(f"\n  ❌ {backend} loading failed!")
            print(f"  Error: {type(e).__name__}: {e}")
            print(f"\n  Full stack trace:")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        print(f"\n{'═'*60}")
        print(f"  Load complete!")
        print(f"{'═'*60}")
        if backend == "cosmos":
            _print_sample_gremlin_queries(database)
        else:
            _print_sample_queries(database)
        return

    # Auto-detect data dir
    if args.data_dir:
        data_dir = args.data_dir
    else:
        project_root = Path(__file__).resolve().parents[2]
        candidate = project_root / "db_schemas_csv"
        if candidate.exists():
            data_dir = str(candidate)
        else:
            data_dir = "db_schemas_csv"

    databases = []
    if args.database == "all":
        databases = ["CLMS", "CrewPortal", "PEP", "HRData", "IJP", "NPS"]
        # Clear entire graph when regenerating all databases
        if not args.skip_neo4j:
            print(f"\n{'═'*60}")
            print(f"  Clearing entire graph before regenerating all databases")
            print(f"{'═'*60}")
            try:
                if args.graph_backend == "cosmos":
                    clear_entire_cosmos_graph()
                else:
                    clear_entire_neo4j_graph()
            except Exception as e:
                print(f"    ⚠️  Warning: Graph clearing failed: {type(e).__name__}: {e}")
                logger.warning(f"Graph clearing failed: {type(e).__name__}: {e}")
    else:
        databases = [args.database]

    # Load table filter if provided
    table_filter = None
    if args.table_filter:
        table_filter = load_table_filter(args.table_filter)
        if table_filter:
            print(f"\n{'═'*60}")
            print(f"  Table Filter Loaded: {len(table_filter)} tables")
            print(f"  Filter file: {args.table_filter}")
            print(f"{'═'*60}")
        else:
            print(f"\n⚠️  Warning: Table filter file '{args.table_filter}' is empty or invalid")
    
    for db in databases:
        run_pipeline(db, data_dir, args.output_dir, args.skip_neo4j, args.query, args.graph_backend, table_filter)

    print(f"\n{'═'*60}")
    print(f"  All done! Check the '{args.output_dir}' folder for outputs.")
    print(f"{'═'*60}")


if __name__ == "__main__":
    main()
