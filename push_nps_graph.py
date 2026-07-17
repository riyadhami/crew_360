"""Additively push IndigoNPS_Summary table + concept vertices into the live Unified_Knowledge_graph.

Pure addV/addE — no drops, no touching of any existing vertex. Same ID scheme as
push_clms_raw_data_graph.py (table__{database}__{nodeId}, concept__{database}__{nodeId}).
"""
import json
from src.utils.cosmos_helpers import (
    get_cosmos_client, run_gremlin, close_cosmos_client, escape_gremlin, serialize_list,
)

DATABASE = "NPS"
TABLE_NODE_ID = "indigonps_summary"
TABLE_NAME = "IndigoNPS_Summary"

COLUMNS = [
    "NPS Type", "Start Date Time", "FLTNBR", "SSR without Description", "DEP", "ARR",
    "Response Status", "Pre Booked Meal", "Baggage Weight", "Baggage Count", "CheckIn Type",
    "Departure Station", "Arrival station", "City Pair", "Title", "First Name", "Middle Name",
    "Last Name", "Source Of Booking", "Departure Date", "Departure Time", "Arrival Station Hindi",
    "CPML", "NPS Score", "Booking experience", "Pre-travel information experience",
    "Check-in experience", "Boarding experience", "On-board experience", "Arrival experience",
    "Please share your reasons for the rating", "Ease of booking (itinerary / meals / s",
    "Required information available on websit", "Ease of payment (booking / add ons)",
    "Relevant information before arriving at", "Query handling – Contact center / Dottie",
    "your pre-travel experience?", "Check in process was easy(Online / Kiosk",
    "Time taken to check in(Counter-within15", "Staff efficiency at the counter",
    "your check-in experience", "Experience with airport security",
    "Boarding information shared / display", "Clarity of announcements",
    "Staff efficiency at the boarding gate", "Crew helpfulness", "Clarity of crew announcements",
    "Clarity of pilot announcements", "Cabin cleanliness", "Toilet cleanliness",
    "your on-board experience?", "Service experience– Contact center",
    "Did you receive your bag within 25 min o", "Staff efficiency at the arrival desk",
    "your arrival experience?", "Crew Base1(LDE)", "Crew Base2(CA)", "Crew Base3(CA)",
    "Crew Base4(CA)", "Is Moved Date", "Total No Of Pax", "Crew Name", "IGA Code",
    "Survey: Name",
]

CONCEPTS = [
    {
        "id": "flight_trip_context",
        "label": "Flight & Trip Context",
        "description": "Flight number, route, dates, check-in type, baggage, and meal/service selections for the surveyed journey.",
        "columns": ["NPS Type", "Start Date Time", "FLTNBR", "SSR without Description", "DEP",
                    "ARR", "Response Status", "Pre Booked Meal", "Baggage Weight",
                    "Baggage Count", "CheckIn Type", "Departure Station", "Arrival station",
                    "City Pair", "Departure Date", "Departure Time", "Arrival Station Hindi",
                    "CPML", "Total No Of Pax", "Is Moved Date"],
    },
    {
        "id": "passenger_identity",
        "label": "Passenger Identity",
        "description": "Identity and booking-channel details of the passenger who took the survey.",
        "columns": ["Title", "First Name", "Middle Name", "Last Name", "Source Of Booking",
                    "Survey: Name"],
    },
    {
        "id": "overall_nps_satisfaction_scores",
        "label": "Overall NPS & Satisfaction Scores",
        "description": "Top-line Net Promoter Score and per-journey-stage satisfaction ratings (booking, pre-travel, check-in, boarding, on-board, arrival), plus free-text rating rationale.",
        "columns": ["NPS Score", "Booking experience", "Pre-travel information experience",
                    "Check-in experience", "Boarding experience", "On-board experience",
                    "Arrival experience", "Please share your reasons for the rating"],
    },
    {
        "id": "booking_pretravel_experience_detail",
        "label": "Booking & Pre-Travel Experience Detail",
        "description": "Granular ratings for the booking and pre-travel information journey stage (ease of booking, website info, payment, contact-center query handling).",
        "columns": ["Ease of booking (itinerary / meals / s",
                    "Required information available on websit",
                    "Ease of payment (booking / add ons)",
                    "Relevant information before arriving at",
                    "Query handling – Contact center / Dottie", "your pre-travel experience?"],
    },
    {
        "id": "checkin_boarding_experience_detail",
        "label": "Check-in & Boarding Experience Detail",
        "description": "Granular ratings for check-in and boarding (process ease, wait time, staff efficiency, security, announcements).",
        "columns": ["Check in process was easy(Online / Kiosk",
                    "Time taken to check in(Counter-within15", "Staff efficiency at the counter",
                    "your check-in experience", "Experience with airport security",
                    "Boarding information shared / display", "Clarity of announcements",
                    "Staff efficiency at the boarding gate"],
    },
    {
        "id": "onboard_crew_cabin_experience_detail",
        "label": "On-board Experience Detail (Crew & Cabin)",
        "description": "Granular in-flight ratings covering crew helpfulness, crew/pilot announcement clarity, and cabin/toilet cleanliness.",
        "columns": ["Crew helpfulness", "Clarity of crew announcements",
                    "Clarity of pilot announcements", "Cabin cleanliness", "Toilet cleanliness",
                    "your on-board experience?", "Service experience– Contact center"],
    },
    {
        "id": "arrival_experience_crew_attribution",
        "label": "Arrival Experience & Crew Attribution",
        "description": "Arrival/baggage-delivery ratings plus the operating crew members (by base and IGA code) attributed to the flight, enabling crew-to-NPS correlation.",
        "columns": ["Did you receive your bag within 25 min o", "Staff efficiency at the arrival desk",
                    "your arrival experience?", "Crew Base1(LDE)", "Crew Base2(CA)",
                    "Crew Base3(CA)", "Crew Base4(CA)", "Crew Name", "IGA Code"],
    },
]

client = get_cosmos_client(graph_container="Unified_Knowledge_graph")
gremlin_log = []

def run(query, ignore_conflict=True):
    gremlin_log.append(query)
    return run_gremlin(client, query, ignore_conflict=ignore_conflict)

table_node_id_ns = f"{DATABASE}__{TABLE_NODE_ID}"
table_vid = f"table__{table_node_id_ns}"

table_desc = (
    "Flat NPS (Net Promoter Score) survey export — one row per passenger survey response, "
    "covering journey-stage satisfaction ratings (booking, pre-travel, check-in, boarding, "
    "on-board, arrival) and the operating crew (by IGA code) attributed to the flight, "
    "enabling correlation between crew members and passenger satisfaction outcomes."
)
concepts_list = [c["label"] for c in CONCEPTS]

stmt = (
    f"g.addV('Table')"
    f".property('id', '{escape_gremlin(table_vid)}')"
    f".property('database', '{escape_gremlin(DATABASE)}')"
    f".property('name', '{escape_gremlin(TABLE_NAME)}')"
    f".property('displayName', 'IndiGo NPS Summary (Passenger Satisfaction Survey)')"
    f".property('description', '{escape_gremlin(table_desc)}')"
    f".property('nodeId', '{escape_gremlin(table_node_id_ns)}')"
    f".property('columns', '{escape_gremlin(serialize_list(COLUMNS))}')"
    f".property('primaryKeys', '{escape_gremlin(serialize_list([]))}')"
    f".property('concepts', '{escape_gremlin(serialize_list(concepts_list))}')"
    f".property('notes', 'Single flat table sourced from indigoNPS_summary_synthetic.xlsx (339 rows, "
    f"one per crew member). IGA Code (format IGA#####) is the natural join key back to "
    f"Indigo_HR_Raw_Data.IGA and CLMS_Raw_Data.CrewID for cross-database crew lookups.')"
)
run(stmt)
print(f"Created table vertex: {table_vid}")

for c in CONCEPTS:
    concept_node_id_ns = f"{DATABASE}__{c['id']}"
    concept_vid = f"concept__{concept_node_id_ns}"
    key_cols = [{"table": TABLE_NAME, "column": col} for col in c["columns"]]
    stmt = (
        f"g.addV('Concept')"
        f".property('id', '{escape_gremlin(concept_vid)}')"
        f".property('database', '{escape_gremlin(DATABASE)}')"
        f".property('name', '{escape_gremlin(c['label'])}')"
        f".property('displayName', '{escape_gremlin(c['label'])}')"
        f".property('description', '{escape_gremlin(c['description'])}')"
        f".property('nodeId', '{escape_gremlin(concept_node_id_ns)}')"
        f".property('sourceTables', '{escape_gremlin(serialize_list([TABLE_NAME]))}')"
        f".property('keyColumns', '{escape_gremlin(json.dumps(key_cols, ensure_ascii=False))}')"
        f".property('notes', '')"
    )
    run(stmt)
    print(f"Created concept vertex: {concept_vid}")

    edge_stmt = (
        f"g.V('{escape_gremlin(table_vid)}')"
        f".addE('CONTAINS_CONCEPT')"
        f".to(g.V('{escape_gremlin(concept_vid)}'))"
        f".property('description', '')"
    )
    run(edge_stmt)

    relates_stmt = (
        f"g.V('{escape_gremlin(table_vid)}')"
        f".addE('RELATES_TO')"
        f".to(g.V('{escape_gremlin(concept_vid)}'))"
    )
    run(relates_stmt)

close_cosmos_client(client)

with open("output/NPS_gremlin_queries.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(gremlin_log))

print("DONE — pushed 1 table vertex, 7 concept vertices, 14 edges (additive only).")
