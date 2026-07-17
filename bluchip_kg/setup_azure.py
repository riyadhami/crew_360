"""
Azure Infrastructure Setup for BluChip 360 Knowledge Graph
============================================================

Run this ONCE before graph_builder.py.
It provisions everything you need in Azure and writes your .env file.

What it creates:
  - Resource group  (if it doesn't exist)
  - Cosmos DB account with Gremlin API
  - Gremlin database
  - Gremlin graph container  (partition key: /pk)
  - Writes credentials to bluchip_kg/.env automatically

Prerequisites:
  - Azure CLI installed  →  https://aka.ms/installazurecli
  - Logged in           →  run:  az login

Usage:
    python setup_azure.py
    python setup_azure.py --resource-group myRG --location eastus --account-name mycosmosaccount
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"

# ── Defaults (override via CLI args) ─────────────────────────────────────────
DEFAULTS = {
    "resource_group":  "bluchip-kg-rg",
    "location":        "eastus",          # az account list-locations -o table
    "account_name":    "bluchip-kg-db",   # must be globally unique, lowercase, 3-44 chars
    "database_name":   "BluChipKG",
    "graph_name":      "BluChip_360",
    "throughput":      400,               # RU/s  —  400 is the free-tier minimum
}


# ── Shell helper ──────────────────────────────────────────────────────────────

def run(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run an az CLI command and return the result."""
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        print(f"\n[ERROR] Command failed:\n  {' '.join(cmd)}")
        print(result.stderr.strip())
        sys.exit(1)
    return result


def az(*args, check: bool = True) -> dict | list | str | None:
    """Run an az command and parse JSON output."""
    cmd = ["az"] + list(args) + ["--output", "json"]
    result = run(cmd, check=check)
    if result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip()
    return None


# ── Steps ─────────────────────────────────────────────────────────────────────

def check_az_login():
    print("Checking Azure CLI login…")
    result = run(["az", "account", "show"], check=False)
    if result.returncode != 0:
        print("\nYou are not logged in. Running 'az login'…")
        run(["az", "login"], capture=False)
    account = json.loads(run(["az", "account", "show"], capture=True).stdout)
    print(f"  Logged in as : {account['user']['name']}")
    print(f"  Subscription : {account['name']}  ({account['id']})")
    print()


def create_resource_group(rg: str, location: str):
    print(f"Resource group  →  {rg}  ({location})")
    existing = run(
        ["az", "group", "exists", "--name", rg, "--output", "json"],
        capture=True, check=True
    )
    if existing.stdout.strip() == "true":
        print("  Already exists, skipping.\n")
        return
    az("group", "create", "--name", rg, "--location", location)
    print("  Created.\n")


def create_cosmos_account(rg: str, account: str, location: str) -> str:
    print(f"Cosmos DB account  →  {account}")
    # Check if already exists
    existing = az("cosmosdb", "show", "--resource-group", rg, "--name", account, check=False)
    if existing and isinstance(existing, dict) and existing.get("id"):
        print("  Already exists, skipping creation.\n")
    else:
        print("  Creating Cosmos DB account (Gremlin API)…")
        print("  This takes 3–5 minutes, please wait…")
        az(
            "cosmosdb", "create",
            "--resource-group",    rg,
            "--name",              account,
            "--locations",         f"regionName={location}",
            "--capabilities",      "EnableGremlin",
            "--default-consistency-level", "Session",
        )
        print("  Created.\n")

    # Return the Gremlin endpoint
    props = az("cosmosdb", "show", "--resource-group", rg, "--name", account)
    endpoint = props["documentEndpoint"].replace("https://", "").replace(":443/", "")
    # Gremlin endpoint format: accountname.gremlin.cosmos.azure.com
    gremlin_endpoint = f"{account}.gremlin.cosmos.azure.com"
    print(f"  Endpoint: {gremlin_endpoint}\n")
    return gremlin_endpoint


def create_gremlin_database(rg: str, account: str, db_name: str):
    print(f"Gremlin database  →  {db_name}")
    existing = az(
        "cosmosdb", "gremlin", "database", "show",
        "--resource-group", rg,
        "--account-name",   account,
        "--name",           db_name,
        check=False,
    )
    if existing and isinstance(existing, dict) and existing.get("id"):
        print("  Already exists, skipping.\n")
        return
    az(
        "cosmosdb", "gremlin", "database", "create",
        "--resource-group", rg,
        "--account-name",   account,
        "--name",           db_name,
    )
    print("  Created.\n")


def create_gremlin_graph(rg: str, account: str, db_name: str, graph_name: str, throughput: int):
    print(f"Gremlin graph  →  {graph_name}  (partition key: /pk,  {throughput} RU/s)")
    existing = az(
        "cosmosdb", "gremlin", "graph", "show",
        "--resource-group", rg,
        "--account-name",   account,
        "--database-name",  db_name,
        "--name",           graph_name,
        check=False,
    )
    if existing and isinstance(existing, dict) and existing.get("id"):
        print("  Already exists, skipping.\n")
        return
    az(
        "cosmosdb", "gremlin", "graph", "create",
        "--resource-group",  rg,
        "--account-name",    account,
        "--database-name",   db_name,
        "--name",            graph_name,
        "--partition-key-path", "/pk",
        "--throughput",      str(throughput),
    )
    print("  Created.\n")


def fetch_primary_key(rg: str, account: str) -> str:
    print("Fetching primary key…")
    keys = az("cosmosdb", "keys", "list", "--resource-group", rg, "--name", account)
    key = keys["primaryMasterKey"]
    print("  Done.\n")
    return key


def write_env(endpoint: str, key: str, db_name: str, graph_name: str):
    content = f"""# Auto-generated by setup_azure.py — do not commit this file
COSMOS_DB_ENDPOINT={endpoint}
COSMOS_DB_KEY={key}
COSMOS_DB_DATABASE={db_name}
COSMOS_DB_GRAPH={graph_name}
"""
    ENV_FILE.write_text(content, encoding="utf-8")
    print(f"Credentials written to  →  {ENV_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Provision Azure Cosmos DB for BluChip KG")
    parser.add_argument("--resource-group", default=DEFAULTS["resource_group"])
    parser.add_argument("--location",       default=DEFAULTS["location"],
                        help="Azure region, e.g. eastus, centralindia, southeastasia")
    parser.add_argument("--account-name",   default=DEFAULTS["account_name"],
                        help="Cosmos DB account name — must be globally unique")
    parser.add_argument("--database-name",  default=DEFAULTS["database_name"])
    parser.add_argument("--graph-name",     default=DEFAULTS["graph_name"])
    parser.add_argument("--throughput",     default=DEFAULTS["throughput"], type=int,
                        help="RU/s for the graph container (min 400)")
    args = parser.parse_args()

    print("=" * 60)
    print(" BluChip 360 KG — Azure Setup")
    print("=" * 60)
    print()

    check_az_login()

    create_resource_group(args.resource_group, args.location)
    gremlin_endpoint = create_cosmos_account(args.resource_group, args.account_name, args.location)
    create_gremlin_database(args.resource_group, args.account_name, args.database_name)
    create_gremlin_graph(
        args.resource_group, args.account_name,
        args.database_name,  args.graph_name,
        args.throughput,
    )
    primary_key = fetch_primary_key(args.resource_group, args.account_name)
    write_env(gremlin_endpoint, primary_key, args.database_name, args.graph_name)

    print()
    print("=" * 60)
    print(" All done! Your graph is ready.")
    print(" Next step:")
    print(f"   python -m src.graph_builder --members 20")
    print("=" * 60)


if __name__ == "__main__":
    main()
