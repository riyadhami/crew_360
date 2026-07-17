"""Load indigoNPS_summary_synthetic.xlsx into a new NPS database as a single flat table."""
import pandas as pd, pyodbc
from pathlib import Path

SERVER = "localhost,1433"; USER = "sa"; PWD = "YourStrong@Passw0rd"; DB = "HRData"
XLSX = Path(__file__).parent / "db_schemas_csv" / "indigoNPS_summary_synthetic.xlsx"
TABLE = "IndigoNPS_Summary"

INT_COLS = {
    "FLTNBR", "Pre Booked Meal", "Baggage Weight", "Baggage Count", "NPS Score",
    "Booking experience", "Pre-travel information experience", "Check-in experience",
    "Boarding experience", "On-board experience", "Arrival experience", "Total No Of Pax",
}
LONGTEXT_COLS = {"Please share your reasons for the rating"}

def conn(db=None):
    cs = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};UID={USER};PWD={PWD};TrustServerCertificate=yes;"
    if db: cs += f"DATABASE={db};"
    return pyodbc.connect(cs)

def sql_type(col):
    if col in INT_COLS: return "INT"
    if col in LONGTEXT_COLS: return "NVARCHAR(1000)"
    return "NVARCHAR(200)"

def val(v, col):
    if pd.isna(v):
        return None
    if col in INT_COLS:
        return int(v)
    return str(v)

# 1) Create database
c = conn(); c.autocommit = True; cur = c.cursor()
cur.execute(f"IF DB_ID('{DB}') IS NULL CREATE DATABASE {DB}")
print(f"{DB} database ready"); cur.close(); c.close()

# 2) Read source data
df = pd.read_excel(XLSX)
print(f"Loaded {len(df)} rows, {len(df.columns)} columns from {XLSX.name}")

# 3) Create table
c = conn(DB); cur = c.cursor()
cur.execute(f"IF OBJECT_ID('dbo.{TABLE}','U') IS NOT NULL DROP TABLE dbo.{TABLE}")
col_defs = ",\n    ".join(f"[{col}] {sql_type(col)}" for col in df.columns)
cur.execute(f"CREATE TABLE dbo.{TABLE} (\n    {col_defs}\n)")
c.commit()
print(f"Table dbo.{TABLE} created with {len(df.columns)} columns")

# 4) Insert rows
cols_bracketed = ",".join(f"[{c_}]" for c_ in df.columns)
placeholders = ",".join("?" for _ in df.columns)
ins = f"INSERT INTO dbo.{TABLE} ({cols_bracketed}) VALUES ({placeholders})"
n = 0
for _, row in df.iterrows():
    cur.execute(ins, *[val(row[col], col) for col in df.columns])
    n += 1
c.commit()
print(f"{TABLE} loaded: {n} rows")

# 5) Verify
cur.execute(f"SELECT COUNT(*) FROM dbo.{TABLE}"); print("verify:", cur.fetchone()[0])
cur.execute(f"SELECT TOP 3 [IGA Code], [Crew Name], [NPS Type], [NPS Score], FLTNBR, DEP, ARR FROM dbo.{TABLE}")
for row in cur.fetchall(): print("  sample:", tuple(row))
cur.close(); c.close()
print("DONE")
