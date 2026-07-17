# Azure Cosmos DB (Gremlin API) — Full Setup Guide

End-to-end walkthrough for provisioning an Azure Cosmos DB account with the Gremlin API and configuring it for the Indigo Knowledge Graph project.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Create the Cosmos DB Account (Gremlin API)](#2-create-the-cosmos-db-account-gremlin-api)
3. [Create the Gremlin Database](#3-create-the-gremlin-database)
4. [Create the Graph Container](#4-create-the-graph-container)
5. [Retrieve Connection Credentials](#5-retrieve-connection-credentials)
6. [Configure the Project (.env)](#6-configure-the-project-env)
7. [Install Python Dependencies](#7-install-python-dependencies)
8. [Build & Load the Knowledge Graph](#8-build--load-the-knowledge-graph)
9. [Explore the Graph (REPL)](#9-explore-the-graph-repl)
10. [Run Inference Agents against Cosmos DB](#10-run-inference-agents-against-cosmos-db)
11. [Web UI with Cosmos DB Backend](#11-web-ui-with-cosmos-db-backend)
12. [Key Design Decisions](#12-key-design-decisions)
13. [Troubleshooting](#13-troubleshooting)
14. [Reference](#14-reference)

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Azure subscription** | With permission to create Cosmos DB resources |
| **Azure CLI** (`az`) | Installed and logged in — run `az login` |
| **Python 3.11+** | Required for the KG pipeline |
| **Git Bash on Windows** | If using Git Bash, note the `MSYS_NO_PATHCONV=1` prefix needed for partition-key paths (see below) |

---

## 2. Create the Cosmos DB Account (Gremlin API)

```bash
az cosmosdb create \
  --name <your-account-name> \
  --resource-group <your-resource-group> \
  --capabilities EnableGremlin \
  --locations regionName=westus2 failoverPriority=0 isZoneRedundant=false \
  --default-consistency-level Session \
  -o table
```

**What each flag does:**

| Flag | Purpose |
|---|---|
| `--name` | Globally unique Cosmos DB account name |
| `--capabilities EnableGremlin` | Enables the Gremlin (graph) API. This **cannot be changed** after account creation |
| `--locations regionName=westus2` | Data region. We used West US 2; choose a region close to you. Set `isZoneRedundant=false` to avoid zone-redundancy quota issues |
| `--default-consistency-level Session` | Session consistency — your reads always see your own writes while remaining fast. Recommended default |

> **Note:** Account creation takes 3–5 minutes.

### Example (actual commands we ran)

```bash
az cosmosdb create \
  --name cosmosdb-gremlin-abpatra \
  --resource-group rg-abpatra-7946 \
  --capabilities EnableGremlin \
  --locations regionName=westus2 failoverPriority=0 isZoneRedundant=false \
  --default-consistency-level Session \
  -o table
```

---

## 3. Create the Gremlin Database

```bash
az cosmosdb gremlin database create \
  --account-name <your-account-name> \
  --resource-group <your-resource-group> \
  --name IndigoKG \
  -o table
```

A **database** in Cosmos DB is a logical namespace that groups one or more graph containers.

### Example

```bash
az cosmosdb gremlin database create \
  --account-name cosmosdb-gremlin-abpatra \
  --resource-group rg-abpatra-7946 \
  --name IndigoKG \
  -o table
```

---

## 4. Create the Graph Container

```bash
MSYS_NO_PATHCONV=1 az cosmosdb gremlin graph create \
  --account-name <your-account-name> \
  --resource-group <your-resource-group> \
  --database-name IndigoKG \
  --name knowledgeGraph \
  --partition-key-path "/database" \
  --throughput 400 \
  -o table
```

| Flag | Purpose |
|---|---|
| `--name knowledgeGraph` | The graph container — stores all vertices and edges |
| `--partition-key-path "/database"` | **Partition key.** Every vertex includes a `database` property (e.g. `"CLMS"`, `"CrewPortal"`, `"PEP"`) so data for each source database is co-located for fast queries |
| `--throughput 400` | Minimum provisioned RU/s — cheapest option for dev/test. Scale up or switch to autoscale for production |
| `MSYS_NO_PATHCONV=1` | **Git Bash on Windows only.** Prevents Git Bash from converting `/database` into a Windows file path |

### Example

```bash
MSYS_NO_PATHCONV=1 az cosmosdb gremlin graph create \
  --account-name cosmosdb-gremlin-abpatra \
  --resource-group rg-abpatra-7946 \
  --database-name IndigoKG \
  --name knowledgeGraph \
  --partition-key-path "/database" \
  --throughput 400 \
  -o table
```

### Optional: Autoscale throughput

For production or larger graphs, use autoscale instead of fixed throughput:

```bash
MSYS_NO_PATHCONV=1 az cosmosdb gremlin graph create \
  --account-name <your-account-name> \
  --resource-group <your-resource-group> \
  --database-name IndigoKG \
  --name knowledgeGraph \
  --partition-key-path "/database" \
  --max-throughput 4000 \
  -o table
```

This autoscales between 400 and 4000 RU/s based on demand.

---

## 5. Retrieve Connection Credentials

### Primary Key (CLI)

```bash
az cosmosdb keys list \
  --name <your-account-name> \
  --resource-group <your-resource-group> \
  --query "primaryKey" -o tsv
```

### Primary Key (Portal)

Navigate to your Cosmos DB account → **Settings** → **Keys** → copy **PRIMARY KEY**.

### Gremlin Endpoint

The Gremlin endpoint follows the pattern:

```
<your-account-name>.gremlin.cosmos.azure.com
```

You can verify it in the Portal under **Overview** → **Gremlin Endpoint**.

---

## 6. Configure the Project (.env)

Create (or update) a `.env` file in the **project root** (`indigo-kg-poc/.env`):

```env
# ── Cosmos DB (Gremlin API) ──────────────────────────
COSMOS_DB_ENDPOINT=<your-account-name>.gremlin.cosmos.azure.com
COSMOS_DB_KEY=<your-primary-key>
COSMOS_DB_DATABASE=IndigoKG
COSMOS_DB_GRAPH=knowledgeGraph

# ── Graph backend selection (optional) ───────────────
# Set this to avoid passing --graph-backend cosmos on every command
GRAPH_BACKEND=cosmos
```

> **Security:** The `.env` file is listed in `.gitignore` to prevent accidental commits of secrets.

---

## 7. Install Python Dependencies

```bash
python -m pip install gremlinpython python-dotenv openai azure-identity neo4j json-repair flask
```

| Package | Purpose |
|---|---|
| `gremlinpython` | Apache TinkerPop Gremlin client — communicates with Cosmos DB over WebSockets |
| `python-dotenv` | Loads `.env` variables at runtime |
| `openai`, `azure-identity` | LLM calls for the inference agents |
| `json-repair` | Robust JSON parsing for LLM outputs |
| `flask` | Web UI server |

---

## 8. Build & Load the Knowledge Graph

### Step 1 — Build per-database concept graphs

```bash
# Build all databases (CLMS, CrewPortal, PEP) — skip loading to any DB for now
python -m src.agents.advanced_graph_builder_agent --database all --skip-neo4j
```

This generates `output/{DB}_concept_graph.json` for each database.

### Step 2 — Unify the graphs pairwise

```bash
# Merge CLMS + CrewPortal
python -m src.graph_unification \
  --graph1 output/CLMS_concept_graph.json \
  --graph2 output/CrewPortal_concept_graph.json \
  --output-dir unified_output/CLMS_CrewPortal_unified

# Merge the result with PEP
python -m src.graph_unification \
  --graph1 unified_output/CLMS_CrewPortal_unified/unified_kg.json \
  --graph2 output/PEP_concept_graph.json \
  --output-dir unified_output/CLMS_CrewPortal_PEP_unified
```

### Step 3 — Load the final unified graph into Cosmos DB

```bash
python -m src.graph_unification \
  --load-only unified_output/CLMS_CrewPortal_PEP_unified/unified_kg.json \
  --load-cosmos
```

The loader automatically:
- Clears existing vertices by database partition before loading
- Creates table vertices, concept vertices, and all edges
- Handles 429 throttling with exponential backoff
- Deduplicates vertices and edges
- Derives `RELATES_TO` edges from concept source-table metadata

### Alternative: Build and load in one step

```bash
python -m src.agents.advanced_graph_builder_agent --database all --graph-backend cosmos
```

---

## 9. Explore the Graph (REPL)

```bash
python -m src.utils.cosmos_graph_traversal
```

This opens an interactive `cosmos-kg>` prompt. Commands:

| Command | Description |
|---|---|
| `schema` | KG schema — labels, relationship types, counts |
| `concepts` | List all concept nodes |
| `concept <name> [database]` | Tables/databases linked to a concept |
| `tables [database]` | List tables (optionally filter by DB) |
| `node <name>` | Full details + neighbours of any node |
| `cross` | All cross-database edges |
| `search <keyword>` | Keyword search across names & descriptions |
| `subgraph <name> [depth]` | N-hop neighbourhood (default depth=2) |
| `path <name1> -> <name2>` | Shortest path between two nodes |
| `columns <column_name>` | Find tables containing a column |
| `shared` | Concepts bridging multiple databases |
| `dbsummary <database>` | Quick overview of one database |
| `trace <name> -> <database>` | Cross-DB connections from a node to a target DB |
| `gremlin <query>` | Run a raw Gremlin query |

You can also use `CosmosGraphDB` as a library:

```python
from src.utils.cosmos_graph_traversal import CosmosGraphDB

db = CosmosGraphDB()
print(db.schema())
db.close()
```

---

## 10. Run Inference Agents against Cosmos DB

All three inference agent variants support Cosmos DB via the `--graph-backend` flag:

```bash
# ReAct agent
python -m src.agents.inference_agent --graph-backend cosmos \
  "Which tables store crew leave balances and how do they connect across databases?"

# Single-shot (basic) agent
python -m src.agents.basic_inference_agent --graph-backend cosmos \
  "How is a crew member's identity tracked across CLMS, CrewPortal, and PEP?"

# Three-phase (tree) agent
python -m src.agents.tree_inference_agent --graph-backend cosmos \
  "Trace the tables and join paths for performance evaluation linked to compensation."

# Interactive mode
python -m src.agents.inference_agent --graph-backend cosmos
```

If `GRAPH_BACKEND=cosmos` is set in `.env`, the `--graph-backend` flag can be omitted.

---

## 11. Web UI with Cosmos DB Backend

### Option A: Server-wide default

```bash
python -m src.app.app --graph-backend cosmos
```

All queries will use Cosmos DB by default.

### Option B: Per-request toggle (no restart needed)

The Web UI has a **Neo4j / Cosmos DB** toggle button in the header. Clicking "Cosmos DB" sends `"backend": "cosmos"` in the request body, overriding the server default for that query.

### Option C: Environment variable

Set `GRAPH_BACKEND=cosmos` in `.env` and start normally:

```bash
python -m src.app.app
```

---

## 12. Key Design Decisions

### Partition Key: `/database`

Every vertex has a `database` property set to the source database name (`CLMS`, `CrewPortal`, `PEP`, or `Unified`). This means:

- Queries scoped to a single database (e.g. `g.V().has('database', 'CLMS')`) are served from a single partition — fast and cheap
- Cross-database queries work but are cross-partition (still performant at our scale)
- Clearing data per-database is efficient: drop by partition rather than scanning all vertices

### Vertex ID Format

Deterministic, globally unique IDs following the pattern:

```
{database}__{type}__{normalized_name}
```

Examples:
- `CLMS__table__CrewMaster`
- `CrewPortal__concept__crew_identity`

This avoids collisions when multiple databases have tables with the same name.

### Throttling & Retry

The Cosmos DB Gremlin client (`src/utils/cosmos_helpers.py`) includes:
- **429 handling**: Exponential backoff (2^attempt seconds) for rate-limit errors
- **409 handling**: Silent skip for conflict errors during idempotent vertex/edge creation
- **Batch clearing**: Deletes vertices in batches of 50 with 2-second pauses to stay within RU budget

### Edge Deduplication

Edges are deduplicated by (source, target, relationship) to prevent duplicate relationships in the graph.

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Missing Cosmos DB env vars` | `.env` not found or incomplete | Verify `.env` exists in project root with all 4 `COSMOS_DB_*` vars |
| `409 Conflict` errors | Vertex/edge already exists | Normal during idempotent loads — silently skipped |
| `429 Request Rate Too Large` | Exceeded provisioned RU/s | The client auto-retries with backoff. For persistent throttling, increase throughput or switch to autoscale |
| Git Bash converts `/database` to a path | MSYS path conversion | Prefix commands with `MSYS_NO_PATHCONV=1` |
| `GremlinServerError: WebSocket closed` | Connection timeout on large drops | The batch-clearing loop handles this; re-run the command |
| `Could not find a version that satisfies gremlinpython` | Missing Python package | Run `python -m pip install gremlinpython` |

---

## 14. Reference

| Resource | Link |
|---|---|
| Azure Cosmos DB for Apache Gremlin docs | https://learn.microsoft.com/en-us/azure/cosmos-db/gremlin/ |
| Gremlin query language reference | https://tinkerpop.apache.org/gremlin.html |
| Cosmos DB partition key best practices | https://learn.microsoft.com/en-us/azure/cosmos-db/partitioning-overview |
| Azure CLI Cosmos DB commands | https://learn.microsoft.com/en-us/cli/azure/cosmosdb |
| Initial Cosmos DB setup guide (demo script) | [`cosmos/SETUP_GUIDE.md`](../cosmos/SETUP_GUIDE.md) |
