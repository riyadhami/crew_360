"""
Comprehensive Test Suite for Data Retrieval Agent

This script combines and replaces the following test files:
- test_agent_akash.py (root directory)
- src/utils/test_data_retrieval_agent.py
- test_find_join_keys.py (root directory)

All functionality from these scripts has been unified into this comprehensive test suite.

Tests:
1. find_join_keys function - Validates JOIN column detection
2. Data Retrieval Agent - Tests end-to-end query execution with real data
3. Multiple query scenarios - Person-specific, performance metrics, etc.

Usage:
    python -m src.utils.test_data_agent_comprehensive
    python -m src.utils.test_data_agent_comprehensive --test join_keys
    python -m src.utils.test_data_agent_comprehensive --test agent
    python -m src.utils.test_data_agent_comprehensive --query "your custom query"
"""

import sys
import json
import argparse
from datetime import datetime
from typing import Dict, Any

# Fix for Python 3.13 + Windows + gremlinpython asyncio compatibility issue
import asyncio
import platform
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add project root to path for imports
sys.path.insert(0, ".")

from src.agents.Data_Retrieval_Agent_New import run_agent, find_join_keys
from src.utils.cosmos_graph_traversal import CosmosGraphDB


# ============================================================================
# Test 1: find_join_keys Function
# ============================================================================

def test_find_join_keys(tables=None, show_details=True):
    """
    Test the enhanced find_join_keys function with metadata and semantic analysis.
    
    Args:
        tables: List of table names to analyze (default: ["Indigo_HR_Raw_Data", "IJP_Employee_scores"])
        show_details: Whether to show detailed output (default: True)
    
    Returns:
        dict: Analysis results from find_join_keys
    """
    if tables is None:
        tables = ["Indigo_HR_Raw_Data", "IJP_Employee_scores"]
    
    print("\n" + "=" * 80)
    print("TEST 1: find_join_keys Function")
    print("=" * 80)
    print(f"Analyzing tables: {tables}")
    print("-" * 80)
    
    try:
        # Get graph client
        graph_db = CosmosGraphDB()
        
        # Run find_join_keys
        result = find_join_keys(graph_db, tables)
        
        if show_details:
            # Display detailed results
            print(f"\n📊 ANALYSIS RESULTS:")
            print(f"   Tables analyzed: {result.get('tables_analyzed')}")
            
            print(f"\n🔑 ID Columns per table:")
            for table, ids in result.get('id_columns_per_table', {}).items():
                print(f"   - {table}: {ids}")
            
            print(f"\n✅ Shared ID columns:")
            shared_ids = result.get('shared_id_columns', {})
            if shared_ids:
                for col, tables_info in shared_ids.items():
                    print(f"   - [{col}]: appears in {len(tables_info)} tables")
                    for t_info in tables_info:
                        print(f"       • {t_info['table']} ({t_info['database']})")
            else:
                print("   (none found)")
            
            print(f"\n⚠️ Shared non-ID columns:")
            shared_other = result.get('shared_other_columns', {})
            if shared_other:
                for col, tables_info in shared_other.items():
                    print(f"   - [{col}]: appears in {len(tables_info)} tables (NOT recommended for JOIN)")
            else:
                print("   (none found)")
            
            print(f"\n🔗 Graph relationships:")
            relationships = result.get('graph_relationships', [])
            if relationships:
                for rel in relationships:
                    print(f"   - {rel['from_table']} {rel['relationship']} {rel['to_table']}")
            else:
                print("   (none found)")
            
            print(f"\n📋 JOIN Recommendations:")
            for i, rec in enumerate(result.get('join_recommendations', []), 1):
                print(f"\n   {i}. Column: [{rec['column']}]")
                print(f"      Priority: {rec['priority']}")
                print(f"      Recommendation: {rec['recommendation']}")
                print(f"      Reason: {rec['reason']}")
                if 'example' in rec and '❌' not in rec['example']:
                    print(f"      Example:\n      {rec['example']}")
            
            print(f"\n🎯 BEST JOIN COLUMN: {result.get('best_join_column')}")
            
            print(f"\n💡 Semantic Note:")
            print(f"   {result.get('semantic_note')}")
            
            if result.get('warning'):
                print(f"\n⚠️ WARNING:")
                print(f"   {result['warning']}")
            
            print(f"\n📊 Column Analysis (shared columns):")
            for col, analysis in result.get('column_analysis', {}).items():
                print(f"   - [{col}]:")
                print(f"       Type: {analysis['type']}")
                print(f"       Is ID: {analysis['is_id']}")
                print(f"       Appears in: {', '.join(analysis['appears_in'])}")
        
        # Close connection
        graph_db.close()
        
        # Test result summary
        best_col = result.get('best_join_column')
        if best_col:
            print(f"\n✅ TEST PASSED: Identified best join column: [{best_col}]")
        else:
            print(f"\n⚠️ TEST WARNING: No shared ID column found")
        
        return result
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================================
# Test 2: Data Retrieval Agent
# ============================================================================

def test_data_retrieval_agent(query: str, verbose: bool = True, show_summary: bool = True) -> Dict[str, Any]:
    """
    Test the data retrieval agent with a specific query.
    
    Args:
        query: The query string to test
        verbose: Whether to show agent progress (default: True)
        show_summary: Whether to show result summary (default: True)
    
    Returns:
        dict: Agent result containing answer, iterations, etc.
    """
    print("\n" + "=" * 80)
    print("TEST 2: Data Retrieval Agent")
    print("=" * 80)
    print(f"Query: {query}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Backend: cosmos (Unified Knowledge Graph)")
    print("-" * 80)
    
    try:
        # Run the agent
        result = run_agent(
            query=query,
            verbose=verbose,
            backend="cosmos"
        )
        
        if show_summary:
            # Display results summary
            print("\n" + "-" * 80)
            print("RESULT SUMMARY")
            print("-" * 80)
            print(f"Iterations: {result.get('iterations', 'N/A')}")
            print(f"Status: {'✅ Success' if result.get('answer') else '❌ No answer'}")
            
            answer = result.get('answer', 'No answer returned')
            
            # Try to parse and display structured information
            try:
                if answer and answer.strip().startswith('{'):
                    answer_dict = json.loads(answer)
                    
                    # Extract data results
                    if 'data' in answer_dict:
                        data = answer_dict['data']
                        if isinstance(data, list) and len(data) > 0:
                            print(f"\n📊 Retrieved {len(data)} row(s)")
                            print(f"\nFirst row:")
                            print(json.dumps(data[0], indent=2))
                        elif isinstance(data, list):
                            print(f"\n⚠️ No data rows returned")
                    
                    # Extract query plan
                    if 'reformulated_query' in answer_dict:
                        print(f"\n🔍 Query used:")
                        print(f"```sql\n{answer_dict['reformulated_query']}\n```")
                    
                    # Extract subgraph info
                    if 'subgraph' in answer_dict and 'tables' in answer_dict['subgraph']:
                        tables = answer_dict['subgraph']['tables']
                        print(f"\n🗂️ Tables involved: {len(tables)}")
                        for tbl in tables:
                            db = tbl.get('database', 'unknown')
                            name = tbl.get('table', 'unknown')
                            print(f"   - {db}.{name}")
                    
                    # Show notes if present
                    if 'notes' in answer_dict:
                        print(f"\n📝 Notes: {answer_dict['notes']}")
                        
            except (json.JSONDecodeError, AttributeError):
                # If not JSON, show plain text summary
                if len(answer) > 500:
                    print(f"\n📄 Answer (truncated):")
                    print(answer[:500] + "...")
                else:
                    print(f"\n📄 Answer:")
                    print(answer)
        
        print("\n" + "=" * 80)
        return result
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


# ============================================================================
# Test Suite Runner
# ============================================================================

def run_test_suite():
    """Run comprehensive test suite with multiple scenarios."""
    
    print("\n" + "=" * 80)
    print(" COMPREHENSIVE DATA AGENT TEST SUITE")
    print("=" * 80)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    test_results = {
        "timestamp": datetime.now().isoformat(),
        "tests": []
    }
    
    # Test 1: find_join_keys
    print("\n\n" + "🔍" * 40)
    join_keys_result = test_find_join_keys()
    test_results["tests"].append({
        "name": "find_join_keys",
        "status": "passed" if join_keys_result else "failed",
        "best_join_column": join_keys_result.get('best_join_column') if join_keys_result else None
    })
    
    # Test 2: Person-specific performance query (Akash Saxena)
    print("\n\n" + "👤" * 40)
    agent_result_1 = test_data_retrieval_agent(
        query="Show ratings and performance metrics for Akash Saxena",
        verbose=False,
        show_summary=True
    )
    test_results["tests"].append({
        "name": "agent_akash_saxena",
        "query": "Show ratings and performance metrics for Akash Saxena",
        "status": "passed" if agent_result_1.get('answer') else "failed",
        "iterations": agent_result_1.get('iterations')
    })
    
    # Test 3: Different person (Kamal Choudhury)
    print("\n\n" + "👤" * 40)
    agent_result_2 = test_data_retrieval_agent(
        query="Show me performance metrics for Kamal Choudhury",
        verbose=False,
        show_summary=True
    )
    test_results["tests"].append({
        "name": "agent_kamal_choudhury",
        "query": "Show me performance metrics for Kamal Choudhury",
        "status": "passed" if agent_result_2.get('answer') else "failed",
        "iterations": agent_result_2.get('iterations')
    })
    
    # Final Summary
    print("\n\n" + "=" * 80)
    print(" TEST SUITE SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for t in test_results["tests"] if t["status"] == "passed")
    total = len(test_results["tests"])
    
    print(f"\nTotal Tests: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {total - passed}")
    print(f"Success Rate: {(passed/total)*100:.1f}%")
    
    print("\n" + "-" * 80)
    print("Individual Test Results:")
    print("-" * 80)
    for test in test_results["tests"]:
        status_icon = "✅" if test["status"] == "passed" else "❌"
        print(f"{status_icon} {test['name']}: {test['status']}")
        if 'query' in test:
            print(f"   Query: {test['query']}")
        if 'iterations' in test and test['iterations']:
            print(f"   Iterations: {test['iterations']}")
        if 'best_join_column' in test and test['best_join_column']:
            print(f"   Best JOIN column: [{test['best_join_column']}]")
    
    print("\n" + "=" * 80)
    print(f"Test suite completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80 + "\n")
    
    return test_results


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point with argument parsing."""
    
    parser = argparse.ArgumentParser(
        description="Comprehensive test suite for Data Retrieval Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full test suite
  python -m src.utils.test_data_agent_comprehensive
  
  # Test only find_join_keys
  python -m src.utils.test_data_agent_comprehensive --test join_keys
  
  # Test only agent
  python -m src.utils.test_data_agent_comprehensive --test agent
  
  # Test with custom query
  python -m src.utils.test_data_agent_comprehensive --query "show performance for John Doe"
  
  # Test with custom tables for join_keys
  python -m src.utils.test_data_agent_comprehensive --test join_keys --tables Indigo_HR_Raw_Data IJP_Employee_scores
        """
    )
    
    parser.add_argument(
        '--test',
        choices=['all', 'join_keys', 'agent'],
        default='all',
        help='Which test to run (default: all)'
    )
    
    parser.add_argument(
        '--query',
        type=str,
        help='Custom query for agent test'
    )
    
    parser.add_argument(
        '--tables',
        nargs='+',
        help='Tables to analyze for join_keys test (space-separated)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        default=True,
        help='Show verbose agent output (default: True)'
    )
    
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Minimize output, show only summary'
    )
    
    args = parser.parse_args()
    
    try:
        if args.test == 'all' and not args.query:
            # Run full test suite
            run_test_suite()
            
        elif args.test == 'join_keys' or (args.test == 'all' and args.tables):
            # Test find_join_keys
            tables = args.tables if args.tables else None
            test_find_join_keys(tables=tables, show_details=not args.quiet)
            
        elif args.test == 'agent' or args.query:
            # Test agent with custom or default query
            query = args.query if args.query else "Show ratings and performance metrics for Akash Saxena"
            test_data_retrieval_agent(
                query=query,
                verbose=args.verbose and not args.quiet,
                show_summary=True
            )
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Fatal error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
