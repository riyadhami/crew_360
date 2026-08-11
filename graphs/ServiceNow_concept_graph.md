# Concept Graph — ServiceNow

*Generated 2026-08-11T03:46:57.918604+00:00*

| Metric | Count |
|---|---|
| Tables | 5 |
| Concepts | 9 |
| Edges | 26 |
| Data-verified joins | 5 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `M_SN_CATEGORY` | Category Summary | One category/sub-category bucket summary | yes | 1 | An aggregated summary of incident reports by category/sub-category and polarity, including the total number of reports in each bucket. It supports analyzing how often different types of crew-reported issues occur and how they trend by scoring polarity. |
| `M_SN_CREW` | Crew Summary | One crew identity summary across reports | yes | 3 | An aggregated performance summary per crew identity (IGA), counting how many reports they appear in and how many of those explicitly mark them as involved. It also tracks first/last appearance and cabin-crew classification to support longitudinal crew performance analysis. |
| `SN_FLIGHT_REPORT` | Flight Report | One incident report per flight | yes | 6 | A single crew portal incident report for one flight, capturing the flight context, crew named by seat-position involvement, category/sub-category, and the reported situation/action/outcome narrative. It also records resolution timing and outcome fields used to measure how quickly and effectively incidents were resolved from first-hand crew feedback. |
| `SN_REPORT_CREW` | Report Crew | One crew member per flight report | yes | 3 | A row links a specific flight report to one crew member identity (IGA) and seat position, with a boolean flag indicating whether that seat position was explicitly named under 'Crew Involved'. This supports attributing involvement and analyzing service outcomes by role and cabin crew status. |
| `SN_SERVICE_CHECK` | Service Check | One checklist answer per flight report | yes | 1 | A single checklist question/answer item associated with a flight report, including the check code, the lead’s check value, and whether the check was passed (or failed) based on the scoring rules. It is used to quantify question scoring and identify where service steps were not completed as expected. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `SN_REPORT_CREW`, `M_SN_CREW` | Crew member identity used across reports and crew-level summaries. Includes the IGA identifier and cabin-crew classification to support grouping/segmentation of crew performance by crew type. |
| **Crew Involvement (Seat Position)** | `SN_REPORT_CREW` | Whether a crew member/seat position was explicitly named as involved in the incident narrative under 'Crew Involved'. Supports filtering checklist performance and outcomes by involvement status at the seat-role level. |
| **Flight Report Context** | `SN_FLIGHT_REPORT` | Flight-level context for each crew portal incident report, including the report identity and flight identifiers used to group narratives, resolution timing, and associated checklist items. |
| **Assessment Lifecycle & Workflow** | `SN_FLIGHT_REPORT` | Lifecycle and workflow/audit fields that describe when the incident was raised and how it progressed to resolution. Used to measure resolution speed and to filter by assessment stage. |
| **Incident Taxonomy (Category/Sub-Category)** | `SN_FLIGHT_REPORT`, `M_SN_CATEGORY` | Crew-reported classification of the incident using category and sub-category fields, enabling analysis of how different issue types occur and how they perform. |
| **Service Checklist Scoring (Item-Level)** | `SN_SERVICE_CHECK` | Checklist question/answer items associated with a flight report, including the check code, the lead’s check value, and whether the item passed/failed based on scoring rules. This is the item-level scoring entity. |
| **Checklist Outcomes by Seat Position & Involvement** | `SN_SERVICE_CHECK`, `SN_REPORT_CREW` | Links checklist item outcomes to crew seat positions (and optionally involvement status) so queries can answer: which checklist items failed/passed for which seat role/crew identity and whether that seat was explicitly marked as involved. |
| **Crew Performance Summary Metrics** | `M_SN_CREW` | Aggregated metrics per crew identity capturing how many reports they appear in, how many explicitly mark them as involved, and first/last appearance. Used for longitudinal crew performance analysis. |
| **Category Performance Summary Metrics** | `M_SN_CATEGORY` | Aggregated counts of incident reports by category/sub-category and polarity to support trend and distribution analysis across the incident taxonomy. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `SN_REPORT_CREW` | `M_SN_CREW` | `IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `SN_REPORT_CREW` | `SN_FLIGHT_REPORT` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.95 | 100% | inferred | many-to-one |
| `SN_SERVICE_CHECK` | `SN_FLIGHT_REPORT` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.95 | 100% | inferred | many-to-one |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `SUB_CATEGORY_CODE` -> `SUB_CATEGORY_CODE` | 0.95 | 100% | inferred | many-to-one |
| `M_SN_CATEGORY` | `SN_FLIGHT_REPORT` | `SUB_CATEGORY_CODE` -> `SUB_CATEGORY_CODE` | 0.80 | 98% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `SN_REPORT_CREW` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.45 | 100% | inferred | many-to-many |
| `SN_SERVICE_CHECK` | `SN_REPORT_CREW` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.45 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `CATEGORY_CODE` -> `CATEGORY_CODE` | 0.45 | 100% | inferred | many-to-many |
| `M_SN_CREW` | `SN_REPORT_CREW` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `SN_SERVICE_CHECK` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.35 | 100% | inferred | many-to-many |
| `SN_REPORT_CREW` | `SN_SERVICE_CHECK` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.35 | 100% | inferred | many-to-many |
| `M_SN_CATEGORY` | `SN_FLIGHT_REPORT` | `CATEGORY_CODE` -> `CATEGORY_CODE` | 0.35 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `CATEGORY` -> `CATEGORY` | 0.35 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `POLARITY` -> `POLARITY` | 0.35 | 100% | inferred | many-to-many |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1. Table coverage gap: SN_SERVICE_CHECK is covered by concepts `service_checklist` and `question_scoring_and_pass_fail`, but the key_columns for `question_scoring_and_pass_fail` re
- concept review round 2: 1) Duplicate/near-duplicate concepts: `crew_involvement_by_seat_position` and `crew_performance_summary_metrics` overlap in the notion of “involvement,” but they are not duplicates

## Rejected join candidates

- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY ~ SUB_CATEGORY_CODE, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY_CODE ~ SUB_CATEGORY_CODE, 84%); REFUTED by data — only 20.5% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ SUB_CATEGORY_CODE, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY ~ CATEGORY_CODE, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ CATEGORY_CODE, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY_CODE ~ CATEGORY_CODE, 84%); REFUTED by data — only 0.3% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY_CODE ~ CATEGORY, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ CATEGORY, 84%); REFUTED by data — only 14.7% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY_CODE ~ CATEGORY, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY ~ SUB_CATEGORY, 84%); REFUTED by data — only 14.7% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY_CODE ~ SUB_CATEGORY, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ SUB_CATEGORY, 100%); REFUTED by data — only 79.9% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY_CODE ~ SUB_CATEGORY, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- M_SN_CREW -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (REPORT_COUNT ~ REPORT_COUNT, 100%); REFUTED by data — only 45.9% of child rows resolve to a parent
