import win32com.client as win32
import os
import random
import pythoncom

# Lists of fictitious first and last names
first_names = [
    "Rahul", "Priya", "Amit", "Neha", "Raj", "Sneha", "Vikram", "Anjali", 
    "Arjun", "Pooja", "Rohan", "Divya", "Karan", "Meera", "Aditya", "Kavya",
    "Sanjay", "Shreya", "Nikhil", "Ritu", "Varun", "Simran", "Harsh", "Nisha",
    "Akash", "Priyanka", "Kunal", "Swati", "Manish", "Sakshi", "Siddharth", "Tanvi",
    "Abhishek", "Isha", "Gaurav", "Megha", "Ashish", "Pallavi", "Rajesh", "Aarti",
    "Vivek", "Deepika", "Suresh", "Ananya", "Pankaj", "Riya", "Naveen", "Jyoti",
    "Sunil", "Sonali", "Ramesh", "Nikita", "Sandeep", "Shweta", "Manoj", "Aditi",
    "Vikas", "Preeti", "Anil", "Komal", "Puneet", "Smita", "Ravi", "Bhavna",
    "Ajay", "Vandana", "Yogesh", "Archana", "Hemant", "Rashmi", "Prakash", "Nidhi",
    "Mahesh", "Seema", "Dinesh", "Poonam", "Naresh", "Sunita", "Satish", "Geeta",
    "Vishal", "Shilpa", "Tarun", "Alpana", "Kapil", "Naina", "Dev", "Tanya",
    "Sameer", "Madhuri", "Ankit", "Rekha", "Sumit", "Lata", "Rohit", "Sudha",
    "Mohit", "Urmila", "Nitin", "Renuka", "Aman", "Suman", "Kamal", "Veena",
    "Anand", "Namita", "Shashi", "Kiran", "Prem", "Nalini", "Mohan", "Reena"
]

last_names = [
    "Sharma", "Kumar", "Singh", "Gupta", "Patel", "Verma", "Reddy", "Rao",
    "Joshi", "Nair", "Iyer", "Menon", "Agarwal", "Mehta", "Shah", "Desai",
    "Kulkarni", "Pandey", "Mishra", "Jain", "Chopra", "Kapoor", "Malhotra", "Bhatia",
    "Saxena", "Sinha", "Das", "Sen", "Ghosh", "Banerjee", "Mukherjee", "Chatterjee",
    "Pillai", "Krishna", "Naik", "Hegde", "Shetty", "Kamath", "Pai", "Bhat",
    "Varma", "Chawla", "Arora", "Khanna", "Sethi", "Madan", "Tandon", "Vohra",
    "Dutta", "Roy", "Bose", "Choudhury", "Paul", "Joseph", "Thomas", "George"
]

def generate_email(name):
    """Generate an email based on the name"""
    clean_name = name.replace(" ", ".").replace("'", "")
    return f"{clean_name}@GOINDIGO.IN"

def generate_name():
    """Generate a fictitious name"""
    first = random.choice(first_names)
    last = random.choice(last_names)
    return f"{first} {last}"

def is_empty_value(val):
    """Check if a cell value is considered empty"""
    if val is None:
        return True
    if isinstance(val, str) and (val.strip() == '' or val == '\xa0'):
        return True
    return False

# File paths
file_path = os.path.abspath('db_schemas_csv/Indigo_HR_Raw_Data.xlsx')
output_path = os.path.abspath('db_schemas_csv/Indigo_HR_Raw_Data_Filled.xlsx')

print(f"Reading file: {file_path}")
print(f"Output file: {output_path}")

# Initialize COM
pythoncom.CoInitialize()

excel = None
wb = None

try:
    # Create Excel application
    excel = win32.DispatchEx("Excel.Application")
    
    # Open the workbook
    wb = excel.Workbooks.Open(file_path)
    ws = wb.Worksheets(1)
    
    used_range = ws.UsedRange
    rows = used_range.Rows.Count
    cols = used_range.Columns.Count
    
    print(f"Workbook opened successfully!")
    print(f"Rows: {rows}, Columns: {cols}")
    
    # Column indices (1-based in COM)
    name_col = 3  # Column C
    email_col = 8  # Column H
    
    # Track used names
    used_names = set()
    
    # First pass: collect existing names
    print("Collecting existing names...")
    for row in range(2, rows + 1):
        name_val = ws.Cells(row, name_col).Value
        if not is_empty_value(name_val):
            used_names.add(str(name_val).strip())
    
    print(f"Found {len(used_names)} existing names")
    
    # Second pass: fill empty rows
    filled_count = 0
    print("Filling empty rows...")
    
    for row in range(2, rows + 1):
        name_val = ws.Cells(row, name_col).Value
        
        if is_empty_value(name_val):
            # Generate a unique name
            attempts = 0
            while attempts < 200:
                new_name = generate_name()
                if new_name not in used_names:
                    used_names.add(new_name)
                    break
                attempts += 1
            else:
                # Add suffix if can't find unique name
                base_name = generate_name()
                suffix = 1
                while f"{base_name} {suffix}" in used_names:
                    suffix += 1
                new_name = f"{base_name} {suffix}"
                used_names.add(new_name)
            
            # Fill name and email
            ws.Cells(row, name_col).Value = new_name
            new_email = generate_email(new_name)
            ws.Cells(row, email_col).Value = new_email
            
            filled_count += 1
            
            if filled_count <= 10 or filled_count % 20 == 0:
                print(f"Row {row}: Added '{new_name}' with email '{new_email}'")
    
    print(f"\nTotal rows filled: {filled_count}")
    
    # Save the file
    wb.SaveAs(output_path)
    print(f"\nFile saved successfully to: {output_path}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

finally:
    # Cleanup
    if wb:
        try:
            wb.Close(False)
        except:
            pass
    
    if excel:
        try:
            excel.Quit()
        except:
            pass
    
    pythoncom.CoUninitialize()

print("\nDone!")
