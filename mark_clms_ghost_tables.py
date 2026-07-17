"""Prepend a clear non-physical warning to the 17 CLMS table vertices that have no
physical SQL table backing them (only CLMS_Raw_Data does). Property updates only —
no vertices/edges added or removed, nothing else touched.
"""
from src.utils.cosmos_helpers import get_cosmos_client, run_gremlin, close_cosmos_client, escape_gremlin

GHOST_TABLE_IDS = [
    "table__CLMS__crew_balance_update_by_scheduler",
    "table__CLMS__crew_master",
    "table__CLMS__leave_balance",
    "table__CLMS__crew_designation_master",
    "table__CLMS__leave_year",
    "table__CLMS__leave_type_catalog",
    "table__CLMS__lms_status_type",
    "table__CLMS__crew_attrition",
    "table__CLMS__gndcode_transactions",
    "table__CLMS__leave_request_detail",
    "table__CLMS__leave_request_other_rocade_codes_detail",
    "table__CLMS__leave_request",
    "table__CLMS__leave_request_other_rocade_codes",
    "table__CLMS__crew_leaves_allocation",
    "table__CLMS__crew_leave_year_transition",
    "table__CLMS__crew_leaveyear_suggested",
    "table__CLMS__crew_appreciation",
]

WARNING = (
    "[DESIGN REFERENCE ONLY — NOT PHYSICALLY QUERYABLE. No SQL table backs this node; "
    "any execute_query against it will fail with 'Invalid object name'. All real CLMS data "
    "lives in the single physical table CLMS_Raw_Data — query that instead.] "
)

client = get_cosmos_client(graph_container="Unified_Knowledge_graph")

updated = 0
for vid in GHOST_TABLE_IDS:
    result = run_gremlin(client, f"g.V('{escape_gremlin(vid)}').values('description')")
    if not result:
        print(f"SKIP (not found): {vid}")
        continue
    current_desc = result[0]
    if current_desc.startswith("[DESIGN REFERENCE ONLY"):
        print(f"SKIP (already marked): {vid}")
        continue
    new_desc = WARNING + current_desc
    stmt = f"g.V('{escape_gremlin(vid)}').property('description', '{escape_gremlin(new_desc)}')"
    run_gremlin(client, stmt)
    updated += 1
    print(f"Marked: {vid}")

close_cosmos_client(client)
print(f"DONE — updated {updated} vertex descriptions (property update only, no add/remove).")
