"""
Test script for semantic search and parallel processing enhancements.

This script demonstrates the new capabilities added to the data retrieval agent:
1. Semantic concept search with multiple keywords
2. Parallel processing of graph operations
3. Node description examination

Run this after starting the Flask app to test the enhancements.
"""

import requests
import json
import time

BASE_URL = "http://localhost:5001"  # Adjust if your app runs on a different port

def test_semantic_search():
    """Test semantic concept search capability."""
    print("\n" + "="*80)
    print("TEST 1: Semantic Concept Search")
    print("="*80)
    
    query = {
        "query": "Find information about employee performance ratings and appraisals",
        "database": "cosmos"
    }
    
    print(f"\nQuery: {query['query']}")
    print("\nExpected behavior:")
    print("- Agent should use semantic_concept_search with keywords like:")
    print("  ['performance', 'rating', 'appraisal', 'score', 'evaluation']")
    print("- Should find IJP node via description matching")
    print("- Should identify IJP_Employee_scores table")
    
    print("\nSending request...")
    response = requests.post(f"{BASE_URL}/data/query", json=query, stream=True)
    
    print("\n--- AGENT RESPONSE ---")
    for line in response.iter_lines():
        if line:
            decoded = line.decode('utf-8')
            if decoded.startswith('data: '):
                data = decoded[6:]  # Remove 'data: ' prefix
                if data.strip() and data != '[DONE]':
                    try:
                        chunk = json.loads(data)
                        if chunk.get('type') == 'content':
                            print(chunk.get('data', ''), end='', flush=True)
                    except json.JSONDecodeError:
                        pass
    print("\n" + "="*80)


def test_specific_crew_rating():
    """Test retrieval of specific crew member rating."""
    print("\n" + "="*80)
    print("TEST 2: Specific Crew Rating Retrieval")
    print("="*80)
    
    query = {
        "query": "What is the rating for crew_id 65804?",
        "database": "cosmos"
    }
    
    print(f"\nQuery: {query['query']}")
    print("\nExpected behavior:")
    print("- Agent should search for 'rating' or use semantic search")
    print("- Should find IJP node via description")
    print("- Should generate SQL: SELECT * FROM IJP_Employee_scores WHERE IGA = '65804'")
    print("- Should return specific employee's performance data")
    
    print("\nSending request...")
    response = requests.post(f"{BASE_URL}/data/query", json=query, stream=True)
    
    print("\n--- AGENT RESPONSE ---")
    for line in response.iter_lines():
        if line:
            decoded = line.decode('utf-8')
            if decoded.startswith('data: '):
                data = decoded[6:]
                if data.strip() and data != '[DONE]':
                    try:
                        chunk = json.loads(data)
                        if chunk.get('type') == 'content':
                            print(chunk.get('data', ''), end='', flush=True)
                    except json.JSONDecodeError:
                        pass
    print("\n" + "="*80)


def test_parallel_exploration():
    """Test parallel graph exploration."""
    print("\n" + "="*80)
    print("TEST 3: Parallel Graph Exploration")
    print("="*80)
    
    query = {
        "query": "Show me all available data about employees, their departments, and performance",
        "database": "cosmos"
    }
    
    print(f"\nQuery: {query['query']}")
    print("\nExpected behavior:")
    print("- Agent should explore multiple concepts in parallel")
    print("- Should search: 'employee', 'department', 'performance' simultaneously")
    print("- Check logs for parallel execution indicators")
    
    print("\nSending request...")
    start_time = time.time()
    response = requests.post(f"{BASE_URL}/data/query", json=query, stream=True)
    
    print("\n--- AGENT RESPONSE ---")
    for line in response.iter_lines():
        if line:
            decoded = line.decode('utf-8')
            if decoded.startswith('data: '):
                data = decoded[6:]
                if data.strip() and data != '[DONE]':
                    try:
                        chunk = json.loads(data)
                        if chunk.get('type') == 'content':
                            print(chunk.get('data', ''), end='', flush=True)
                    except json.JSONDecodeError:
                        pass
    
    elapsed = time.time() - start_time
    print(f"\n\nTotal response time: {elapsed:.2f} seconds")
    print("="*80)


def test_description_matching():
    """Test node description examination."""
    print("\n" + "="*80)
    print("TEST 4: Node Description Matching")
    print("="*80)
    
    query = {
        "query": "Find tables containing employee recognition and performance metrics",
        "database": "cosmos"
    }
    
    print(f"\nQuery: {query['query']}")
    print("\nExpected behavior:")
    print("- Agent should examine node descriptions")
    print("- Should find IJP via description: 'performance metrics, ratings, and recognitions'")
    print("- Description contains both 'recognition' and 'performance metrics'")
    
    print("\nSending request...")
    response = requests.post(f"{BASE_URL}/data/query", json=query, stream=True)
    
    print("\n--- AGENT RESPONSE ---")
    for line in response.iter_lines():
        if line:
            decoded = line.decode('utf-8')
            if decoded.startswith('data: '):
                data = decoded[6:]
                if data.strip() and data != '[DONE]':
                    try:
                        chunk = json.loads(data)
                        if chunk.get('type') == 'content':
                            print(chunk.get('data', ''), end='', flush=True)
                    except json.JSONDecodeError:
                        pass
    print("\n" + "="*80)


def run_all_tests():
    """Run all test cases."""
    print("\n" + "="*80)
    print("DATA RETRIEVAL AGENT ENHANCEMENT TESTS")
    print("="*80)
    print("\nTesting semantic search and parallel processing capabilities...")
    print("\nMake sure the Flask app is running on port 5001!")
    print("Start with: python -m src.end-user-app.app")
    
    input("\nPress Enter to start tests...")
    
    try:
        # Test 1: Semantic search
        test_semantic_search()
        input("\nPress Enter to continue to next test...")
        
        # Test 2: Specific crew rating
        test_specific_crew_rating()
        input("\nPress Enter to continue to next test...")
        
        # Test 3: Parallel exploration
        test_parallel_exploration()
        input("\nPress Enter to continue to next test...")
        
        # Test 4: Description matching
        test_description_matching()
        
        print("\n" + "="*80)
        print("ALL TESTS COMPLETED!")
        print("="*80)
        print("\nCheck the following:")
        print("1. Did semantic_concept_search get called? (check logs)")
        print("2. Were node descriptions examined?")
        print("3. Did agent find IJP data via semantic search?")
        print("4. Check logs for parallel execution indicators")
        print("="*80)
        
    except requests.exceptions.ConnectionError:
        print("\n❌ ERROR: Could not connect to Flask app!")
        print("Make sure it's running: python -m src.end-user-app.app")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")


if __name__ == "__main__":
    run_all_tests()
