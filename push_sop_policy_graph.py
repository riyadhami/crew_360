"""Additively push the crew SOP (docs/sop_crew_performance.md) into the live
Unified_Knowledge_graph as 'Policy' vertices — one per section.

Idempotent: creates a vertex if it doesn't exist yet, otherwise updates its
content/parameters properties in place (never duplicates, never drops).

The "Weighted Performance Scoring" section also carries a `parameters` JSON
property — the single source of truth _get_scoring_parameters() reads at
runtime instead of hardcoding weights/thresholds in Python. Editing that
property changes scoring behavior immediately, with no code deploy. The other
sections are prose-only (`content` field), each RELATES_TO-linked to the table
it governs so the agent can navigate from data it's examining into the
procedure that governs it, not just keyword-match prose.

Pure addV/addE/property updates — no drops, no touching of any other vertex.
"""
import json
from src.utils.cosmos_helpers import (
    get_cosmos_client, run_gremlin, close_cosmos_client, escape_gremlin,
)

# Single source of truth for the scoring formula. Matches docs/sop_crew_performance.md
# section "Weighted Performance Scoring" exactly.
SCORING_PARAMETERS = {
    "availability": {"weight": 0.40, "penalty_per_leave_day": 10},
    "appreciation": {"weight": 0.20, "points_per_letter": 10, "max_score": 100},
    "bmi": {
        "weight": 0.10,
        "ideal_min": 18.5, "ideal_max": 24.9, "ideal_score": 100.0,
        "near_min": 17.0, "near_max_low": 25.0, "near_max_high": 27.0, "near_score": 80.0,
        "moderate_min": 16.0, "moderate_max": 30.0, "moderate_score": 60.0,
        "far_score": 40.0,
    },
    "non_performance": {"weight": 0.10, "penalty_per_caution_letter": 20},
    "nps": {"weight": 0.10, "scale_factor": 10},
    "coaching": {"weight": 0.05, "points_per_session": 20, "max_score": 100},
    "recognition": {"weight": 0.05, "clap_score": 100, "other_score": 80},
}

SECTIONS = [
    {
        "id": "policy__weighted_performance_scoring",
        "name": "Weighted Performance Scoring Policy",
        "description": (
            "Single source of truth for crew weighted performance scoring: 7 components "
            "(Crew Availability, Appreciation Letters, BMI, Non-performance Discussions, "
            "Passenger NPS Feedback, Curative Training, Recognition). The scoring function "
            "reads its weights and thresholds from this node."
        ),
        "content": (
            "## Weighted Performance Scoring\n\n"
            "Every crew member's score is 7 components, each normalized 0-100, summing to a "
            "weighted total out of 100. Missing components are excluded from the total "
            "(partial scores are valid) and reported individually rather than blocking the "
            "score.\n\n"
            "| Component | Weight | Formula | Source table |\n"
            "|---|---|---|---|\n"
            "| Crew Availability | 40% | 0 if released/inactive; else max(100 - NoOfLeaves*10, 0) using the most recent leave record | CLMS_Raw_Data |\n"
            "| Appreciation Letters | 20% | min(letters*10, 100) | IJP_Employee_scores |\n"
            "| Poise & Grace (BMI) | 10% | 100 if 18.5-24.9; 80 if 17.0-18.4 or 25.0-27.0; 60 if 16.0-16.9 or 27.1-30.0; else 40 | Indigo_HR_Raw_Data |\n"
            "| Non-performance Discussions | 10% | max(100 - caution_letters*20, 0) - lower is better | IJP_Employee_scores |\n"
            "| Passenger NPS Feedback | 10% | NPS_Score * 10 (NPS is 0-10) | IndigoNPS_Summary |\n"
            "| Curative Training/iCoach | 5% | min(sessions*20, 100) | IJP_Employee_scores |\n"
            "| Extra Initiatives/Recognition | 5% | 100 for \"6E Clap\"; 80 for other recognition; 0 for none | IJP_Employee_scores |\n\n"
            "All source tables live in one HRData SQL Server database and join on IGA. "
            "Available as a single-employee score or ranked across all crew (best or worst, "
            "optionally filtered by base/designation)."
        ),
        "parameters": SCORING_PARAMETERS,
        "linked_table": None,  # conceptually spans all four tables; skip a single link
    },
    {
        "id": "policy__leave_management_clms",
        "name": "Leave Management (CLMS) Policy",
        "description": (
            "Governs crew leave requests, types (SL/PL/URTI/CL/GND/SPL), balance checks, "
            "scheduler-driven SL/URTI adjustments, separation handling (Status/Active/LWD), "
            "and base transfers."
        ),
        "content": (
            "## Leave Management (CLMS)\n"
            "- Leave types: SL Sick Leave, PL Privilege Leave, URTI, CL Casual Leave, "
            "GND Ground Duty, SPL Special Leave.\n"
            "- A leave request specifies type and duration (NoOfLeaves); balance is checked "
            "against the leave-year allocation (LeavesAllotted).\n"
            "- Scheduler-driven SL/URTI balance adjustments are logged automatically.\n"
            "- On separation: Status = Released, Active = 'N', LWD recorded. This zeroes the "
            "crew member's Availability score regardless of prior leave history.\n"
            "- Base transfers are logged (Curr_Base/Pre_Base/Trans_Date) for leave-year "
            "continuity."
        ),
        "parameters": None,
        "linked_table": "table__CLMS__clms_raw_data",
    },
    {
        "id": "policy__passenger_nps_feedback_integration",
        "name": "Passenger NPS Feedback Integration Policy",
        "description": (
            "Governs how post-flight passenger NPS survey responses are attributed to "
            "operating crew and used in performance reviews."
        ),
        "content": (
            "## Passenger NPS Feedback Integration\n"
            "- Post-flight survey covers six journey stages: booking, pre-travel, check-in, "
            "boarding, on-board, arrival.\n"
            "- Up to 4 operating crew are attributed to each response via IGA Code.\n"
            "- Free-text feedback is retained verbatim and used as-is in reviews - never "
            "compressed into a qualitative label.\n"
            "- On-board sub-ratings (crew helpfulness, announcement clarity, cabin "
            "cleanliness) provide context but aren't separately weighted."
        ),
        "parameters": None,
        "linked_table": "table__NPS__indigonps_summary",
    },
    {
        "id": "policy__ijp_eligibility",
        "name": "IJP Eligibility Policy",
        "description": (
            "Governs Internal Job Posting (IJP) eligibility checks, preferred-location "
            "submission, and disqualification messaging."
        ),
        "content": (
            "## IJP Eligibility\n"
            "- HR evaluates Eligibility Check as per HR against LWP days, caution letters, "
            "and tenure (Months in IndiGo).\n"
            "- Eligible crew may submit up to 3 ranked preferred locations before IJP End "
            "Date.\n"
            "- Ineligible applicants receive a system-generated message citing the specific "
            "disqualifying criterion."
        ),
        "parameters": None,
        "linked_table": "table__HRData__indigo_hr_employee_data",
    },
    {
        "id": "policy__disciplinary_escalation",
        "name": "Disciplinary Escalation Policy",
        "description": (
            "Governs the escalation path from non-performance discussions through caution "
            "letters, coaching, and committee review."
        ),
        "content": (
            "## Disciplinary Escalation\n"
            "Non-Performance Discussion -> Caution Letter (-20pts in scoring, each) -> "
            "Curative Training/iCoach Session (+20pts in scoring, each) -> Review Committee "
            "escalation if unresolved after 2 cycles."
        ),
        "parameters": None,
        "linked_table": "table__IJP__employee_scores",
    },
    {
        "id": "policy__data_sources",
        "name": "SOP Data Sources Reference",
        "description": (
            "Maps each policy domain (employee master data, performance metrics, leave & "
            "attrition, passenger feedback) to its physical source table."
        ),
        "content": (
            "## Data Sources\n"
            "| Domain | Table |\n|---|---|\n"
            "| Employee master data | Indigo_HR_Raw_Data |\n"
            "| Performance metrics | IJP_Employee_scores |\n"
            "| Leave & attrition | CLMS_Raw_Data |\n"
            "| Passenger feedback | IndigoNPS_Summary |"
        ),
        "parameters": None,
        "linked_table": None,  # conceptually spans all four tables; skip a single link
    },
]

client = get_cosmos_client(graph_container="Unified_Knowledge_graph")


def run(query, ignore_conflict=True):
    return run_gremlin(client, query, ignore_conflict=ignore_conflict)


created, updated = 0, 0
for sec in SECTIONS:
    vid = sec["id"]

    prop_clauses = f".property('content', '{escape_gremlin(sec['content'])}')"
    if sec["parameters"] is not None:
        prop_clauses += f".property('parameters', '{escape_gremlin(json.dumps(sec['parameters']))}')"

    exists = run(f"g.V('{escape_gremlin(vid)}').count()")
    if exists and exists[0] > 0:
        run(f"g.V('{escape_gremlin(vid)}')" + prop_clauses)
        updated += 1
        print(f"Updated: {vid}")
    else:
        stmt = (
            f"g.addV('Policy')"
            f".property('id', '{escape_gremlin(vid)}')"
            f".property('database', 'Policy')"
            f".property('name', '{escape_gremlin(sec['name'])}')"
            f".property('displayName', '{escape_gremlin(sec['name'])}')"
            f".property('description', '{escape_gremlin(sec['description'])}')"
            f".property('nodeId', '{escape_gremlin(vid)}')"
            + prop_clauses
        )
        run(stmt)
        created += 1
        print(f"Created: {vid}")

    if sec["linked_table"]:
        already_linked = run(
            f"g.V('{escape_gremlin(vid)}').out('RELATES_TO')"
            f".hasId('{escape_gremlin(sec['linked_table'])}').count()"
        )
        if not already_linked or already_linked[0] == 0:
            run(
                f"g.V('{escape_gremlin(vid)}').addE('RELATES_TO')"
                f".to(g.V('{escape_gremlin(sec['linked_table'])}'))"
            )
            print(f"  linked -> {sec['linked_table']}")

close_cosmos_client(client)
print(f"DONE — {created} created, {updated} updated (additive only, no drops).")
