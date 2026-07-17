"""Excel-free loader: create HRData DB, load IJP from CSV, derive HR table."""
import pandas as pd, pyodbc
from pathlib import Path
from datetime import date, timedelta

SERVER = "localhost,1433"; USER = "sa"; PWD = "YourStrong@Passw0rd"; DB = "HRData"
CSV = Path(__file__).parent / "db_schemas_csv" / "IJP_Employee_scores.csv"

MAP = {
    'Fleet Type':'FleetType','IGA':'IGA','Designation':'Designation','Base':'Base','NAME':'Name',
    'LWP Days - extracted from CLMS':'LWPDays','BMI Data':'BMI',
    'Appreciation Letter received ':'AppreciationLetters',
    'Any non performance discussion  -   Caution letter / Counselling form':'CautionLetters',
    'Any non performance discussion  ':'NonPerfDiscussion',
    'Curative training/icoach session/Performance Coaching Session  ':'CoachingSessions',
    'Extra Initiatives/ recognition - "employee of the month / For each other / 3 or more 6e clap / 6e WOW champions':'Recognition',
}

def conn(db=None):
    cs = f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={SERVER};UID={USER};PWD={PWD};TrustServerCertificate=yes;"
    if db: cs += f"DATABASE={db};"
    return pyodbc.connect(cs)

def si(v):
    try: return int(float(v))
    except: return None
def sf(v):
    try: return float(v)
    except: return None

# 1) Create database
c = conn(); c.autocommit = True; cur = c.cursor()
cur.execute("IF DB_ID('HRData') IS NULL CREATE DATABASE HRData")
print("HRData database ready"); cur.close(); c.close()

# 2) Load IJP from CSV
df = pd.read_csv(CSV, encoding="latin-1").rename(columns=MAP).dropna(how="all")
df = df[df["IGA"].notna()].reset_index(drop=True)
print(f"IJP rows with IGA: {len(df)}")

# Fill blank names with generated ones (pure-Python port of generate_fictitious_data.py)
import random
random.seed(42)
FIRST = ["Rahul","Priya","Amit","Neha","Raj","Sneha","Vikram","Anjali","Arjun","Pooja","Rohan","Divya",
    "Karan","Meera","Aditya","Kavya","Sanjay","Shreya","Nikhil","Ritu","Varun","Simran","Harsh","Nisha",
    "Akash","Priyanka","Kunal","Swati","Manish","Sakshi","Siddharth","Tanvi","Abhishek","Isha","Gaurav",
    "Megha","Ashish","Pallavi","Rajesh","Aarti","Vivek","Deepika","Suresh","Ananya","Pankaj","Riya","Naveen"]
LAST = ["Sharma","Kumar","Singh","Gupta","Patel","Verma","Reddy","Rao","Joshi","Nair","Iyer","Menon",
    "Agarwal","Mehta","Shah","Desai","Kulkarni","Pandey","Mishra","Jain","Chopra","Kapoor","Malhotra",
    "Bhatia","Saxena","Sinha","Das","Sen","Ghosh","Banerjee","Mukherjee","Chatterjee","Yadav","Thakur"]
used = set()
def gen_name():
    for _ in range(200):
        nm = f"{random.choice(FIRST)} {random.choice(LAST)}"
        if nm not in used:
            used.add(nm); return nm
    return f"{random.choice(FIRST)} {random.choice(LAST)}"
def has_name(v): return not (pd.isna(v) or str(v).strip() in ("", "nan"))
df["Name"] = [v if has_name(v) else gen_name() for v in df["Name"]]
print(f"Named employees after fill: {df['Name'].notna().sum()}")

# Fictitious values for the HR columns IJP has no data for (pilots: Designation is CA/SCA)
TODAY = date.today()
INDIGO_FOUNDING = date(2006, 8, 4)
BASES = ["DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "PNQ", "GOI", "AMD", "COK", "JAI", "LKO"]
used_phones = set()

def gen_dob(designation):
    min_age, max_age = (34, 58) if designation == "SCA" else (26, 50)
    age_days = random.randint(min_age * 365, max_age * 365)
    return TODAY - timedelta(days=age_days)

def gen_doj(dob):
    earliest = max(INDIGO_FOUNDING, dob + timedelta(days=21 * 365))
    latest = TODAY - timedelta(days=30)
    if earliest >= latest:
        earliest = latest - timedelta(days=365)
    return earliest + timedelta(days=random.randint(0, (latest - earliest).days))

def gen_release_date(doj):
    if random.random() < 0.08:
        span = (TODAY - doj).days
        if span > 60:
            return doj + timedelta(days=random.randint(60, span))
    return None

def months_between(d1, d2):
    return max(0, (d2.year - d1.year) * 12 + (d2.month - d1.month))

def gen_flying_hours(months_tenure, designation):
    base = 1800.0 if designation == "SCA" else 900.0
    return round(base + months_tenure * random.uniform(45, 85), 1)

def gen_phone():
    for _ in range(50):
        n = str(random.choice([6, 7, 8, 9])) + "".join(str(random.randint(0, 9)) for _ in range(9))
        if n not in used_phones:
            used_phones.add(n); return n
    return n

def gen_coc():
    r = random.random()
    return "Valid" if r < 0.85 else ("Renewal Due" if r < 0.95 else "Expired")

def gen_eligibility():
    r = random.random()
    return "Eligible" if r < 0.8 else ("Not Eligible" if r < 0.9 else "Under Review")

def gen_message(elig):
    if elig == "Eligible":
        return random.choice([
            "Meets all IJP eligibility criteria.",
            "Cleared HR screening for internal job posting.",
            "No disciplinary flags; eligible to apply.",
        ])
    if elig == "Not Eligible":
        return random.choice([
            "Does not meet minimum tenure requirement.",
            "Active caution letter on file; review required.",
            "LWP days exceed policy threshold for this cycle.",
        ])
    return "Pending documentation review by HR."

def gen_function(aircraft_type):
    return "Flight Operations - Widebody" if any(x in str(aircraft_type) for x in ("77W", "789")) else "Flight Operations - Narrowbody"

def gen_preferred_locations(base):
    return random.sample([b for b in BASES if b != base], 3)

c = conn(DB); cur = c.cursor()
cur.execute("IF OBJECT_ID('dbo.IJP_Employee_scores','U') IS NOT NULL DROP TABLE dbo.IJP_Employee_scores")
cur.execute("""CREATE TABLE dbo.IJP_Employee_scores(
    ID INT IDENTITY(1,1) PRIMARY KEY, FleetType NVARCHAR(100), IGA INT, Designation NVARCHAR(50),
    Base NVARCHAR(50), Name NVARCHAR(200), LWPDays INT, BMI FLOAT, AppreciationLetters INT,
    CautionLetters NVARCHAR(100), NonPerfDiscussion NVARCHAR(100), CoachingSessions NVARCHAR(100),
    Recognition NVARCHAR(200))""")
c.commit()
ins = """INSERT INTO dbo.IJP_Employee_scores
    (FleetType,IGA,Designation,Base,Name,LWPDays,BMI,AppreciationLetters,CautionLetters,NonPerfDiscussion,CoachingSessions,Recognition)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
n = 0
def s(v): return None if pd.isna(v) else str(v)
for _, r in df.iterrows():
    cur.execute(ins, s(r.get('FleetType')), si(r.get('IGA')), s(r.get('Designation')), s(r.get('Base')),
                s(r.get('Name')), si(r.get('LWPDays')), sf(r.get('BMI')), si(r.get('AppreciationLetters')),
                s(r.get('CautionLetters')), s(r.get('NonPerfDiscussion')), s(r.get('CoachingSessions')), s(r.get('Recognition')))
    n += 1
c.commit(); print(f"IJP_Employee_scores loaded: {n} rows")

# 3) Derive Indigo_HR_Raw_Data from IJP (full HR schema, key cols populated)
cur.execute("IF OBJECT_ID('dbo.Indigo_HR_Raw_Data','U') IS NOT NULL DROP TABLE dbo.Indigo_HR_Raw_Data")
cur.execute("""CREATE TABLE dbo.Indigo_HR_Raw_Data(
    [Req Id] FLOAT, [IGA] FLOAT, [Name] NVARCHAR(255), [Base] NVARCHAR(50), [Designation] NVARCHAR(100),
    [Applicant's Function] NVARCHAR(100), [IJP End Date] DATETIME, [Email id] NVARCHAR(255),
    [Phone No] NVARCHAR(50), [DOB] DATETIME, [Age (As on the last date)] NVARCHAR(50), [DOJ] DATETIME,
    [Months in IndiGo] NVARCHAR(50), [Date of Release] DATETIME, [Total Flying Expeirence (In IndiGo)] FLOAT,
    [COC] NVARCHAR(50), [Aircraft type] NVARCHAR(50), [Eligibility Check as per HR] NVARCHAR(50),
    [Message] NVARCHAR(MAX), [Preferred Location 1] NVARCHAR(50), [Preferred Location 2] NVARCHAR(50),
    [Preferred Location 3] NVARCHAR(50))""")
c.commit()
# Populate identity + key attributes from IJP, plus fictitious values for the rest
hr_ins = """INSERT INTO dbo.Indigo_HR_Raw_Data
    ([Req Id],[IGA],[Name],[Base],[Designation],[Applicant's Function],[IJP End Date],[Email id],
     [Phone No],[DOB],[Age (As on the last date)],[DOJ],[Months in IndiGo],[Date of Release],
     [Total Flying Expeirence (In IndiGo)],[COC],[Aircraft type],[Eligibility Check as per HR],
     [Message],[Preferred Location 1],[Preferred Location 2],[Preferred Location 3])
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
hr = 0
for i, r in enumerate(df.iterrows(), 1):
    _, row = r
    iga = si(row.get('IGA'))
    if iga is None: continue
    name = s(row.get('Name'))
    base = s(row.get('Base')) or random.choice(BASES)
    designation = s(row.get('Designation')) or "CA"
    aircraft = s(row.get('FleetType'))
    email = (str(name).replace(", ", ".").replace(" ", ".") + "@GOINDIGO.IN") if name else None

    dob = gen_dob(designation)
    doj = gen_doj(dob)
    release = gen_release_date(doj)
    tenure_end = release or TODAY
    months_tenure = months_between(doj, tenure_end)
    eligibility = gen_eligibility()
    loc1, loc2, loc3 = gen_preferred_locations(base)

    ijp_end_date = TODAY - timedelta(days=random.randint(10, 270))
    age = months_between(dob, TODAY) // 12
    cur.execute(hr_ins,
        float(i), float(iga), name, base, designation,
        gen_function(aircraft), ijp_end_date, email,
        gen_phone(), dob, str(age), doj, str(months_tenure), release,
        gen_flying_hours(months_tenure, designation), gen_coc(), aircraft, eligibility,
        gen_message(eligibility), loc1, loc2, loc3)
    hr += 1
c.commit(); print(f"Indigo_HR_Raw_Data derived: {hr} rows")

# 4) Verify
cur.execute("SELECT COUNT(*) FROM dbo.IJP_Employee_scores"); print("verify IJP:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM dbo.Indigo_HR_Raw_Data"); print("verify HR:", cur.fetchone()[0])
cur.execute("SELECT TOP 3 [IGA],[Name],[Base],[Designation] FROM dbo.Indigo_HR_Raw_Data")
for row in cur.fetchall(): print("  HR sample:", tuple(row))
cur.close(); c.close()
print("DONE")
