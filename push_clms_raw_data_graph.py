"""Additively push CLMS_Raw_Data table + concept vertices into the live Unified_Knowledge_graph.

Pure addV/addE — no drops, no touching of any existing vertex. Mirrors the exact
ID scheme and property shape produced by src/graph_unification.py's load_to_cosmos
(table__{database}__{nodeId}, concept__{database}__{nodeId}) so the new table is
discoverable by the Data Retrieval Agent exactly like every other table.
"""
import json
from src.utils.cosmos_helpers import (
    get_cosmos_client, run_gremlin, close_cosmos_client, escape_gremlin, serialize_list,
)

DATABASE = "CLMS"
TABLE_NODE_ID = "clms_raw_data"          # namespaced -> CLMS__clms_raw_data
TABLE_NAME = "CLMS_Raw_Data"              # exact physical SQL table name

COLUMNS = [
    "CrewID", "CrewName", "Email", "Designation", "Base", "Region", "DOJ", "DOL", "RoleID",
    "Active", "Status", "Category", "ContactNo", "HireAs", "IgoExp", "TotalExp", "MedicalDueDt",
    "PassportDueDt", "CreatedDtm", "UpdatedDtm", "LeaveYearId", "LeaveYearName",
    "LeaveYearShortName", "LeaveYearStartDate", "LeaveYearEndDate", "LeaveTypeID", "LeaveType",
    "LeaveDescription", "StatusTypeID", "StatusType", "BalanceID", "Balance", "StartDate",
    "EndDate", "LastTmuBalance", "LastStartDate", "LastEndDate", "LeaveAllocationId",
    "LeavesAllotted", "IsContChangeEntitlment", "LeaveAllocationActive", "LeaveDetailID",
    "LeaveReqSubId", "RequestedDate", "FromDT", "ToDt", "NoOfLeaves", "CurrentLeave", "WLNO",
    "CrewComment", "Remarks", "ActionBy", "ActionDate", "RequestCreatedDtm", "GndCode", "EMPNO",
    "LEGCD", "STARTTIME", "ENDTIME", "LEGSTATUS", "GNDACTCODE", "ApprovedStatus", "IsPayable",
    "GNDRemark", "OldSLBalance", "updatedSLBalance", "OldURTIBalance", "updatedURTIBalance",
    "LWD", "IsSchedulerUpdate", "EligibleMonths", "UpdatedTime", "IsRevoked", "Suggested_SL",
    "Suggested_SL_ID", "Curr_Base", "Pre_Base", "Trans_Date", "AttritionReason",
    "AttritionSubReason", "AppreciationFromDt", "AppreciationToDt", "isAwardReceived",
]

CONCEPTS = [
    {
        "id": "crew_identity_employment_raw",
        "label": "Crew Identity & Employment Profile (Raw)",
        "description": "Crew member identity, contact, base/region assignment, employment status, experience, and compliance due dates from the flat CLMS raw export.",
        "columns": ["CrewID", "CrewName", "Email", "Designation", "Base", "Region", "DOJ", "DOL",
                    "RoleID", "Active", "Status", "Category", "ContactNo", "HireAs", "IgoExp",
                    "TotalExp", "MedicalDueDt", "PassportDueDt", "CreatedDtm", "UpdatedDtm"],
    },
    {
        "id": "leave_year_type_config_raw",
        "label": "Leave Year & Type Configuration (Raw)",
        "description": "Leave year definitions and leave type/status catalog fields attached to each leave record in the flat CLMS raw export.",
        "columns": ["LeaveYearId", "LeaveYearName", "LeaveYearShortName", "LeaveYearStartDate",
                    "LeaveYearEndDate", "LeaveTypeID", "LeaveType", "LeaveDescription",
                    "StatusTypeID", "StatusType"],
    },
    {
        "id": "leave_balance_allocation_raw",
        "label": "Leave Balance & Allocation (Raw)",
        "description": "Per-crew leave balance snapshots (current and prior period) and annual leave allocation entitlements.",
        "columns": ["BalanceID", "Balance", "StartDate", "EndDate", "LastTmuBalance",
                    "LastStartDate", "LastEndDate", "LeaveAllocationId", "LeavesAllotted",
                    "IsContChangeEntitlment", "LeaveAllocationActive"],
    },
    {
        "id": "leave_request_ground_duty_raw",
        "label": "Leave Requests & Ground Duty Codes (Raw)",
        "description": "Individual leave request details (dates, comments, approval trail) plus ground-duty/roster code transactions for the crew member.",
        "columns": ["LeaveDetailID", "LeaveReqSubId", "RequestedDate", "FromDT", "ToDt",
                    "NoOfLeaves", "CurrentLeave", "WLNO", "CrewComment", "Remarks", "ActionBy",
                    "ActionDate", "RequestCreatedDtm", "GndCode", "EMPNO", "LEGCD", "STARTTIME",
                    "ENDTIME", "LEGSTATUS", "GNDACTCODE", "ApprovedStatus", "IsPayable",
                    "GNDRemark"],
    },
    {
        "id": "scheduler_sl_urti_updates_raw",
        "label": "Scheduler SL/URTI Balance Updates (Raw)",
        "description": "Scheduler-driven Sick Leave and URTI balance change tracking, including eligibility and revocation status.",
        "columns": ["OldSLBalance", "updatedSLBalance", "OldURTIBalance", "updatedURTIBalance",
                    "LWD", "IsSchedulerUpdate", "EligibleMonths", "UpdatedTime", "IsRevoked",
                    "Suggested_SL", "Suggested_SL_ID"],
    },
    {
        "id": "attrition_transfer_appreciation_raw",
        "label": "Attrition, Base Transfer & Appreciation (Raw)",
        "description": "Crew attrition/exit reasons, base-transfer history, and appreciation/award records.",
        "columns": ["Curr_Base", "Pre_Base", "Trans_Date", "AttritionReason", "AttritionSubReason",
                    "AppreciationFromDt", "AppreciationToDt", "isAwardReceived"],
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
    "Flat, denormalized raw export of Crew Leave Management System data — one row per "
    "leave/balance/ground-duty transaction, combining crew identity, leave year/type "
    "configuration, leave balances and allocations, leave requests, scheduler SL/URTI "
    "balance updates, attrition, base transfers, and appreciation records."
)
concepts_list = [c["label"] for c in CONCEPTS]

stmt = (
    f"g.addV('Table')"
    f".property('id', '{escape_gremlin(table_vid)}')"
    f".property('database', '{escape_gremlin(DATABASE)}')"
    f".property('name', '{escape_gremlin(TABLE_NAME)}')"
    f".property('displayName', 'CLMS Raw Data (Crew Leave, Balance & Attrition Snapshot)')"
    f".property('description', '{escape_gremlin(table_desc)}')"
    f".property('nodeId', '{escape_gremlin(table_node_id_ns)}')"
    f".property('columns', '{escape_gremlin(serialize_list(COLUMNS))}')"
    f".property('primaryKeys', '{escape_gremlin(serialize_list([]))}')"
    f".property('concepts', '{escape_gremlin(serialize_list(concepts_list))}')"
    f".property('notes', 'Single flat table sourced from clms_rawdata.xlsx (100 sample rows). "
    f"No declared primary key; CrewID is the natural crew identifier for joins.')"
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

with open("output/CLMS_Raw_Data_gremlin_queries.txt", "w", encoding="utf-8") as f:
    f.write("\n\n".join(gremlin_log))

print("DONE — pushed 1 table vertex, 6 concept vertices, 12 edges (additive only).")
