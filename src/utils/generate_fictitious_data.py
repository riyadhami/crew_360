import win32com.client
import os
import random

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
    "Dutta", "Roy", "Bose", "Choudhury", "Paul", "Joseph", "Thomas", "George",
    "Menon", "Nambiar", "Krishnan", "Sundaram", "Ramesh", "Suresh", "Narayanan", "Subramanian",
    "Yadav", "Thakur", "Chauhan", "Rawat", "Bisht", "Joshi", "Chandra", "Tiwari",
    "Dubey", "Tripathi", "Dixit", "Shukla", "Awasthi", "Dwivedi", "Upadhyay", "Srivastava",
    "Goyal", "Bansal", "Mittal", "Singhal", "Garg", "Jindal", "Bhandari", "Goel",
    "Kohli", "Dhawan", "Bajaj", "Talwar", "Chugh", "Anand", "Mohan", "Rajan",
    "Vijay", "Prakash", "Nath", "Khan", "Ahmed", "Ansari", "Malik", "Qureshi"
]

# Function to generate a fictitious email
def generate_email(name):
    """Generate an email based on the name"""
    clean_name = name.replace(" ", ".").replace("'", "")
    return f"{clean_name}@GOINDIGO.IN"

# Function to generate a random name
def generate_name():
    """Generate a fictitious name"""
    first = random.choice(first_names)
    last = random.choice(last_names)
    return f"{first} {last}"

# Absolute path
file_path = os.path.abspath('db_schemas_csv/Indigo_HR_Raw_Data.xlsx')
output_path = os.path.abspath('db_schemas_csv/Indigo_HR_Raw_Data_Filled.xlsx')

print(f"Reading file: {file_path}")
print(f"Output file: {output_path}")

# Open Excel via COM
try:
    excel = win32com.client.gencache.EnsureDispatch("Excel.Application")
except:
    excel = win32com.client.Dispatch("Excel.Application")

try:
    excel.Visible = False
except:
    pass  # Some Excel versions may not allow this
    
try:
    excel.DisplayAlerts = False
except:
    pass

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
    
    # Find Name and Email columns (columns 3 and 8 based on the header)
    name_col = 3
    email_col = 8
    
    filled_count = 0
    
    # Track used names to avoid duplicates
    used_names = set()
    
    # First, collect existing names
    for row in range(2, rows + 1):
        name_val = ws.Cells(row, name_col).Value
        if name_val and name_val != '\xa0' and name_val.strip():
            used_names.add(name_val.strip())
    
    print(f"\nProcessing rows...")
    print(f"Found {len(used_names)} existing names")
    
    # Fill empty rows with fictitious data
    for row in range(2, rows + 1):
        name_val = ws.Cells(row, name_col).Value
        email_val = ws.Cells(row, email_col).Value
        
        # Check if name is empty or just whitespace
        is_empty = (name_val is None or 
                    name_val == '\xa0' or 
                    (isinstance(name_val, str) and name_val.strip() == ''))
        
        if is_empty:
            # Generate a unique fictitious name
            attempts = 0
            while attempts < 100:
                new_name = generate_name()
                if new_name not in used_names:
                    used_names.add(new_name)
                    break
                attempts += 1
            
            # Fill in the name
            ws.Cells(row, name_col).Value = new_name
            
            # Fill in the email
            new_email = generate_email(new_name)
            ws.Cells(row, email_col).Value = new_email
            
            filled_count += 1
            
            if filled_count <= 10:
                print(f"Row {row}: Added '{new_name}' with email '{new_email}'")
    
    print(f"\nTotal rows filled: {filled_count}")
    
    # Save as new file
    wb.SaveAs(output_path)
    print(f"\nFile saved successfully to: {output_path}")
    
    wb.Close(SaveChanges=False)
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    
finally:
    excel.Quit()

print("\nDone!")
