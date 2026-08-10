# Concept Graph — CrewPortal

*Generated 2026-08-09T17:39:26.870261+00:00*

| Metric | Count |
|---|---|
| Tables | 16 |
| Concepts | 15 |
| Edges | 68 |
| Data-verified joins | 13 |
| Excluded by relevance gate | 4 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `M_AIRCRAFT` | Aircraft | One aircraft/type record | yes | 1 | Defines aircraft assets and their descriptive attributes (type, description, sub-fleet) used to contextualize crew performance and flight issue reporting. Each row represents an aircraft classification record, including whether it is active and how it should be displayed. |
| `M_AIRPORT_NAMES` | Airport | One airport reference record | yes | 4 | Provides airport reference data such as code, name, and location, along with operational flags used when interpreting flight-related crew issues. Each row represents one airport naming/location record with status and capability indicators. |
| `M_CREW_DETAILS` | Crew Details | One crew member details record | yes | 0 | Stores crew master attributes used to identify and categorize crew members in self-service and flight reporting. Each row represents one crew member’s details (category/type/base and location attributes). |
| `M_CREW_POSITION` | Crew Position | One crew position definition | yes | 2 | Defines the set of crew positions (e.g., roles) that can be attributed to individuals in flight issue reports. Each row represents one position definition, including its sequence and whether it is currently active. |
| `M_FR_PROCESS_COMPLIANCE` | Process Compliance | One compliance requirement definition | yes | 3 | Catalogs flight-reporting process compliance items that crew must follow, including display order and whether remarks are required. Each row represents one compliance requirement definition used to structure compliance checks in reports. |
| `M_FR_PROCESS_COMPLIANCE_VALUE` | Compliance Value | One compliance option record | yes | 1 | Provides the allowed values/options for each process compliance item, including display text and optional descriptive guidance. Each row represents one selectable compliance value used when recording crew compliance outcomes. |
| `M_ISSUE_CATEGORY` | Issue Category | One issue category definition | yes | 1 | Defines the master list of issue categories that can be selected when recording a flight issue report. Each row represents one category and whether it is currently active for use in crew performance reporting. |
| `M_STATION` | Station | One station definition | yes | 1 | Maintains the master data for operational stations used to contextualize crew events and flight issues. Each row represents one station (code/name) and whether it is active for reporting. |
| `T_ALERT_ACKNOWLEDGED_DATA` | Alert Acknowledgement | One crew alert acknowledgement record | yes | 1 | Stores crew acknowledgements of specific alert items, including the alert code/value and how many times it has been acknowledged. Each row represents one crew member’s acknowledgement record for a particular alert item at a point in time. |
| `T_CHECKIN` | Check-in | One crew check-in attempt | yes | 2 | Captures the outcome of a crew check-in attempt for a given crew check-in model and IGA code. Each row represents one check-in run status (run/fail) and when it was updated, supporting compliance and readiness tracking. |
| `T_FLIGHT_ISSUE_DETAILS` | Flight Issue Details | One flight issue detail within a report | yes | 4 | Holds the detailed, per-issue record attached to a flight issue report, including category/subcategory, involved crew, station context, and resolution metadata. Each row represents one specific issue detail instance within a report, including actions/outcomes and export/send status. |
| `T_FLIGHT_ISSUE_GENERAL_INFO` | Flight Issue Info | One flight issue report header | yes | 1 | Stores the general header information for a flight issue report, such as flight identifiers, route (DEP/ARR), flight date/time, and flight lead base. Each row represents one flight issue report’s core context and its active status for downstream crew performance analysis. |
| `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | Issue Position Details | One issue report position detail snapshot | yes | 0 | Stores per-flight-issue attribution details for each crew position involved in an issue report, capturing the IGA values for roles such as L1/L2/R1/R2/Capt/FO/ACM. Each row represents one crew-position-specific snapshot of the issue’s work context for a given report. |
| `T_FLIGHT_PROCESS_COMPLIANCE` | Process Compliance | One process compliance assessment item | yes | 2 | Records compliance outcomes for specific flight processes tied to an issue report, including a numeric compliance value and remarks. Each row represents one process compliance assessment item (with status/active flag) for a given report. |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | Report Work Positions | One crew-position assignment per report | yes | 1 | Defines which crew positions are associated with a flight report and whether each position is active for that report. Each row represents one crew position assignment (and its active indicator) for a given report. |
| `T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST` | Missing Report Requests | One missing-report generation request | yes | 2 | Tracks requests to generate a flight report when it is missing, including the input payload, request timing, and whether the report was generated and/or emailed. Each row represents one generation request event in the crew self-service workflow. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `M_CREW_DETAILS`, `T_CHECKIN`, `T_ALERT_ACKNOWLEDGED_DATA`, `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS`, `T_FLIGHT_REPORT_WORK_POSITION_DETAILS`, `M_AIRPORT_NAMES` | Master identity attributes used to identify crew members across self-service and flight reporting, including crew category/type/base and location attributes. This concept is the anchor for crew attribution in reports and for crew-level events like check-in and alert acknowledgements. |
| **Crew Position Catalog** | `M_CREW_POSITION`, `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS`, `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | Catalog of crew positions/roles that can be attributed in flight issue reports, including ordering and active status. Used to interpret role-specific attribution (e.g., L1/L2/R1/R2/Capt/FO/ACM). |
| **Flight Issue Report Header** | `T_FLIGHT_ISSUE_GENERAL_INFO` | Core header context for a flight issue report: flight identifiers, route direction (DEP/ARR), flight date/time, and lead base. This concept provides the flight context used by downstream issue details, per-position snapshots, and compliance outcomes. |
| **Report-Level Crew Position Assignment** | `T_FLIGHT_REPORT_WORK_POSITION_DETAILS`, `T_FLIGHT_ISSUE_GENERAL_INFO`, `M_CREW_POSITION`, `M_CREW_DETAILS` | Defines which crew member is assigned to which crew position for a given flight issue report, including whether that assignment is active for the report. This is the report’s role-to-crew mapping (not the IGA snapshot). |
| **Issue Position IGA Snapshot (Per-Position Work Context)** | `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS`, `T_FLIGHT_ISSUE_GENERAL_INFO`, `M_CREW_POSITION`, `M_CREW_DETAILS` | Per-flight-issue, per-position snapshot of work context captured as IGA values for each role position. This concept is explicitly the IGA/context snapshot tied to (report_id, crew_id, position_code), distinct from the report-level crew position assignment mapping. |
| **Issue Category Taxonomy** | `M_ISSUE_CATEGORY`, `T_FLIGHT_ISSUE_DETAILS` | Master list of issue categories selectable when recording a flight issue report, including active status. Used to classify each issue detail instance. |
| **Flight Issue Detail & Resolution Lifecycle** | `T_FLIGHT_ISSUE_DETAILS`, `T_FLIGHT_ISSUE_GENERAL_INFO`, `M_STATION`, `M_CREW_DETAILS`, `M_ISSUE_CATEGORY` | Detailed per-issue records attached to a flight issue report, including category/subcategory, involved crew attribution, station context, and resolution metadata such as actions/outcomes and export/send status. |
| **Location Reference (Station)** | `M_STATION`, `T_FLIGHT_ISSUE_DETAILS` | Station reference data used to contextualize crew events and flight issues, including station code/name and operational flags. Station is used by issue details and potentially other flight-context joins. |
| **Aircraft Reference (Type/Fleet Classification)** | `M_AIRCRAFT`, `T_FLIGHT_ISSUE_GENERAL_INFO`, `T_FLIGHT_ISSUE_DETAILS` | Aircraft asset/type reference data used to contextualize crew performance and flight issue reporting, including type/sub-fleet and active/display flags. |
| **Process Compliance Catalog (Requirements & Allowed Values)** | `M_FR_PROCESS_COMPLIANCE`, `M_FR_PROCESS_COMPLIANCE_VALUE`, `T_FLIGHT_PROCESS_COMPLIANCE` | Defines the flight-reporting process compliance items crew must follow, including display order and whether remarks are required, plus the allowed selectable outcome values for each compliance item. |
| **Crew Process Compliance Outcomes (Per Report)** | `T_FLIGHT_PROCESS_COMPLIANCE`, `T_FLIGHT_ISSUE_GENERAL_INFO`, `M_FR_PROCESS_COMPLIANCE`, `M_FR_PROCESS_COMPLIANCE_VALUE` | Recorded compliance outcomes for specific flight processes tied to a flight issue report, including numeric compliance value and remarks. This is the event-level compliance assessment layer used for crew performance rollups. |
| **Crew Readiness Check-in Events** | `T_CHECKIN`, `M_CREW_DETAILS` | Captures the outcome of a crew check-in attempt for a given crew check-in model and IGA code, including run/fail and update timing. Used as a readiness/compliance input for crew performance rollups. |
| **Crew Alert Acknowledgement Events** | `T_ALERT_ACKNOWLEDGED_DATA`, `M_CREW_DETAILS` | Crew acknowledgements of specific alert items, including alert code/value and acknowledgement count. Note: this concept is crew-level; if report_id linkage exists in the data model, it should be added to enable flight/report-scoped timeliness metrics. |
| **Crew Performance Rollup (Readiness & Compliance)** | `T_CHECKIN`, `T_FLIGHT_PROCESS_COMPLIANCE`, `T_FLIGHT_ISSUE_GENERAL_INFO`, `T_FLIGHT_REPORT_WORK_POSITION_DETAILS`, `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` | Derived/aggregated performance layer combining crew readiness signals (check-in outcomes) and process compliance outcomes at a consistent analysis grain (typically crew_id + flight/report context). This concept supports questions like 'how did crew perform' by summarizing readiness status and compliance results across the relevant report window. |
| **Missing Report Generation Requests** | `T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST` | Tracks requests to generate a flight issue report when it is missing, including the input payload, request timing, and whether the report was generated and/or emailed. Supports operational workflow auditing for the self-service portal. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `T_FLIGHT_ISSUE_GENERAL_INFO` | `M_AIRCRAFT` | `FLIGHT_TYPE_ID` -> `AIRCRAFT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_PROCESS_COMPLIANCE` | `T_FLIGHT_ISSUE_GENERAL_INFO` | `REPORT_ID` -> `REPORT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_PROCESS_COMPLIANCE` | `M_FR_PROCESS_COMPLIANCE` | `PROCESS_COMP_ID` -> `COMPLIANCE_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_PROCESS_COMPLIANCE` | `M_FR_PROCESS_COMPLIANCE_VALUE` | `PROCESS_COMP_VALUE` -> `COMPLIANCE_VALUE_ID` | 1.00 | 83% | declared | many-to-one |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | `T_FLIGHT_ISSUE_GENERAL_INFO` | `REPORT_ID` -> `REPORT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | `M_CREW_POSITION` | `CREW_POSITION_ID` -> `CREW_POSITION_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_REPORT_WORK_POSITION_DETAILS` | `M_CREW_DETAILS` | `CREW_IGA` -> `IGA` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_ISSUE_DETAILS` | `T_FLIGHT_ISSUE_GENERAL_INFO` | `REPORT_ID` -> `REPORT_ID` | 1.00 | 100% | declared | many-to-one |
| `T_FLIGHT_ISSUE_DETAILS` | `M_ISSUE_CATEGORY` | `ISSUE_CATEGORY_GUID` -> `CATEGORY_UNIQUE_ID` | 1.00 | — | declared | many-to-one |
| `T_ALERT_ACKNOWLEDGED_DATA` | `M_CREW_DETAILS` | `CREW_IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `T_CHECKIN` | `M_CREW_DETAILS` | `IGA_CODE` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
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

- concept review round 1: 1) Table coverage gap: M_AIRPORT_NAMES is not covered by any concept. Add a concept that uses M_AIRPORT_NAMES (e.g., airport reference/lookup for codes to names) or include it in a
- concept review round 2: 1) Duplicate/near-duplicate concepts: `flight_issue_position_work_context` and `flight_report_work_position_assignments` overlap in purpose (both model per-position role/crew conte

## Excluded by relevance gate

| Table | Reason |
|---|---|
| `DOCUMENT` | Unclear content; likely generic document storage not directly tied to performance scoring. |
| `DOCUMENT_DETAILS` | Document metadata/details; without clear linkage to crew performance signals. |
| `T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT` | Notification acknowledgement without clear performance/assessment linkage; likely admin/communication only. |
| `T_FLIGHT_REPORTS_FILLED_THROUGH_API` | Integration/logging of API-filled reports; not a performance signal by itself. |

## Rejected join candidates

- shared by 6 tables with no name-identifiable owner; too ambiguous to propose
- M_CREW_DETAILS -> T_FLIGHT_ISSUE_GENERAL_INFO: registry-declared identity key IGA for source CrewPortal; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 69.4% of child rows resolve to a parent
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
- T_FLIGHT_ISSUE_DETAILS -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 49.9% of child rows resolve to a parent
- T_FLIGHT_ISSUE_GENERAL_INFO -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 49.9% of child rows resolve to a parent
- T_FLIGHT_ISSUE_WORK_POSITION_DETAILS -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 49.9% of child rows resolve to a parent
- T_FLIGHT_PROCESS_COMPLIANCE -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 49.9% of child rows resolve to a parent
- T_FLIGHT_REPORTS_FILLED_THROUGH_API -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (REPORT_ID ~ REPORT_ID, 100%); REFUTED by data — only 49.9% of child rows resolve to a parent
- T_ALERT_ACKNOWLEDGED_DATA -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (CREW_IGA ~ CREW_IGA, 100%); REFUTED by data — only 35.9% of child rows resolve to a parent
- T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT -> T_FLIGHT_REPORT_WORK_POSITION_DETAILS: reference lookup into T_FLIGHT_REPORT_WORK_POSITION_DETAILS (497 rows); column names align (CREW_IGA ~ CREW_IGA, 100%); REFUTED by data — only 35.3% of child rows resolve to a parent
- T_ALERT_ACKNOWLEDGED_DATA -> T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST: reference lookup into T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST (1 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT -> T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST: reference lookup into T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST (1 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_FLIGHT_ISSUE_DETAILS -> T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST: reference lookup into T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST (1 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.1% of child rows resolve to a parent

## Warnings

- M_AIRPORT_NAMES attached to concept 'Crew Identity' from its enrichment hint (match 67%)
