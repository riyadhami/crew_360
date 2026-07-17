#!/usr/bin/env python3
"""
Cosmos DB Container Purge Utility

This script purges data from any Cosmos DB Gremlin graph container.
You can purge all data or filter by database property.

Usage:
    # Purge all data from a container:
    python -m src.utils.purge_cosmos_container --container crew_leave_management
    
    # Purge only CLMS data from a container:
    python -m src.utils.purge_cosmos_container --container crew_leave_management --database CLMS
    
    # Purge HRData from hr_master_recruitment container:
    python -m src.utils.purge_cosmos_container --container hr_master_recruitment --database HRData
    
    # Purge all data from multiple containers:
    python -m src.utils.purge_cosmos_container --container crew_leave_management crew_performance_evaluation
"""

import argparse
import sys

from .cosmos_helpers import get_cosmos_client, run_gremlin, close_cosmos_client


def purge_container(graph_container: str, database_filter: str | None = None) -> dict:
    """Purge data from a Cosmos DB Gremlin graph container.
    
    Args:
        graph_container: Name of the graph container to purge
        database_filter: Optional database name to filter (e.g., 'CLMS', 'HRData')
                        If None, purges ALL data in the container
    
    Returns:
        dict with purge statistics: {
            'vertices_deleted': int,
            'edges_deleted': int,
            'success': bool,
            'error': str | None
        }
    """
    print("=" * 70)
    if database_filter:
        print(f"🗑️  PURGING {database_filter} DATA FROM COSMOS DB")
    else:
        print("🗑️  PURGING ALL DATA FROM COSMOS DB CONTAINER")
    print("=" * 70)
    print(f"Graph Container: {graph_container}")
    if database_filter:
        print(f"Database Filter: {database_filter}")
    else:
        print("⚠️  WARNING: No database filter - will delete ALL data in container!")
    print()
    
    stats = {
        'vertices_deleted': 0,
        'edges_deleted': 0,
        'success': False,
        'error': None,
    }
    
    client = None
    try:
        # Connect to Cosmos DB Gremlin
        print("📡 Connecting to Cosmos DB...")
        client = get_cosmos_client(graph_container)
        print("✅ Connected successfully!")
        print()
        
        # Build queries based on filter
        if database_filter:
            vertex_count_query = f"g.V().has('database', '{database_filter}').count()"
            edge_count_query = f"g.E().has('database', '{database_filter}').count()"
            drop_edges_query = f"g.E().has('database', '{database_filter}').drop()"
            drop_vertices_query = f"g.V().has('database', '{database_filter}').drop()"
        else:
            vertex_count_query = "g.V().count()"
            edge_count_query = "g.E().count()"
            drop_edges_query = "g.E().drop()"
            drop_vertices_query = "g.V().drop()"
        
        # Count vertices before deletion
        print("🔍 Counting vertices...")
        result = run_gremlin(client, vertex_count_query)
        vertex_count = result[0] if result else 0
        print(f"   Found {vertex_count} vertices")
        
        # Count edges before deletion
        print("🔍 Counting edges...")
        result = run_gremlin(client, edge_count_query)
        edge_count = result[0] if result else 0
        print(f"   Found {edge_count} edges")
        print()
        
        if vertex_count == 0 and edge_count == 0:
            print("ℹ️  No data found. Nothing to delete.")
            print()
            stats['success'] = True
            return stats
        
        # Delete edges first (edges must be deleted before vertices)
        if edge_count > 0:
            print(f"🗑️  Deleting {edge_count} edges...")
            run_gremlin(client, drop_edges_query)
            stats['edges_deleted'] = edge_count
            print("✅ Edges deleted!")
            print()
        
        # Delete vertices
        if vertex_count > 0:
            print(f"🗑️  Deleting {vertex_count} vertices...")
            run_gremlin(client, drop_vertices_query)
            stats['vertices_deleted'] = vertex_count
            print("✅ Vertices deleted!")
            print()
        
        # Verify deletion
        print("✔️  Verifying deletion...")
        result = run_gremlin(client, vertex_count_query)
        remaining = result[0] if result else 0
        if remaining == 0:
            print("✅ All data successfully purged from Cosmos DB!")
            stats['success'] = True
        else:
            print(f"⚠️  Warning: {remaining} vertices still remain")
            stats['error'] = f"{remaining} vertices still remain after purge"
        print()
        
    except Exception as exc:
        error_msg = f"Error purging data from Cosmos DB: {exc}"
        print(f"❌ {error_msg}")
        print()
        stats['error'] = str(exc)
        raise
    finally:
        if client:
            close_cosmos_client(client)
            print("🔌 Cosmos DB connection closed.")
            print()
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Purge data from Cosmos DB Gremlin graph containers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Purge all data from crew_leave_management:
  python -m src.utils.purge_cosmos_container --container crew_leave_management
  
  # Purge only CLMS data:
  python -m src.utils.purge_cosmos_container --container crew_leave_management --database CLMS
  
  # Purge multiple containers:
  python -m src.utils.purge_cosmos_container --container crew_leave_management hr_master_recruitment
        """
    )
    parser.add_argument(
        "--container",
        nargs="+",
        required=True,
        metavar="NAME",
        help="Cosmos DB graph container name(s) to purge. Can specify multiple.",
    )
    parser.add_argument(
        "--database",
        default=None,
        metavar="NAME",
        help="Optional database filter (e.g., CLMS, HRData). If not specified, purges ALL data in container.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt and proceed with purge immediately",
    )
    
    args = parser.parse_args()
    
    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + "  COSMOS DB CONTAINER PURGE UTILITY".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print()
    
    containers = args.container
    database_filter = args.database
    
    # Show what will be purged
    print("📋 Purge Summary:")
    print(f"   Containers: {', '.join(containers)}")
    if database_filter:
        print(f"   Database Filter: {database_filter}")
    else:
        print("   ⚠️  Database Filter: NONE (will delete ALL data)")
    print()
    
    # Confirmation prompt
    if not args.yes:
        if database_filter:
            prompt = f"Are you sure you want to purge {database_filter} data from {len(containers)} container(s)? (yes/no): "
        else:
            prompt = f"⚠️  WARNING: This will delete ALL data from {len(containers)} container(s)! Type 'yes' to confirm: "
        
        response = input(prompt).strip().lower()
        if response not in ['yes', 'y']:
            print("❌ Purge cancelled.")
            sys.exit(0)
        print()
    
    # Purge each container
    total_stats = {
        'vertices_deleted': 0,
        'edges_deleted': 0,
        'containers_purged': 0,
        'containers_failed': 0,
    }
    
    for container in containers:
        try:
            stats = purge_container(container, database_filter)
            if stats['success']:
                total_stats['containers_purged'] += 1
            else:
                total_stats['containers_failed'] += 1
            total_stats['vertices_deleted'] += stats['vertices_deleted']
            total_stats['edges_deleted'] += stats['edges_deleted']
        except Exception as exc:
            total_stats['containers_failed'] += 1
            print(f"❌ Failed to purge container '{container}': {exc}")
            print()
    
    # Final summary
    print("=" * 70)
    print("📊 PURGE SUMMARY")
    print("=" * 70)
    print(f"   Containers purged: {total_stats['containers_purged']}/{len(containers)}")
    if total_stats['containers_failed'] > 0:
        print(f"   ⚠️  Failed: {total_stats['containers_failed']}")
    print(f"   Total vertices deleted: {total_stats['vertices_deleted']}")
    print(f"   Total edges deleted: {total_stats['edges_deleted']}")
    print("=" * 70)
    
    if total_stats['containers_failed'] > 0:
        sys.exit(1)
    else:
        print("✅ All containers purged successfully!")
        print()


if __name__ == "__main__":
    main()
