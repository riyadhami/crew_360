# Concept Graph — CrewPortal

*Generated 2026-08-11T03:45:59.412979+00:00*

| Metric | Count |
|---|---|
| Tables | 16 |
| Concepts | 13 |
| Edges | 51 |
| Data-verified joins | 14 |
| Excluded by relevance gate | 4 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `M_AIRCRAFT` | Aircraft | One aircraft reference record | yes | 1 | Defines an aircraft asset/type reference used by the portal, including descriptive attributes and whether the record is currently active. This supports consistent flight-context tagging when attributing issues and compliance to the aircraft involved. |
| `M_AIRPORT_NAMES` | Airport | One airport reference record | yes | 4 | Provides reference data for airports, including airport code/name and key operational flags such as active status and special station characteristics. This enables consistent location context for crew check-in, flight reporting, and issue reporting. |
| `M_CREW_DETAILS` | Crew Details | One crew member attribute record | yes | 0 | Stores crew member identity and base attributes (e.g., crew category/type and home base location) for use across the portal. This supports per-position crew attribution when linking flight issue reports to the correct crew member. |
| `M_CREW_POSITION` | Crew Position | One crew position catalog record | yes | 2 | Defines the catalog of crew positions (with sequence ordering and active status) used to structure per-position reporting. This standardizes how issue reports and crew-related workflows map to roles like captain/first officer/attendant positions. |
| `M_FR_PROCESS_COMPLIANCE` | Process Compliance | One compliance requirement definition | yes | 3 | Lists the set of flight process compliance items that crew must complete or acknowledge, including whether remarks are required and how the item should be displayed. This drives the compliance checklist used during flight reporting and crew performance tracking. |
| `M_FR_PROCESS_COMPLIANCE_VALUE` | Compliance Value | One compliance option record | yes | 1 | Defines the allowed values/options for each process compliance item, including display text and descriptive guidance. This standardizes the answers recorded for compliance checks in flight reports. |
| `M_ISSUE_CATEGORY` | Issue Category | one issue category definition | yes | 1 | Defines the master list of issue categories that crew can select when reporting a flight issue. Each row is a category definition used to classify and analyze crew-reported problems. |
| `M_STATION` | Station | one station definition | yes | 1 | Maintains the master list of operational stations (airport/locations) used to tag where a crew issue occurred. Each row is a station definition that supports station-level reporting and compliance analysis. |
| `T_ALERT_ACKNOWLEDGED_DATA` | Alert Acknowledgement | one crew alert acknowledgement event | yes | 1 | Stores crew acknowledgements of specific alert codes/values, including when the acknowledgement was made and how many times it was acknowledged. Each row represents one crew’s acknowledgement event for an alert payload. |
| `T_CHECKIN` | Crew Check-in | one crew check-in attempt | yes | 2 | Records whether a crew check-in process ran successfully or failed for a given crew check-in model and IGA code. Each row is one check-in attempt outcome used to monitor crew readiness and process compliance. |
| `T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT` | PAL Acknowledgement | one crew document acknowledgement | yes | 1 | Captures crew acknowledgements of PAL (notification/document) items, including the document and department context, message content, and current status. Each row is one crew member’s acknowledgement of a specific document/notification item. |
| `T_FLIGHT_ISSUE_DETAILS` | Flight Issue Detail | one flight issue detail entry | yes | 4 | Holds the detailed records of flight issue reports, including the selected issue category/subcategory, the crew involved, station context, and resolution metadata such as action and outcome. Each row is one attributed issue detail tied to a specific report and supports downstream export and follow-up. |
| `T_FLIGHT_ISSUE_GENERAL_INFO` | Flight Issue | One flight issue report header | yes | 1 | A row represents one flight issue report’s general header information, including the flight context (date, times, flight number, route, registration) and report metadata. It anchors the issue record that is later attributed to specific crew positions and evaluated for process compliance. |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | Issue Positions | One issue report’s crew position attribution | yes | 0 | A row represents the crew-position attribution for a specific flight issue report, capturing which crew members (by IGA identifiers) are associated with each role on that report. It links the issue to the relevant captain/FO/ACM and other positions for performance and accountability views. |
| `T_FLIGHT_PROCESS_COMPLIANCE` | Process Compliance | One compliance check for an issue report | yes | 2 | A row represents one process compliance item recorded for a flight issue report, including a numeric compliance value and optional remarks. It supports assessing whether required flight processes were followed as part of the issue evaluation. |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | Report Positions | One crew position assignment per flight report | yes | 1 | A row represents the assignment of a crew position to a flight report (by report id and crew position id), along with whether that assignment is active. It provides the roster of positions used to structure crew-related reporting and issue attribution. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Flight Report Header Context** | `T_FLIGHT_ISSUE_GENERAL_INFO` | Anchors the flight-report/issue header context used to identify the flight being reported (date, times, flight number, route, registration) and the report metadata that frames downstream crew-position attribution and analytics. |
| **Flight Issue Location Context** | `T_FLIGHT_ISSUE_GENERAL_INFO`, `M_STATION`, `M_AIRPORT_NAMES`, `T_CHECKIN` | Models where the issue occurred using station/airport context, supporting location-based reporting and crew check-in linkage. |
| **Aircraft Operational Context** | `M_AIRCRAFT`, `T_FLIGHT_ISSUE_GENERAL_INFO` | Provides aircraft reference context (asset/type and active status) to consistently tag flight issues and compliance by aircraft. |
| **Issue Taxonomy & Classification** | `T_FLIGHT_ISSUE_DETAILS`, `M_ISSUE_CATEGORY` | Captures how the flight issue is categorized (category/subcategory) to support taxonomy-based analytics and workflow routing. |
| **Crew Identity (Master Attributes)** | `M_CREW_DETAILS` | Represents crew member identity and base attributes from the crew master, used across crew attribution, check-in, and acknowledgements. This concept focuses on master attributes only (not role assignment). |
| **Crew Position Catalog & Role Structure** | `M_CREW_POSITION` | Defines the catalog of crew positions (sequence ordering and active status) used to structure per-position reporting and attribution roles. |
| **Issue Position Attribution (Crew on the Issue)** | `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS`, `T_FLIGHT_REPORT_WORK_POSITION_DETAILS`, `M_CREW_POSITION`, `M_CREW_DETAILS` | Links a flight issue report to crew members by role/position using IGA identifiers and crew-position attribution fields. This is the role assignment layer (distinct from crew master attributes). |
| **Process Compliance Checklist & Values** | `M_FR_PROCESS_COMPLIANCE`, `M_FR_PROCESS_COMPLIANCE_VALUE` | Defines the compliance items that crew must complete/acknowledge and the allowed answer values/options used when recording compliance results. |
| **Issue-Level Process Compliance Results** | `T_FLIGHT_PROCESS_COMPLIANCE`, `T_FLIGHT_ISSUE_GENERAL_INFO` | Stores the recorded compliance outcomes (numeric value and optional remarks) for a flight issue report. This concept is issue-scoped (not yet per-position). |
| **Crew-Position Compliance Inheritance & Scoring** | `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS`, `T_FLIGHT_PROCESS_COMPLIANCE`, `T_FLIGHT_ISSUE_DETAILS` | Connects issue-level compliance results to crew-position performance views by mapping compliance outcomes to the attributed crew positions on the same issue. If compliance is issue-scoped, this concept represents the inheritance rule used for per-position performance reporting. |
| **Crew Readiness & Check-in Outcomes** | `T_CHECKIN`, `M_CREW_DETAILS`, `M_AIRPORT_NAMES`, `M_STATION` | Captures whether crew check-in processes succeeded or failed for a given check-in model and IGA code, enabling readiness monitoring alongside issue reporting. |
| **Alert & Document Acknowledgements** | `T_ALERT_ACKNOWLEDGED_DATA`, `T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT` | Stores crew acknowledgements of alert codes/values and PAL/document notifications, including timestamps/status and message/document context. |
| **Issue Resolution & Export Metadata** | `T_FLIGHT_ISSUE_DETAILS` | Captures resolution and export-related metadata for the flight issue detail record (used for follow-up and analytics). This is intentionally kept as a cross-table analytics hook because resolution_outcome can be used alongside compliance/scoring views. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `T_FLIGHT_ISSUE_GENERAL_INFO` | `M_AIRCRAFT` | `FLIGHT_TYPE_ID` -> `AIRCRAFT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_PROCESS_COMPLIANCE` | `T_FLIGHT_ISSUE_GENERAL_INFO` | `REPORT_ID` -> `REPORT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_PROCESS_COMPLIANCE` | `M_FR_PROCESS_COMPLIANCE` | `PROCESS_COMP_ID` -> `COMPLIANCE_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_PROCESS_COMPLIANCE` | `M_FR_PROCESS_COMPLIANCE_VALUE` | `PROCESS_COMP_VALUE` -> `COMPLIANCE_VALUE_ID` | 1.00 | 84% | declared | many-to-one |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | `T_FLIGHT_ISSUE_GENERAL_INFO` | `REPORT_ID` -> `REPORT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | `M_CREW_POSITION` | `CREW_POSITION_ID` -> `CREW_POSITION_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `CREW_IGA` -> `IGA` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_ISSUE_DETAILS` | `T_FLIGHT_ISSUE_GENERAL_INFO` | `REPORT_ID` -> `REPORT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_ISSUE_DETAILS` | `M_ISSUE_CATEGORY` | `ISSUE_CATEGORY_GUID` -> `CATEGORY_UNIQUE_ID` | 1.00 | — | declared | many-to-one |
| `T_ALERT_ACKNOWLEDGED_DATA` | `M_CREW_DETAILS` | `CREW_IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `T_CHECKIN` | `M_CREW_DETAILS` | `IGA_CODE` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT` | `M_CREW_DETAILS` | `CREW_IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `T_FLIGHT_ISSUE_GENERAL_INFO` | `M_CREW_DETAILS` | `IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `L1_IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `T_FLIGHT_ISSUE_DETAILS` | `M_STATION` | `STATION` -> `STATION_CODE` | 0.95 | 100% | inferred | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `L2_IGA` -> `IGA` | 0.90 | — | asserted | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `R1_IGA` -> `IGA` | 0.90 | — | asserted | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `R2_IGA` -> `IGA` | 0.90 | — | asserted | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `CAPT_IGA` -> `IGA` | 0.90 | — | asserted | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `FO_IGA` -> `IGA` | 0.90 | — | asserted | many-to-one |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `ACM_IGA` -> `IGA` | 0.90 | — | asserted | many-to-one |
| `T_FLIGHT_ISSUE_DETAILS` | `M_ISSUE_CATEGORY` | `ISSUE_CATEGORY_GUID` -> `CATEGORY_NAME` | 0.35 | — | inferred | many-to-one |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1) Table coverage gap: M_AIRPORT_NAMES is covered by only the concept 'flight_context_location' (good), but M_AIRCRAFT is covered by 'flight_context_aircraft' (good). However, M_FR
- concept review round 2: 1) Duplicate/near-duplicate concepts: `crew_identity` and `flight_report_position_roster` both model crew identity via `crew_iga_code` and link to crew master, but they are not dup

## Excluded by relevance gate

| Table | Reason |
|---|---|
| `DOCUMENT` | Unclear content; likely generic document storage/checklists not directly tied to performance scoring. |
| `DOCUMENT_DETAILS` | Document metadata/details without clear linkage to crew performance outcomes. |
| `T_FLIGHT_REPORTS_FILLED_THROUGH_API` | Likely integration/logging of report submission via API rather than performance outcomes. |
| `T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST` | Operational request tracking for missing reports; not a direct performance/complaint signal. |

## Rejected join candidates

- shared by 6 tables with no name-identifiable owner; too ambiguous to propose
- M_CREW_DETAILS -> T_FLIGHT_ISSUE_GENERAL_INFO: registry-declared identity key IGA for source CrewPortal; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 68.1% of child rows resolve to a parent
- M_CREW_POSITION -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: shared key-like column CREW_POSITION_ID; name evidence favoured M_CREW_POSITION as the owner; this direction is proposed so data verification can settle it; TYPE MISMATCH (NUMBER vs TEXT); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT -> DOCUMENT: reference lookup into DOCUMENT (3 rows); column name carries reference table stem 'DOCUMENT'; REFUTED by data — only 0.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> M_AIRPORT_NAMES: reference lookup into M_AIRPORT_NAMES (8 rows); column names align (REPORT_ID ~ AIRPORT_ID, 77%); REFUTED by data — only 0.8% of child rows resolve to a parent
- T_FLIGHT_ISSUE_GENERAL_INFO -> M_AIRPORT_NAMES: reference lookup into M_AIRPORT_NAMES (8 rows); column names align (REPORT_ID ~ AIRPORT_ID, 77%); REFUTED by data — only 0.8% of child rows resolve to a parent
- T_FLIGHT_ISSUE_WORK_POSITION_DETAILS -> M_AIRPORT_NAMES: reference lookup into M_AIRPORT_NAMES (8 rows); column names align (REPORT_ID ~ AIRPORT_ID, 77%); REFUTED by data — only 0.8% of child rows resolve to a parent
- T_FLIGHT_PROCESS_COMPLIANCE -> M_AIRPORT_NAMES: reference lookup into M_AIRPORT_NAMES (8 rows); column names align (REPORT_ID ~ AIRPORT_ID, 77%); REFUTED by data — only 0.8% of child rows resolve to a parent
- T_FLIGHT_REPORTS_FILLED_THROUGH_API -> M_AIRPORT_NAMES: reference lookup into M_AIRPORT_NAMES (8 rows); column names align (REPORT_ID ~ AIRPORT_ID, 77%); REFUTED by data — only 0.8% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> M_FR_PROCESS_COMPLIANCE: reference lookup into M_FR_PROCESS_COMPLIANCE (18 rows); column names align (IS_ACTIVE ~ IS_ACTIVE, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_FLIGHT_ISSUE_GENERAL_INFO -> M_FR_PROCESS_COMPLIANCE: reference lookup into M_FR_PROCESS_COMPLIANCE (18 rows); column names align (IS_ACTIVE ~ IS_ACTIVE, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_FLIGHT_PROCESS_COMPLIANCE -> M_FR_PROCESS_COMPLIANCE: reference lookup into M_FR_PROCESS_COMPLIANCE (18 rows); column names align (IS_ACTIVE ~ IS_ACTIVE, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_FLIGHT_ISSUE_DETAILS -> M_ISSUE_CATEGORY: reference lookup into M_ISSUE_CATEGORY (31 rows); column names align (IS_ACTIVE ~ IS_ACTIVE, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_FLIGHT_ISSUE_GENERAL_INFO -> M_ISSUE_CATEGORY: reference lookup into M_ISSUE_CATEGORY (31 rows); column names align (IS_ACTIVE ~ IS_ACTIVE, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_FLIGHT_PROCESS_COMPLIANCE -> M_ISSUE_CATEGORY: reference lookup into M_ISSUE_CATEGORY (31 rows); column names align (IS_ACTIVE ~ IS_ACTIVE, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_FLIGHT_ISSUE_DETAILS -> M_STATION: reference lookup into M_STATION (8 rows); column name carries reference table stem 'STATION'; REFUTED by data — only 0.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> M_STATION: reference lookup into M_STATION (8 rows); column names align (ACTION ~ STATION_CODE, 77%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> M_STATION: reference lookup into M_STATION (8 rows); column names align (STATION ~ STATION_NAME, 78%); column name carries reference table stem 'STATION'; REFUTED by data — only 0.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 50.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_GENERAL_INFO -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 50.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_WORK_POSITION_DETAILS -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 50.0% of child rows resolve to a parent
- T_FLIGHT_PROCESS_COMPLIANCE -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 50.0% of child rows resolve to a parent
- T_FLIGHT_REPORTS_FILLED_THROUGH_API -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 50.0% of child rows resolve to a parent
- T_ALERT_ACKNOWLEDGED_DATA -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (CREW_IGA ~ CREW_IGA, 100%); REFUTED by data — only 33.4% of child rows resolve to a parent
- T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (488 rows); column names align (CREW_IGA ~ CREW_IGA, 100%); REFUTED by data — only 32.9% of child rows resolve to a parent
- T_ALERT_ACKNOWLEDGED_DATA -> T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST: reference lookup into T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST (1 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT -> T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST: reference lookup into T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST (1 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST: reference lookup into T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST (1 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.1% of child rows resolve to a parent
