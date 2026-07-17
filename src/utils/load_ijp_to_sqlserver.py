"""
Load IJP Employee Scores from CSV to SQL Server
"""
import pandas as pd
import pyodbc
from pathlib import Path

# SQL Server connection details
SERVER = 'localhost,1433'
USERNAME = 'sa'
PASSWORD = 'YourStrong@Passw0rd'
DATABASE = 'HRData'

# CSV file path
CSV_FILE = Path(__file__).parent.parent.parent / 'db_schemas_csv' / 'IJP_Employee_scores.csv'

# Column mapping: Original name -> Short name
COLUMN_MAPPING = {
    'Fleet Type': 'FleetType',
    'IGA': 'IGA',
    'Designation': 'Designation',
    'Base': 'Base',
    'NAME': 'Name',
    'LWP Days - extracted from CLMS': 'LWPDays',
    'BMI Data': 'BMI',
    'Appreciation Letter received ': 'AppreciationLetters',
    'Any non performance discussion  -   Caution letter / Counselling form': 'CautionLetters',
    'Any non performance discussion  ': 'NonPerfDiscussion',
    'Curative training/icoach session/Performance Coaching Session  ': 'CoachingSessions',
    'Extra Initiatives/ recognition - "employee of the month / For each other / 3 or more 6e clap / 6e WOW champions': 'Recognition'
}

def create_connection(database=None):
    """Create SQL Server connection"""
    conn_str = (
        f'DRIVER={{ODBC Driver 18 for SQL Server}};'
        f'SERVER={SERVER};'
        f'UID={USERNAME};'
        f'PWD={PASSWORD};'
        f'TrustServerCertificate=yes;'
    )
    if database:
        conn_str += f'DATABASE={database};'
    
    return pyodbc.connect(conn_str)

def create_table(cursor):
    """Create IJP_Employee_scores table"""
    print("Creating IJP_Employee_scores table...")
    
    # Drop table if exists
    cursor.execute("""
        IF OBJECT_ID('dbo.IJP_Employee_scores', 'U') IS NOT NULL
            DROP TABLE dbo.IJP_Employee_scores;
    """)
    
    # Create table
    create_table_sql = """
    CREATE TABLE dbo.IJP_Employee_scores (
        ID INT IDENTITY(1,1) PRIMARY KEY,
        FleetType NVARCHAR(100),
        IGA INT,
        Designation NVARCHAR(50),
        Base NVARCHAR(50),
        Name NVARCHAR(200),
        LWPDays INT,
        BMI FLOAT,
        AppreciationLetters INT,
        CautionLetters NVARCHAR(100),
        NonPerfDiscussion NVARCHAR(100),
        CoachingSessions NVARCHAR(100),
        Recognition NVARCHAR(200)
    );
    """
    cursor.execute(create_table_sql)
    print("Table created successfully!")

def load_data():
    """Load CSV data into SQL Server"""
    try:
        print(f"Reading CSV file: {CSV_FILE}")
        
        # Read CSV file
        if not CSV_FILE.exists():
            print(f"ERROR: CSV file not found at {CSV_FILE}")
            return
        
        # Try different encodings
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252', 'utf-16']
        df = None
        for encoding in encodings:
            try:
                print(f"Trying encoding: {encoding}")
                df = pd.read_csv(CSV_FILE, encoding=encoding)
                print(f"Successfully read with {encoding} encoding")
                break
            except Exception as e:
                print(f"Failed with {encoding}: {str(e)[:100]}")
                continue
        
        if df is None:
            print("ERROR: Could not read CSV with any encoding")
            return
        
        print(f"Loaded {len(df)} rows from CSV")
        print(f"Original columns: {df.columns.tolist()}")
        
        # Rename columns
        df = df.rename(columns=COLUMN_MAPPING)
        print(f"Renamed columns to: {df.columns.tolist()}")
        
        # Remove empty rows (rows where all values are NaN)
        df = df.dropna(how='all')
        print(f"After removing empty rows: {len(df)} rows")
        
        # Connect to SQL Server
        print("Connecting to SQL Server...")
        conn = create_connection(DATABASE)
        cursor = conn.cursor()
        
        # Create table
        create_table(cursor)
        conn.commit()
        
        # Insert data
        print("Inserting data...")
        insert_sql = """
        INSERT INTO dbo.IJP_Employee_scores 
        (FleetType, IGA, Designation, Base, Name, LWPDays, BMI, 
         AppreciationLetters, CautionLetters, NonPerfDiscussion, 
         CoachingSessions, Recognition)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        inserted_count = 0
        for idx, row in df.iterrows():
            try:
                cursor.execute(
                    insert_sql,
                    str(row['FleetType']) if pd.notna(row['FleetType']) else None,
                    int(row['IGA']) if pd.notna(row['IGA']) else None,
                    str(row['Designation']) if pd.notna(row['Designation']) else None,
                    str(row['Base']) if pd.notna(row['Base']) else None,
                    str(row['Name']) if pd.notna(row['Name']) else None,
                    int(row['LWPDays']) if pd.notna(row['LWPDays']) else None,
                    float(row['BMI']) if pd.notna(row['BMI']) else None,
                    int(row['AppreciationLetters']) if pd.notna(row['AppreciationLetters']) else None,
                    str(row['CautionLetters']) if pd.notna(row['CautionLetters']) else None,
                    str(row['NonPerfDiscussion']) if pd.notna(row['NonPerfDiscussion']) else None,
                    str(row['CoachingSessions']) if pd.notna(row['CoachingSessions']) else None,
                    str(row['Recognition']) if pd.notna(row['Recognition']) else None
                )
                inserted_count += 1
                if inserted_count % 100 == 0:
                    print(f"Inserted {inserted_count} rows...")
            except Exception as e:
                print(f"Error inserting row {idx}: {e}")
                print(f"Row data: {row.to_dict()}")
        
        conn.commit()
        print(f"\nSUCCESS! {inserted_count} rows loaded to SQL Server table: IJP_Employee_scores")
        
        # Verify data
        cursor.execute("SELECT COUNT(*) FROM dbo.IJP_Employee_scores")
        count = cursor.fetchone()[0]
        print(f"Verification: Table now contains {count} rows")
        
        # Show sample data
        print("\nSample data:")
        cursor.execute("SELECT TOP 5 * FROM dbo.IJP_Employee_scores")
        rows = cursor.fetchall()
        for row in rows:
            print(row)
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    print("=" * 60)
    print("Loading IJP Employee Scores to SQL Server")
    print("=" * 60)
    load_data()
