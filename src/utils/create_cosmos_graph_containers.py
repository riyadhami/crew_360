"""
create_cosmos_graph_containers.py - Utility to create dedicated Cosmos DB graph containers
for each database schema.

This script creates separate graph containers for CLMS, PEP, CrewPortal, and HRData databases
to enable schema-specific isolation and better organization.

Usage:
    python src/utils/create_cosmos_graph_containers.py --all
    python src/utils/create_cosmos_graph_containers.py --database CLMS
    python src/utils/create_cosmos_graph_containers.py --database PEP
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv()

# Cosmos DB settings from .env
COSMOS_ACCOUNT = os.getenv("COSMOS_DB_ENDPOINT", "").replace(".gremlin.cosmos.azure.com", "")
COSMOS_RG = os.getenv("AZURE_RESOURCE_GROUP", "")
COSMOS_DATABASE = os.getenv("COSMOS_DB_DATABASE", "IndigoKG")

# Import database contexts from the agent
from src.agents.advanced_graph_builder_agent import DATABASE_CONTEXTS, get_graph_container_name


def create_graph_container(database: str, throughput: int = 400) -> bool:
    """Create a Cosmos DB graph container for a specific database using Azure CLI.
    
    Args:
        database: Database name (CLMS, PEP, CrewPortal, HRData)
        throughput: RU/s throughput (default 400)
    
    Returns:
        True if successful, False otherwise
    """
    import subprocess
    
    graph_name = get_graph_container_name(database)
    db_upper = database.upper()
    
    if db_upper not in DATABASE_CONTEXTS:
        print(f"❌ Unknown database: {database}")
        return False
    
    ctx = DATABASE_CONTEXTS[db_upper]
    print(f"\n{'='*70}")
    print(f"Creating graph container: {graph_name}")
    print(f"Database: {database}")
    print(f"Purpose: {ctx['purpose']}")
    print(f"Description: {ctx['description']}")
    print(f"{'='*70}\n")
    
    cmd = [
        "az", "cosmosdb", "gremlin", "graph", "create",
        "--account-name", COSMOS_ACCOUNT,
        "--resource-group", COSMOS_RG,
        "--database-name", COSMOS_DATABASE,
        "--name", graph_name,
        "--partition-key-path", "/database",
        "--throughput", str(throughput)
    ]
    
    print(f"Running: {' '.join(cmd)}\n")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"✅ Successfully created graph container: {graph_name}")
        return True
    except subprocess.CalledProcessError as e:
        if "already exists" in e.stderr.lower() or "conflict" in e.stderr.lower():
            print(f"⚠️  Graph container {graph_name} already exists (skipping)")
            return True
        else:
            print(f"❌ Failed to create graph container: {graph_name}")
            print(f"Error: {e.stderr}")
            return False


def list_existing_containers():
    """List existing graph containers in the Cosmos DB database."""
    import subprocess
    
    print(f"\n{'='*70}")
    print(f"Existing graph containers in database: {COSMOS_DATABASE}")
    print(f"{'='*70}\n")
    
    cmd = [
        "az", "cosmosdb", "gremlin", "graph", "list",
        "--account-name", COSMOS_ACCOUNT,
        "--resource-group", COSMOS_RG,
        "--database-name", COSMOS_DATABASE,
        "--query", "[].{name:name, throughput:resource.throughput}",
        "-o", "table"
    ]
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to list graph containers: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Create Cosmos DB graph containers for database schemas"
    )
    parser.add_argument(
        "--database",
        choices=["CLMS", "PEP", "CrewPortal", "HRData", "IJP"],
        help="Database to create graph container for"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Create graph containers for all databases"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing graph containers"
    )
    parser.add_argument(
        "--throughput",
        type=int,
        default=400,
        help="RU/s throughput (default: 400)"
    )
    
    args = parser.parse_args()
    
    if not COSMOS_ACCOUNT or not COSMOS_RG:
        print("❌ Error: Missing environment variables")
        print("   Set COSMOS_DB_ENDPOINT and AZURE_RESOURCE_GROUP in .env")
        sys.exit(1)
    
    print(f"\nCosmos DB Account: {COSMOS_ACCOUNT}")
    print(f"Resource Group: {COSMOS_RG}")
    print(f"Database: {COSMOS_DATABASE}")
    
    if args.list:
        list_existing_containers()
        return
    
    if args.all:
        databases = list(DATABASE_CONTEXTS.keys())
        print(f"\nCreating graph containers for all databases: {', '.join(databases)}\n")
        
        success_count = 0
        for db in databases:
            if create_graph_container(db, args.throughput):
                success_count += 1
        
        print(f"\n{'='*70}")
        print(f"Summary: {success_count}/{len(databases)} graph containers created successfully")
        print(f"{'='*70}\n")
        
        list_existing_containers()
    
    elif args.database:
        create_graph_container(args.database, args.throughput)
        list_existing_containers()
    
    else:
        parser.print_help()
        print("\nAvailable databases:")
        for db, ctx in DATABASE_CONTEXTS.items():
            graph_name = get_graph_container_name(db)
            print(f"  {db:15} → {graph_name:30} ({ctx['purpose']})")


if __name__ == "__main__":
    main()
