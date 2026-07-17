"""
data_retrieval_agent.py — ReAct agent that explores the unified knowledge graph
to identify relevant nodes, then retrieves actual data from the physical databases.

**Responsibility:**
This agent accurately interprets a user's natural language query, rewrites it in 
a lucid manner, queries the unified knowledge graph (Gremlin/Cosmos DB) to identify 
logical nodes (tables and concepts) related to the query, executes queries on the 
respective physical databases to retrieve actual data, and returns both the sub-graph
structure and the associated data.

**Key Capabilities:**
1. Natural language query interpretation and reformulation
2. Knowledge graph exploration to identify relevant tables and concepts
3. Physical database query execution (SQL Server, etc.)
4. Unified response with both graph structure and actual data

**Grounding:**
- Analysis is strictly grounded in the unified knowledge graph
- No assumptions about entities outside the unified knowledge graph
- Data retrieval only for logical entities identified in the graph
- Queries executed on the respective databases indicated in the graph metadata

Usage:
    python -m src.agents.Data_Retrieval_Agent_New "Get all employees from HR and their leave balance"
    python -m src.agents.Data_Retrieval_Agent_New   # interactive mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import pyodbc
from sqlalchemy import create_engine, text

from src.utils import get_graph_db
from src.utils.llm import get_llm_client, LLM_MODEL
from src.utils.agent_logger import (
    setup_agent_logger,
    log_subgraph_extraction,
    log_query_plan,
    log_tool_execution,
)

# Initialize logger
logger = setup_agent_logger("data_retrieval_agent")


# ===============================================================================
#  Database Connection Manager
# ===============================================================================

class DatabaseConnectionManager:
    """Manages connections to physical databases for data retrieval."""
    
    def __init__(self):
        self.connections = {}
        self._initialize_connections()
    
    def _initialize_connections(self):
        """Initialize database connections from environment variables."""
        sql_server = os.getenv("SQL_SERVER", "localhost")
        sql_user = os.getenv("SQL_USER", "sa")
        sql_password = os.getenv("SQL_PASSWORD", "YourStrong@Passw0rd")
        
        # SQL Server connection for HRData
        try:
            hrdata_conn_str = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={sql_server};"
                f"DATABASE=HRData;"
                f"UID={sql_user};"
                f"PWD={sql_password};"
                f"TrustServerCertificate=yes;"
            )
            self.connections["HRData"] = {
                "type": "sqlserver",
                "connection_string": hrdata_conn_str,
                "engine": create_engine(f"mssql+pyodbc:///?odbc_connect={hrdata_conn_str}")
            }
            logger.info(f"Initialized SQL Server connection for HRData database")
        except Exception as e:
            logger.warning(f"Failed to initialize HRData connection: {e}")
        
        # SQL Server connection for IJP
        try:
            ijp_conn_str = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={sql_server};"
                f"DATABASE=HRData;"  # IJP table is in HRData database
                f"UID={sql_user};"
                f"PWD={sql_password};"
                f"TrustServerCertificate=yes;"
            )
            self.connections["IJP"] = {
                "type": "sqlserver",
                "connection_string": ijp_conn_str,
                "engine": create_engine(f"mssql+pyodbc:///?odbc_connect={ijp_conn_str}")
            }
            logger.info(f"Initialized SQL Server connection for IJP database")
        except Exception as e:
            logger.warning(f"Failed to initialize IJP connection: {e}")
        
        # SQL Server connection for CLMS
        # Note: CLMS_Raw_Data physically lives in the HRData database (not a
        # separate CLMS database) so that single-query JOINs across
        # HR/CLMS/NPS tables work — SQL Server can't JOIN unqualified table
        # names across different physical databases in one query.
        try:
            clms_conn_str = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={sql_server};"
                f"DATABASE=HRData;"
                f"UID={sql_user};"
                f"PWD={sql_password};"
                f"TrustServerCertificate=yes;"
            )
            self.connections["CLMS"] = {
                "type": "sqlserver",
                "connection_string": clms_conn_str,
                "engine": create_engine(f"mssql+pyodbc:///?odbc_connect={clms_conn_str}")
            }
            logger.info(f"Initialized SQL Server connection for CLMS database")
        except Exception as e:
            logger.warning(f"Failed to initialize CLMS connection: {e}")

        # SQL Server connection for NPS
        # Note: IndigoNPS_Summary physically lives in the HRData database — see
        # the CLMS connection comment above for why.
        try:
            nps_conn_str = (
                f"DRIVER={{ODBC Driver 18 for SQL Server}};"
                f"SERVER={sql_server};"
                f"DATABASE=HRData;"
                f"UID={sql_user};"
                f"PWD={sql_password};"
                f"TrustServerCertificate=yes;"
            )
            self.connections["NPS"] = {
                "type": "sqlserver",
                "connection_string": nps_conn_str,
                "engine": create_engine(f"mssql+pyodbc:///?odbc_connect={nps_conn_str}")
            }
            logger.info(f"Initialized SQL Server connection for NPS database")
        except Exception as e:
            logger.warning(f"Failed to initialize NPS connection: {e}")

        # Note: PEP, CrewPortal and other databases would be added here when available
        logger.info(f"Database connection manager initialized with {len(self.connections)} connection(s)")
    
    def execute_query(self, database: str, query: str, limit: int = 100) -> dict:
        """Execute a SQL query on the specified database.
        
        Args:
            database: Database name (e.g., 'HRData', 'CLMS')
            query: SQL query to execute
            limit: Maximum number of rows to return
            
        Returns:
            Dictionary with columns, data, and metadata
        """
        if database not in self.connections:
            return {
                "error": f"Database '{database}' is not connected. "
                         f"Available databases: {list(self.connections.keys())}",
                "note": f"The table exists in the knowledge graph but physical database access is not yet configured."
            }
        
        try:
            conn_info = self.connections[database]
            engine = conn_info["engine"]
            
            # For SQL Server, don't add LIMIT (it uses TOP instead)
            # Only add limit if query doesn't already have TOP or LIMIT
            query_lower = query.lower().strip()
            has_limit = "limit" in query_lower or " top " in query_lower or query_lower.startswith("select top")
            
            if not has_limit and not query_lower.endswith(";"):
                # For SQL Server, wrap in a subquery with TOP
                if conn_info.get("type") == "sqlserver":
                    # Don't add LIMIT for SQL Server - TOP should already be in the query
                    # If no TOP, the query will return all rows (which is what the user asked for)
                    pass
                else:
                    query = f"{query.rstrip(';')} LIMIT {limit}"
            
            with engine.connect() as conn:
                result = conn.execute(text(query))
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchall()]
                
                logger.info(f"Query executed successfully on {database}: {len(rows)} rows returned")
                
                return {
                    "database": database,
                    "columns": columns,
                    "row_count": len(rows),
                    "data": rows,
                    "query": query
                }
        except Exception as e:
            logger.error(f"Query execution failed on {database}: {e}")
            return {
                "error": str(e),
                "database": database,
                "query": query
            }
    
    def get_sample_data(self, database: str, table: str, columns: list[str] = None, limit: int = 10) -> dict:
        """Retrieve sample data from a table.
        
        Args:
            database: Database name
            table: Table name
            columns: Optional list of specific columns (None = all columns)
            limit: Number of rows to return
            
        Returns:
            Dictionary with sample data
        """
        if columns:
            cols = ", ".join(columns)
            query = f"SELECT TOP {limit} {cols} FROM {table}"
        else:
            query = f"SELECT TOP {limit} * FROM {table}"
        
        return self.execute_query(database, query, limit=limit)
    
    def close(self):
        """Close all database connections."""
        for db_name, conn_info in self.connections.items():
            try:
                if "engine" in conn_info:
                    conn_info["engine"].dispose()
                logger.info(f"Closed connection to {db_name}")
            except Exception as e:
                logger.warning(f"Error closing connection to {db_name}: {e}")


# ===============================================================================
#  Tool definitions (extends inference agent tools with data retrieval)
# ===============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schema",
            "description": (
                "Get the full knowledge-graph schema: node labels, "
                "relationship types, node counts per label, and total edge count. "
                "Call this first to understand the unified graph structure."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_concepts",
            "description": (
                "List all Concept nodes with their name, description, database, "
                "and source tables. Use this to discover which business domains "
                "exist in the unified graph."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "concept_links",
            "description": (
                "For a given concept name, return the tables and related concepts "
                "it connects to, grouped by database. Use this to drill into a "
                "specific business domain and identify physical tables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "concept_name": {"type": "string", "description": "Name of the concept to look up."},
                    "database": {
                        "type": "string",
                        "description": "Optional — filter to a specific database (discovered from graph exploration).",
                    },
                },
                "required": ["concept_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "node_details",
            "description": (
                "Get full properties and all neighbours of any node by its exact name. "
                "Returns metadata including:\n"
                "- columns: Array of column objects [{column: 'Name', data_type: 'VARCHAR', description: '...'}] or column names\n"
                "- primary keys, database name, description, and all relationships.\n"
                "**IMPORTANT**: Extract column NAMES from the response - columns may be objects with 'column' field.\n"
                "Essential for understanding what data is available in a table and verifying column existence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact name of the node (table or concept)."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": (
                "List all Table nodes in the unified graph. Optionally filter by database name. "
                "Use this to discover what physical tables are available in each database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Optional database filter.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Case-insensitive keyword search across node names, labels, and "
                "descriptions in the unified knowledge graph. Searches node descriptions "
                "for semantic matches. Best first step for vague or exploratory queries. "
                "**ALWAYS examine the 'description' field in results** to understand node purpose. "
                "Optionally filter to a single database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search keyword or concept (e.g., 'performance', 'rating', 'appraisal')."},
                    "database": {
                        "type": "string",
                        "description": "Optional — only return results from this database (discovered from graph).",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_concept_search",
            "description": (
                "Search for concepts by semantic purpose. Use this when you need to find nodes "
                "that serve a particular business function. For example: 'employee' performance evaluation', "
                "'leave management', 'job postings'. This tool searches node descriptions and purposes, "
                "not just names. Use BEFORE giving up when initial searches fail."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "purpose": {
                        "type": "string",
                        "description": "The semantic purpose or business function you're looking for (e.g., 'employee ratings and appraisals', 'crew performance metrics')."
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of related keywords to search (e.g., ['performance', 'rating', 'appraisal', 'score'])."
                    },
                },
                "required": ["purpose", "keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_columns",
            "description": (
                "Find tables whose column list contains the given keyword. "
                "Use for 'where does column X live?' questions. "
                "Returns table metadata from the unified graph. "
                "Optionally filter to a single database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {"type": "string", "description": "Column name or keyword to search for."},
                    "database": {
                        "type": "string",
                        "description": "Optional — only return results from this database (discovered from graph).",
                    },
                },
                "required": ["column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subgraph",
            "description": (
                "Get the N-hop neighbourhood around a node — all reachable nodes "
                "and edges within a given depth in the unified graph. "
                "Good for understanding context and relationships."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Center node name."},
                    "depth": {
                        "type": "integer",
                        "description": "Hops to expand (default 2, max 4).",
                        "default": 2,
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shared_concepts",
            "description": (
                "Return concepts that bridge multiple databases in the unified graph — "
                "the high-value cross-domain nodes. Use to find integration points."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_cross_db",
            "description": (
                "Starting from a concept or table, find what it connects to in a "
                "specific target database (1-hop and 2-hop) in the unified graph. "
                "This is KEY for cross-database questions like 'how does Leave Management "
                "relate to Employee data?'. Returns direct connections and 2-hop paths "
                "through intermediate nodes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Starting node name (concept or table)."},
                    "target_database": {
                        "type": "string",
                        "description": "Target database to trace connections into (discovered from graph).",
                    },
                },
                "required": ["name", "target_database"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_join_keys",
            "description": (
                "**CRITICAL FOR JOINS**: Intelligently analyze multiple tables using metadata and semantic "
                "understanding to identify the BEST join columns. This tool:\n"
                "1. Examines full node metadata (columns, descriptions, primary keys, relationships)\n"
                "2. Analyzes graph relationships between tables\n"
                "3. Uses semantic understanding to classify column types (ID, name, metric, etc.)\n"
                "4. Identifies shared ID columns (IGA, EmployeeID, etc.) that are reliable for JOINs\n"
                "5. Warns against using non-ID columns like Name\n\n"
                "Returns prioritized recommendations with reasons and examples. "
                "ALWAYS use this before constructing JOIN queries - it ensures you join on proper key columns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of table names to analyze for shared join columns (e.g., ['Indigo_HR_Raw_Data', 'IJP_Employee_scores']).",
                    },
                },
                "required": ["tables"],
            },
        },
    },
    # === NEW DATA RETRIEVAL TOOLS ===
    {
        "type": "function",
        "function": {
            "name": "get_sample_data",
            "description": (
                "🔍 EXPLORATION ONLY: Retrieve sample data (default 10 rows) to understand table structure. "
                "⚠️ DO NOT USE THIS TO ANSWER USER QUERIES! Sample data is limited and may not contain "
                "the specific records users ask for. Use execute_query with WHERE clause for actual data retrieval. "
                "This tool is ONLY for understanding column types and data format before writing queries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Database name (must match the 'database' property from the graph node).",
                    },
                    "table": {
                        "type": "string",
                        "description": "Exact table name from the knowledge graph.",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of specific column names to retrieve.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of rows (default 10, max 100).",
                        "default": 10,
                    },
                },
                "required": ["database", "table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_query",
            "description": (
                "✅ PRIMARY DATA RETRIEVAL TOOL: Execute SQL queries on physical databases. "
                "Use this for ALL actual data retrieval including: "
                "(1) Filtering by names, IDs, dates (WHERE clause), "
                "(2) Joining multiple tables (INNER JOIN), "
                "(3) Answering user queries about specific records. "
                "ALWAYS use this instead of get_sample_data when user asks for specific data. "
                "The query must be valid SQL for the target database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Database name (discovered from graph exploration).",
                    },
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute. Must be SELECT only (no INSERT/UPDATE/DELETE).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of rows (default 100).",
                        "default": 100,
                    },
                },
                "required": ["database", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_employee_score",
            "description": (
                "Calculate comprehensive weighted performance score for an employee based on multiple criteria. "
                "This tool automatically fetches data from multiple tables and computes:"
                "\n- Crew Availability (40%) - from CLMS database"
                "\n- Poise & Grace/BMI (10%) - from HR data"
                "\n- Appreciation Letters (20%) - from performance records"
                "\n- Non-performance Discussions (10%) - from disciplinary records"
                "\n- Curative Training/iCoach sessions (5%) - from training logs"
                "\n- Extra Initiatives/Recognition (5%) - from 6E Clap and other recognitions"
                "\n- Passenger NPS Feedback (10%) - from NPS survey responses attributed to the crew member"
                "\n\nReturns detailed breakdown with individual scores, weights, and final aggregate score."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "employee_identifier": {
                        "type": "string",
                        "description": "Employee name or IGA number to calculate score for.",
                    },
                    "identifier_type": {
                        "type": "string",
                        "description": "Type of identifier: 'name' or 'iga' (default: 'name').",
                        "enum": ["name", "iga"],
                        "default": "name",
                    },
                },
                "required": ["employee_identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_top_performing_crew",
            "description": (
                "Rank ALL crew members by the SAME weighted performance score formula used by "
                "calculate_employee_score, and return the top N. Use this whenever the user asks "
                "who the BEST, TOP, HIGHEST-RATED, STAR, or WORST/LOWEST performing crew member(s) "
                "are — i.e. any question that requires COMPARING or RANKING employees, not scoring "
                "one named individual. Scores every employee in a single query (no per-employee "
                "tool calls) and sorts by total_score descending.\n\n"
                "Examples this tool answers: 'who is the top performing crew?', 'who are our best "
                "crew members?', 'show me the highest rated employees', 'top 10 star performers', "
                "'who has the best performance score at DEL base?', 'who is the worst performing crew?'.\n\n"
                "Do NOT use for a single named employee's score — use calculate_employee_score instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many performers to return (default 5). Use 1 if the user asks for a single 'the best'/'the worst' crew member.",
                        "default": 5,
                    },
                    "base": {
                        "type": "string",
                        "description": "Optional: only rank crew based at this location (e.g. 'DEL').",
                    },
                    "designation": {
                        "type": "string",
                        "description": "Optional: only rank crew with this designation (e.g. 'CA', 'SCA').",
                    },
                    "order": {
                        "type": "string",
                        "description": "'best' (default) for highest-scoring first, or 'worst' for lowest-scoring first.",
                        "enum": ["best", "worst"],
                        "default": "best",
                    },
                },
                "required": [],
            },
        },
    },
]


# ===============================================================================
#  Helper functions
# ===============================================================================

EMPLOYEE_SCORE_JOIN_SQL = """
    FROM [Indigo_HR_Raw_Data] t1
    LEFT JOIN [IJP_Employee_scores] t2 ON t1.[IGA] = t2.[IGA]
    LEFT JOIN [CLMS_Raw_Data] t3 ON t1.[IGA] = t3.[IGA]
    LEFT JOIN [IndigoNPS_Summary] t4 ON t1.[IGA] = t4.[IGA]
"""

EMPLOYEE_SCORE_SELECT_SQL = """
    t1.[Name], t1.[IGA], t1.[Base], t1.[Designation],
    t2.[BMI], t2.[AppreciationLetters], t2.[CautionLetters],
    t2.[CoachingSessions], t2.[Recognition], t2.[LWPDays],
    t3.[Active], t3.[Status], t3.[NoOfLeaves], t3.[LWD],
    t3.[Balance], t3.[LeaveType],
    t4.[NPS Score], t4.[Crew helpfulness],
    t4.[Please share your reasons for the rating]
"""


def _score_employee_row(employee_data: dict) -> dict:
    """Pure scoring function: compute the weighted performance score breakdown
    from one already-fetched, joined employee row (Indigo_HR_Raw_Data ⋈
    IJP_Employee_scores ⋈ CLMS_Raw_Data ⋈ IndigoNPS_Summary on IGA).

    Shared by calculate_employee_score (single employee) and
    find_top_performing_crew (ranks all employees) so the formula never drifts
    between the two.

    Formula:
    - Crew Availability: 40%
    - Poise & Grace (BMI): 10%
    - Appreciation Letters: 20%
    - Non-performance Discussions: 10%
    - Curative Training/iCoach sessions: 5%
    - Extra Initiatives/Recognition: 5%
    - Passenger NPS Feedback: 10%

    Returns:
        Dictionary with "components", "total_score", "errors"
    """
    score_components = {
        "components": [],
        "total_score": 0,
        "errors": [],
    }

    # BMI scoring: Ideal BMI 18.5-24.9 = 100%, outside range reduces score
    bmi_value = employee_data.get("BMI")
    if bmi_value is not None:
        try:
            bmi_float = float(bmi_value)
            if 18.5 <= bmi_float <= 24.9:
                bmi_score = 100.0
            elif 17.0 <= bmi_float < 18.5 or 25.0 <= bmi_float <= 27.0:
                bmi_score = 80.0
            elif 16.0 <= bmi_float < 17.0 or 27.0 < bmi_float <= 30.0:
                bmi_score = 60.0
            else:
                bmi_score = 40.0

            weighted_bmi = bmi_score * 0.10
            score_components["components"].append({
                "name": "Poise & Grace (BMI)",
                "weight": "10%",
                "raw_value": bmi_float,
                "normalized_score": bmi_score,
                "weighted_score": weighted_bmi,
                "data_source": "HR Data"
            })
            score_components["total_score"] += weighted_bmi
        except (ValueError, TypeError):
            score_components["errors"].append(f"Invalid BMI value: {bmi_value}")
    else:
        score_components["errors"].append("BMI data not available")

    # Calculate Appreciation Letters Score (20% weight)
    # Scoring: More letters = higher score (max 10 letters = 100%)
    appreciation = employee_data.get("AppreciationLetters")
    if appreciation is not None:
        try:
            appreciation_count = int(appreciation)
            appreciation_score = min(appreciation_count * 10, 100)  # 10 points per letter, max 100
            weighted_appreciation = appreciation_score * 0.20
            score_components["components"].append({
                "name": "Appreciation Letters",
                "weight": "20%",
                "raw_value": appreciation_count,
                "normalized_score": appreciation_score,
                "weighted_score": weighted_appreciation,
                "data_source": "CAC Portal"
            })
            score_components["total_score"] += weighted_appreciation
        except (ValueError, TypeError):
            score_components["errors"].append(f"Invalid appreciation letters value: {appreciation}")
    else:
        score_components["errors"].append("Appreciation letters data not available")

    # Calculate Non-performance Discussions Score (10% weight)
    # Scoring: Fewer is better (0 caution letters = 100%, each letter reduces score)
    caution = employee_data.get("CautionLetters")
    if caution is not None:
        try:
            caution_count = int(caution)
            caution_score = max(100 - (caution_count * 20), 0)
            weighted_caution = caution_score * 0.10
            score_components["components"].append({
                "name": "Non-performance Discussions",
                "weight": "10%",
                "raw_value": caution_count,
                "normalized_score": caution_score,
                "weighted_score": weighted_caution,
                "data_source": "Disciplinary Records",
                "note": "Lower is better (fewer caution letters)"
            })
            score_components["total_score"] += weighted_caution
        except (ValueError, TypeError):
            score_components["errors"].append(f"Invalid caution letters value: {caution}")
    else:
        score_components["errors"].append("Caution letters data not available")

    # Calculate Curative Training/iCoach Sessions Score (5% weight)
    # Scoring: More sessions = proactive improvement = higher score
    coaching = employee_data.get("CoachingSessions")
    if coaching is not None:
        try:
            coaching_count = int(coaching)
            coaching_score = min(coaching_count * 20, 100)
            weighted_coaching = coaching_score * 0.05
            score_components["components"].append({
                "name": "Curative Training/iCoach Sessions",
                "weight": "5%",
                "raw_value": coaching_count,
                "normalized_score": coaching_score,
                "weighted_score": weighted_coaching,
                "data_source": "Training Logs (LMS)"
            })
            score_components["total_score"] += weighted_coaching
        except (ValueError, TypeError):
            score_components["errors"].append(f"Invalid coaching sessions value: {coaching}")
    else:
        score_components["errors"].append("Coaching sessions data not available")

    # Calculate Extra Initiatives/Recognition Score (5% weight)
    # Scoring: Recognition like "6e Clap" = bonus points
    recognition = employee_data.get("Recognition")
    if recognition is not None:
        recognition_str = str(recognition).strip()
        if recognition_str and recognition_str.lower() != "none" and recognition_str.lower() != "null":
            if "6e clap" in recognition_str.lower():
                recognition_score = 100
            else:
                recognition_score = 80
            weighted_recognition = recognition_score * 0.05
            score_components["components"].append({
                "name": "Extra Initiatives/Recognition",
                "weight": "5%",
                "raw_value": recognition_str,
                "normalized_score": recognition_score,
                "weighted_score": weighted_recognition,
                "data_source": "6E Clap and Recognitions"
            })
            score_components["total_score"] += weighted_recognition
        else:
            score_components["errors"].append("No recognition data available")
    else:
        score_components["errors"].append("Recognition data not available")

    # Crew Availability Score (40% weight) - from real CLMS leave data
    status = employee_data.get("Status")
    active = employee_data.get("Active")
    lwd = employee_data.get("LWD")

    if status is None and active is None:
        score_components["components"].append({
            "name": "Crew Availability",
            "weight": "40%",
            "raw_value": "Not available",
            "normalized_score": 0,
            "weighted_score": 0,
            "data_source": "CLMS_Raw_Data",
            "note": "⚠️ No CLMS_Raw_Data record found for this employee (IGA)"
        })
        score_components["errors"].append("Crew Availability: no CLMS_Raw_Data record for this employee")
    elif status == "Released" or active == "N" or lwd is not None:
        score_components["components"].append({
            "name": "Crew Availability",
            "weight": "40%",
            "raw_value": f"Status={status}, Active={active}, LWD={lwd}",
            "normalized_score": 0,
            "weighted_score": 0,
            "data_source": "CLMS_Raw_Data",
            "note": "Crew member has been released / is inactive — availability is 0"
        })
        score_components["total_score"] += 0
    else:
        no_of_leaves = employee_data.get("NoOfLeaves")
        try:
            leaves_float = float(no_of_leaves) if no_of_leaves is not None else 0.0
            availability_score = max(100 - (leaves_float * 10), 0)
            weighted_availability = availability_score * 0.40
            score_components["components"].append({
                "name": "Crew Availability",
                "weight": "40%",
                "raw_value": f"{leaves_float} leave days (most recent request), "
                             f"balance {employee_data.get('Balance')} ({employee_data.get('LeaveType')})",
                "normalized_score": availability_score,
                "weighted_score": weighted_availability,
                "data_source": "CLMS_Raw_Data",
                "note": "Calculated from recent leave days requested; lower is better"
            })
            score_components["total_score"] += weighted_availability
        except (ValueError, TypeError):
            score_components["errors"].append("Could not calculate availability from CLMS_Raw_Data.NoOfLeaves")

    # Passenger NPS Feedback Score (10% weight) - from IndigoNPS_Summary
    nps_score = employee_data.get("NPS Score")
    crew_helpfulness = employee_data.get("Crew helpfulness")
    feedback_text = employee_data.get("Please share your reasons for the rating")

    if nps_score is None:
        score_components["components"].append({
            "name": "Passenger NPS Feedback",
            "weight": "10%",
            "raw_value": "Not available",
            "normalized_score": 0,
            "weighted_score": 0,
            "data_source": "IndigoNPS_Summary",
            "note": "⚠️ No IndigoNPS_Summary record found for this employee (IGA)"
        })
        score_components["errors"].append("Passenger NPS Feedback: no IndigoNPS_Summary record for this employee")
    else:
        try:
            nps_float = float(nps_score)
            nps_normalized = max(min(nps_float * 10, 100), 0)
            weighted_nps = nps_normalized * 0.10
            component = {
                "name": "Passenger NPS Feedback",
                "weight": "10%",
                "raw_value": f"NPS Score {nps_float}/10"
                             + (f", Crew helpfulness {crew_helpfulness}/5" if crew_helpfulness not in (None, "No") else ""),
                "normalized_score": nps_normalized,
                "weighted_score": weighted_nps,
                "data_source": "IndigoNPS_Summary",
                "note": "Scaled from passenger Net Promoter Score (0-10 -> 0-100)"
            }
            if feedback_text:
                component["feedback"] = feedback_text
            score_components["components"].append(component)
            score_components["total_score"] += weighted_nps
        except (ValueError, TypeError):
            score_components["errors"].append("Could not calculate score from IndigoNPS_Summary.NPS Score")

    score_components["total_score"] = round(score_components["total_score"], 2)
    return score_components


def calculate_employee_score(db_manager: DatabaseConnectionManager, employee_identifier: str,
                           identifier_type: str = "name") -> dict:
    """Calculate comprehensive weighted performance score for a single named employee.

    Fetches the joined HR/IJP/CLMS/NPS row for this employee and delegates the
    actual formula to _score_employee_row (shared with find_top_performing_crew).

    Args:
        db_manager: Database connection manager
        employee_identifier: Employee name or IGA number
        identifier_type: 'name' or 'iga'

    Returns:
        Dictionary with score breakdown and aggregate score
    """
    try:
        if identifier_type.lower() == "iga":
            where_clause = f"t1.[IGA] = '{employee_identifier}'"
            identifier_display = f"IGA: {employee_identifier}"
        else:
            where_clause = f"t1.[Name] = '{employee_identifier}'"
            identifier_display = f"Name: {employee_identifier}"

        logger.info(f"Calculating employee score for {identifier_display}")

        hr_query = f"""
            SELECT TOP 1 {EMPLOYEE_SCORE_SELECT_SQL}
            {EMPLOYEE_SCORE_JOIN_SQL}
            WHERE {where_clause}
        """

        hr_result = db_manager.execute_query(database="HRData", query=hr_query, limit=1)

        if not hr_result.get("data") or len(hr_result["data"]) == 0:
            return {
                "error": f"Employee not found with {identifier_display}",
                "query_used": hr_query,
                "suggestion": "Verify the employee identifier is correct and exists in the database."
            }

        employee_data = hr_result["data"][0]

        score_components = _score_employee_row(employee_data)
        score_components["employee_identifier"] = employee_identifier
        score_components["identifier_type"] = identifier_type
        score_components["employee_details"] = {
            "name": employee_data.get("Name", "Unknown"),
            "iga": employee_data.get("IGA", "Unknown"),
            "base": employee_data.get("Base", "Unknown"),
            "designation": employee_data.get("Designation", "Unknown")
        }
        score_components["summary"] = {
            "total_score": score_components["total_score"],
            "max_possible_score": 100.0,
            "percentage": f"{score_components['total_score']}%",
            "components_calculated": len([c for c in score_components["components"] if c["weighted_score"] > 0]),
            "total_components": 7,
            "missing_data": len(score_components["errors"])
        }

        return score_components

    except Exception as e:
        logger.error(f"Error calculating employee score: {e}", exc_info=True)
        return {
            "error": f"Failed to calculate employee score: {str(e)}",
            "employee_identifier": employee_identifier,
            "identifier_type": identifier_type
        }


def find_top_performing_crew(db_manager: DatabaseConnectionManager, limit: int = 5,
                              base: str | None = None, designation: str | None = None,
                              order: str = "best") -> dict:
    """Rank ALL crew members by the same weighted performance score used by
    calculate_employee_score, and return the top (or bottom) N.

    Fetches every employee's joined HR/IJP/CLMS/NPS row in a single query, scores
    each one with _score_employee_row (same formula, no drift), sorts by
    total_score, and returns the top `limit`.

    Args:
        db_manager: Database connection manager
        limit: How many crew to return (default 5)
        base: Optional filter — only consider crew based at this location
        designation: Optional filter — only consider crew with this designation
        order: 'best' (default, highest scores first) or 'worst' (lowest scores first)

    Returns:
        Dictionary with ranked performers and how many crew were scored
    """
    try:
        where_clauses = []
        if base:
            where_clauses.append(f"t1.[Base] = '{base}'")
        if designation:
            where_clauses.append(f"t1.[Designation] = '{designation}'")
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        query = f"""
            SELECT {EMPLOYEE_SCORE_SELECT_SQL}
            {EMPLOYEE_SCORE_JOIN_SQL}
            {where_sql}
        """

        result = db_manager.execute_query(database="HRData", query=query, limit=10000)

        if "error" in result:
            return {"error": f"Failed to fetch crew for ranking: {result['error']}"}

        rows = result.get("data", [])
        if not rows:
            return {"error": "No crew members found matching the given filters."}

        ranked = []
        for row in rows:
            scored = _score_employee_row(row)
            ranked.append({
                "name": row.get("Name", "Unknown"),
                "iga": row.get("IGA", "Unknown"),
                "base": row.get("Base", "Unknown"),
                "designation": row.get("Designation", "Unknown"),
                "total_score": scored["total_score"],
                "percentage": f"{scored['total_score']}%",
                "components_calculated": len([c for c in scored["components"] if c["weighted_score"] > 0]),
                "missing_data": len(scored["errors"]),
                "component_highlights": [
                    {"name": c["name"], "raw_value": c["raw_value"], "normalized_score": c["normalized_score"]}
                    for c in scored["components"]
                ],
            })

        reverse = order.lower() != "worst"
        ranked.sort(key=lambda r: r["total_score"], reverse=reverse)

        for i, entry in enumerate(ranked[:limit], 1):
            entry["rank"] = i

        return {
            "total_crew_scored": len(ranked),
            "filters_applied": {"base": base, "designation": designation},
            "order": order,
            "requested_limit": limit,
            "top_performers": ranked[:limit],
        }

    except Exception as e:
        logger.error(f"Error ranking top-performing crew: {e}", exc_info=True)
        return {"error": f"Failed to rank top-performing crew: {str(e)}"}


def find_join_keys(graph_db, tables: list[str]) -> dict:
    """Identify shared ID/key columns across multiple tables using metadata and semantic analysis.
    
    Uses:
    1. Node metadata (column names, descriptions, properties)
    2. Graph relationships between tables
    3. Semantic understanding of column purposes
    4. Pattern matching for ID columns
    
    Args:
        graph_db: Graph database instance
        tables: List of table names to analyze
        
    Returns:
        Dictionary with shared columns, JOIN recommendations, and semantic analysis
    """
    if len(tables) < 2:
        return {
            "error": "Need at least 2 tables to find join keys",
            "note": "Provide a list of table names to compare"
        }
    
    # Step 1: Fetch full node details for all tables in parallel (with metadata)
    table_metadata = {}
    with ThreadPoolExecutor(max_workers=min(len(tables), 5)) as executor:
        future_to_table = {executor.submit(graph_db.node_details, table): table for table in tables}
        
        for future in as_completed(future_to_table):
            table = future_to_table[future]
            try:
                details = future.result()
                if details and details.get('node'):
                    node_props = details.get('node', {}).get('props', {})
                    
                    # Get columns - may be JSON string or array
                    columns_raw = node_props.get('columns', [])
                    if isinstance(columns_raw, str):
                        # Stored as JSON string in Cosmos DB: "[\"IGA\", \"Name\", ...]"
                        try:
                            columns = json.loads(columns_raw)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse columns JSON for table '{table}': {columns_raw}")
                            columns = []
                    elif isinstance(columns_raw, list):
                        # Already an array
                        columns = columns_raw
                    else:
                        columns = []
                    
                    # Get primary keys - may also be JSON string
                    pk_raw = node_props.get('primary_key', node_props.get('primaryKeys', ''))
                    if isinstance(pk_raw, str) and pk_raw.startswith('['):
                        try:
                            primary_keys = json.loads(pk_raw)
                        except (json.JSONDecodeError, TypeError):
                            primary_keys = [pk_raw] if pk_raw else []
                    elif isinstance(pk_raw, list):
                        primary_keys = pk_raw
                    elif pk_raw:
                        primary_keys = [pk_raw]
                    else:
                        primary_keys = []
                    
                    if columns:
                        table_metadata[table] = {
                            'columns': columns,
                            'database': node_props.get('database', 'Unknown'),
                            'description': node_props.get('description', ''),
                            'primary_key': primary_keys[0] if primary_keys else '',
                            'foreign_keys': node_props.get('foreign_keys', []),
                            'neighbours': details.get('neighbours', [])
                        }
                    else:
                        table_metadata[table] = {
                            'columns': [],
                            'database': node_props.get('database', 'Unknown'),
                            'error': f"Table '{table}' found but has no columns metadata"
                        }
                else:
                    table_metadata[table] = {
                        'columns': [],
                        'database': 'Unknown',
                        'error': f"Table '{table}' not found in knowledge graph"
                    }
            except Exception as e:
                logger.warning(f"Failed to get details for table '{table}': {e}")
                table_metadata[table] = {
                    'columns': [],
                    'database': 'Unknown',
                    'error': str(e)
                }
    
    # Step 2: Analyze graph relationships to find explicit connections
    graph_relationships = []
    for table, metadata in table_metadata.items():
        if 'error' in metadata:
            continue
        
        # Check if this table connects to other tables in our list
        for neighbour in metadata.get('neighbours', []):
            neighbour_name = neighbour.get('neighbour_name', '')
            if neighbour_name in tables and neighbour_name != table:
                relationship = neighbour.get('relationship', '')
                graph_relationships.append({
                    'from_table': table,
                    'to_table': neighbour_name,
                    'relationship': relationship,
                    'note': f"Graph shows {table} {relationship} {neighbour_name}"
                })
    
    # Step 3: ID column pattern matching (traditional approach)
    id_patterns = [
        'iga', 'employeeid', 'empid', 'employee_id', 'emp_id', 'userid', 'user_id',
        'personid', 'person_id', 'staffid', 'staff_id', 'employeenumber', 'employee_number',
        'uniqueid', 'recordid', 'crewid', 'crew_id', 'memberid'
    ]
    
    # Step 4: Find shared columns and analyze semantically
    all_columns = {}
    id_columns_per_table = {}
    column_analysis = {}
    
    for table, metadata in table_metadata.items():
        if 'error' in metadata:
            continue
            
        columns = metadata['columns']
        id_columns = []
        
        for col in columns:
            # Handle both dict format and string format for columns
            if isinstance(col, dict):
                # Extract column name from dict: {"column": "IGA", "data_type": "...", "description": "..."}
                col_name = col.get('column', '')
                if not col_name:
                    continue
            elif isinstance(col, str):
                # Direct string format
                col_name = col
            else:
                # Unknown format, skip
                continue
            
            col_lower = col_name.lower().replace('[', '').replace(']', '').replace(' ', '').replace('_', '')
            col_clean = col_name.replace('[', '').replace(']', '')
            
            # Track all columns with metadata
            if col_clean not in all_columns:
                all_columns[col_clean] = []
            all_columns[col_clean].append({
                'table': table,
                'database': metadata['database'],
                'column_name': col_name
            })
            
            # Semantic analysis: identify column purpose
            is_id_column = False
            column_type = 'data'
            
            # Pattern matching for ID columns
            if any(pattern in col_lower for pattern in id_patterns) or col_lower.endswith('id'):
                is_id_column = True
                column_type = 'identifier'
                id_columns.append(col_clean)
            # Check if it's a primary key from metadata
            elif metadata.get('primary_key'):
                pk = metadata['primary_key']
                # primary_key is now stored as a string (first from list) in table_metadata
                if isinstance(pk, str) and col_clean.lower() == pk.lower():
                    is_id_column = True
                    column_type = 'primary_key'
                    id_columns.append(col_clean)
            # Semantic indicators for name fields
            elif 'name' in col_lower and col_lower in ['name', 'fullname', 'employeename', 'username']:
                column_type = 'name_field'
            # Numeric/descriptive data
            elif any(x in col_lower for x in ['score', 'rating', 'bmi', 'count', 'total', 'amount']):
                column_type = 'metric'
            
            # Store analysis
            if col_clean not in column_analysis:
                column_analysis[col_clean] = {
                    'appears_in': [],
                    'column_type': column_type,
                    'is_id_column': is_id_column
                }
            column_analysis[col_clean]['appears_in'].append(table)
        
        id_columns_per_table[table] = id_columns
    
    # Step 5: Find shared columns (appearing in 2+ tables)
    shared_columns = {col: tables_list for col, tables_list in all_columns.items() if len(tables_list) >= 2}
    
    # Step 6: Prioritize shared ID columns
    shared_id_columns = {}
    for col in shared_columns:
        if column_analysis.get(col, {}).get('is_id_column', False):
            shared_id_columns[col] = shared_columns[col]
    
    # Step 7: Generate intelligent JOIN recommendations
    join_recommendations = []
    
    if shared_id_columns:
        # Primary recommendation: Use ID columns
        for col, table_list in shared_id_columns.items():
            if len(table_list) >= 2:
                analysis = column_analysis.get(col, {})
                join_recommendations.append({
                    'column': col,
                    'tables': [t['table'] for t in table_list],
                    'databases': list(set(t['database'] for t in table_list)),
                    'column_type': analysis.get('column_type', 'identifier'),
                    'priority': 'HIGH',
                    'recommendation': f"✅ RECOMMENDED: Use [{col}] for JOIN across {len(table_list)} tables (ID column)",
                    'reason': f"[{col}] is an identifier column appearing in all tables - reliable for JOINs",
                    'example': (
                        f"SELECT t1.*, t2.* \n"
                        f"FROM [{table_list[0]['table']}] t1 \n"
                        f"INNER JOIN [{table_list[1]['table']}] t2 ON t1.[{col}] = t2.[{col}]"
                    )
                })
    
    # Step 8: Warn about non-ID shared columns (like Name)
    other_shared_columns = {}
    for col, table_list in shared_columns.items():
        if col not in shared_id_columns:
            other_shared_columns[col] = table_list
            analysis = column_analysis.get(col, {})
            
            if len(table_list) >= 2:
                join_recommendations.append({
                    'column': col,
                    'tables': [t['table'] for t in table_list],
                    'databases': list(set(t['database'] for t in table_list)),
                    'column_type': analysis.get('column_type', 'data'),
                    'priority': 'LOW',
                    'recommendation': f"⚠️ NOT RECOMMENDED: [{col}] found but it's a {analysis.get('column_type', 'data')} field",
                    'reason': (
                        f"[{col}] is not an ID column - joining on this may produce incorrect results "
                        f"(duplicates, typos, formatting differences)"
                    ),
                    'example': f"❌ AVOID: INNER JOIN ON t1.[{col}] = t2.[{col}]"
                })
    
    # Step 9: Prepare final result with semantic insights
    return {
        "tables_analyzed": list(table_metadata.keys()),
        "id_columns_per_table": id_columns_per_table,
        "shared_id_columns": shared_id_columns,
        "shared_other_columns": other_shared_columns,
        "graph_relationships": graph_relationships,
        "join_recommendations": sorted(join_recommendations, key=lambda x: 0 if x['priority'] == 'HIGH' else 1),
        "column_analysis": {
            col: {
                'type': analysis['column_type'],
                'is_id': analysis['is_id_column'],
                'appears_in': analysis['appears_in']
            }
            for col, analysis in column_analysis.items() if len(analysis['appears_in']) >= 2
        },
        "semantic_note": (
            "✅ Analysis uses: (1) Graph metadata, (2) Column name patterns, (3) Primary key info, (4) Semantic understanding. "
            "ID columns (IGA, EmployeeID, etc.) are strongly preferred for JOINs over descriptive fields like Name."
        ),
        "best_join_column": list(shared_id_columns.keys())[0] if shared_id_columns else None,
        "warning": (
            "⚠️ NO SHARED ID COLUMNS FOUND! "
            "Joining on non-ID columns (like Name) is risky. "
            "Verify that these tables should actually be joined."
        ) if not shared_id_columns and other_shared_columns else None
    }


def semantic_concept_search(graph_db, purpose: str, keywords: list[str]) -> dict:
    """Enhanced semantic search that tries multiple keywords and examines descriptions."""
    all_results = {}
    seen_names = set()
    
    # Search all keywords in parallel
    with ThreadPoolExecutor(max_workers=min(len(keywords), 5)) as executor:
        future_to_keyword = {executor.submit(graph_db.search, kw): kw for kw in keywords}
        
        for future in as_completed(future_to_keyword):
            keyword = future_to_keyword[future]
            try:
                results = future.result()
                for r in results:
                    name = r.get('name')
                    if name and name not in seen_names:
                        seen_names.add(name)
                        score = 0
                        desc = (r.get('description') or '').lower()
                        label = (r.get('label') or '').lower()
                        
                        for kw in keywords:
                            kw_lower = kw.lower()
                            if kw_lower in desc:
                                score += 2
                            if kw_lower in label:
                                score += 1
                            if kw_lower in name.lower():
                                score += 3
                        
                        r['relevance_score'] = score
                        r['matched_keyword'] = keyword
                        all_results[name] = r
            except Exception as e:
                logger.warning(f"Search failed for keyword '{keyword}': {e}")
    
    sorted_results = sorted(all_results.values(), key=lambda x: x['relevance_score'], reverse=True)
    
    return {
        "purpose": purpose,
        "keywords_searched": keywords,
        "total_results": len(sorted_results),
        "results": sorted_results[:15],
        "note": "Results ranked by semantic relevance. Check 'description' field for node purpose."
    }


def dispatch_tool(graph_db, db_manager: DatabaseConnectionManager, name: str, args: dict, 
                  execution_tracker: dict = None) -> Any:
    """Route a tool call to the corresponding method (graph or database).
    
    Args:
        graph_db: Graph database instance
        db_manager: Database connection manager
        name: Tool name
        args: Tool arguments
        execution_tracker: Dictionary tracking query executions to prevent sequential queries
    """
    if execution_tracker is None:
        execution_tracker = {}
    
    # Enforce single-query rule for multi-table scenarios
    if name == "execute_query":
        database = args.get("database")
        query = args.get("query", "")
        query_upper = query.upper()
        
        # Check if this is a multi-table scenario (previous query executed)
        if "executed_queries" not in execution_tracker:
            execution_tracker["executed_queries"] = []
        
        # Check if a query was already executed
        if execution_tracker["executed_queries"]:
            prev_query = execution_tracker["executed_queries"][0]
            prev_query_text = prev_query.get("query", "")
            
            # Allow if current query is a JOIN (correct recovery from failed single-table query)
            is_current_join = "JOIN" in query_upper
            was_previous_join = "JOIN" in prev_query_text.upper()
            
            # Only block if BOTH queries are single-table (sequential querying)
            if not is_current_join and not was_previous_join:
                return {
                    "error": "🚨 SEQUENTIAL QUERY DETECTED - This violates the multi-table query protocol!",
                    "previous_query": {
                        "database": prev_query.get("database"),
                        "query": prev_query.get("query")
                    },
                    "current_attempt": {
                        "database": database,
                        "query": query
                    },
                    "instruction": (
                        "❌ You MUST NOT query tables sequentially!\n\n"
                        "✅ CORRECT APPROACH:\n"
                        "1. Call find_join_keys([table1, table2, ...]) to identify shared ID columns\n"
                        "2. Construct ONE query with INNER JOIN using the shared ID column\n"
                        "3. Execute that single JOIN query\n\n"
                        "Example:\n"
                        "SELECT t1.[NameColumn], t1.[SharedID], t2.[MetricA], t2.[MetricB]\n"
                        "FROM [EmployeeTable] t1\n"
                        "INNER JOIN [MetricsTable] t2 ON t1.[SharedID] = t2.[SharedID]\n"
                        "WHERE t1.[NameColumn] = 'Person Name'\n\n"
                        "This single query retrieves ALL needed data at once."
                    )
                }
            
            # If current is JOIN but previous was also JOIN, that's suspicious (but allow it)
            if is_current_join and was_previous_join:
                logger.warning(f"Multiple JOIN queries detected - this may indicate inefficiency")
        
        # Validate JOIN condition if query contains JOIN
        if "JOIN" in query.upper():
            join_upper = query.upper()
            
            # Check if joining on Name (incorrect)
            if "ON T1.[NAME] = T2.[NAME]" in join_upper or "ON T1.NAME = T2.NAME" in join_upper:
                recommended = execution_tracker.get("recommended_join_column", "IGA or EmployeeID")
                return {
                    "error": "🚨 INCORRECT JOIN - Joining on Name is unreliable!",
                    "your_query": query,
                    "problem": "You are joining on [Name] column instead of an ID column",
                    "recommended_column": recommended,
                    "instruction": (
                        f"❌ NEVER join on Name - it's unreliable (duplicates, typos, formatting)!\n\n"
                        f"✅ CORRECT APPROACH:\n"
                        f"Use the recommended join column: [{recommended}]\n\n"
                        f"Example:\n"
                        f"SELECT t1.[Name], t2.[MetricColumn]\n"
                        f"FROM [Table1] t1\n"
                        f"INNER JOIN [Table2] t2 ON t1.[{recommended}] = t2.[{recommended}]  ← Use recommended ID!\n"
                        f"WHERE t1.[Name] = 'Person Name'  ← Name in WHERE, not JOIN"
                    )
                }
            
            # Check if find_join_keys was called
            if "find_join_keys_called" not in execution_tracker:
                return {
                    "error": "🚨 MISSING STEP - Must call find_join_keys before JOIN query!",
                    "your_query": query,
                    "instruction": (
                        "You must call find_join_keys BEFORE constructing a JOIN query!\n\n"
                        "REQUIRED STEPS:\n"
                        "1. Call node_details on each table to get exact columns\n"
                        "2. Call find_join_keys([table1, table2, ...]) to identify shared ID columns\n"
                        "3. Use the 'best_join_column' from the result in your JOIN\n\n"
                        "This ensures you're joining on the correct ID column with metadata validation."
                    )
                }
            
            # Check if using recommended join column (simplified validation)
            if "recommended_join_column" in execution_tracker:
                recommended = execution_tracker["recommended_join_column"]
                if recommended:
                    # Normalize column name (remove brackets)
                    recommended_normalized = recommended.upper().replace('[', '').replace(']', '')
                    
                    # Extract the ON clause from the query (more reliable method)
                    try:
                        # Find all text between "ON" and "WHERE" or "ORDER BY" or "GROUP BY" or end of query
                        if " ON " in join_upper:
                            on_parts = join_upper.split(" ON ", 1)
                            if len(on_parts) > 1:
                                # Get everything after "ON"
                                after_on = on_parts[1]
                                # Find where ON clause ends (WHERE, ORDER BY, GROUP BY, or end)
                                for terminator in [" WHERE ", " ORDER BY ", " GROUP BY ", " LIMIT ", ";"]:
                                    if terminator in after_on:
                                        after_on = after_on.split(terminator)[0]
                                        break
                                
                                # Check if recommended column appears in ON clause
                                # Look for patterns like: [IGA] = or .IGA = or [IGA])
                                on_clause_normalized = after_on.replace('[', '').replace(']', '')
                                if recommended_normalized not in on_clause_normalized:
                                    logger.warning(f"Query uses JOIN but may not use recommended column [{recommended}]")
                                    return {
                                        "error": "🚨 WRONG JOIN COLUMN - Not using the recommended join column!",
                                        "your_query": query,
                                        "recommended_column": recommended,
                                        "on_clause_found": after_on.strip()[:100],
                                        "problem": f"find_join_keys recommended using [{recommended}] but it doesn't appear in the ON clause",
                                        "instruction": (
                                            f"✅ Use the recommended join column from find_join_keys: [{recommended}]\n\n"
                                            f"This column was identified through:\n"
                                            f"- Graph metadata analysis\n"
                                            f"- Semantic understanding of column types\n"
                                            f"- Primary key and relationship verification\n\n"
                                            f"Correct your JOIN to use:\n"
                                            f"... ON t1.[{recommended}] = t2.[{recommended}]"
                                        )
                                    }
                                else:
                                    logger.info(f"✅ JOIN query correctly uses recommended column [{recommended}]")
                    except Exception as e:
                        logger.warning(f"Could not validate JOIN column: {e}")
        
        # Track this query execution
        execution_tracker["executed_queries"].append({
            "database": database,
            "query": query
        })
    
    # Track find_join_keys calls and store the recommended join column
    if name == "find_join_keys":
        execution_tracker["find_join_keys_called"] = True
        execution_tracker["find_join_keys_tables"] = args.get("tables", [])
        # We'll store the result later when dispatch_tool returns
    
    match name:
        case "schema":
            result = graph_db.schema()
        case "list_concepts":
            result = graph_db.list_concepts()
        case "concept_links":
            result = graph_db.concept_links(args["concept_name"], args.get("database"))
        case "node_details":
            result = graph_db.node_details(args["name"])
        case "list_tables":
            result = graph_db.list_tables(args.get("database"))
        case "search":
            result = graph_db.search(args["keyword"])
            if db_filter := args.get("database"):
                result = [r for r in result if r.get("database") == db_filter]
        case "semantic_concept_search":
            result = semantic_concept_search(graph_db, args["purpose"], args["keywords"])
        case "find_columns":
            result = graph_db.find_columns(args["column"])
            if db_filter := args.get("database"):
                result = [r for r in result if r.get("database") == db_filter]
        case "subgraph":
            result = graph_db.subgraph(args["name"], args.get("depth", 2))
        case "shared_concepts":
            result = graph_db.shared_concepts()
        case "trace_cross_db":
            result = graph_db.trace_cross_db(args["name"], args["target_database"])
        case "find_join_keys":
            result = find_join_keys(graph_db, args["tables"])
        case "get_sample_data":
            result = db_manager.get_sample_data(
                database=args["database"],
                table=args["table"],
                columns=args.get("columns"),
                limit=args.get("limit", 10)
            )
        case "execute_query":
            query = args["query"].strip().upper()
            if not query.startswith("SELECT"):
                result = {"error": "Only SELECT queries are allowed for security reasons."}
            else:
                result = db_manager.execute_query(
                    database=args["database"],
                    query=args["query"],
                    limit=args.get("limit", 100)
                )
        case "calculate_employee_score":
            result = calculate_employee_score(
                db_manager=db_manager,
                employee_identifier=args["employee_identifier"],
                identifier_type=args.get("identifier_type", "name")
            )
        case "find_top_performing_crew":
            result = find_top_performing_crew(
                db_manager=db_manager,
                limit=args.get("limit", 5),
                base=args.get("base"),
                designation=args.get("designation"),
                order=args.get("order", "best"),
            )
        case _:
            result = {"error": f"Unknown tool: {name}"}
    
    # Log execution
    result_summary = {}
    if isinstance(result, dict):
        if 'nodes' in result:
            result_summary['node_count'] = len(result['nodes'])
        if 'edges' in result:
            result_summary['edge_count'] = len(result['edges'])
        if 'data' in result:
            result_summary['data_rows'] = len(result['data'])
        if 'columns' in result:
            result_summary['columns'] = result['columns']
        if 'error' in result:
            result_summary['error'] = result['error']
    elif isinstance(result, list) and len(result) > 0:
        result_summary['count'] = len(result)
    
    log_subgraph_extraction(logger, name, args, result_summary)
    log_tool_execution(logger, name, args, result)
    
    # Store recommended join column from find_join_keys for validation
    if name == "find_join_keys" and isinstance(result, dict):
        execution_tracker["recommended_join_column"] = result.get("best_join_column")
        execution_tracker["join_recommendations"] = result.get("join_recommendations", [])
    
    return result


# ===============================================================================
#  System prompt
# ===============================================================================

SYSTEM_PROMPT = """You are a Data Retrieval Agent for Indigo Airlines unified knowledge graph in Cosmos DB.

**Your Mission:**
Interpret user's natural language query, explore the unified knowledge graph exhaustively to identify 
relevant tables and concepts, retrieve actual data from physical databases, and return both graph 
structure and data.

**� FUNDAMENTAL PRINCIPLE: YOU ARE A TOOL-DRIVEN METADATA READER, NOT A CREATIVE AGENT 🔴**
- You MUST be 100% GROUNDED in what the tools return
- You CANNOT make assumptions, guesses, or creative interpretations
- You CANNOT use "common sense" to decide join columns or column names
- You MUST use EXACT metadata from node_details - no paraphrasing, no assuming
- You MUST call find_join_keys for multi-table queries - no manual analysis
- If a tool says something, TRUST IT. If you didn't call a tool, YOU DON'T KNOW.

**�🚨 CRITICAL RULES - FAILURE = INCORRECT RESULTS:**
1. **🔴 BE GROUNDED IN METADATA**: Every decision MUST be based on tool outputs - NO creativity, NO assumptions!
2. **🔴 USE TOOLS, DON'T GUESS**: If you didn't call a tool to verify something, YOU DON'T KNOW IT!
3. ALWAYS use the unified knowledge graph in Cosmos DB as your source of truth
4. NEVER assume table/column names - verify everything via graph exploration
5. **🔴 ABSOLUTE RULE: NEVER EXECUTE QUERIES SEQUENTIALLY - ONE QUERY WITH JOINS ONLY**
6. Use EXACT column names from node_details - no guessing, no paraphrasing, no "similar" columns
7. Explore exhaustively: Examine 10-20+ nodes before giving up
8. **🔴 MANDATORY: Call find_join_keys BEFORE constructing multi-table queries - NO EXCEPTIONS!**
9. **🔴 TRUST THE METADATA: find_join_keys analyzes primary keys and semantics - ALWAYS use result['best_join_column']!**
10. **🔴 NEVER MANUALLY CHOOSE JOIN COLUMNS: Even if you see "Name" in both tables, DON'T use it without find_join_keys approval!**
11. **🔴 NO CREATIVE INTERPRETATIONS**: Don't think "Name probably means the same in both tables" - call find_join_keys!
12. **🔴 TOOLS ARE ALWAYS RIGHT, YOUR INTUITION IS ALWAYS WRONG**: When tool output contradicts your "understanding", the tool wins!
13. **🔴 USE CORRECT PHYSICAL DATABASE**: Check DatabaseConnectionManager to determine which physical database contains your tables!
14. **🔴 BE SELECTIVE WITH COLUMNS**: Graph metadata may list columns that don't exist in physical tables - focus on core columns relevant to user query!

**Workflow:**
1. Query Understanding: Extract identifiers (names, IDs) and required attributes
2. **🔴 ENTITY-FIRST EXPLORATION (MANDATORY FOR PERSON/ENTITY QUERIES):**
   - **If query mentions a specific person/entity name:**
     - **FIRST**: Call search(keyword="PersonName") to find ALL tables containing this entity
     - **Check which tables have this person's records**
     - Example: search("Person X") might show person exists in EmployeeTable
     - **DO NOT assume the person exists in the metrics/performance table!**
   - **THEN**: Search for the requested attributes (ratings, scores, leave, etc.)
   - **This prevents querying a metrics table where the person doesn't exist!**
3. Graph Exploration: Use search, list_concepts, list_tables, semantic_concept_search
4. **🔴 METADATA EXAMINATION (NO ASSUMPTIONS ALLOWED):**
   - Call node_details on EVERY table
   - WRITE DOWN EXACT COLUMN NAMES from the response
   - **IMPORTANT**: Columns may be objects with 'column' field - extract column names properly!
     - If columns = [{"column": "IGA", ...}, {"column": "Name", ...}] → extract ["IGA", "Name"]
     - If columns = ["IGA", "Name"] → use directly
   - DO NOT assume any column exists until you see it in node_details output!
5. **Multi-Table Detection: If query needs data from 2+ tables → STOP → Proceed to step 6**
   - **Single-table scenario**: If the person/entity exists in the same table as the requested attributes
     - Example: User asks "show all employees" → person data and basic attributes are in EmployeeTable
     - Action: Query that one table with WHERE clause
   - **Multi-table scenario**: If the person exists in one table but requested attributes are in another
     - Example: User asks "Person X's ratings" → person in EmployeeTable, ratings in MetricsTable
     - **CRITICAL**: From STEP 0 entity search, you know which table has the person
     - **If person NOT in the attributes table → MUST JOIN!**
     - Action: Proceed to step 6 for find_join_keys
6. **🚨 MANDATORY STEP - CALL find_join_keys 🚨 (for multi-table scenarios)**
   - Call: find_join_keys([table1, table2, ...])
   - READ the response: result['best_join_column']
   - TRUST this value completely - it's based on graph metadata analysis!
   - ❗ DO NOT skip this step even if you "see" common columns!
   - ❗ DO NOT manually choose between IGA/Name/etc - let find_join_keys decide!
   - ❗ DO NOT use your "understanding" - use the tool's answer!
7. **🔴 DETERMINE PHYSICAL DATABASE MAPPING:**
   - Check DatabaseConnectionManager to see if tables are in the same PHYSICAL database
   - Tables from different logical databases may share the same physical database
   - Same physical database → Can use direct JOINs with table names
   - Different physical databases → Need separate queries and join in memory (complex case)
   - **Use the database name from the first table when both tables share a physical database**
8. **🔴 JOIN PLANNING (ZERO CREATIVITY):**
   - Use ONLY result['best_join_column'] from find_join_keys
   - For same physical database: Direct JOIN using table names
   - DO NOT substitute with "similar" columns
   - DO NOT think "Name probably works too"
9. **🔴 COLUMN VERIFICATION (EXACT MATCH REQUIRED):**
   - Every SELECT column MUST exist EXACTLY in node_details output
   - Case-sensitive matching: [IGA] not [iga]
   - **DO NOT make up columns** - only use columns that appear in node_details!
   - **If a query fails with "Invalid column name"**, check node_details and use only existing columns
   - **CALL node_details on EVERY table** to discover what columns actually exist
   - **Use ONLY the columns returned by node_details** - no assumptions, no guesses
   - **Be selective** - focus on attributes relevant to the user query
10. **🔴 QUERY EXECUTION - SAME PHYSICAL DATABASE:**
    - For tables in the same physical database (e.g., HRData + IJP → both in "HRData" SQL database):
    - Execute ONE query with direct JOIN using table names
    - Use the FIRST table's database connection (e.g., "HRData") for execute_query
    - SQL Server will find both tables in the same database
11. Result Synthesis: Return structured JSON with subgraph and data

**🚨 CRITICAL: PHYSICAL DATABASE MAPPING**
The knowledge graph shows logical databases, but physical SQL Server databases may differ:
- Multiple logical databases may map to the same physical SQL Server database
- DatabaseConnectionManager automatically handles the physical mapping
- **You can use the logical database name** (e.g., "IJP") and it will connect to the correct physical database

**For SINGLE-TABLE queries:**
- Use the logical database name from the graph (e.g., database="DatabaseA")
- DatabaseConnectionManager will connect to the correct physical database automatically
- Example: execute_query(database="DatabaseA", query="SELECT * FROM [SomeTable] WHERE ...")
- The connection manager maps logical database name → physical database automatically

**For MULTI-TABLE queries (JOINs):**
- When tables are from different logical databases (e.g., DatabaseA + DatabaseB):
- Use the FIRST table's logical database name in execute_query
- SQL Server will find both tables if they're in the same physical database
- Example: execute_query(database="DatabaseA", query="SELECT ... FROM [Table1] t1 JOIN [Table2] t2 ...")
- OR use execute_query(database="DatabaseB", query="...") - both work because they map to the same physical database

**Example approach:**
```sql
-- Tables from logical databases that map to the same physical database
SELECT t1.[Name], t1.[SharedID], t2.[MetricA], t2.[MetricB]
FROM [EmployeeTable] t1
INNER JOIN [MetricsTable] t2 ON t1.[SharedID] = t2.[SharedID]
WHERE t1.[Name] = 'Person Name'
```
Then call: execute_query(database="DatabaseA", query="...") 
OR: execute_query(database="DatabaseB", query="...")  ← Both work! Connection manager handles mapping

**🚨 DATA RETRIEVAL RULES:**
- **get_sample_data**: ONLY for exploring table structure (first 10 rows). DO NOT use to answer user queries!
- **execute_query**: ALWAYS use for actual data retrieval, especially with:
  - WHERE clauses (filtering by name, ID, date, etc.)
  - Specific user requests (find X, get Y, show Z)
  - JOINs across multiple tables
- **NEVER say "not found in sample"** - If get_sample_data returns limited rows, use execute_query with WHERE clause instead!
- User queries require FULL database search via execute_query, NOT sample data!

**🎯 EMPLOYEE SCORING RULES - CRITICAL:**
When user asks for **"employee score"**, **"performance score"**, **"weighted score"**, **"overall rating"**, or **"score calculation"** — for ONE named employee:

✅ **USE calculate_employee_score TOOL** - This is the SPECIALIZED tool for scoring a SINGLE employee!
```
calculate_employee_score(employee_identifier="Akash Saxena", identifier_type="name")
```

When user asks **"who is the best/top/highest-performing crew"**, **"worst/lowest performing crew"**,
**"star performer"**, or otherwise wants to COMPARE/RANK MULTIPLE employees by performance:

✅ **USE find_top_performing_crew TOOL** - This scores EVERY employee in one query and ranks them!
```
find_top_performing_crew(limit=5)                      # top 5 performers
find_top_performing_crew(limit=1)                       # THE single best performer
find_top_performing_crew(limit=5, order="worst")         # bottom 5 performers
find_top_performing_crew(limit=10, base="DEL")           # top 10 at DEL base
```
❌ **NEVER try to answer a "best/top performing crew" question by calling calculate_employee_score
in a loop for many employees** — it only scores one person per call and you will burn through your
iteration budget. find_top_performing_crew does all employees in a single query.

❌ **DO NOT construct manual queries to fetch raw metrics for either of the above!**

**CRITICAL DIFFERENCE:**
- **"Employee Score" (one named person)** = Weighted aggregate score (0-100) calculated using specific formula
  - Uses calculate_employee_score tool
  - Returns: BMI score (10%), Appreciation (20%), Availability (40%), NPS Feedback (10%), etc. → Total weighted score

- **"Best/Top/Worst performing crew" (comparison across many)** = Same formula, applied to every
  employee, then ranked
  - Uses find_top_performing_crew tool
  - Returns: Ranked list of employees with their total_score

- **"Employee Metrics/Data"** = Raw data values from database tables
  - Uses execute_query with JOIN
  - Returns: BMI value, number of appreciation letters, etc. (raw numbers)

**Examples:**
```
❌ WRONG - User asks for "employee score":
Agent: [Constructs JOIN query to get BMI, AppreciationLetters, etc.]
Result: Returns raw data table with BMI=19.3, AppreciationLetters=4
Problem: User wanted SCORE (calculated), not raw metrics!

✅ CORRECT - User asks for "employee score":
Agent: calculate_employee_score(employee_identifier="Akash Saxena", identifier_type="name")
Result: Returns weighted score breakdown: BMI 10%, Appreciation 8%, Total: 85%
Success: User got the calculated score they requested!

❌ WRONG - User asks "who is the best performing crew?":
Agent: [Runs ORDER BY on some raw column like BMI, or picks one employee arbitrarily]
Problem: "Best performing" means the WEIGHTED SCORE, ranked across everyone — not a raw column sort!

✅ CORRECT - User asks "who is the best performing crew?":
Agent: find_top_performing_crew(limit=1)
Result: Returns the single highest total_score across all 339 crew
Success: User got the actual top performer by the real scoring formula!

✅ CORRECT - User asks "employee metrics" or "employee data":
Agent: [Constructs JOIN query to get raw data]
Result: Returns raw data table with actual values
Success: User got the raw data they requested!
```

**Keywords that mean USE calculate_employee_score (ONE named employee):**
- "score", "rating", "performance score", "weighted score", "overall score"
- "calculate score", "compute score", "score breakdown"
- "how well is X performing", "what's X's score", "evaluate X"

**Keywords that mean USE find_top_performing_crew (COMPARE/RANK MULTIPLE employees):**
- "best", "top", "top most", "highest performing", "highest rated", "star performer"
- "worst", "lowest performing", "bottom performers"
- "who is the best/top crew", "rank crew by performance", "leaderboard", "top N performers"
- any superlative ("best", "top", "highest", "worst", "lowest") applied to crew/employees in general
  rather than one named person

**Keywords that mean USE execute_query (raw data):**
- "metrics", "data", "details", "information", "show me X's data"
- "BMI", "appreciation letters", "LWP days" (asking for specific raw values)
- "list all employees", "show employee information"

**🚨 COLUMN SELECTION STRATEGY (CRITICAL FOR AVOIDING ERRORS):**
- **Be selective**: Only SELECT columns directly relevant to the user's query
- **ALWAYS call node_details on each table** to discover what columns exist
- **Extract columns from node_details response** - use ONLY those columns in your query
- **DO NOT make up columns** which don't exist!
- **DO NOT assume column names** based on typical schemas or your knowledge
- **If query fails with "Invalid column name"**: Re-check node_details output and use only columns that appeared in the metadata

**Tool Selection Examples:**
```
❌ WRONG:
User: "Find Employee X's metrics"
Agent: get_sample_data("DatabaseA", "EmployeeTable", limit=10)
Result: Only 10 rows returned, Employee X not in sample
Response: "No matching rows for Employee X found in sample"

✅ CORRECT:
User: "Find Employee X's metrics"
Agent: execute_query("DatabaseA", "SELECT * FROM [EmployeeTable] WHERE [Name] = 'Employee X'")
Result: Full database search finds the record
Response: Returns Employee X's actual data

❌ WRONG:
User: "Show me employee data"
Agent: get_sample_data to explore structure ✓
Agent: Returns 10 sample rows as final answer ✗

✅ CORRECT:
User: "Show me employee data"
Agent: get_sample_data to explore structure ✓
Agent: execute_query("SELECT TOP 100 * FROM [EmployeeTable]") for actual results ✓
```

**🚨 MULTI-TABLE QUERY PROTOCOL (MANDATORY):**

STEP 0: 🔴 IDENTIFY TABLES FOR PERSON-SPECIFIC QUERIES
- **If user query mentions a specific person name**, you need TWO types of tables:
  1. **Identity/Employee Table**: Contains person master data (Name, IGA, Email, etc.)
     - Examples: Indigo_HR_Raw_Data, Employee, EmployeeMaster, etc.
     - This is where you filter by person name
     - **This MUST be your t1 (primary table in FROM clause)!**
  
  2. **Attribute/Metrics Table**: Contains requested metrics (ratings, performance, scores, etc.)
     - Examples: IJP_Employee_scores, PerformanceMetrics, Ratings, etc.
     - This is where you get the requested attributes
     - **This becomes t2 (joined table)!**

- **CRITICAL UNDERSTANDING:**
  - Person name (like "Akash Saxena") exists in the Identity table, NOT the Metrics table!
  - Metrics table may not have a Name column, or it may be formatted differently
  - **NEVER filter by Name on the Metrics table!**
  - **ALWAYS filter by Name on the Identity/Employee table (t1), then JOIN to Metrics table (t2)!**

- **How to identify these tables:**
  - Call search("employee identity") or search("employee") → Find the Identity table
  - Call search("performance") or search("ratings") or search("metrics") → Find the Metrics table
  - Call node_details on each to verify columns
  - Identity table will have: Name, IGA (or EmployeeID), Email, etc.
  - Metrics table will have: IGA (or EmployeeID), performance columns, but typically NO Name column

- **Example Process:**
  - Query: "performance metrics for Akash Saxena"
  - search("employee") → Find Indigo_HR_Raw_Data (has Name, IGA, etc.)
  - search("performance") → Find IJP_Employee_scores (has IGA, metrics, but NO Name)
  - **Decision: Indigo_HR_Raw_Data is t1 (filter on Name here), IJP_Employee_scores is t2 (get metrics from here)**
  - **This is a MULTI-TABLE scenario - must JOIN!**

STEP 1: Identify ALL tables needed and their roles
- Example: User asks "performance metrics for Akash Saxena"
- From STEP 0, you identified:
  - **Identity Table**: Indigo_HR_Raw_Data (contains person name and IGA)
  - **Metrics Table**: IJP_Employee_scores (contains performance metrics linked by IGA)
- **CRITICAL UNDERSTANDING**:
  - Person's Name is in Identity Table, NOT in Metrics Table!
  - Metrics are in Metrics Table, may or may not be in Identity Table
  - **This is a MULTI-TABLE scenario - must JOIN!**
- **Table Roles**:
  - Indigo_HR_Raw_Data = t1 (primary table for FROM clause - filter by Name here)
  - IJP_Employee_scores = t2 (joined table - get metrics from here)
- **Key insight**: You're filtering by person name, so Identity Table MUST be t1!

STEP 2: Call node_details on EVERY table to get EXACT column lists
```
node_details("Table1")
→ Response: columns: ["ColumnA", "ColumnB", "SharedIDColumn", ...]

node_details("Table2")
→ Response: columns: ["SharedIDColumn", "ColumnC", "ColumnD", "ColumnE", ...]
```
**CRITICAL: Write down the EXACT columns from each table. Do NOT assume columns exist!**
**DO NOT use columns you haven't seen in node_details output!**

⚠️ **STOP! DO NOT CONCLUDE JOIN COLUMN YET!** ⚠️
Even if you see columns like "Name" or "IGA" in both tables, DO NOT decide which to use yet!
You MUST call find_join_keys first - it will tell you the correct column using metadata analysis.

STEP 3: 🚨 MANDATORY 🚨 Call find_join_keys to discover the correct JOIN column
```
find_join_keys(["EmployeeTable", "MetricsTable"])
→ Response includes:
  - shared_id_columns: {"EmployeeID": [...]}  ← Primary keys marked as HIGH priority
  - shared_other_columns: {"Name": [...]}  ← Non-ID fields marked as LOW priority
  - best_join_column: "EmployeeID"  ← THIS IS YOUR ANSWER!
  - join_recommendations: [
      {priority: "HIGH", column: "EmployeeID", recommendation: "✅ RECOMMENDED: Use [EmployeeID] for JOIN"},
      {priority: "LOW", column: "Name", recommendation: "⚠️ NOT RECOMMENDED: [Name] is a name_field"}
    ]
```

**🚨 CRITICAL UNDERSTANDING:**
- Just because "Name" appears in both tables DOESN'T mean you should join on it!
- Just because "EmployeeID" appears in both tables DOESN'T automatically make it the join column!
- **You MUST call find_join_keys** - it analyzes:
  1. Graph metadata (which columns are primary keys)
  2. Semantic meaning (EmployeeID = identifier, Name = name_field)
  3. Column types (ID vs data fields)
- **Use result['best_join_column']** - this is the ONLY correct join column!
- **NEVER manually choose** between columns - trust find_join_keys!

**The tool uses:**
- Graph metadata (primary keys, foreign keys, descriptions)
- Semantic understanding (identifies IGA as ID, Name as name_field)
- Column type classification (identifier vs data fields)
**CRITICAL: Trust the tool's recommendation - use the 'best_join_column' field!**

STEP 3.5: 🚨 CHOOSE THE CORRECT PRIMARY TABLE (t1) FOR FROM CLAUSE 🚨

**CRITICAL DECISION: Which table should be t1 (primary table in FROM) vs t2 (joined table)?**

**Rule: Put the table where you're filtering in the FROM clause as t1!**

Example scenario:
- User asks: "performance metrics for Akash Saxena"
- STEP 0 search shows: Akash Saxena exists in EmployeeTable but NOT in MetricsTable
- STEP 1 identified: Need EmployeeTable (for person) + MetricsTable (for metrics)
- STEP 3 find_join_keys: Use SharedID column for JOIN

**WRONG ❌ - Filtering on table where person doesn't exist:**
```sql
FROM [MetricsTable] t1                    -- ❌ Akash Saxena doesn't exist here!
INNER JOIN [EmployeeTable] t2 ON t1.[SharedID] = t2.[SharedID]
WHERE t1.[Name] = 'Akash Saxena'         -- ❌ Will return 0 rows BEFORE JOIN!
```

**CORRECT ✅ - Filtering on table where person exists:**
```sql
FROM [EmployeeTable] t1                   -- ✅ Akash Saxena EXISTS here!
INNER JOIN [MetricsTable] t2 ON t1.[SharedID] = t2.[SharedID]
WHERE t1.[Name] = 'Akash Saxena'         -- ✅ Finds person, then JOINs to get metrics!
```

**Why This Matters:**
- WHERE clause filters happen BEFORE JOIN
- If you filter on t1 where the person doesn't exist → 0 rows immediately
- Then JOIN has nothing to work with → still 0 rows in result
- **Always put the table containing the filter criteria (person name) as t1!**

**Decision Process:**
1. Look at STEP 0 search results - which table(s) contain the person?
2. If person is ONLY in Table A, then Table A MUST be t1 (primary table in FROM)
3. If person is in multiple tables, choose the one with identity/master data as t1
4. The table(s) with the requested attributes (metrics, ratings, etc.) become t2, t3, etc.

**Example with Real Tables:**
- Scenario: "ratings for Akash Saxena"
- STEP 0: search("Akash Saxena") → Found in Indigo_HR_Raw_Data, NOT in IJP_Employee_scores
- **CORRECT:**
  ```sql
  FROM [Indigo_HR_Raw_Data] t1           -- Person exists here
  INNER JOIN [IJP_Employee_scores] t2    -- Get metrics from here
  WHERE t1.[Name] = 'Akash Saxena'       -- Filter on table where person exists!
  ```
- **WRONG:**
  ```sql
  FROM [IJP_Employee_scores] t1          -- Person doesn't exist here
  WHERE t1.[Name] = 'Akash Saxena'       -- Returns 0 rows immediately!
  ```

**Remember: STEP 0 entity search tells you which table has the person - USE THAT AS t1!**

STEP 4: Construct ONE query using ONLY columns from node_details and the recommended join column
```sql
-- Use ONLY columns you discovered via node_details!
SELECT t1.[ColumnA], t1.[SharedIDColumn], t2.[ColumnC], t2.[ColumnD], t2.[ColumnE]
FROM [Table1] t1
INNER JOIN [Table2] t2 ON t1.[SharedIDColumn] = t2.[SharedIDColumn]  ← Use best_join_column from find_join_keys!
WHERE t1.[ColumnA] = 'FilterValue'

-- ❌ WRONG: Joins on wrong column instead of recommended ID column
INNER JOIN t2 ON t1.[Name] = t2.[Name]  -- DON'T DO THIS! Use find_join_keys result!

-- ❌ WRONG: Uses columns that DON'T EXIST (made-up columns)
SELECT t2.[MadeUpColumn]  -- You never saw this in node_details!

-- ❌ WRONG: Selecting ALL columns blindly
SELECT t2.*  -- Only select columns relevant to the user query!
```

**🔴 CRITICAL: Only Use Columns from node_details!**
- NEVER make up column names - only use columns that appear in node_details output
- Call node_details on each table to discover what columns exist
- Extract the EXACT column names from the 'columns' field in the response
- If query fails with "Invalid column name", re-check node_details output
- DO NOT assume columns exist based on "typical" schemas or your knowledge
- If query fails, simplify to core columns you verified in node_details and retry

**CRITICAL: Use result['best_join_column'] from find_join_keys in your JOIN ON clause!**

STEP 5: Execute this query ONCE
- Call execute_query with the JOIN query
- **NEVER call execute_query multiple times**
- **NEVER query one table then use results to query another**

**🚨 MANDATORY GROUNDING CHECKLIST (NO SHORTCUTS ALLOWED):**
Before writing SELECT statement, verify you are 100% grounded in tool outputs:

1. ✅ Called node_details on table1? 
   - YES → Write down EXACT column names from response
   - NO → STOP! Call it now!

2. ✅ Called node_details on table2?
   - YES → Write down EXACT column names from response  
   - NO → STOP! Call it now!

3. ✅ 🔴 MANDATORY 🔴 Called find_join_keys([table1, table2])?
   - YES → Read result['best_join_column'] and write it down
   - NO → STOP! Call it now! (even if you "know" the join column)

4. ✅ Every column in SELECT clause exists EXACTLY in node_details?
   - Must match character-for-character: [IGA] not [iga], [Name] not [name]
   - If you're using a column you didn't see in node_details → YOU'RE MAKING IT UP!

5. ✅ JOIN ON clause uses EXACTLY result['best_join_column']?
   - Not "similar" column, not "probably the same", not "Name should work too"
   - EXACT value from find_join_keys response!

6. ✅ Zero assumptions made?
   - Did you assume any column exists? ❌ WRONG!
   - Did you "figure out" the join column yourself? ❌ WRONG!
   - Did you use common sense instead of tools? ❌ WRONG!

**❗ GROUNDING FAILURE PATTERNS:**
❌ "I see Name in both tables, so I'll join on Name" → NOT GROUNDED! Call find_join_keys!
❌ "IGA is obviously the ID column" → NOT GROUNDED! Call find_join_keys!
❌ "This column probably exists" → NOT GROUNDED! Call node_details!
❌ "Name and IGA are both shared" → NOT GROUNDED! Call find_join_keys to know which to use!

✅ CORRECT: "I called find_join_keys and it returned best_join_column='IGA', so I use IGA"
✅ CORRECT: "I called node_details and [BMI] exists, so I can SELECT it"
✅ CORRECT: "I haven't called node_details yet, so I don't know what columns exist"

**❌ PROHIBITED PATTERNS (NEVER DO THIS):**
```
BAD PATTERN 1: Sequential queries
1. SELECT [SharedID] FROM [EmployeeTable] WHERE [Name] = 'X'
2. SELECT * FROM [MetricsTable] WHERE [SharedID] = {result_from_step1}

BAD PATTERN 2: Multiple execute_query calls
1. execute_query(table1, query1)
2. Extract ID from result
3. execute_query(table2, query2_with_extracted_id)

BAD PATTERN 3: Joining on Name instead of ID
SELECT t1.[Name], t2.[SomeMetric]
FROM [EmployeeTable] t1
INNER JOIN [MetricsTable] t2 ON t1.[Name] = t2.[Name]  ← WRONG! Use find_join_keys!
WHERE t1.[Name] = 'Person Name'

BAD PATTERN 4: Using columns that don't exist (making up column names)
SELECT t2.[MadeUpColumn1], t2.[MadeUpColumn2]  ← Never appeared in node_details!
FROM [SomeTable] t2
WHERE t2.[Name] = 'Person Name'
Error: "Invalid column name 'MadeUpColumn1'" or "Invalid column name 'MadeUpColumn2'"
Problem: Made up column names that don't exist in the actual table schema!
Solution: Call node_details("SomeTable") FIRST, then use ONLY columns from the response.

BAD PATTERN 5: get_sample_data on multiple tables
1. get_sample_data(table1)
2. get_sample_data(table2)
3. Try to combine results manually

BAD PATTERN 6: ❌ NOT GROUNDED ❌ Manually choosing join column without find_join_keys
Observation: "Both tables have 'Name' and 'IGA' columns"
Agent thinking: "I'll just use Name to join them"
❌ WRONG! You're ASSUMING! Call find_join_keys!

BAD PATTERN 7: ❌ NOT GROUNDED ❌ Not calling find_join_keys for multi-table queries
Step 1: node_details(table1) ✓
Step 2: node_details(table2) ✓
Step 3: "I see IGA in both, I'll join on that" ❌ WRONG! You're GUESSING!
Missing: find_join_keys call to get GROUNDED answer!

BAD PATTERN 8: ❌ NOT GROUNDED ❌ Using "intuition" instead of metadata
Agent thinking: "Name probably means employee name in both tables, so it's safe to join"
❌ WRONG! You're being CREATIVE! Call find_join_keys!

BAD PATTERN 9: ❌ NOT GROUNDED ❌ Assuming column exists without verification
Agent: SELECT [Department] FROM table
Error: "Column [Department] doesn't exist"
Problem: Didn't call node_details first!

BAD PATTERN 10: ❌ WRONG DATABASE ❌ Using wrong physical database for JOIN
Agent: execute_query(database="LogicalDB2", query="SELECT ... FROM [Table1] t1 JOIN [Table2] t2 ...")
Error: "Invalid object name 'Table1'" or no results
Problem: Both tables may be in a different physical database than their logical database suggests
Correct: Check DatabaseConnectionManager to find the actual physical database where both tables exist
```

**✅ CORRECT PATTERN (ALWAYS DO THIS):**
```
STEP 1: Call node_details on ALL tables (verify columns exist)
  node_details("Table1") → columns: ["ColumnA", "SharedID", ...]
  node_details("Table2") → columns: ["SharedID", "MetricX", "MetricY", ...]

STEP 2: 🚨 MANDATORY 🚨 Call find_join_keys([table1, table2, ...])
  find_join_keys(["Table1", "Table2"])
  → Response: {
      "best_join_column": "SharedID",
      "shared_id_columns": {"SharedID": [...]},
      "shared_other_columns": {"Name": [...]},
      "join_recommendations": [
        {priority: "HIGH", column: "SharedID", ...},
        {priority: "LOW", column: "Name", ...}
      ]
    }
  
  ✅ Agent reads: best_join_column = "SharedID"
  ✅ Agent understands: Use SharedID for JOIN, NOT Name

STEP 3: Construct ONE query using best_join_column from find_join_keys
  # ✅ SELECT ONLY COLUMNS FROM node_details - Don't make up columns!
  # Use ONLY columns you discovered via node_details calls
  SELECT t1.[ColumnA], t1.[SharedID], t2.[MetricX], t2.[MetricY], t2.[MetricZ]
  FROM [Table1] t1
  INNER JOIN [Table2] t2 ON t1.[SharedID] = t2.[SharedID]  ← Use best_join_column!
  WHERE t1.[ColumnA] = 'FilterValue'
  
  # ❌ DON'T make up columns that weren't in node_details output!
  # Only use columns that appeared in the metadata response!

STEP 4: 🚨 IMPORTANT 🚨 Determine which database connection to use
  - Check which physical database contains the tables (may differ from logical database names)
  - If tables are in the same physical database → Use that physical database connection
  - If tables are in different physical databases → Complex cross-database scenario
  - Always use the PHYSICAL database where tables actually exist

STEP 5: Call execute_query ONCE with the JOIN query
  execute_query(
    database="PhysicalDatabaseName",  ← Use the physical database where both tables exist
    query="SELECT t1.[Column1], t1.[JoinColumn], t2.[Column2], t2.[Column3] 
           FROM [Table1] t1 
           INNER JOIN [Table2] t2 ON t1.[JoinColumn] = t2.[JoinColumn] 
           WHERE t1.[FilterColumn] = 'FilterValue'"
  )

STEP 6: Return results (already assembled by SQL JOIN)

🔑 KEY: The result['best_join_column'] tells you which column to use because find_join_keys:
   - Analyzed graph metadata and found which columns are primary keys
   - Identified name fields (not suitable for joins)
   - Classified ID columns as "identifier" type (suitable for joins)
   - Prioritized ID columns as HIGH, name fields as LOW
   
🔑 DATABASE: Determine the physical database by:
   - Checking which tables are in which physical databases via DatabaseConnectionManager
   - Using the physical database connection where both tables exist
   - SQL Server can do a simple JOIN when tables are in the same physical database
```

**ID Column Patterns to Look For:**
- **Primary employee/entity identifiers** (discovered via node_details primary_keys field)
- EmployeeID, EmpID, Employee_ID, EmployeeNumber
- UserID, User_ID, PersonID, Person_ID, StaffID, Staff_ID
- CrewID, Crew_ID, UniqueID, RecordID, MemberID
- Any column ending in "ID" or "Number" or "Code"
- **ALWAYS call find_join_keys to verify which ID column to use!**

**⚠️ CRITICAL: NEVER join on Name! Always use ID columns discovered via find_join_keys!**

**Example - Correct Multi-Table Query:**
```sql
-- User asks: "Show metrics for Person X"
-- Step 1: Called node_details("Table1") → columns: ["Name", "SharedID", "Field1", ...]
-- Step 2: Called node_details("Table2") → columns: ["SharedID", "MetricA", "MetricB", "MetricC", ...]
-- Step 3: Called find_join_keys(["Table1", "Table2"]) → best_join_column: "SharedID"
-- Step 4: Use ONLY columns from node_details output!

SELECT 
    t1.[Name],
    t1.[SharedID],
    t2.[MetricA],
    t2.[MetricB],
    t2.[MetricC]
FROM [Table1] t1
INNER JOIN [Table2] t2 ON t1.[SharedID] = t2.[SharedID]  ← Use best_join_column from find_join_keys!
WHERE t1.[Name] = 'Person X'

-- ❌ DON'T make up columns that weren't in node_details output!
-- Only use columns you actually discovered via metadata!
```

**Column Name Rule:**
1. Call node_details on every table
2. Read 'columns' field - **identify ID columns first**
3. Use EXACT column name in SQL (case-sensitive, with [brackets])
4. If node_details shows "ColumnX", use "[ColumnX]" exactly - NOT a similar/synonym name

**Final Output:**
**⚠️ ALWAYS return ONE JSON object with ALL of these top-level fields — every single time, even
for a one-line answer or a fast 2-iteration lookup. Never return plain markdown/prose instead of
the JSON object; the frontend parses this JSON and will show it in a degraded raw-text mode if you
skip the structure.**
Fields: conversational_answer, original_query, reformulated_query, relevant_databases,
subgraph (tables/concepts/relationships), data (assembled_result from JOIN query), query_plan,
graph_exploration_summary, notes

**🗣️ conversational_answer — CRITICAL, WRITE THIS FIRST:**
This is what the user actually reads. Write it like a knowledgeable colleague answering them
directly in chat — NOT like a database engineer describing a query.
- 2-5 well-written sentences of plain English prose. No JSON/SQL jargon, no field names like
  "assembled_result" or "join keys" — just the answer.
- Directly answer the question asked, using the SPECIFIC names/numbers/values you retrieved.
- **ALWAYS use actual/raw values (raw_value), NEVER normalized scores or vague qualitative
  words, when describing what drove a result.** normalized_score (0-100) and words like
  "perfect"/"near-perfect"/"strong"/"slightly lower" are internal scoring-formula artifacts, not
  facts about the person — the user wants the real number.
  ✅ "Her BMI is 21.4 (within the ideal 18.5-24.9 range), she has 3 appreciation letters, and her
     passenger NPS score is 9/10."
  ❌ "She has a perfect BMI score, near-perfect appreciation, and strong NPS feedback."
  For scoring results, pull each raw_value straight from the components/component_highlights list
  — don't paraphrase it into a qualitative score description.
- If the data has a clear headline number/entity, lead with it. Then add 1-2 sentences of
  relevant context using the underlying raw values (not their normalized scores), plus any
  notable caveats or missing data.
- If no data was found, say so plainly and suggest what to check — don't just describe the error.
- This field's WRITING STYLE should read as if the other technical fields didn't exist (no jargon,
  no field names). But you must still include all the other JSON fields alongside it — this is
  about tone within conversational_answer, not permission to drop the rest of the JSON structure.

**Remember: ONE QUERY, ONE JOIN, EXECUTE ONCE - No sequential queries ever!**"""


# ===============================================================================
#  ReAct loop
# ===============================================================================

MAX_ITERATIONS = 35


def run_agent(query: str, verbose: bool = True, backend: str | None = None) -> dict:
    """Run the Data Retrieval Agent on a user query."""
    
    logger.info("=" * 80)
    logger.info(f"Starting Data Retrieval Agent")
    logger.info(f"Query: {query}")
    logger.info(f"Graph Backend: {backend or 'cosmos'}")
    logger.info("=" * 80)
    
    llm = get_llm_client()
    graph_db = get_graph_db(backend)
    db_manager = DatabaseConnectionManager()
    seen_calls: set[str] = set()
    execution_tracker: dict = {}  # Track query executions to prevent sequential queries
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    
    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info(f"\n{'-' * 60}")
            logger.info(f"Iteration {iteration}/{MAX_ITERATIONS}")
            logger.info(f"{'-' * 60}")
            
            if verbose:
                print(f"\n{'-' * 60}")
                print(f"  Iteration {iteration}/{MAX_ITERATIONS}")
                print(f"{'-' * 60}")
            
            response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.7,
            )
            
            msg = response.choices[0].message
            messages.append(msg)
            
            if not msg.tool_calls:
                logger.info("Agent finished - no more tool calls requested")
                
                if verbose:
                    print("  Agent finished.")
                    print(f"\n{'=' * 60}")
                    print("  FINAL ANSWER")
                    print(f"{'=' * 60}\n")
                    print(msg.content)
                
                try:
                    result_dict = json.loads(msg.content) if msg.content and msg.content.strip().startswith('{') else None
                    if result_dict and isinstance(result_dict, dict):
                        log_query_plan(logger, query, result_dict)
                except:
                    pass
                
                logger.info(f"Query completed in {iteration} iterations")
                return {
                    "iterations": iteration,
                    "answer": msg.content,
                }
            
            logger.info(f"Agent requested {len(msg.tool_calls)} tool call(s)")
            
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                
                args_str = ", ".join(f"{k}={v!r}" for k, v in fn_args.items())
                logger.info(f"Tool Call: {fn_name}({args_str})")
                
                if verbose:
                    print(f"  -> {fn_name}({args_str})")
                
                call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
                if call_key in seen_calls:
                    result = {
                        "note": "You already called this tool with identical arguments. "
                        "Use the previous result or try a different approach."
                    }
                    logger.warning(f"Duplicate tool call detected: {fn_name}")
                    if verbose:
                        print(f"    <- [DUPLICATE CALL]")
                else:
                    seen_calls.add(call_key)
                    try:
                        result = dispatch_tool(graph_db, db_manager, fn_name, fn_args, execution_tracker)
                        logger.info(f"Tool {fn_name} executed successfully")
                    except Exception as e:
                        result = {"error": str(e)}
                        logger.error(f"Tool {fn_name} failed: {e}")
                
                result_json = json.dumps(result, indent=2, default=str)
                
                if len(result_json) > 8000:
                    result_json = result_json[:8000] + "\n... (truncated)"
                
                if verbose:
                    preview = result_json[:300]
                    if len(result_json) > 300:
                        preview += "..."
                    print(f"    <- {preview}")
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })
        
        # Max iterations reached
        logger.warning(f"Reached maximum iterations ({MAX_ITERATIONS})")
        
        if verbose:
            print(f"\n  Warning: Reached max iterations ({MAX_ITERATIONS})")
        
        last_answer = None
        for m in reversed(messages):
            content = m.content if hasattr(m, "content") else m.get("content")
            role = m.role if hasattr(m, "role") else m.get("role")
            if role == "assistant" and content:
                last_answer = content
                break
        
        return {
            "iterations": MAX_ITERATIONS,
            "answer": last_answer,
            "warning": "Max iterations reached",
        }
    
    finally:
        logger.info("Closing database connections")
        graph_db.close()
        db_manager.close()
        logger.info("Data Retrieval Agent session completed")
        logger.info("=" * 80 + "\n")


def extract_graph_hints(tool_name: str, result: Any) -> dict:
    """Extract lightweight nodes + edges from a tool result for the live
    graph panel.  Returns ``{"nodes": [...], "edges": [...]}``.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    _sn: set[tuple] = set()
    _se: set[tuple] = set()

    def _n(name: str, ntype: str, database: str = ""):
        if not name:
            return
        key = (name, database)
        if key not in _sn:
            _sn.add(key)
            nodes.append({"name": name, "type": ntype, "database": database or ""})

    def _e(src: str, tgt: str, label: str = "", src_db: str = "", tgt_db: str = ""):
        if not src or not tgt:
            return
        key = (src, src_db, tgt, tgt_db)
        if key not in _se:
            _se.add(key)
            edges.append({"source": src, "target": tgt, "label": label,
                          "source_db": src_db, "target_db": tgt_db})

    def _type_from_labels(labels: list) -> str:
        return "Concept" if "Concept" in labels else "Table"

    try:
        match tool_name:
            case "list_concepts":
                if isinstance(result, list):
                    for c in result:
                        _n(c.get("name"), "Concept", c.get("database", ""))

            case "concept_links":
                if isinstance(result, list):
                    for entry in result:
                        cn = entry.get("concept", "")
                        cdb = entry.get("concept_database", "")
                        _n(cn, "Concept", cdb)
                        for t in entry.get("linked_tables", []):
                            tn, tdb = t.get("table_name", ""), t.get("database", "")
                            _n(tn, "Table", tdb)
                            _e(cn, tn, t.get("relationship", ""), cdb, tdb)
                        for rc in entry.get("related_concepts", []):
                            _n(rc.get("concept", ""), "Concept", rc.get("database", ""))
                            _e(cn, rc.get("concept", ""), rc.get("relationship", ""),
                               cdb, rc.get("database", ""))

            case "node_details":
                if isinstance(result, dict):
                    node = result.get("node", {})
                    props = node.get("props", {})
                    nm, db = props.get("name", ""), props.get("database", "")
                    _n(nm, _type_from_labels(node.get("labels", [])), db)
                    for nb in result.get("neighbours", []):
                        nbn = nb.get("neighbour_name", "")
                        nbdb = nb.get("neighbour_database", "")
                        _n(nbn, _type_from_labels(nb.get("neighbour_labels", [])), nbdb)
                        _e(nm, nbn, nb.get("relationship", ""), db, nbdb)

            case "list_tables":
                if isinstance(result, list):
                    for t in result:
                        _n(t.get("name", ""), "Table", t.get("database", ""))

            case "search":
                if isinstance(result, list):
                    for r in result:
                        _n(r.get("name", ""), _type_from_labels(r.get("labels", [])),
                           r.get("database", ""))
            
            case "semantic_concept_search":
                if isinstance(result, dict):
                    for r in result.get("results", []):
                        _n(r.get("name", ""), _type_from_labels(r.get("labels", [])),
                           r.get("database", ""))

            case "find_columns":
                if isinstance(result, list):
                    for t in result:
                        _n(t.get("table_name", ""), "Table", t.get("database", ""))

            case "subgraph":
                if isinstance(result, dict):
                    db_map: dict[str, str] = {}
                    for n in result.get("nodes", []):
                        nm = n.get("name", "")
                        db = n.get("database", "")
                        db_map[nm] = db
                        _n(nm, _type_from_labels(n.get("labels", [])), db)
                    for e in result.get("edges", []):
                        s, t = e.get("source", ""), e.get("target", "")
                        _e(s, t, e.get("relationship", ""),
                           db_map.get(s, ""), db_map.get(t, ""))

            case "shared_concepts":
                if isinstance(result, list):
                    for r in result:
                        if r.get("bridge_type") == "same_name":
                            for db in r.get("databases", []):
                                _n(r.get("concept", ""), "Concept", db)
                        elif r.get("bridge_type") == "cross_edge":
                            _n(r.get("concept", ""), "Concept", r.get("database", ""))

            case "trace_cross_db":
                if isinstance(result, dict):
                    src = result.get("source", "")
                    for d in result.get("direct_connections", []):
                        sdb = d.get("source_db", "")
                        tn, tdb = d.get("target_name", ""), d.get("target_db", "")
                        _n(src, "Unknown", sdb)
                        _n(tn, _type_from_labels(d.get("target_labels", [])), tdb)
                        _e(src, tn, d.get("relationship", ""), sdb, tdb)
                    for h in result.get("two_hop_connections", []):
                        via, vdb = h.get("via_node", ""), h.get("via_db", "")
                        tn, tdb = h.get("target_name", ""), h.get("target_db", "")
                        _n(via, _type_from_labels(h.get("via_labels", [])), vdb)
                        _n(tn, _type_from_labels(h.get("target_labels", [])), tdb)
                        _e(src, via, h.get("rel1", ""), "", vdb)
                        _e(via, tn, h.get("rel2", ""), vdb, tdb)
            
            case "find_join_keys":
                if isinstance(result, dict):
                    # Extract tables that were analyzed
                    for rec in result.get("join_recommendations", []):
                        tables = rec.get("tables", [])
                        col = rec.get("column", "")
                        # Add nodes for each table
                        for tbl in tables:
                            _n(tbl, "Table", "")
                        # Add edges between tables that share this ID column
                        for i in range(len(tables)):
                            for j in range(i + 1, len(tables)):
                                _e(tables[i], tables[j], f"JOIN on {col}", "", "")
            
            # Scoring tools don't do graph exploration, but the score is built from
            # a real 4-table JOIN — show those source tables so the panel isn't empty.
            case "calculate_employee_score" | "find_top_performing_crew":
                sources = [
                    ("Indigo_HR_Raw_Data", "HRData"),
                    ("IJP_Employee_scores", "IJP"),
                    ("CLMS_Raw_Data", "CLMS"),
                    ("IndigoNPS_Summary", "NPS"),
                ]
                for name, db in sources:
                    _n(name, "Table", db)
                for i in range(len(sources)):
                    for j in range(i + 1, len(sources)):
                        _e(sources[i][0], sources[j][0], "JOIN on IGA",
                           sources[i][1], sources[j][1])

            # Data retrieval tools don't generate graph hints
            case "get_sample_data" | "execute_query":
                pass
                
    except Exception:
        pass  # never break the SSE stream for visualisation

    return {"nodes": nodes, "edges": edges}


def run_agent_stream(query: str, backend: str | None = None):
    """Generator that yields JSON event dicts as the agent works.

    Event types:
      {"type": "iteration", "number": N, "max": 35}
      {"type": "tool_call", "name": "...", "args": {...}}
      {"type": "tool_result", "name": "...", "preview": "...", "duplicate": bool, "graph": {...}}
      {"type": "data_retrieved", "database": "...", "table": "...", "row_count": N}
      {"type": "done", "iterations": N, "answer": "..."}
      {"type": "error", "message": "..."}
    """
    llm = get_llm_client()
    graph_db = get_graph_db(backend)
    db_manager = DatabaseConnectionManager()
    seen_calls: set[str] = set()
    execution_tracker: dict = {}  # Track query executions to prevent sequential queries

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            yield {"type": "iteration", "number": iteration, "max": MAX_ITERATIONS}

            response = llm.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.7,
            )

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                yield {"type": "done", "iterations": iteration, "answer": msg.content}
                return

            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}

                yield {"type": "tool_call", "name": fn_name, "args": fn_args}

                call_key = json.dumps({"fn": fn_name, "args": fn_args}, sort_keys=True)
                is_dup = call_key in seen_calls
                if is_dup:
                    result = {
                        "note": "You already called this tool with identical arguments. "
                        "Use the previous result or try a different approach."
                    }
                else:
                    seen_calls.add(call_key)
                    try:
                        result = dispatch_tool(graph_db, db_manager, fn_name, fn_args, execution_tracker)
                    except Exception as e:
                        result = {"error": str(e)}

                result_json = json.dumps(result, indent=2, default=str)
                if len(result_json) > 8000:
                    result_json = result_json[:8000] + "\n... (truncated)"

                preview = result_json[:500] + ("..." if len(result_json) > 500 else "")
                hints = extract_graph_hints(fn_name, result) if not is_dup else {"nodes": [], "edges": []}
                
                # Special event for data retrieval
                if fn_name in ("get_sample_data", "execute_query") and "data" in result:
                    yield {
                        "type": "data_retrieved",
                        "database": result.get("database", ""),
                        "table": fn_args.get("table", "query"),
                        "row_count": result.get("row_count", len(result.get("data", [])))
                    }
                
                yield {
                    "type": "tool_result",
                    "name": fn_name,
                    "preview": preview,
                    "duplicate": is_dup,
                    "graph": hints
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        # Max iterations
        last_answer = None
        for m in reversed(messages):
            content = m.content if hasattr(m, "content") else m.get("content")
            role = m.role if hasattr(m, "role") else m.get("role")
            if role == "assistant" and content:
                last_answer = content
                break
        yield {
            "type": "done",
            "iterations": MAX_ITERATIONS,
            "answer": last_answer,
            "warning": "Max iterations reached",
        }
    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        graph_db.close()
        db_manager.close()


# ===============================================================================
#  CLI
# ===============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Data Retrieval Agent — Graph exploration + data retrieval"
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Query to run (omit for interactive mode)"
    )
    parser.add_argument(
        "--graph-backend",
        choices=["neo4j", "cosmos"],
        default=None,
        help="Graph DB backend (default: cosmos)"
    )
    args = parser.parse_args()
    backend = args.graph_backend or "cosmos"
    
    if args.query:
        query = " ".join(args.query)
        logger.info(f"\n{'#' * 80}")
        logger.info(f"# Data Retrieval Agent - Command Line Mode")
        logger.info(f"# Backend: {backend}")
        logger.info(f"{'#' * 80}\n")
        run_agent(query, backend=backend)
    else:
        logger.info(f"\n{'#' * 80}")
        logger.info(f"# Data Retrieval Agent - Interactive Mode")
        logger.info(f"# Backend: {backend}")
        logger.info(f"{'#' * 80}\n")
        print(f"Data Retrieval Agent [unified graph: {backend}]")
        print("Ask questions about data in the knowledge graph\n")
        while True:
            try:
                q = input("? ").strip()
            except (EOFError, KeyboardInterrupt):
                logger.info("Interactive session ended by user")
                break
            if not q or q.lower() in ("quit", "exit"):
                logger.info("Interactive session ended by user")
                break
            run_agent(q, backend=backend)
            print()


if __name__ == "__main__":
    main()
