# Concept Graph — ServiceNow

*Generated 2026-08-09T17:40:34.297804+00:00*

| Metric | Count |
|---|---|
| Tables | 5 |
| Concepts | 8 |
| Edges | 25 |
| Data-verified joins | 5 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `M_SN_CATEGORY` | Category Summary | One category/sub-category summary | yes | 1 | Represents aggregated counts for each category/sub-category (and polarity) across flight reports, including how many reports fall into each classification. This supports analysis of incident themes and how they distribute across performance polarity. |
| `M_SN_CREW` | Crew Metrics | One crew identity summary | yes | 3 | Represents aggregated performance metrics for a crew identity (IGA), summarizing how many reports they are associated with and how many of those mark them as involved. This table supports trend and benchmarking of crew involvement over time, including cabin-crew segmentation. |
| `SN_FLIGHT_REPORT` | Flight Report | One incident report per flight | yes | 6 | Represents one Crew Portal incident report raised for a specific flight, including flight context, category/sub-category, resolution status, and timing metrics (lag and resolution hours). This is the primary record of first-hand crew feedback and the customer-facing service outcome for that incident. |
| `SN_REPORT_CREW` | Crew Involvement | One crew member row per report | yes | 3 | Represents one crew member (by seat position/IGA) listed on a specific flight report, including whether they are marked as involved in the incident. This enables attribution of crew performance signals to the correct seat position while distinguishing involved vs not implicated crew. |
| `SN_SERVICE_CHECK` | Service Check | One checklist question response per report | yes | 1 | Represents one checklist question/answer pair for a specific flight report, including whether the lead answered and whether the check is considered pass/fail under the scoring rules. This captures the lead’s service checklist responses that drive crew performance scoring. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Flight Report Core Context** | `SN_FLIGHT_REPORT` | Core identifiers and flight context for each Crew Portal incident report, including the flight being reported and the incident lifecycle timing fields used for lag and resolution analysis. |
| **Incident Taxonomy (Category/Sub-category)** | `SN_FLIGHT_REPORT`, `M_SN_CATEGORY` | Crew Portal incident classification fields captured on the flight report: category, sub-category, and polarity (if present). This is the per-report taxonomy used to segment themes and outcomes. |
| **Crew Identity, Seat Position, and Involvement** | `SN_REPORT_CREW`, `M_SN_CREW` | Crew identity attributes (IGA) and seat-position mapping for each flight report, including whether the crew member is marked as involved in the incident. This concept unifies identity + seat-position + involvement attribution to avoid overlap between separate identity and involvement concepts. |
| **Crew Involvement and Performance Aggregation** | `M_SN_CREW` | Aggregated performance/involvement metrics per crew identity (IGA), summarizing report counts and how many reports mark them as involved. This supports benchmarking and trend analysis without duplicating identity/seat-position fields already modeled above. |
| **Service Checklist Question/Answer** | `SN_SERVICE_CHECK` | Structured Crew Portal checklist responses captured per flight report, including the lead’s answer and whether the question was answered. This is the primary structured questionnaire content used for crew performance scoring. |
| **Service Check Scoring and Pass/Fail Outcome** | `SN_SERVICE_CHECK` | Scoring interpretation of each checklist question response, including pass/fail under the scoring rules. This concept captures the computed grading outcome for each checklist item. |
| **Narrative, Action/Outcome, and Resolution** | `SN_FLIGHT_REPORT` | Customer-facing service narrative captured on the flight report: situation/action/outcome text, lead’s service checklist narrative (if stored in the same record), and how the incident was resolved. |
| **Assessment Lifecycle and Workflow/Audit** | `SN_FLIGHT_REPORT` | Fields on the flight report that support the assessment lifecycle and auditability of the incident record (e.g., creation/assessment timestamps and workflow status). |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `SN_REPORT_CREW` | `M_SN_CREW` | `IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `SN_REPORT_CREW` | `SN_FLIGHT_REPORT` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.95 | 100% | inferred | many-to-one |
| `SN_SERVICE_CHECK` | `SN_FLIGHT_REPORT` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.95 | 100% | inferred | many-to-one |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `SUB_CATEGORY_CODE` -> `SUB_CATEGORY_CODE` | 0.95 | 100% | inferred | many-to-one |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `SUB_CATEGORY` -> `SUB_CATEGORY` | 0.80 | 80% | inferred | many-to-one |
| `SN_FLIGHT_REPORT` | `SN_REPORT_CREW` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.45 | 100% | inferred | many-to-many |
| `SN_SERVICE_CHECK` | `SN_REPORT_CREW` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.45 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `CATEGORY_CODE` -> `CATEGORY_CODE` | 0.45 | 100% | inferred | many-to-many |
| `M_SN_CREW` | `SN_REPORT_CREW` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `SN_SERVICE_CHECK` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.35 | 100% | inferred | many-to-many |
| `SN_REPORT_CREW` | `SN_SERVICE_CHECK` | `SN_REPORT_ID` -> `SN_REPORT_ID` | 0.35 | 100% | inferred | many-to-many |
| `M_SN_CATEGORY` | `SN_FLIGHT_REPORT` | `CATEGORY_CODE` -> `CATEGORY_CODE` | 0.35 | 100% | inferred | many-to-many |
| `M_SN_CATEGORY` | `SN_FLIGHT_REPORT` | `SUB_CATEGORY_CODE` -> `SUB_CATEGORY_CODE` | 0.35 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `CATEGORY` -> `CATEGORY` | 0.35 | 100% | inferred | many-to-many |
| `SN_FLIGHT_REPORT` | `M_SN_CATEGORY` | `POLARITY` -> `POLARITY` | 0.35 | 100% | inferred | many-to-many |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1) Duplicate/near-duplicate concepts: `crew_involvement_attribution` and `crew_performance_aggregation` overlap heavily. Both use `M_SN_CREW`-style involvement counts/rates (report
- concept review round 2: 1) Table coverage issue: SN_SERVICE_CHECK is covered by concept `service_check_response_and_scoring`, but SN_REPORT_CREW and M_SN_CREW are only covered by `crew_identity_and_seat_p

## Rejected join candidates

- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY ~ SUB_CATEGORY_CODE, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY_CODE ~ SUB_CATEGORY_CODE, 84%); REFUTED by data — only 19.9% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ SUB_CATEGORY_CODE, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY ~ CATEGORY_CODE, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ CATEGORY_CODE, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY_CODE ~ CATEGORY_CODE, 84%); REFUTED by data — only 0.2% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY_CODE ~ CATEGORY, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY ~ CATEGORY, 84%); REFUTED by data — only 14.9% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY_CODE ~ CATEGORY, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY ~ SUB_CATEGORY, 84%); REFUTED by data — only 14.9% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (CATEGORY_CODE ~ SUB_CATEGORY, 84%); REFUTED by data — only 0.0% of child rows resolve to a parent
- SN_FLIGHT_REPORT -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (SUB_CATEGORY_CODE ~ SUB_CATEGORY, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- M_SN_CREW -> M_SN_CATEGORY: reference lookup into M_SN_CATEGORY (65 rows); column names align (REPORT_COUNT ~ REPORT_COUNT, 100%); REFUTED by data — only 47.1% of child rows resolve to a parent
