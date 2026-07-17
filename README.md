# Indigo Airlines Knowledge Layer

A unified knowledge graph system for Indigo Airlines that integrates multiple HR databases (HRData, IJP, CLMS, PEP) into a semantic layer powered by Azure Cosmos DB (Gremlin API) and SQL Server, with AI-powered data retrieval agents.

## 🎯 Overview

This system provides:
- **Unified Knowledge Graph**: Merges concepts across multiple databases into a single Cosmos DB (Gremlin API) or Neo4j graph
- **Multiple AI Agents**: Suite of specialized agents for different query complexity levels
  - **Data Retrieval Agent**: Retrieves actual data from physical databases using KG metadata
  - **Tree Inference Agent**: Multi-threaded advanced graph exploration
  - **Standard Inference Agent**: Single-path graph exploration
  - **Basic Inference Agent**: Simple concept lookups
- **Employee Performance Scoring**: Weighted scoring system based on multiple performance criteria
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
│ • Cosmos DB Gremlin │         │  - HRData                  │
│ • Neo4j (Optional)  │         │  - IJP_Employee_scores     │
│ • Concepts          │         │  - CLMS                    │
│ • Tables/Columns    │         │  - PEP                     │
│ • Relationships     │         │  - CrewPortal              │
└─────────────────────┘         └────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  Azure Infrastructure Layer                      │
│  • Container Apps (Graph Builder, Unifier)                      │
│  • Azure AI Foundry (OpenAI, Embeddings, Custom Connections)   │
│  • Container Registry, Key Vault, Application Insights         │
└─────────────────────────────────────────────────────────────────┘
```

## 📋 Prerequisites

### System Requirements
- **Python**: 3.11 or higher
- **SQL Server**: Any version with ODBC Driver 17+
- **Azure Cosmos DB**: Account with Gremlin API enabled (primary graph database)
- **Neo4j**: Optional alternative graph database
- **Azure OpenAI**: GPT-4 deployment for LLM inference
- **Azure AI Foundry**: For deployment and Prompt Flow integration (optional)

### Platform-Specific Requirements

> **Note**: These requirements are only needed if running the **Data Retrieval Agent locally** with SQL Server on Docker or a local SQL Server instance. If using only the Knowledge Graph agents (without physical database queries) or deploying to Azure Container Apps, these are handled by the container images.

**Windows:**
- [Microsoft ODBC Driver 17+ for SQL Server](https://docs.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)

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
brew update
brew install msodbcsql18
```

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

# For development (optional)
pip install -r requirements-dev.txt
```

### 4. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
# Azure OpenAI (for LLM)
AZURE_OPENAI_ENDPOINT=https://your-openai-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2024-08-01-preview

# Cosmos DB (Gremlin API)
COSMOS_DB_ENDPOINT=your-cosmos-account.gremlin.cosmos.azure.com
COSMOS_DB_KEY=your-cosmos-key-here
COSMOS_DB_DATABASE=IndigoKG
COSMOS_DB_GRAPH=unified_graph

# SQL Server
SQL_SERVER_HOST=localhost
SQL_SERVER_PORT=1433
SQL_SERVER_USER=sa
SQL_SERVER_PASSWORD=YourStrong@Passw0rd
```

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
│   │   ├── check_hrdata_graph_nodes.py      # Verify HRData nodes in graph
│   │   ├── check_hrdata_tables.py           # Verify HRData tables in SQL Server
│   │   ├── load_hr_to_sqlserver.py          # Load HRData CSV into SQL Server
│   │   ├── load_ijp_to_sqlserver.py         # Load IJP data into SQL Server
│   │   ├── query_hr_from_sqlserver.py       # Query HRData from SQL Server
│   │   ├── purge_logs.py                    # Clean up old log files
│   │   └── __init__.py
│   └── graph_unification.py                 # Graph merging logic with embeddings
├── db_schemas_csv/                          # Database schema definitions
│   ├── HRData_Schema.csv
│   ├── IJP_Employee_scores_Schema.csv
│   ├── CLMS Table Details.csv
│   ├── PEP_Schema_Defn_2026-04-15.csv
│   └── ...
├── docs/                                    # Documentation
│   ├── cosmos_db_setup.md                   # Cosmos DB setup guide
│   └── employee_scoring.md                  # Employee scoring methodology
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
├── tests/                                   # Test suite
├── output/                                  # Individual concept graphs (gitignored)
├── unified_output/                          # Unified graph output (gitignored)
├── logs/                                    # Agent execution logs (gitignored)
├── test_employee_scoring.py                 # Employee scoring test script
├── .env                                     # Environment config (gitignored)
├── requirements.txt                         # Python dependencies
└── README.md                                # This file
```

## 🎮 Usage

### 1. Build Individual Concept Graphs

Extract concepts from each database schema:

```bash
# Build concept graph for HRData
python -m src.agents.advanced_graph_builder_agent --csv db_schemas_csv/HRData_Schema.csv --output output

# Build for IJP
python -m src.agents.advanced_graph_builder_agent --csv db_schemas_csv/IJP_Employee_scores_Schema.csv --output output
```

### 2. Unify Concept Graphs

Merge multiple concept graphs into a unified graph:

```bash
# Auto-discover and unify all graphs in output/ folder
python -m src.graph_unification --auto-discover --output unified_output

# Or specify graphs explicitly
python -m src.graph_unification --graphs output/HRData_concept_graph.json output/IJP_concept_graph.json
```

### 3. Load into Cosmos DB

```bash
python -m src.utils.initialize_cosmos_connection
```

### 4. Run the Web Application

```bash
# Run with default settings (Cosmos DB)
python -m src.end-user-app.app

# Or specify graph backend explicitly
python -m src.end-user-app.app --graph-backend cosmos
python -m src.end-user-app.app --graph-backend neo4j
```

Access the interfaces:
- **Basic Interface**: http://localhost:5000/basic (Simple KG queries)
- **Advanced Interface**: http://localhost:5000/advanced (Multi-threaded exploration)
- **Data Retrieval**: http://localhost:5000/data (Physical DB queries with SQL generation)

### 5. Test the Data Retrieval Agent

```bash
# Run comprehensive test suite
python -m src.utils.test_data_agent_comprehensive

# Test with custom query
python -m src.utils.test_data_agent_comprehensive --query "Show performance metrics for Akash Saxena"

# Test find_join_keys function
python -m src.utils.test_data_agent_comprehensive --test join_keys
```

### 6. Test Employee Scoring

```bash
# Calculate score by employee name
python test_employee_scoring.py --name "Akash Saxena"

# Calculate score by IGA number
python test_employee_scoring.py --iga 62913

# Output as JSON
python test_employee_scoring.py --name "Kamal Choudhury" --json
```

See [docs/employee_scoring.md](docs/employee_scoring.md) for methodology details.

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

# Initialize/load unified graph into Cosmos DB
python -m src.utils.initialize_cosmos_connection

# Create Cosmos DB database and graph containers
python -m src.utils.create_cosmos_graph_containers

# Purge all data from Cosmos DB graph
python -m src.utils.purge_cosmos_container
```

**SQL Server Data Management:**
```bash
# Load HRData CSV into SQL Server
python -m src.utils.load_hr_to_sqlserver

# Load IJP data into SQL Server
python -m src.utils.load_ijp_to_sqlserver

# Query HRData from SQL Server
python -m src.utils.query_hr_from_sqlserver

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

### 6. Employee Performance Scoring
Comprehensive weighted scoring system with 6 components:
- Crew Availability (50%) - Based on LWP days
- Poise & Grace/BMI (10%) - Physical fitness indicator
- Appreciation Letters (20%) - Positive recognition
- Non-performance Discussions (10%) - Disciplinary records
- Curative Training/iCoach (5%) - Coaching sessions
- Extra Initiatives/Recognition (5%) - Special awards

### 7. Flexible Graph Backend
- Primary: Azure Cosmos DB with Gremlin API
- Alternative: Neo4j (legacy support)
- Switch backends via command-line flag or environment variable

### 8. Production-Ready Infrastructure
- Azure Container Apps for agent hosting
- Bicep templates for reproducible deployments
- Azure AI Foundry integration with custom connections
- Managed Identity, Key Vault, Application Insights
- Health checks and monitoring

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
**Solution**: Install Microsoft ODBC Driver 17+ for SQL Server

### Issue: Cosmos DB Connection Timeout
**Solution**: Check firewall rules and ensure IP is whitelisted in Cosmos DB

### Issue: Agent Returns 0 Rows
**Solution**: 
- Verify entity exists in database using test scripts
- Check agent logs in `logs/` folder
- Ensure `find_join_keys` is being called
- Try different agent interfaces (Basic/Advanced/Data)

### Issue: Container Apps Deployment Fails
**Solution**: 
- Check Azure subscription quota for Container Apps
- Verify all required Azure services are available in target region
- Review [infra/Infra_README.md](infra/Infra_README.md) for prerequisites
- Check deployment logs in Application Insights

### Issue: Import Errors
**Solution**: Ensure virtual environment is activated and dependencies installed

## 📝 License

MIT License

Copyright (c) Microsoft Corporation. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
