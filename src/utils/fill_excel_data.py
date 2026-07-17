import xlrd
from xlutils.copy import copy
import random
import os

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
    "Anand", "Namita", "Shashi", "Kiran", "Prem", "Nalini", "Mohan", "Reena",
    "Sachin", "Rita", "Ajit", "Sunanda", "Bala", "Vani", "Chetan", "Usha",
    "Dilip", "Vidya", "Gagan", "Yamini", "Hari", "Zoya", "Inder", "Alka"
]

last_names = [
    "Sharma", "Kumar", "Singh", "Gupta", "Patel", "Verma", "Reddy", "Rao",
    "Joshi", "Nair", "Iyer", "Menon", "Agarwal", "Mehta", "Shah", "Desai",
    "Kulkarni", "Pandey", "Mishra", "Jain", "Chopra", "Kapoor", "Malhotra", "Bhatia",
    "Saxena", "Sinha", "Das", "Sen", "Ghosh", "Banerjee", "Mukherjee", "Chatterjee",
    "Pillai", "Krishna", "Naik", "Hegde", "Shetty", "Kamath", "Pai", "Bhat",
    "Varma", "Chawla", "Arora", "Khanna", "Sethi", "Madan", "Tandon", "Vohra",
    "Dutta", "Roy", "Bose", "Choudhury", "Paul", "Joseph", "Thomas", "George",
    "Menon", "Nambiar", "Krishnan", "Sundaram", "Ramesh", "Suresh", "Narayanan", "Subramanian",
    "Yadav", "Thakur", "Chauhan", "Rawat", "Bisht", "Joshi", "Chandra", "Tiwari",
    "Dubey", "Tripathi", "Dixit", "Shukla", "Awasthi", "Dwivedi", "Upadhyay", "Srivastava",
    "Goyal", "Bansal", "Mittal", "Singhal", "Garg", "Jindal", "Bhandari", "Goel",
    "Kohli", "Dhawan", "Bajaj", "Talwar", "Chugh", "Anand", "Mohan", "Rajan",
    "Vijay", "Prakash", "Nath", "Khan", "Ahmed", "Ansari", "Malik", "Qureshi",
    "Ali", "Hussain", "Hassan", "Abbas", "Rahman", "Siddiqui", "Rizvi", "Zaidi"
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
    if val is None or val == '':
        return True
    if isinstance(val, str) and (val.strip() == '' or val == '\xa0'):
        return True
    return False

# File paths
file_path = 'db_schemas_csv/Indigo_HR_Raw_Data.xlsx'
output_path = 'db_schemas_csv/Indigo_HR_Raw_Data_Filled.xls'

print(f"Reading file: {file_path}")

try:
    # Try opening without formatting_info first
    try:
        rb = xlrd.open_workbook(file_path, formatting_info=False, ignore_workbook_corruption=True)
    except:
        rb = xlrd.open_workbook(file_path, formatting_info=False)
    
    sheet = rb.sheet_by_index(0)
    
    print(f"Workbook opened successfully!")
    print(f"Rows: {sheet.nrows}, Columns: {sheet.ncols}")
    
    # Since we can't use copy without formatting_info, we'll create a new workbook
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet('Sheet1')
    
    # Column indices (0-based)
    name_col = 2  # Column C (Name)
    email_col = 7  # Column H (Email id)
    
    # Track used names to avoid duplicates
    used_names = set()
    
    # First pass: collect existing names
    for row in range(1, sheet.nrows):
        name_val = sheet.cell_value(row, name_col)
        if not is_empty_value(name_val):
            used_names.add(str(name_val).strip())
    
    print(f"Found {len(used_names)} existing names")
    
    # Copy all data to new workbook and fill empty rows
    filled_count = 0
    
    for row in range(sheet.nrows):
        for col in range(sheet.ncols):
            original_val = sheet.cell_value(row, col)
            
            # Check if this is a name column cell that needs filling
            if row > 0 and col == name_col and is_empty_value(original_val):
                # Generate a unique fictitious name
                attempts = 0
                while attempts < 200:
                    new_name = generate_name()
                    if new_name not in used_names:
                        used_names.add(new_name)
                        break
                    attempts += 1
                else:
                    # If we can't find a unique name, add a number
                    base_name = generate_name()
                    suffix = 1
                    while f"{base_name}{suffix}" in used_names:
                        suffix += 1
                    new_name = f"{base_name}{suffix}"
                    used_names.add(new_name)
                
                # Write the new name
                ws.write(row, col, new_name)
                
                # Generate and write email in the same row
                new_email = generate_email(new_name)
                ws.write(row, email_col, new_email)
                
                filled_count += 1
                
                if filled_count <= 10 or filled_count % 20 == 0:
                    print(f"Row {row + 1}: Added '{new_name}' with email '{new_email}'")
            
            # Check if this is an email column cell that needs filling (if name was just filled, skip)
            elif row > 0 and col == email_col and is_empty_value(original_val):
                # Check if name in this row is not empty
                name_val = sheet.cell_value(row, name_col)
                if not is_empty_value(name_val):
                    # Generate email from existing name
                    new_email = generate_email(str(name_val))
                    ws.write(row, col, new_email)
                else:
                    # Copy empty value
                    ws.write(row, col, original_val if original_val else '')
            else:
                # Copy the original value
                # Handle different cell types
                cell_type = sheet.cell_type(row, col)
                if cell_type == xlrd.XL_CELL_DATE:
                    # Date cell
                    date_tuple = xlrd.xldate_as_tuple(original_val, rb.datemode)
                    import datetime
                    date_val = datetime.datetime(*date_tuple)
                    ws.write(row, col, date_val)
                elif cell_type == xlrd.XL_CELL_BOOLEAN:
                    ws.write(row, col, bool(original_val))
                elif cell_type == xlrd.XL_CELL_ERROR:
                    ws.write(row, col, xlrd.error_text_from_code[original_val])
                else:
                    ws.write(row, col, original_val)
    
    print(f"\nTotal rows filled: {filled_count}")
    
    # Save the workbook
    wb.save(output_path)
    print(f"\nFile saved successfully to: {output_path}")
    print(f"Note: File was saved as .xls format (old Excel format)")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("\nDone!")
