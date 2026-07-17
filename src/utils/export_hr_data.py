"""Export dbo.Indigo_HR_Raw_Data from SQL Server to a CSV file.

Usage:
    python -m src.utils.export_hr_data
    python -m src.utils.export_hr_data --output my_export.csv
"""
import argparse
import os

import pandas as pd
import pyodbc
from dotenv import load_dotenv

load_dotenv()

SQL_SERVER = os.getenv("SQL_SERVER", "localhost,1433")
SQL_USER = os.getenv("SQL_USER", "sa")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "YourStrong@Passw0rd")
TABLE = "Indigo_HR_Raw_Data"


def main():
    parser = argparse.ArgumentParser(description="Export Indigo_HR_Raw_Data to CSV")
    parser.add_argument("--output", default="Indigo_HR_Raw_Data.csv", help="Output CSV path")
    args = parser.parse_args()

    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE=HRData;"
        f"UID={SQL_USER};"
        f"PWD={SQL_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(conn_str)
    df = pd.read_sql(f"SELECT * FROM dbo.{TABLE}", conn)
    conn.close()

    df.to_csv(args.output, index=False)
    print(f"Exported {len(df)} rows, {len(df.columns)} columns -> {args.output}")


if __name__ == "__main__":
    main()
