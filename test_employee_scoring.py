"""
Test script for the employee weighted scoring function.

This script demonstrates how to use the calculate_employee_score tool
to compute comprehensive performance scores for employees.

Usage:
    python test_employee_scoring.py --name "Akash Saxena"
    python test_employee_scoring.py --iga 62913
    python test_employee_scoring.py --name "Kamal Choudhury"
"""

import argparse
import json
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.agents.Data_Retrieval_Agent_New import DatabaseConnectionManager, calculate_employee_score


def display_score_breakdown(score_result):
    """Display the score breakdown in a readable format."""
    print("\n" + "="*80)
    
    if "error" in score_result:
        print(f"❌ ERROR: {score_result['error']}")
        if "suggestion" in score_result:
            print(f"💡 Suggestion: {score_result['suggestion']}")
        return
    
    # Display employee details
    if "employee_details" in score_result:
        details = score_result["employee_details"]
        print(f"👤 EMPLOYEE: {details['name']}")
        print(f"   IGA: {details['iga']}")
        print(f"   Base: {details['base']}")
        print(f"   Designation: {details['designation']}")
    
    print("\n" + "="*80)
    print("📊 PERFORMANCE SCORE BREAKDOWN")
    print("="*80)
    
    # Display each component
    for component in score_result.get("components", []):
        print(f"\n{component['name']} ({component['weight']})")
        print(f"  Raw Value: {component['raw_value']}")
        print(f"  Normalized Score: {component['normalized_score']:.2f}/100")
        print(f"  Weighted Score: {component['weighted_score']:.2f}")
        print(f"  Source: {component['data_source']}")
        if "note" in component:
            print(f"  Note: {component['note']}")
        if "feedback" in component:
            print(f"  Passenger Feedback: \"{component['feedback']}\"")
    
    # Display summary
    print("\n" + "="*80)
    print("📈 SUMMARY")
    print("="*80)
    summary = score_result.get("summary", {})
    print(f"Total Score: {summary.get('total_score', 0):.2f} / {summary.get('max_possible_score', 100):.2f}")
    print(f"Percentage: {summary.get('percentage', '0%')}")
    print(f"Components Calculated: {summary.get('components_calculated', 0)} / {summary.get('total_components', 7)}")
    
    if summary.get('missing_data', 0) > 0:
        print(f"\n⚠️  Missing Data: {summary['missing_data']} component(s)")
    
    # Display errors if any
    if score_result.get("errors"):
        print("\n" + "="*80)
        print("⚠️  WARNINGS/ERRORS")
        print("="*80)
        for error in score_result["errors"]:
            print(f"  • {error}")
    
    print("\n" + "="*80)


def main():
    parser = argparse.ArgumentParser(
        description="Calculate weighted performance score for an employee"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", type=str, help="Employee name")
    group.add_argument("--iga", type=str, help="Employee IGA number")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of formatted display")
    
    args = parser.parse_args()
    
    # Initialize database manager
    print("🔄 Initializing database connection...")
    db_manager = DatabaseConnectionManager()
    
    # Determine identifier type and value
    if args.name:
        identifier = args.name
        identifier_type = "name"
        print(f"🔍 Calculating score for employee: {identifier}")
    else:
        identifier = args.iga
        identifier_type = "iga"
        print(f"🔍 Calculating score for IGA: {identifier}")
    
    # Calculate score
    print("⏳ Fetching data and calculating scores...")
    result = calculate_employee_score(
        db_manager=db_manager,
        employee_identifier=identifier,
        identifier_type=identifier_type
    )
    
    # Display results
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        display_score_breakdown(result)


if __name__ == "__main__":
    main()
