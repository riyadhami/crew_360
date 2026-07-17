"""
Load HR Data from Excel to SQL Server running in Docker
Uses COM automation to read Excel files (works with .xls format)
"""
import pandas as pd
import pyodbc
from pathlib import Path
import win32com.client
import pythoncom
import datetime
import pywintypes

# SQL Server connection details
SERVER = 'localhost,1433'
USERNAME = 'sa'
PASSWORD = 'YourStrong@Passw0rd'
DATABASE = 'HRData'

# Excel file path
EXCEL_FILE = Path(__file__).parent / 'db_schemas_csv' / 'Indigo_HR_Raw_Data_Filled.xlsx'

def convert_value(val):
    """Convert Excel value to Python type"""
    if val is None:
        return None
    elif isinstance(val, pywintypes.TimeType):
        # Convert pywintypes datetime to Python datetime
        return datetime.datetime(
            year=val.year,
            month=val.month,
            day=val.day,
            hour=val.hour,
            minute=val.minute,
            second=val.second
        )
    else:
        return val

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

def create_database():
    """Create HRData database if it doesn't exist"""
    print("Connecting to SQL Server...")
    conn = create_connection()
    conn.autocommit = True  # Need autocommit for CREATE DATABASE
    cursor = conn.cursor()
    
    # Check if database exists
    cursor.execute(
        "SELECT database_id FROM sys.databases WHERE name = ?", 
        (DATABASE,)
    )
    
    if cursor.fetchone() is None:
        print(f"Creating database '{DATABASE}'...")
        cursor.execute(f"CREATE DATABASE {DATABASE}")
        print(f"✓ Database '{DATABASE}' created")
    else:
        print(f"✓ Database '{DATABASE}' already exists")
    
    cursor.close()
    conn.close()

def load_data():
    """Load Excel data into SQL Server using COM automation"""
    print(f"\nReading Excel file: {EXCEL_FILE}")
    
    # Initialize COM
    pythoncom.CoInitialize()
    
    try:
        # Open Excel using COM
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.DisplayAlerts = False
        
        # Open workbook
        wb = excel.Workbooks.Open(str(EXCEL_FILE.absolute()))
        ws = wb.Worksheets(1)
        
        # Get used range
        used_range = ws.UsedRange
        rows = used_range.Rows.Count
        cols = used_range.Columns.Count
        
        print(f"✓ Loaded {rows} rows and {cols} columns")
        
        # Read data into lists
        data = []
        headers = []
        
        # Get headers (row 1)
        for col in range(1, cols + 1):
            headers.append(ws.Cells(1, col).Value)
        
        print("\nColumns:")
        for i, col in enumerate(headers, 1):
            print(f"  {i}. {col}")
        
        # Get data (rows 2 onwards)
        for row in range(2, rows + 1):
            row_data = []
            for col in range(1, cols + 1):
                val = ws.Cells(row, col).Value
                # Convert pywintypes datetime to Python datetime
                row_data.append(convert_value(val))
            data.append(row_data)
        
        # Close workbook
        wb.Close(False)
        excel.Quit()
        
        # Create DataFrame
        df = pd.DataFrame(data, columns=headers)
        
    finally:
        pythoncom.CoUninitialize()
    
    print(f"✓ Converted to DataFrame with {len(df)} data rows")
    
    # Clean column names for SQL (remove spaces, special characters)
    df.columns = df.columns.str.strip()
    
    # Connect to the database
    print(f"\nConnecting to database '{DATABASE}'...")
    conn = create_connection(DATABASE)
    cursor = conn.cursor()
    
    # Drop table if exists
    print("Dropping existing table if it exists...")
    cursor.execute("DROP TABLE IF EXISTS Indigo_HR_Raw_Data")
    conn.commit()
    
    # Create table with proper data types
    print("Creating table 'Indigo_HR_Raw_Data'...")
    create_table_sql = """
    CREATE TABLE Indigo_HR_Raw_Data (
        [Req Id] FLOAT,
        [IGA] FLOAT,
        [Name] NVARCHAR(255),
        [Base] NVARCHAR(50),
        [Designation] NVARCHAR(100),
        [Applicant's Function] NVARCHAR(100),
        [IJP End Date] DATETIME,
        [Email id] NVARCHAR(255),
        [Phone No] NVARCHAR(50),
        [DOB] DATETIME,
        [Age (As on the last date)] NVARCHAR(50),
        [DOJ] DATETIME,
        [Months in IndiGo] NVARCHAR(50),
        [Date of Release] DATETIME,
        [Total Flying Expeirence (In IndiGo)] FLOAT,
        [COC] NVARCHAR(50),
        [Aircraft type] NVARCHAR(50),
        [Eligibility Check as per HR] NVARCHAR(50),
        [Message] NVARCHAR(MAX),
        [Preferred Location 1] NVARCHAR(50),
        [Preferred Location 2] NVARCHAR(50),
        [Preferred Location 3] NVARCHAR(50)
    )
    """
    cursor.execute(create_table_sql)
    conn.commit()
    print("✓ Table created")
    
    # Insert data row by row
    print(f"\nInserting {len(df)} rows...")
    
    insert_sql = """
    INSERT INTO Indigo_HR_Raw_Data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    rows_inserted = 0
    errors = 0
    
    for idx, row in df.iterrows():
        try:
            # Convert row to list, handling NaN values
            values = []
            for val in row:
                if pd.isna(val):
                    values.append(None)
                else:
                    values.append(val)
            
            cursor.execute(insert_sql, values)
            rows_inserted += 1
            
            # Commit every 100 rows
            if rows_inserted % 100 == 0:
                conn.commit()
                print(f"  {rows_inserted} rows inserted...")
        
        except Exception as e:
            errors += 1
            if errors <= 5:  # Show first 5 errors
                print(f"  Error inserting row {idx}: {e}")
    
    # Final commit
    conn.commit()
    
    print(f"\n✓ Data loading complete!")
    print(f"  Total rows inserted: {rows_inserted}")
    if errors > 0:
        print(f"  Errors: {errors}")
    
    # Verify data
    cursor.execute("SELECT COUNT(*) FROM Indigo_HR_Raw_Data")
    count = cursor.fetchone()[0]
    print(f"\n✓ Verification: {count} rows in table")
    
    # Show sample data
    print("\nSample data (first 5 rows):")
    cursor.execute("SELECT TOP 5 [Req Id], [IGA], [Name], [Email id] FROM Indigo_HR_Raw_Data")
    for row in cursor.fetchall():
        print(f"  {row}")
    
    cursor.close()
    conn.close()

def main():
    """Main execution"""
    print("="*60)
    print("Loading HR Data to SQL Server")
    print("="*60)
    
    try:
        # Create database
        create_database()
        
        # Load data
        load_data()
        
        print("\n" + "="*60)
        print("SUCCESS! Data loaded to SQL Server")
        print("="*60)
        print("\nConnection Details:")
        print(f"  Server: {SERVER}")
        print(f"  Database: {DATABASE}")
        print(f"  Table: Indigo_HR_Raw_Data")
        print(f"  Username: {USERNAME}")
        print(f"  Password: {PASSWORD}")
        print("\nConnection String:")
        print(f"  Server={SERVER};Database={DATABASE};User Id={USERNAME};Password={PASSWORD};TrustServerCertificate=True;")
        
    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
