import csv
from datetime import datetime, timedelta
import random
import hashlib

# Read the schema definition
schema_file = 'db_schemas_csv/PEP_Schema_Defn_2026-04-15.csv'
output_file = 'db_schemas_csv/PEP_sample_data.csv'

# Parse schema
tables_schema = {}
with open(schema_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        table_name = row['TABLE_NAME']
        if table_name not in tables_schema:
            tables_schema[table_name] = []
        tables_schema[table_name].append(row)

# Sample data generators
def generate_timestamp():
    base = datetime(2026, 1, 1)
    random_days = random.randint(0, 120)
    return (base + timedelta(days=random_days)).strftime('%Y-%m-%d %H:%M:%S')

def generate_date():
    base = datetime(2026, 1, 1)
    random_days = random.randint(0, 120)
    return (base + timedelta(days=random_days)).strftime('%Y-%m-%d')

def generate_hash():
    return hashlib.md5(str(random.random()).encode()).hexdigest()[:16]

def generate_value(column_name, data_type, max_length):
    """Generate sample data based on column type"""
    
    # Common audit fields
    if column_name == 'P_CREATED_BY' or column_name == 'P_MODIFIED_BY' or column_name == 'CREATED_BY' or column_name == 'MODIFIED_BY' or column_name == 'UPDATED_BY':
        return random.choice(['system_user', 'admin', 'john.doe', 'jane.smith'])
    if column_name in ['P_CREATED_DT', 'P_MODIFIED_DT', 'CREATED_DATE', 'CREATED_ON', 'MODIFIED_ON', 'LOAD_DATE', 'UPDATED_ON', 'UPDATED_DATE']:
        return generate_timestamp()
    if column_name == 'P_IS_CURRENT' or column_name == 'IS_CURRENT':
        return 'TRUE'
    if column_name == 'ROW_HASH':
        return generate_hash()
    
    # Boolean fields
    if data_type == 'BOOLEAN':
        if 'ACTIVE' in column_name or 'IS_ACTIVE' in column_name:
            return random.choice(['TRUE', 'TRUE', 'TRUE', 'FALSE'])  # More active records
        return random.choice(['TRUE', 'FALSE'])
    
    # Number fields
    if data_type == 'NUMBER':
        if 'ID' in column_name:
            return str(random.randint(1000, 9999))
        elif 'POSITION' in column_name or 'ORDER' in column_name:
            return str(random.randint(1, 10))
        elif 'RANGE' in column_name:
            return str(random.randint(0, 100))
        else:
            return str(random.randint(1, 100))
    
    # Float fields
    if data_type == 'FLOAT':
        if 'MARK' in column_name:
            return str(round(random.uniform(50, 100), 2))
        return str(round(random.uniform(0, 100), 2))
    
    # Date fields
    if data_type == 'DATE':
        return generate_date()
    
    # Timestamp fields
    if data_type == 'TIMESTAMP_NTZ':
        return generate_timestamp()
    
    # Text fields - context specific
    if data_type == 'TEXT':
        # Email fields
        if 'EMAIL' in column_name or 'MAIL' in column_name:
            return random.choice(['john.doe@airline.com', 'jane.smith@airline.com', 'mentor@airline.com', 'crew@airline.com'])
        
        # IGA/Login fields
        if column_name == 'IGA' or 'LOGIN' in column_name:
            return f"IGA{random.randint(10000, 99999)}"
        
        # Name fields
        if 'NAME' in column_name:
            return random.choice(['John Doe', 'Jane Smith', 'Mike Johnson', 'Sarah Williams', 'David Brown'])
        
        # Base/Location
        if column_name == 'BASE':
            return random.choice(['DEL', 'BOM', 'BLR', 'HYD', 'CCU'])
        
        # Designation
        if 'DESIGNATION' in column_name or 'DEGN' in column_name:
            return random.choice(['CA', 'FO', 'CC', 'SCC', 'CP'])
        
        # Status
        if 'STATUS' in column_name:
            return random.choice(['Active', 'Completed', 'Pending', 'Scheduled'])
        
        # Grade
        if 'GRADE' in column_name:
            return random.choice(['A', 'B', 'C', 'D'])
        
        # Flight related
        if 'FLIGHT' in column_name:
            if 'TYPE' in column_name:
                return random.choice(['ATR', 'A320', 'A321'])
            elif 'NO' in column_name:
                return f"6E{random.randint(100, 999)}"
        
        # Sector
        if column_name == 'SECTOR':
            airports = ['DEL', 'BOM', 'BLR', 'HYD', 'CCU', 'MAA', 'AMD', 'GOI']
            return f"{random.choice(airports)}-{random.choice(airports)}"
        
        # Code fields
        if 'CODE' in column_name:
            if max_length and int(max_length) <= 3:
                return random.choice(['CA', 'FO', 'CC'])
            elif max_length and int(max_length) <= 20:
                return f"CODE{random.randint(100, 999)}"
            else:
                return f"CODE_{random.randint(1000, 9999)}"
        
        # Reason/Remark fields
        if 'REASON' in column_name or 'REMARK' in column_name or 'FEEDBACK' in column_name:
            return random.choice(['Good performance', 'Needs improvement', 'Excellent', 'Satisfactory', 'Outstanding work'])
        
        # Long text fields
        if max_length and int(max_length) > 1000:
            return 'Sample detailed text content with feedback and observations'
        
        # Default text
        if max_length:
            max_len = min(int(max_length), 50)
            return f"Sample_{random.randint(1, 100)}"[:max_len]
        return f"Sample_{random.randint(1, 100)}"
    
    return ''

# Generate sample data
all_sample_data = []

for table_name, columns in tables_schema.items():
    # Generate 3 sample rows per table
    for row_num in range(3):
        sample_row = {
            'TABLE_NAME': table_name,
            'ROW_NUMBER': row_num + 1
        }
        
        for col_info in columns:
            col_name = col_info['COLUMN_NAME']
            data_type = col_info['DATA_TYPE']
            max_length = col_info['CHARACTER_MAXIMUM_LENGTH']
            
            value = generate_value(col_name, data_type, max_length)
            sample_row[col_name] = value
        
        all_sample_data.append(sample_row)

# Write to CSV
if all_sample_data:
    # Get all unique column names across all tables
    all_columns = set()
    for row in all_sample_data:
        all_columns.update(row.keys())
    
    # Sort columns for better readability
    sorted_columns = sorted(all_columns)
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=sorted_columns)
        writer.writeheader()
        writer.writerows(all_sample_data)
    
    print(f"✓ Generated sample data for {len(tables_schema)} tables")
    print(f"✓ Total {len(all_sample_data)} sample rows created")
    print(f"✓ Output saved to: {output_file}")
else:
    print("No data generated")
