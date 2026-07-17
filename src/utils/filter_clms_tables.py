import csv
import os

# File paths
data_file = 'db_schemas_csv/CLMS Table Data .csv'
details_file = 'db_schemas_csv/CLMS Table Details.csv'
relationship_file = 'db_schemas_csv/CLMS Table RelationShip.csv'

# Backup original files
details_backup = details_file.replace('.csv', '_backup.csv')
relationship_backup = relationship_file.replace('.csv', '_backup.csv')

print("Creating backups...")
os.system(f'copy "{details_file}" "{details_backup}"')
os.system(f'copy "{relationship_file}" "{relationship_backup}"')

# Step 1: Read table names from CLMS Table Data .csv
print(f"\nReading table names from: {data_file}")
valid_tables = set()

with open(data_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        table_name = row['TABLE_NAME'].strip()
        if table_name:
            valid_tables.add(table_name)

print(f"Found {len(valid_tables)} valid tables:")
for table in sorted(valid_tables):
    print(f"  - {table}")

# Step 2: Filter CLMS Table Details.csv
print(f"\nFiltering: {details_file}")
filtered_details = []
removed_details = []

with open(details_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    
    for row in reader:
        table_name = row['TABLE_NAME'].strip()
        if table_name in valid_tables:
            filtered_details.append(row)
        else:
            removed_details.append(table_name)

# Write filtered details
with open(details_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(filtered_details)

print(f"  Kept: {len(filtered_details)} rows")
print(f"  Removed: {len(removed_details)} rows")
if removed_details:
    print(f"  Removed tables: {', '.join(sorted(set(removed_details)))}")

# Step 3: Filter CLMS Table RelationShip.csv
print(f"\nFiltering: {relationship_file}")
filtered_relationships = []
removed_relationships = []

with open(relationship_file, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    
    for row in reader:
        # Note: The column name has a space: "TABLE NAME"
        table_name = row['TABLE NAME'].strip()
        if table_name in valid_tables:
            filtered_relationships.append(row)
        else:
            removed_relationships.append(table_name)

# Write filtered relationships
with open(relationship_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(filtered_relationships)

print(f"  Kept: {len(filtered_relationships)} rows")
print(f"  Removed: {len(removed_relationships)} rows")
if removed_relationships:
    print(f"  Removed tables: {', '.join(sorted(set(removed_relationships)))}")

print("\n✓ Filtering complete!")
print(f"✓ Backups saved:")
print(f"  - {details_backup}")
print(f"  - {relationship_backup}")
