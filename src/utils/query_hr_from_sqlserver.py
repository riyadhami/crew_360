"""
Query HR Data from SQL Server running in Docker
Retrieves and displays sample records from the database
"""
import pyodbc
import pandas as pd

# SQL Server connection details
SERVER = 'localhost,1433'
USERNAME = 'sa'
PASSWORD = 'YourStrong@Passw0rd'
DATABASE = 'HRData'
TABLE = 'Indigo_HR_Raw_Data'

def create_connection():
    """Create SQL Server connection"""
    conn_str = (
        f'DRIVER={{ODBC Driver 18 for SQL Server}};'
        f'SERVER={SERVER};'
        f'UID={USERNAME};'
        f'PWD={PASSWORD};'
        f'DATABASE={DATABASE};'
        f'TrustServerCertificate=yes;'
    )
    return pyodbc.connect(conn_str)

def get_sample_records(limit=10):
    """Retrieve sample records from SQL Server"""
    print("="*80)
    print(f"Querying {limit} records from SQL Server")
    print("="*80)
    print(f"Server: {SERVER}")
    print(f"Database: {DATABASE}")
    print(f"Table: {TABLE}\n")
    
    try:
        # Connect to database
        conn = create_connection()
        
        # Get table statistics
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE}")
        total_rows = cursor.fetchone()[0]
        print(f"Total rows in table: {total_rows}\n")
        
        # Get column names
        cursor.execute(f"SELECT TOP 0 * FROM {TABLE}")
        columns = [column[0] for column in cursor.description]
        print(f"Columns ({len(columns)}):")
        for i, col in enumerate(columns, 1):
            print(f"  {i}. {col}")
        
        # Retrieve sample data
        print(f"\nRetrieving top {limit} records...\n")
        query = f"SELECT TOP {limit} * FROM {TABLE}"
        
        # Use pandas for better display
        df = pd.read_sql(query, conn)
        
        # Display data
        print("="*80)
        print(f"Sample Data ({len(df)} rows):")
        print("="*80)
        
        # Show full dataframe without truncation
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', None)
        pd.set_option('display.max_colwidth', 50)
        
        print(df.to_string(index=True))
        
        print("\n" + "="*80)
        print("Summary Statistics:")
        print("="*80)
        
        # Show basic stats for numeric columns
        numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns
        if len(numeric_cols) > 0:
            print("\nNumeric Columns:")
            print(df[numeric_cols].describe().to_string())
        
        # Show value counts for key columns
        print("\n\nKey Column Distributions:")
        
        if 'Base' in df.columns:
            print("\nBase Locations:")
            print(df['Base'].value_counts().head(10).to_string())
        
        if 'Designation' in df.columns:
            print("\nDesignations:")
            print(df['Designation'].value_counts().head(10).to_string())
        
        if 'Eligibility Check as per HR' in df.columns:
            print("\nEligibility Status:")
            print(df['Eligibility Check as per HR'].value_counts().to_string())
        
        # Close connection
        cursor.close()
        conn.close()
        
        print("\n" + "="*80)
        print("Query completed successfully!")
        print("="*80)
        
        return df
        
    except pyodbc.Error as e:
        print(f"\n❌ Database Error: {e}")
        return None
    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None

def main():
    """Main execution"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Query HR data from SQL Server"
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=10,
        help='Number of records to retrieve (default: 10)'
    )
    parser.add_argument(
        '--export',
        type=str,
        help='Export results to CSV file (provide filename)'
    )
    
    args = parser.parse_args()
    
    # Get records
    df = get_sample_records(limit=args.limit)
    
    # Export if requested
    if df is not None and args.export:
        try:
            df.to_csv(args.export, index=False)
            print(f"\n✓ Data exported to: {args.export}")
        except Exception as e:
            print(f"\n❌ Export failed: {e}")

if __name__ == "__main__":
    main()
