"""Load clms_rawdata.xlsx into a new CLMS database as a single flat raw table."""
import pandas as pd, pyodbc
from pathlib import Path

SERVER = "localhost,1433"; USER = "sa"; PWD = "YourStrong@Passw0rd"; DB = "HRData"
XLSX = Path(__file__).parent / "db_schemas_csv" / "clms_raw_data_synthetic.xlsx"
TABLE = "CLMS_Raw_Data"

# Column -> SQL type. Everything else defaults to NVARCHAR(200).
DATE_COLS = {
    "DOJ", "DOL", "MedicalDueDt", "PassportDueDt", "LeaveYearStartDate", "LeaveYearEndDate",
    "StartDate", "EndDate", "LastStartDate", "LastEndDate", "FromDT", "ToDt", "LWD", "Trans_Date",
    "AppreciationFromDt", "AppreciationToDt",
}
DATETIME_COLS = {
    "CreatedDtm", "UpdatedDtm", "RequestedDate", "ActionDate", "RequestCreatedDtm",
    "STARTTIME", "ENDTIME", "UpdatedTime",
}
INT_COLS = {
    "RoleID", "LeaveYearId", "LeaveYearShortName", "LeaveTypeID", "StatusTypeID", "BalanceID",
    "LeaveAllocationId", "LeavesAllotted", "LeaveDetailID", "LeaveReqSubId", "NoOfLeaves",
    "EligibleMonths", "Suggested_SL", "Suggested_SL_ID",
}
FLOAT_COLS = {
    "IgoExp", "TotalExp", "Balance", "LastTmuBalance", "CurrentLeave",
    "OldSLBalance", "updatedSLBalance", "OldURTIBalance", "updatedURTIBalance",
}

def conn(db=None):
    cs = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};UID={USER};PWD={PWD};TrustServerCertificate=yes;"
    if db: cs += f"DATABASE={db};"
    return pyodbc.connect(cs)

def sql_type(col):
    if col in DATE_COLS: return "DATE"
    if col in DATETIME_COLS: return "DATETIME"
    if col in INT_COLS: return "INT"
    if col in FLOAT_COLS: return "FLOAT"
    if col == "ContactNo": return "NVARCHAR(20)"
    if col in ("CrewComment", "Remarks", "GNDRemark"): return "NVARCHAR(500)"
    return "NVARCHAR(200)"

# 1) Create database
c = conn(); c.autocommit = True; cur = c.cursor()
cur.execute(f"IF DB_ID('{DB}') IS NULL CREATE DATABASE {DB}")
print(f"{DB} database ready"); cur.close(); c.close()

# 2) Read source data
df = pd.read_excel(XLSX)
print(f"Loaded {len(df)} rows, {len(df.columns)} columns from {XLSX.name}")

for col in list(DATE_COLS) + list(DATETIME_COLS):
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")

def val(v, col):
    if pd.isna(v):
        return None
    if col in DATE_COLS:
        return v.date() if hasattr(v, "date") else v
    if col in DATETIME_COLS:
        return v.to_pydatetime() if hasattr(v, "to_pydatetime") else v
    if col in INT_COLS:
        return int(v)
    if col in FLOAT_COLS:
        return float(v)
    return str(v)

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
cur.execute(f"SELECT TOP 3 CrewID, CrewName, Designation, Base, LeaveType, Balance FROM dbo.{TABLE}")
for row in cur.fetchall(): print("  sample:", tuple(row))
cur.close(); c.close()
print("DONE")
