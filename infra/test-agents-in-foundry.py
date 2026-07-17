# Azure AI Foundry Notebook - Test Knowledge Graph Agents
# This notebook demonstrates how to call your deployed Container App agents from Foundry

import requests
import json
from typing import Dict, Any

# ============================================================================
# Configuration
# ============================================================================

GRAPH_BUILDER_URL = "https://indigo-kg-builder.proudocean-a4621ff3.westus2.azurecontainerapps.io"
GRAPH_UNIFIER_URL = "https://indigo-kg-unifier.proudocean-a4621ff3.westus2.azurecontainerapps.io"

# ============================================================================
# Helper Functions
# ============================================================================

def call_graph_builder(database: str, skip_neo4j: bool = True) -> Dict[str, Any]:
    """
    Call the Graph Builder agent to extract concepts from a database schema
    
    Args:
        database: Database name (e.g., "CLMS", "CrewPortal", "PEP")
        skip_neo4j: Whether to skip Neo4j storage (use True for Cosmos DB)
    
    Returns:
        Response JSON with extracted concepts and relationships
    """
    endpoint = f"{GRAPH_BUILDER_URL}/score"
    payload = {
        "database": database,
        "skip_neo4j": skip_neo4j
    }
    
    print(f"🚀 Calling Graph Builder for database: {database}")
    
    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300  # 5 minutes timeout
        )
        response.raise_for_status()
        
        result = response.json()
        print(f"✅ Success! Extracted {len(result.get('concepts', []))} concepts")
        return result
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error calling Graph Builder: {e}")
        raise


def call_graph_unifier(graph1_path: str, graph2_path: str, graph3_path: str = None) -> Dict[str, Any]:
    """
    Call the Graph Unifier agent to merge multiple knowledge graphs
    
    Args:
        graph1_path: Path to first graph JSON file
        graph2_path: Path to second graph JSON file
        graph3_path: Optional path to third graph JSON file
    
    Returns:
        Response JSON with unified knowledge graph
    """
    endpoint = f"{GRAPH_UNIFIER_URL}/score"
    payload = {
        "graph1": graph1_path,
        "graph2": graph2_path
    }
    
    if graph3_path:
        payload["graph3"] = graph3_path
    
    print(f"🚀 Calling Graph Unifier to merge graphs")
    
    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300
        )
        response.raise_for_status()
        
        result = response.json()
        print(f"✅ Success! Unified graph created")
        return result
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error calling Graph Unifier: {e}")
        raise


def check_agent_health(agent_url: str) -> bool:
    """Check if an agent is healthy and responding"""
    try:
        response = requests.get(f"{agent_url}/health", timeout=10)
        is_healthy = response.status_code == 200
        
        if is_healthy:
            print(f"✅ Agent at {agent_url} is healthy")
        else:
            print(f"⚠️  Agent at {agent_url} returned status {response.status_code}")
            
        return is_healthy
        
    except Exception as e:
        print(f"❌ Agent at {agent_url} is not responding: {e}")
        return False


# ============================================================================
# Example 1: Check Agent Health
# ============================================================================

print("=" * 80)
print("🏥 Checking Agent Health")
print("=" * 80)

builder_healthy = check_agent_health(GRAPH_BUILDER_URL)
unifier_healthy = check_agent_health(GRAPH_UNIFIER_URL)

print()

# ============================================================================
# Example 2: Extract Concepts from CLMS Database
# ============================================================================

if builder_healthy:
    print("=" * 80)
    print("📊 Example 2: Extract Concepts from CLMS Database")
    print("=" * 80)
    
    result = call_graph_builder(database="CLMS", skip_neo4j=True)
    
    # Display sample results
    if "concepts" in result:
        print(f"\n📝 Sample Concepts (first 5):")
        for i, concept in enumerate(result["concepts"][:5]):
            print(f"   {i+1}. {concept.get('name', 'Unknown')} - {concept.get('type', 'Unknown')}")
    
    print()

# ============================================================================
# Example 3: Process All Databases
# ============================================================================

if builder_healthy:
    print("=" * 80)
    print("📊 Example 3: Process Multiple Databases")
    print("=" * 80)
    
    databases = ["CLMS", "CrewPortal", "PEP"]
    
    for db in databases:
        try:
            print(f"\n🔄 Processing {db}...")
            result = call_graph_builder(database=db, skip_neo4j=True)
            print(f"   ✅ {db}: {len(result.get('concepts', []))} concepts extracted")
        except Exception as e:
            print(f"   ❌ {db}: Failed - {str(e)}")
    
    print()

# ============================================================================
# Example 4: Unify Multiple Graphs (if you have graph files)
# ============================================================================

if unifier_healthy:
    print("=" * 80)
    print("🔗 Example 4: Unify Knowledge Graphs")
    print("=" * 80)
    print("Note: This requires existing graph JSON files from previous runs")
    print()
    
    # Uncomment and modify paths if you have graph files:
    # result = call_graph_unifier(
    #     graph1_path="output/CLMS_concept_graph.json",
    #     graph2_path="output/CrewPortal_concept_graph.json",
    #     graph3_path="output/PEP_concept_graph.json"
    # )

# ============================================================================
# Example 5: Use in Azure AI Foundry Prompt Flow
# ============================================================================

print("=" * 80)
print("💡 Using in Prompt Flow")
print("=" * 80)
print("""
To use these agents in Prompt Flow:

1. Create a new Prompt Flow in Azure AI Foundry Studio
2. Add an HTTP tool with these settings:
   - URL: {GRAPH_BUILDER_URL}/score
   - Method: POST
   - Headers: {"Content-Type": "application/json"}
   - Body: {"database": "CLMS", "skip_neo4j": true}

3. Connect the HTTP output to downstream tools for analysis

Example Prompt Flow Structure:
   Input → HTTP (Graph Builder) → LLM (Analyze) → Output
""")

print("\n✅ Notebook execution complete!")
