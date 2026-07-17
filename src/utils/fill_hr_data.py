import win32com.client
import os
import random

# Absolute path
file_path = os.path.abspath('db_schemas_csv/Indigo_HR_Raw_Data.xlsx')
output_path = os.path.abspath('db_schemas_csv/Indigo_HR_Raw_Data_Filled.xlsx')

print(f"Reading file: {file_path}")

# Open Excel via COM
excel = win32com.client.Dispatch("Excel.Application")
excel.Visible = False
excel.DisplayAlerts = False

try:
    # Open the workbook
    wb = excel.Workbooks.Open(file_path)
    ws = wb.ActiveSheet
    
    # Get dimensions
    used_range = ws.UsedRange
    rows = used_range.Rows.Count
    cols = used_range.Columns.Count
    
    print(f"Workbook opened successfully!")
    print(f"Rows: {rows}, Columns: {cols}")
    
    # Read header row
    headers = []
    for col in range(1, cols + 1):
        headers.append(ws.Cells(1, col).Value)
    
    print(f"\nHeaders: {headers}")
    
    # Read first 10 rows to see the data
    print("\nFirst 10 rows:")
    for row in range(1, min(11, rows + 1)):
        row_data = []
        for col in range(1, cols + 1):
            row_data.append(ws.Cells(row, col).Value)
        print(f"Row {row}: {row_data}")
    
    # Count empty rows
    empty_count = 0
    for row in range(2, rows + 1):  # Start from row 2 (skip header)
        if ws.Cells(row, 1).Value is None or ws.Cells(row, 1).Value == "":
            empty_count += 1
    
    print(f"\nEmpty rows found: {empty_count}")
    
    # Close without saving for now
    wb.Close(SaveChanges=False)
    
except Exception as e:
    print(f"Error: {e}")
    
finally:
    excel.Quit()
