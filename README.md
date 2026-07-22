# Indigo Airlines Knowledge Layer

A unified knowledge graph system for Indigo Airlines that integrates multiple HR databases (HRData, IJP, CLMS, PEP, NPS) into a semantic layer powered by Azure Cosmos DB (Gremlin API) and SQL Server, with AI-powered data retrieval agents.

## 🎯 Overview

This system provides:
- **Unified Knowledge Graph**: Merges concepts across multiple databases into a single Cosmos DB (Gremlin API) or Neo4j graph
- **Multiple AI Agents**: Suite of specialized agents for different query complexity levels
  - **Data Retrieval Agent**: Retrieves actual data from physical databases using KG metadata
  - **Tree Inference Agent**: Multi-threaded advanced graph exploration
  - **Standard Inference Agent**: Single-path graph exploration
  - **Basic Inference Agent**: Simple concept lookups
- **Employee Performance Scoring & Ranking**: Weighted scoring system (7 components, including real CLMS leave data and passenger NPS feedback) for one employee or ranked across all crew
- **Policy-Driven Configuration**: Crew SOPs live in the knowledge graph as `Policy` vertices — scoring weights/thresholds are read from there at runtime (not hardcoded), so editing the graph changes agent behavior with no code deploy
- **Automated Graph Building**: Extracts database schemas and relationships to build concept graphs
- **Graph Unification**: Intelligent merging of multiple concept graphs using embedding-based semantic matching
- **Azure Container Apps Deployment**: Production-ready infrastructure with Bicep templates
- **Azure AI Foundry Integration**: Custom connections for Prompt Flow workflows
- **Multi-Interface Web App**: Basic, Advanced, and Data retrieval interfaces

## 📊 Graph Visualization

Visualize the knowledge graph:

```bash
cd graph_viz
python app.py
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    End-User Interfaces                           │
│         (Flask Web App - Basic/Advanced/Data Views)             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼──────┐  ┌────────▼─────────┐  ┌──────▼──────────────┐
│ Basic        │  │ Standard/Tree    │  │ Data Retrieval      │
│ Inference    │  │ Inference        │  │ Agent (Physical DB) │
│ Agent        │  │ Agents           │  │ • Entity-first      │
│ • Simple KG  │  │ • Single/Multi   │  │ • Auto JOIN keys    │
│   queries    │  │   threaded       │  │ • SQL generation    │
└───────┬──────┘  └────────┬─────────┘  └──────┬──────────────┘
        │                  │                    │
        └──────────────────┼────────────────────┘
                           │
        ┌──────────────────┴──────────────────┐
        │                                      │
┌───────▼─────────────┐         ┌─────────────▼──────────────┐
│ Unified Knowledge   │         │   Physical Databases       │
│ Graph               │         │   (SQL Server)             │
│ • Cosmos DB Gremlin │         │  HRData database:          │
│ • Neo4j (Optional)  │         │  - Indigo_HR_Raw_Data      │
│ • Concepts          │         │  - IJP_Employee_scores     │
│ • Tables/Columns    │         │  - CLMS_Raw_Data           │
│ • Relationships     │         │  - IndigoNPS_Summary       │
│ • Policies (SOPs)   │         │                            │
└─────────────────────┘         └────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  Azure Infrastructure Layer                      │
│  • Container Apps (Graph Builder, Unifier)                      │
│  • Azure AI Foundry (OpenAI, Embeddings, Custom Connections)   │
│  • Container Registry, Key Vault, Application Insights         │
└─────────────────────────────────────────────────────────────────┘
```

> **Why one physical database?** The Data Retrieval Agent answers multi-table questions with a
> single SQL query (never sequential per-table queries), and SQL Server can't `JOIN` unqualified
> table names across two different physical databases in one query. So `Indigo_HR_Raw_Data`,
> `IJP_Employee_scores`, `CLMS_Raw_Data`, and `IndigoNPS_Summary` all live in the same `HRData`
> database — even though they represent logically distinct source systems (HRData/IJP/CLMS/NPS).
> The knowledge graph still tags each table's `database` property with its logical source, and
> `IGA` is the shared join key across all four tables.

## 📋 Prerequisites

### System Requirements
- **Python**: 3.11 or higher
- **SQL Server**: Any version with ODBC Driver 18+ (a local Docker/Colima instance works fine — see below)
- **Azure Cosmos DB**: Account with Gremlin API enabled (primary graph database)
- **Neo4j**: Optional alternative graph database
- **Azure OpenAI**: Chat + embedding deployments for LLM inference
- **Azure AI Foundry**: For deployment and Prompt Flow integration (optional)

### Platform-Specific Requirements

> **Note**: These requirements are only needed if running the **Data Retrieval Agent locally** with SQL Server on Docker or a local SQL Server instance. If using only the Knowledge Graph agents (without physical database queries) or deploying to Azure Container Apps, these are handled by the container images.

**Windows:**
- [Microsoft ODBC Driver 18+ for SQL Server](https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

**Linux (Ubuntu/Debian):**
```bash
# Install UnixODBC driver manager
sudo apt-get install unixodbc unixodbc-dev

# Install Microsoft ODBC Driver 18 for SQL Server
curl https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

**macOS:**
```bash
# Install UnixODBC driver manager
brew install unixodbc

# Install Microsoft ODBC Driver 18 for SQL Server
brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
brew install msodbcsql18
```

### Local SQL Server on macOS / Apple Silicon

There's no native SQL Server for macOS, and Microsoft's real `mssql/server` image doesn't run on
Apple Silicon. Use **Colima + Docker CLI** (lighter than Docker Desktop) with **Azure SQL Edge**
(ARM64-native, wire-compatible with SQL Server for the T-SQL this project uses):

```bash
# 1. Install a container runtime — Colima is a lightweight, licence-free alternative
#    to Docker Desktop and is what this project was set up with.
brew install colima docker
brew services start colima     # starts Colima at login; or `colima start` for a one-off session

# 2. Run Azure SQL Edge, matching the credentials the code expects (see .env below)
docker run -e "ACCEPT_EULA=1" -e "MSSQL_SA_PASSWORD=YourStrong@Passw0rd" \
  -p 1433:1433 --name indigo-sqlserver --restart unless-stopped \
  -d mcr.microsoft.com/azure-sql-edge:latest

# 3. Verify it's up (may take ~20s after first start to accept connections)
docker logs indigo-sqlserver --tail 5
```

`--restart unless-stopped` means the container survives Colima restarts on its own; if you ever
see connection errors, it almost always just means Colima's VM itself isn't running — run
`colima start` and wait a few seconds.

## 🚀 Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd Indigo_Knowledge_Layer
```

### 2. Create Virtual Environment
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt

# neo4j is commented out in requirements.txt (Cosmos is the default backend), but
# the Flask app imports every agent — including the Neo4j-backed one — at startup
# regardless of which --graph-backend you actually use. Install it even if you're
# Cosmos-only, or `python -m src.end-user-app.app` will fail with ModuleNotFoundError.
pip install "neo4j>=5.16.0"

# For development (optional)
pip install -r requirements-dev.txt
```

### 4. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials. **Note**: the code reads `SQL_SERVER`
/ `SQL_USER` / `SQL_PASSWORD` (not the `SQL_SERVER_HOST` / `SQL_SERVER_PORT` names you'll see in
some older references) — use the exact keys below:

```bash
# Azure OpenAI (for LLM + embeddings)
AZURE_OPENAI_ENDPOINT=https://your-openai-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-api-key
API_VERSION=2024-12-01-preview
LLM_MODEL=gpt-4.1                          # your chat deployment name
EMBEDDING_MODEL=text-embedding-3-small     # required to build/unify the graph

# Cosmos DB (Gremlin API)
COSMOS_DB_ENDPOINT=your-cosmos-account.gremlin.cosmos.azure.com
COSMOS_DB_KEY=your-cosmos-key-here
COSMOS_DB_DATABASE=IndigoKG
COSMOS_DB_GRAPH=Unified_Knowledge_graph    # single container, partition key /database

# Graph backend selection
GRAPH_BACKEND=cosmos

# SQL Server (matches the local Colima/Docker container from Prerequisites,
# or point these at a real Azure SQL Server / on-prem instance)
SQL_SERVER=localhost,1433
SQL_USER=sa
SQL_PASSWORD=YourStrong@Passw0rd
```

### 5. Set Up SQL Server Data

Once SQL Server is reachable (local Docker/Colima container from Prerequisites, or a real
instance), load the demo data — all four tables land in one `HRData` database (see the
architecture note above for why):

```bash
python load_local_sql.py          # creates HRData DB, loads Indigo_HR_Raw_Data + IJP_Employee_scores
python load_clms_local_sql.py     # loads CLMS_Raw_Data into the same HRData database
python load_nps_local_sql.py      # loads IndigoNPS_Summary into the same HRData database

# Verify
python -m src.utils.check_hrdata_tables
```

Each loader reads its source file from `db_schemas_csv/` (`IJP_Employee_scores.csv`,
`clms_raw_data_synthetic.xlsx`, `indigoNPS_summary_synthetic.xlsx`) and is safe to re-run — it
drops and recreates its own table(s) each time.

## 📦 Project Structure

```
Indigo_Knowledge_Layer/
├── src/
│   ├── agents/                              # AI Agents
│   │   ├── advanced_graph_builder_agent.py  # Extracts concepts from DB schemas
│   │   ├── Data_Retrieval_Agent_New.py      # Main data retrieval agent (physical DB queries)
│   │   ├── inference_agent.py               # Standard KG exploration agent
│   │   ├── tree_inference_agent.py          # Advanced multi-threaded exploration
│   │   ├── basic_inference_agent.py         # Simple concept lookup
│   │   └── __init__.py
│   ├── end-user-app/                        # Web interface
│   │   ├── app.py                           # Flask application with multiple routes
│   │   └── templates/                       # HTML templates (basic, advanced, data)
│   ├── utils/                               # Utilities
│   │   ├── agent_logger.py                  # Structured logging for all agents
│   │   ├── cosmos_graph_traversal.py        # Cosmos DB Gremlin graph operations
│   │   ├── cosmos_helpers.py                # Shared Cosmos DB connection & query helpers
│   │   ├── graph_traversal.py               # Neo4j graph operations
│   │   ├── neo4j_helpers.py                 # Shared Neo4j connection & query helpers
│   │   ├── llm.py                           # LLM & embedding clients (Azure OpenAI)
│   │   ├── initialize_cosmos_connection.py  # Load unified graph into Cosmos DB
│   │   ├── create_cosmos_graph_containers.py # Create Cosmos DB database and graph containers
│   │   ├── purge_cosmos_container.py        # Delete all vertices/edges from Cosmos DB graph
│   │   ├── query_unified_graph.py           # Query unified knowledge graph
│   │   ├── check_hrdata_graph_nodes.py      # Verify HRData nodes in graph (read-only)
│   │   ├── check_hrdata_tables.py           # Verify HRData tables in SQL Server (read-only)
│   │   ├── export_hr_data.py                # Export Indigo_HR_Raw_Data to CSV
│   │   ├── query_hr_from_sqlserver.py       # Query HRData from SQL Server
│   │   ├── load_hr_to_sqlserver.py          # Legacy loader — Windows-only (pywin32 Excel COM); use ../../load_local_sql.py instead
│   │   ├── load_ijp_to_sqlserver.py         # Legacy loader — superseded by ../../load_local_sql.py
│   │   ├── purge_logs.py                    # Clean up old log files
│   │   └── __init__.py
│   └── graph_unification.py                 # Graph merging logic with embeddings
├── db_schemas_csv/                          # Database schema definitions + demo data
│   ├── HRData_Schema.csv
│   ├── IJP_Employee_scores_Schema.csv
│   ├── IndigoNPS_Summary_Schema.csv
│   ├── CLMS Table Details.csv               # + "CLMS Table Data .csv", "CLMS Table RelationShip.csv"
│   ├── PEP_Schema_Defn_2026-04-15.csv
│   ├── IJP_Employee_scores.csv              # source data for load_local_sql.py
│   ├── clms_raw_data_synthetic.xlsx         # source data for load_clms_local_sql.py
│   ├── indigoNPS_summary_synthetic.xlsx     # source data for load_nps_local_sql.py
│   └── ...
├── docs/                                    # Documentation
│   ├── cosmos_db_setup.md                   # Cosmos DB setup guide
│   ├── employee_scoring.md                  # Employee scoring methodology (dev-facing reference)
│   └── sop_crew_performance.md              # Crew SOP — mirrored into the graph as Policy vertices (see Usage §6)
├── infra/                                   # Infrastructure as Code
│   ├── main.bicep                           # Main infrastructure template
│   ├── deploy-all.ps1                       # Complete deployment script
│   ├── deploy-agents-to-container-apps.ps1  # Container Apps deployment
│   ├── register-foundry-connections.ps1     # Foundry integration
│   ├── Dockerfile.graph-builder             # Graph builder container
│   ├── Dockerfile.graph-unifier             # Graph unifier container
│   ├── connection-graph-builder.yml         # Foundry connection config
│   ├── foundry-test-flow.yaml               # Test workflow
│   └── Infra_README.md                      # Infrastructure guide
├── graph_viz/                               # Graph visualization tool
│   ├── app.py                               # Visualization Flask app
│   └── templates/
├── tests/                                   # Test suite (incl. test_data_agent_comprehensive.py)
├── output/                                  # Individual concept graphs (gitignored)
├── unified_output/                          # Unified graph output (gitignored)
├── logs/                                    # Agent execution logs (gitignored)
├── load_local_sql.py                        # Loads Indigo_HR_Raw_Data + IJP_Employee_scores into HRData DB
├── load_clms_local_sql.py                   # Loads CLMS_Raw_Data into the same HRData DB
├── load_nps_local_sql.py                    # Loads IndigoNPS_Summary into the same HRData DB
├── push_sop_policy_graph.py                 # Pushes/updates all 6 SOP Policy vertices (scoring parameters + prose, linked to their tables)
├── test_employee_scoring.py                 # Employee scoring CLI test script
├── .env                                     # Environment config (gitignored)
├── requirements.txt                         # Python dependencies
└── README.md                                # This file
```

## 🎮 Usage

> **Skip steps 1-3 if the Cosmos graph is already built** (e.g. you're pointing `.env` at an
> existing account/database/container). They're only needed to bootstrap a **brand-new**
> environment. Step 2 does a full drop-and-reload of every database's vertices in the target
> Cosmos container before rebuilding — safe for a fresh container, but don't run it against a
> graph with manual/one-off additions you want to keep without backing up `unified_output/` first.

### 1. Build Individual Concept Graphs

Extract concepts from each database's schema CSVs (calls Azure OpenAI):

```bash
# Build one database at a time — valid names: CrewPortal, CLMS, PEP, HRData, IJP, NPS
python -m src.agents.advanced_graph_builder_agent --database HRData
python -m src.agents.advanced_graph_builder_agent --database NPS

# Or build all six in one go
python -m src.agents.advanced_graph_builder_agent --database all
```
This writes `output/{Database}_concept_graph.json` (+ `.md` summary + agent log) per database.
(Note: despite older references you may see elsewhere, there is no `--csv` flag — the tool reads
the fixed schema CSV filenames per database from `db_schemas_csv/` automatically.)

### 2. Unify Concept Graphs

Merge all per-database concept graphs into one, with embedding-based cross-database concept
matching, and push the result into Cosmos DB (`COSMOS_DB_GRAPH` from `.env`):

```bash
# Auto-discover and unify all graphs in output/ folder
python -m src.graph_unification --auto-discover --output unified_output

# Or specify graphs explicitly
python -m src.graph_unification --graphs output/HRData_concept_graph.json output/IJP_concept_graph.json
```
The Cosmos load happens automatically as the last step of this command — there's no separate
"load" step. (`src/utils/initialize_cosmos_connection.py` is a different, optional helper that
uses the Azure CLI to *discover* a Cosmos account's connection details and write them to `.env`;
it does not load graph data.)

### 3. Run the Web Application

```bash
# Run with default settings (Cosmos DB)
python -m src.end-user-app.app

# Or specify graph backend / port explicitly
python -m src.end-user-app.app --graph-backend cosmos --port 5000
python -m src.end-user-app.app --graph-backend neo4j
```

Access the interfaces:
- **Basic Interface**: http://localhost:5000/basic (Simple KG queries)
- **Advanced Interface**: http://localhost:5000/advanced (Multi-threaded exploration)
- **Data Retrieval**: http://localhost:5000/data (Physical DB queries, SQL generation, employee scoring & ranking, live graph panel)

> If you edit backend Python files (agents, utils) while the app is running, Flask's debug
> reloader doesn't always pick up the change reliably — if behavior seems stale, kill the process
> and restart it (see Troubleshooting).

### 4. Test the Data Retrieval Agent

```bash
# Run comprehensive test suite (lives in tests/, run as a script — not a src.utils module)
python tests/test_data_agent_comprehensive.py

# Test with custom query
python tests/test_data_agent_comprehensive.py --query "Show performance metrics for Akash Saxena"

# Test find_join_keys function
python tests/test_data_agent_comprehensive.py --test join_keys
```

### 5. Employee Scoring & Ranking

Single-employee weighted score (7 components — see [docs/employee_scoring.md](docs/employee_scoring.md)):

```bash
# Calculate score by employee name
python test_employee_scoring.py --name "Akash Saxena"

# Calculate score by IGA number
python test_employee_scoring.py --iga 62913

# Output as JSON
python test_employee_scoring.py --name "Kamal Choudhury" --json
```

Ranking across **all** crew ("who's the best/worst performing crew") isn't a CLI flag — ask the
Data Retrieval Agent directly through http://localhost:5000/data, e.g. *"Who is the top 5 best
performing crew at DEL?"* or *"Who is the worst performing crew?"*. It scores every employee in
one query (`find_top_performing_crew`) rather than looping the single-employee tool.

### 6. Crew SOP — Policy-Driven Configuration in the Knowledge Graph

The scoring weights/thresholds and the rest of the crew SOP ([docs/sop_crew_performance.md](docs/sop_crew_performance.md))
aren't hardcoded in Python or the system prompt — they live in the graph as **`Policy`** vertices,
a third node type alongside `Table`/`Concept`. The agent's existing `search`/`node_details`/
`semantic_concept_search` tools are label-agnostic (no `hasLabel('Table')` filter), so `Policy`
nodes are discoverable and retrievable with **zero new agent code**.

**How scoring actually reads from the graph** — `calculate_employee_score`/`find_top_performing_crew`
call `_get_scoring_parameters(graph_db)`, which fetches the `Weighted Performance Scoring Policy`
vertex's `parameters` JSON property (weights, penalties, BMI thresholds — one number per formula
constant) via `node_details`, once per call (not once per row, even when ranking all 339 crew).
`_score_employee_row()` takes that `params` dict as an argument and contains no hardcoded weight
literals at all. **Editing the vertex's `parameters` property changes scoring behavior immediately,
with no code deploy** — this is the actual single source of truth, not just documentation.

The other 5 SOP sections (Leave Management, Passenger NPS Feedback Integration, IJP Eligibility,
Disciplinary Escalation, Data Sources) are plain-prose `Policy` vertices with a `content` field —
no structured parameters, since there's nothing to compute — each linked via a `RELATES_TO` edge
to the table it governs (e.g. Disciplinary Escalation → `IJP_Employee_scores`), so the agent can
navigate from a table it's already examining into the procedure that governs it, not just
keyword-match prose.

```bash
# Push/update all 6 SOP Policy vertices (idempotent — creates if missing, updates in place if not)
python push_sop_policy_graph.py
```

This script is purely additive (`addV`/`addE`/property updates only, no drops) — safe to run
against a live graph without risking any other vertex, unlike `graph_unification.py`'s full
rebuild (see the warning in §1 above). If you edit `docs/sop_crew_performance.md`, update the
corresponding `content`/`SCORING_PARAMETERS` entry in `SECTIONS` and re-run it to sync the graph.

### 7. Deploy to Azure (Optional)

```bash
# Complete infrastructure deployment
cd infra
.\deploy-all.ps1

# Deploy only Container Apps
.\deploy-agents-to-container-apps.ps1

# Register as Foundry custom connections
.\register-foundry-connections.ps1
```

See [infra/Infra_README.md](infra/Infra_README.md) for detailed deployment guide.

### 8. Utility Scripts

The `src/utils/` folder contains helper scripts for database operations, graph management, and testing:

**Graph Database Operations:**
```bash
# Interactive Cosmos DB graph explorer (REPL)
python -m src.utils.cosmos_graph_traversal

# Interactive Neo4j graph explorer (REPL)
python -m src.utils.graph_traversal

# Query the unified knowledge graph
python -m src.utils.query_unified_graph

# Discover a Cosmos account's connection details via Azure CLI and write to .env
# (does NOT load graph data — see "Unify Concept Graphs" above for that)
python -m src.utils.initialize_cosmos_connection

# Create Cosmos DB database and graph containers
python -m src.utils.create_cosmos_graph_containers

# Purge all data from Cosmos DB graph
python -m src.utils.purge_cosmos_container
```

**SQL Server Data Management:**
```bash
# Load all demo data into the HRData database (see Installation step 5)
python load_local_sql.py          # Indigo_HR_Raw_Data + IJP_Employee_scores
python load_clms_local_sql.py     # CLMS_Raw_Data
python load_nps_local_sql.py      # IndigoNPS_Summary

# Query HRData from SQL Server
python -m src.utils.query_hr_from_sqlserver

# Export Indigo_HR_Raw_Data to CSV
python -m src.utils.export_hr_data --output Indigo_HR_Raw_Data.csv

# Verify HRData tables exist in SQL Server
python -m src.utils.check_hrdata_tables
```

**Verification & Maintenance:**
```bash
# Verify HRData nodes exist in knowledge graph
python -m src.utils.check_hrdata_graph_nodes

# Clean up old log files
python -m src.utils.purge_logs
```

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_cosmos_connection.py
```

## 🔑 Key Features

### 1. Multiple Specialized Agents
- **Data Retrieval Agent**: Queries physical databases using KG metadata for context
- **Tree Inference Agent**: Multi-threaded parallel exploration of graph concepts
- **Standard Inference Agent**: Single-path depth-first graph traversal
- **Basic Inference Agent**: Quick concept lookups for simple queries

### 2. Entity-First Exploration
The data retrieval agent first identifies which tables contain the requested entity before constructing queries, preventing 0-row results.

### 3. Automatic JOIN Detection
The `find_join_keys` function analyzes graph metadata to identify optimal JOIN columns:
- Prefers ID columns (IGA, EmployeeID) over data fields (Name)
- Uses semantic understanding and primary key metadata
- Warns against unreliable joins (e.g., on Name field)

### 4. Metadata-Driven Query Generation
All queries are grounded in actual database metadata:
- No hardcoded column names or table names
- Agent discovers columns via `node_details` tool
- Only uses columns that exist in actual schema

### 5. Multi-Table Query Protocol
Enforces best practices for multi-table queries:
- Single JOIN query instead of sequential queries
- Correct table order in FROM clause
- WHERE filters on tables where data exists

### 6. Employee Performance Scoring & Ranking
Comprehensive weighted scoring system with 7 components (see [docs/employee_scoring.md](docs/employee_scoring.md)):
- Crew Availability (40%) - Real CLMS leave/status data (0 if released/inactive)
- Appreciation Letters (20%) - Positive recognition
- Poise & Grace/BMI (10%) - Physical fitness indicator
- Non-performance Discussions (10%) - Disciplinary records
- Passenger NPS Feedback (10%) - Real passenger NPS score + free-text feedback attributed to the crew member
- Curative Training/iCoach (5%) - Coaching sessions
- Extra Initiatives/Recognition (5%) - Special awards

Two tools share the same underlying formula:
- `calculate_employee_score` — one named employee
- `find_top_performing_crew` — ranks **every** employee in a single query (best or worst, with
  optional base/designation filters); the agent invokes this automatically for "best/top/worst
  performing crew" style questions rather than looping the single-employee tool

### 7. Conversational Answers
The Data Retrieval Agent always leads with a `conversational_answer` — 2-5 sentences of plain
English using the actual retrieved values (never normalized 0-100 scores or vague qualitative
words) — with the full technical breakdown (subgraph, query plan, raw data tables) available
underneath via a collapsible "Show technical details" toggle in the `/data` UI.

### 8. Flexible Graph Backend
- Primary: Azure Cosmos DB with Gremlin API
- Alternative: Neo4j (legacy support)
- Switch backends via command-line flag or environment variable

### 9. Production-Ready Infrastructure
- Azure Container Apps for agent hosting
- Bicep templates for reproducible deployments
- Azure AI Foundry integration with custom connections
- Managed Identity, Key Vault, Application Insights
- Health checks and monitoring

### 10. Policy-Driven Scoring (Graph as Single Source of Truth)
- Crew SOP lives in the knowledge graph as `Policy` vertices — not hardcoded in Python or the
  system prompt (see Usage §6)
- Scoring weights/thresholds are fetched from the graph at runtime; changing the graph vertex
  changes agent behavior immediately, no code deploy
- Discovered/retrieved through the same label-agnostic `search`/`node_details` tools used for
  tables — no bespoke "policy lookup" tool was needed
- Policy vertices for procedural sections (leave management, disciplinary escalation, etc.) are
  `RELATES_TO`-linked to the table they govern, enabling navigation from data to procedure

## 🛠️ Development

### Running in Development Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG  # Linux/Mac
set LOG_LEVEL=DEBUG     # Windows

# Run with auto-reload
flask --app src.end-user-app.app run --debug
```

### Code Quality

```bash
# Format code
black src/

# Lint
ruff check src/

# Type checking
mypy src/
```

Access at: http://localhost:8080

## 🤝 Azure AI Foundry Integration

The agents can be registered as custom connections in Azure AI Foundry for use in Prompt Flow workflows:

```bash
cd infra
.\register-foundry-connections.ps1
```

This creates:
- **Graph Builder Connection**: For building concept graphs from schemas
- **Graph Unifier Connection**: For merging multiple concept graphs

Test the connections with the provided [foundry-test-flow.yaml](infra/foundry-test-flow.yaml).

See [infra/Infra_README.md](infra/Infra_README.md) for architecture details.

## 🔧 Troubleshooting

### Issue: ODBC Driver Not Found
**Solution**: Install Microsoft ODBC Driver 18+ for SQL Server (see Prerequisites). The connection
string driver name must match exactly what's registered — on macOS/Linux this is
`{ODBC Driver 18 for SQL Server}`, **not** the Windows-only generic `{SQL Server}` name.

### Issue: `Could not obtain exclusive lock on database 'model'` when creating/dropping a database
**Solution**: Some other session (often a stray SQL client tool with a saved connection that
auto-reconnects) is holding a lock. Find and kill it:
```sql
SELECT session_id, program_name, status FROM sys.dm_exec_sessions WHERE is_user_process = 1;
KILL <session_id>;
```
If this keeps recurring, check whether you have a DB browser/extension configured to auto-connect
to this server.

### Issue: SQL Server connection refused / timeout (local Docker setup)
**Solution**: Colima's VM isn't running — this is the most common cause after a reboot or sleep.
```bash
colima start
docker ps --filter name=indigo-sqlserver   # should show "Up"
```
The container itself has `--restart unless-stopped`, so once Colima is up it recovers on its own.

### Issue: Cosmos DB Connection Timeout
**Solution**: Check firewall rules and ensure IP is whitelisted in Cosmos DB

### Issue: Agent Returns 0 Rows
**Solution**: 
- Verify entity exists in database using test scripts
- Check agent logs in `logs/` folder
- Ensure `find_join_keys` is being called
- Try different agent interfaces (Basic/Advanced/Data)

### Issue: "Live Knowledge Graph" panel stays empty on `/data`
**Solution**: Open the browser console for errors first. Known causes already fixed in this
codebase: `extract_graph_hints()` not having a case for the tool the agent used (falls through to
empty graph hints), or a dangling edge reference (guarded against, but if you add new tools/graph
hint cases, make sure edges only reference nodes emitted in the same event).

### Issue: Backend code changes don't seem to take effect
**Solution**: Flask's debug reloader doesn't always catch every change. Kill the running process
and start a fresh one rather than assuming it auto-reloaded:
```bash
lsof -nP -iTCP:5000 -sTCP:LISTEN | grep Python   # find the PID(s)
kill <pid> <pid>
python -m src.end-user-app.app --graph-backend cosmos --port 5000
```

### Issue: Container Apps Deployment Fails
**Solution**: 
- Check Azure subscription quota for Container Apps
- Verify all required Azure services are available in target region
- Review [infra/Infra_README.md](infra/Infra_README.md) for prerequisites
- Check deployment logs in Application Insights

### Issue: Import Errors
**Solution**: Ensure virtual environment is activated and dependencies installed — including
`neo4j` (see Installation step 3), which is required even when only using the Cosmos backend
because the Flask app imports all agents at startup.

## 📝 License

MIT License

Copyright (c) Microsoft Corporation. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
