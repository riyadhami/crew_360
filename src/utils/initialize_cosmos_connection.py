"""
initialize_cosmos_connection.py

Initialize Cosmos DB connection from Azure subscription and resource group.
Retrieves connection details using Azure CLI and sets up environment variables.

Usage:
    python src/utils/initialize_cosmos_connection.py
    
This script will:
1. Use Azure CLI to get Cosmos DB details from the specified resource group
2. Retrieve connection string and keys
3. Set environment variables for the application
4. Test the connection
5. Save to .env file (optional)
"""

import os
import sys
import json
import subprocess
import asyncio
import platform
from pathlib import Path

# Fix for Python 3.13 + Windows + gremlinpython asyncio compatibility issue
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Azure Configuration
SUBSCRIPTION_ID = "69642945-f464-4724-ba83-205eecbe5937"
RESOURCE_GROUP = "Indigosetup_04272026"
COSMOS_ACCOUNT_NAME = None  # Will be auto-detected
DATABASE_NAME = "IndigoKG"
GRAPH_CONTAINER = "Unified_Knowledge_graph"


def run_az_command(command: list[str]) -> dict:
    """Run Azure CLI command and return JSON result."""
    try:
        # Join command for shell execution on Windows
        cmd_str = ' '.join(command)
        result = subprocess.run(
            cmd_str,
            capture_output=True,
            text=True,
            check=True,
            shell=True  # Required on Windows to resolve 'az' command
        )
        if result.stdout.strip():
            return json.loads(result.stdout)
        return {}
    except subprocess.CalledProcessError as e:
        print(f"❌ Azure CLI command failed: {' '.join(command)}")
        print(f"Error: {e.stderr}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse Azure CLI output")
        print(f"Output: {result.stdout}")
        sys.exit(1)


def check_azure_login():
    """Check if user is logged into Azure CLI."""
    print("\n📋 Checking Azure CLI login status...")
    try:
        result = subprocess.run(
            "az account show",
            capture_output=True,
            text=True,
            check=True,
            shell=True
        )
        account = json.loads(result.stdout)
        print(f"✅ Logged in as: {account.get('user', {}).get('name', 'unknown')}")
        return True
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        print("❌ Not logged into Azure CLI")
        print("   Run: az login")
        return False


def set_subscription():
    """Set the Azure subscription."""
    print(f"\n🔄 Setting subscription: {SUBSCRIPTION_ID}")
    run_az_command([
        "az", "account", "set",
        "--subscription", SUBSCRIPTION_ID
    ])
    print("✅ Subscription set successfully")


def find_cosmos_account():
    """Find Cosmos DB account in the resource group."""
    global COSMOS_ACCOUNT_NAME
    
    print(f"\n🔍 Searching for Cosmos DB account in resource group: {RESOURCE_GROUP}")
    
    resources = run_az_command([
        "az", "cosmosdb", "list",
        "--resource-group", RESOURCE_GROUP,
        "--output", "json"
    ])
    
    if not resources or len(resources) == 0:
        print(f"❌ No Cosmos DB accounts found in resource group: {RESOURCE_GROUP}")
        sys.exit(1)
    
    # Use the first Cosmos DB account found
    COSMOS_ACCOUNT_NAME = resources[0]["name"]
    endpoint = resources[0]["documentEndpoint"]
    
    print(f"✅ Found Cosmos DB account: {COSMOS_ACCOUNT_NAME}")
    print(f"   Endpoint: {endpoint}")
    
    # Convert documentEndpoint to Gremlin endpoint
    gremlin_endpoint = endpoint.replace("https://", "").replace("/", "")
    gremlin_endpoint = gremlin_endpoint.replace(".documents.azure.com", ".gremlin.cosmos.azure.com")
    
    return gremlin_endpoint


def get_cosmos_keys():
    """Retrieve Cosmos DB keys."""
    print(f"\n🔑 Retrieving Cosmos DB keys for: {COSMOS_ACCOUNT_NAME}")
    
    keys = run_az_command([
        "az", "cosmosdb", "keys", "list",
        "--name", COSMOS_ACCOUNT_NAME,
        "--resource-group", RESOURCE_GROUP,
        "--type", "keys",
        "--output", "json"
    ])
    
    primary_key = keys.get("primaryMasterKey")
    if not primary_key:
        print("❌ Failed to retrieve primary key")
        sys.exit(1)
    
    print("✅ Primary key retrieved successfully")
    return primary_key


def check_database_exists(endpoint: str, key: str):
    """Check if the specified database exists."""
    print(f"\n🔍 Checking if database '{DATABASE_NAME}' exists...")
    
    databases = run_az_command([
        "az", "cosmosdb", "gremlin", "database", "list",
        "--account-name", COSMOS_ACCOUNT_NAME,
        "--resource-group", RESOURCE_GROUP,
        "--output", "json"
    ])
    
    db_names = [db["name"] for db in databases]
    
    if DATABASE_NAME in db_names:
        print(f"✅ Database '{DATABASE_NAME}' exists")
        return True
    else:
        print(f"⚠️  Database '{DATABASE_NAME}' not found")
        print(f"   Available databases: {', '.join(db_names)}")
        return False


def check_graph_exists():
    """Check if the specified graph container exists."""
    print(f"\n🔍 Checking if graph container '{GRAPH_CONTAINER}' exists...")
    
    try:
        graphs = run_az_command([
            "az", "cosmosdb", "gremlin", "graph", "list",
            "--account-name", COSMOS_ACCOUNT_NAME,
            "--resource-group", RESOURCE_GROUP,
            "--database-name", DATABASE_NAME,
            "--output", "json"
        ])
        
        graph_names = [g["name"] for g in graphs]
        
        if GRAPH_CONTAINER in graph_names:
            print(f"✅ Graph container '{GRAPH_CONTAINER}' exists")
            return True
        else:
            print(f"⚠️  Graph container '{GRAPH_CONTAINER}' not found")
            print(f"   Available graphs: {', '.join(graph_names)}")
            return False
    except:
        print(f"⚠️  Could not list graph containers (database may not exist)")
        return False


def test_connection(endpoint: str, key: str):
    """Test the Cosmos DB connection using gremlin_python."""
    print(f"\n🧪 Testing connection to Cosmos DB...")
    
    try:
        from gremlin_python.driver import client, serializer
        
        gremlin_client = client.Client(
            f'wss://{endpoint}',
            'g',
            username=f"/dbs/{DATABASE_NAME}/colls/{GRAPH_CONTAINER}",
            password=key,
            message_serializer=serializer.GraphSONSerializersV2d0()
        )
        
        # Test query
        callback = gremlin_client.submit("g.V().count()")
        result = callback.result()
        count = result.one()
        
        print(f"✅ Connection successful!")
        print(f"   Vertex count: {count}")
        
        gremlin_client.close()
        return True
        
    except ImportError:
        print("⚠️  gremlinpython not installed - skipping connection test")
        print("   Install with: pip install gremlinpython")
        return None
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        return False


def save_to_env_file(endpoint: str, key: str):
    """Save connection details to .env file."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    
    print(f"\n💾 Do you want to save these settings to .env file?")
    print(f"   Location: {env_path}")
    
    response = input("   Save to .env? (y/N): ").strip().lower()
    
    if response == 'y':
        # Read existing .env if it exists
        existing_env = {}
        if env_path.exists():
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key_name, value = line.split('=', 1)
                        existing_env[key_name] = value
        
        # Update with new values
        existing_env['COSMOS_DB_ENDPOINT'] = endpoint
        existing_env['COSMOS_DB_KEY'] = key
        existing_env['COSMOS_DB_DATABASE'] = DATABASE_NAME
        existing_env['COSMOS_DB_GRAPH'] = GRAPH_CONTAINER
        existing_env['AZURE_SUBSCRIPTION_ID'] = SUBSCRIPTION_ID
        existing_env['AZURE_RESOURCE_GROUP'] = RESOURCE_GROUP
        
        # Write back to file
        with open(env_path, 'w') as f:
            f.write("# Azure Cosmos DB Configuration\n")
            f.write("# Auto-generated by initialize_cosmos_connection.py\n\n")
            for key_name, value in existing_env.items():
                f.write(f"{key_name}={value}\n")
        
        print(f"✅ Configuration saved to .env")
    else:
        print("   Skipped saving to .env")


def print_summary(endpoint: str, key: str):
    """Print summary of connection details."""
    print("\n" + "="*80)
    print("COSMOS DB CONNECTION INITIALIZED")
    print("="*80)
    print(f"Subscription:     {SUBSCRIPTION_ID}")
    print(f"Resource Group:   {RESOURCE_GROUP}")
    print(f"Cosmos Account:   {COSMOS_ACCOUNT_NAME}")
    print(f"Endpoint:         {endpoint}")
    print(f"Database:         {DATABASE_NAME}")
    print(f"Graph Container:  {GRAPH_CONTAINER}")
    print("="*80)
    
    print("\n📝 To use these settings in your application:")
    print(f"   export COSMOS_DB_ENDPOINT={endpoint}")
    print(f"   export COSMOS_DB_KEY={key}")
    print(f"   export COSMOS_DB_DATABASE={DATABASE_NAME}")
    print(f"   export COSMOS_DB_GRAPH={GRAPH_CONTAINER}")
    
    print("\n📝 Or set them in your current session:")
    print(f'   os.environ["COSMOS_DB_ENDPOINT"] = "{endpoint}"')
    print(f'   os.environ["COSMOS_DB_KEY"] = "{key}"')
    print(f'   os.environ["COSMOS_DB_DATABASE"] = "{DATABASE_NAME}"')
    print(f'   os.environ["COSMOS_DB_GRAPH"] = "{GRAPH_CONTAINER}"')
    print()


def main():
    """Main initialization flow."""
    print("="*80)
    print("COSMOS DB CONNECTION INITIALIZER")
    print("="*80)
    print(f"Target Subscription: {SUBSCRIPTION_ID}")
    print(f"Target Resource Group: {RESOURCE_GROUP}")
    print("="*80)
    
    # Step 1: Check Azure login
    if not check_azure_login():
        sys.exit(1)
    
    # Step 2: Set subscription
    set_subscription()
    
    # Step 3: Find Cosmos DB account
    endpoint = find_cosmos_account()
    
    # Step 4: Get keys
    key = get_cosmos_keys()
    
    # Step 5: Check database exists
    db_exists = check_database_exists(endpoint, key)
    
    # Step 6: Check graph exists (only if database exists)
    if db_exists:
        check_graph_exists()
    
    # Step 7: Test connection
    test_result = test_connection(endpoint, key)
    
    # Step 8: Print summary
    print_summary(endpoint, key)
    
    # Step 9: Optionally save to .env
    save_to_env_file(endpoint, key)
    
    if test_result == False:
        print("\n⚠️  Warning: Connection test failed. Please verify the configuration.")
        sys.exit(1)
    
    print("\n✅ Initialization complete!")


if __name__ == "__main__":
    main()
