# Concept Graph — PEP

*Generated 2026-08-11T03:41:42.876814+00:00*

| Metric | Count |
|---|---|
| Tables | 10 |
| Concepts | 10 |
| Edges | 58 |
| Data-verified joins | 18 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `EMPLOYEE_INFO` | Crew Identity | One crew member snapshot | yes | 1 | Stores the crew member’s identity and employment context used by the PEP system, including designation and home base for filtering assessments. Each row represents a versioned snapshot of one employee’s attributes over time. |
| `MENTOR_FEEDBACK` | Mentor Feedback | One mentor evaluation record | yes | 2 | Captures the mentor’s evaluation outcome for a specific PEP assessment, including the overall MARK and derived GRADE plus narrative strengths and improvement areas. Each row represents one mentor feedback record tied to a flight and assessment category/allocation context. |
| `PEP_CATEGORY` | Template Categories | One template-scoped category | yes | 3 | Defines the categories that belong to a specific PEP template, including category codes/names and whether the category is printable and active. Each row represents one category within a template used to structure the assessment. |
| `PEP_DEVIATION_MATRIX` | Grading Rules | One mark-range grading rule | yes | 4 | Maps overall mark ranges to an ordinal grade for a given crew assessment context and template, using START_RANGE/END_RANGE to determine the grade band. Each row represents one mark-to-grade rule entry for a template and crew assessment type. |
| `PEP_FLIGHT_DETAILS` | Flight Context | One flight instance for an assessment | yes | 1 | Provides the flight context for a PEP assessment, including flight number, sector, and key timestamps (flight date, STD/STA and SLA-related times) plus flight type. Each row represents one flight instance associated with a PEP assessment. |
| `PEP_GRADE` | Grade Bands | One grade band definition | yes | 1 | Defines the grade band master data (grade codes and labels) used by the PEP system, including which grades are currently active. Each row represents one grade band definition. |
| `PEP_QUESTIONS` | Questions | One question definition with role/template marks | yes | 10 | Defines the question bank used in PEP assessments, including which category each question belongs to and whether it is critical/crucial/safety-related. Also stores the per-template scoring weights for the same question across different crew roles (e.g., Cabin Attendant vs Lead, and A320 vs ATR). |
| `PEP_QUESTION_FEEDBACK` | Mentor Feedback | One mentor answer per question per assessment | yes | 1 | Stores the per-question outcome for a specific assessment, where the mentor’s boolean answer determines whether the question’s marks are awarded (true) or zeroed (false). Optionally includes remarks when the answer is false. |
| `PEP_SCHEDULER` | PEP Scheduling | One scheduled PEP assessment instance | yes | 3 | Represents the planning and lifecycle of a PEP assessment for a crew member, including which mentor is assigned, the scheduled date, and whether it is planned or cancelled. Captures crew context such as designation/base and flags like whether the crew member is a new joinee. |
| `PEP_TEMPLATE` | Assessment Templates | One assessment template definition | yes | 2 | Defines the active PEP assessment templates by designation and flight type, including the template code/name and calculation type used to compute results. Templates determine which question scoring weights apply for a given crew role and context. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `EMPLOYEE_INFO`, `PEP_SCHEDULER` | Crew member identity and employment context used to filter and scope PEP assessments (e.g., designation and home base). Includes versioned snapshot attributes for the crew member over time. |
| **Assessment Lifecycle & Audit** | `PEP_SCHEDULER`, `MENTOR_FEEDBACK`, `PEP_QUESTION_FEEDBACK` | Planning and lifecycle state of a PEP assessment instance, including whether it is planned/cancelled, scheduling timestamps, and workflow/audit linkage across mentor assignment and results. |
| **Mentor Assignment & Overall Outcome** | `PEP_SCHEDULER`, `MENTOR_FEEDBACK` | Mentor assignment and the mentor’s overall evaluation outcome for a specific PEP assessment, including overall MARK and derived GRADE plus narrative strengths/improvement areas. |
| **Flight Context** | `PEP_FLIGHT_DETAILS`, `PEP_SCHEDULER`, `MENTOR_FEEDBACK` | Flight instance details used as context for the PEP assessment (flight number/sector and key timestamps such as flight date and STD/STA/SLA-related times). |
| **Assessment Template Selection** | `PEP_TEMPLATE`, `PEP_SCHEDULER`, `EMPLOYEE_INFO` | Determines which PEP assessment template applies based on designation and flight type, including template code/name and calculation type used to compute results. |
| **Template Categories & Structure** | `PEP_CATEGORY`, `PEP_TEMPLATE` | Template-scoped categories that structure the assessment, including category codes/names and whether the category is printable and active. |
| **Question Bank & Scoring Weights** | `PEP_QUESTIONS`, `PEP_TEMPLATE`, `PEP_CATEGORY` | Question definitions and per-template/per-role/per-fleet scoring weights (e.g., marks awarded when mentor answer is true). Includes question criticality/safety classification and the category membership of each question. |
| **Question Feedback (Outcome + Remarks)** | `PEP_QUESTION_FEEDBACK` | Per-question mentor evaluation outcome for a specific assessment, where the mentor boolean answer determines whether marks are awarded (true) or zeroed (false). Optionally includes remarks when the answer is false. |
| **Category-Level Scoring Aggregation** | `PEP_CATEGORY`, `PEP_QUESTIONS`, `PEP_QUESTION_FEEDBACK`, `MENTOR_FEEDBACK` | Aggregated scoring results used for reporting/printable output at the template category level (e.g., category marks/score totals derived from per-question outcomes and question weights). This concept represents the computed breakdown that is implied by template categories and question scoring weights. |
| **Grading Rules & Grade Bands** | `PEP_DEVIATION_MATRIX`, `PEP_GRADE`, `MENTOR_FEEDBACK` | Grade band master data and the deviation matrix rules that map overall mark ranges to an ordinal grade for a given template/assessment context. Used to derive the final grade from the overall mark. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `PEP_FLIGHT_DETAILS` | `MENTOR_FEEDBACK` | `PEP_ID` -> `PEP_ID` | 1.00 | 85% | declared | many-to-one |
| `MENTOR_FEEDBACK` | `EMPLOYEE_INFO` | `IGA` -> `IGA` | 1.00 | 100% | declared | many-to-one |
| `PEP_QUESTION_FEEDBACK` | `MENTOR_FEEDBACK` | `FEEDBACK_ID` -> `ID` | 1.00 | 100% | declared | many-to-one |
| `PEP_QUESTION_FEEDBACK` | `PEP_QUESTIONS` | `QUESTION_ID` -> `QUESTION_ID` | 1.00 | 100% | declared | many-to-one |
| `MENTOR_FEEDBACK` | `PEP_GRADE` | `GRADE` -> `GRADE` | 1.00 | 100% | declared | many-to-one |
| `PEP_SCHEDULER` | `EMPLOYEE_INFO` | `IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `MENTOR_FEEDBACK` | `PEP_SCHEDULER` | `PEP_SCHDULER_ID` -> `PEP_SCHDULER_ID` | 0.95 | 100% | inferred | many-to-one |
| `MENTOR_FEEDBACK` | `PEP_FLIGHT_DETAILS` | `PEP_ID` -> `PEP_ID` | 0.95 | 100% | inferred | many-to-one |
| `MENTOR_FEEDBACK` | `PEP_SCHEDULER` | `PEP_ID` -> `PEP_ID` | 0.95 | 100% | inferred | many-to-one |
| `PEP_FLIGHT_DETAILS` | `PEP_SCHEDULER` | `PEP_ID` -> `PEP_ID` | 0.95 | 100% | inferred | many-to-one |
| `PEP_SCHEDULER` | `PEP_FLIGHT_DETAILS` | `PEP_ID` -> `PEP_ID` | 0.95 | 100% | inferred | many-to-one |
| `PEP_QUESTIONS` | `PEP_CATEGORY` | `CATEGORY_ID` -> `CATEGORY_ID` | 0.95 | 100% | inferred | many-to-one |
| `PEP_CATEGORY` | `PEP_TEMPLATE` | `TEMPLATE_ID` -> `TEMPLATE_ID` | 0.95 | 100% | inferred | many-to-one |
| `PEP_DEVIATION_MATRIX` | `PEP_TEMPLATE` | `TEMPLATE_ID` -> `TEMPLATE_ID` | 0.95 | 100% | inferred | many-to-one |
| `PEP_CATEGORY` | `PEP_TEMPLATE` | `TEMPLATE_CODE` -> `TEMPLATE_CODE` | 0.95 | 100% | inferred | many-to-one |
| `MENTOR_FEEDBACK` | `PEP_GRADE` | `GRADE` -> `GRADE_CODE` | 0.95 | 100% | inferred | many-to-one |
| `PEP_SCHEDULER` | `MENTOR_FEEDBACK` | `PEP_SCHDULER_ID` -> `PEP_SCHDULER_ID` | 0.80 | 85% | inferred | many-to-one |
| `PEP_SCHEDULER` | `MENTOR_FEEDBACK` | `PEP_ID` -> `PEP_ID` | 0.80 | 85% | inferred | many-to-one |
| `EMPLOYEE_INFO` | `MENTOR_FEEDBACK` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |
| `EMPLOYEE_INFO` | `PEP_SCHEDULER` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |
| `MENTOR_FEEDBACK` | `PEP_SCHEDULER` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |
| `PEP_SCHEDULER` | `MENTOR_FEEDBACK` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |
| `PEP_QUESTIONS` | `PEP_CATEGORY` | `CATEGORY_CODE` -> `CATEGORY_CODE` | 0.45 | 100% | inferred | many-to-many |
| `PEP_CATEGORY` | `PEP_QUESTIONS` | `CATEGORY_ID` -> `CATEGORY_ID` | 0.35 | 100% | inferred | many-to-many |
| `PEP_CATEGORY` | `PEP_DEVIATION_MATRIX` | `TEMPLATE_ID` -> `TEMPLATE_ID` | 0.35 | 100% | inferred | many-to-many |
| `PEP_DEVIATION_MATRIX` | `PEP_CATEGORY` | `TEMPLATE_ID` -> `TEMPLATE_ID` | 0.35 | 100% | inferred | many-to-many |
| `PEP_TEMPLATE` | `PEP_CATEGORY` | `TEMPLATE_ID` -> `TEMPLATE_ID` | 0.35 | 100% | inferred | many-to-many |
| `PEP_TEMPLATE` | `PEP_DEVIATION_MATRIX` | `TEMPLATE_ID` -> `TEMPLATE_ID` | 0.35 | 100% | inferred | many-to-many |
| `PEP_TEMPLATE` | `PEP_CATEGORY` | `TEMPLATE_CODE` -> `TEMPLATE_CODE` | 0.35 | 100% | inferred | many-to-many |
| `PEP_CATEGORY` | `PEP_QUESTIONS` | `CATEGORY_CODE` -> `CATEGORY_CODE` | 0.35 | 100% | inferred | many-to-many |
| `PEP_QUESTIONS` | `PEP_QUESTION_FEEDBACK` | `QUESTION_ID` -> `QUESTION_ID` | 0.35 | 100% | inferred | many-to-many |
| `MENTOR_FEEDBACK` | `PEP_DEVIATION_MATRIX` | `GRADE` -> `GRADE` | 0.35 | 100% | inferred | many-to-many |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1) Table coverage failure: PEP_TEMPLATE is not covered by any concept. (The concept `assessment_template_design` references PEP_TEMPLATE in source_tables, but its key_columns inclu
- concept review round 2: Issues found:
1) Table coverage gap: PEP_QUESTION_FEEDBACK is not covered by any concept that includes it in `source_tables`. The concept `question_outcomes` includes PEP_QUESTION_

## Rejected join candidates

- MENTOR_FEEDBACK -> PEP_CATEGORY: reference lookup into PEP_CATEGORY (26 rows); column name carries reference table stem 'CATEGORY'; REFUTED by data — only 74.2% of child rows resolve to a parent
- MENTOR_FEEDBACK -> PEP_CATEGORY: reference lookup into PEP_CATEGORY (26 rows); column names align (CATEGORY ~ CATEGORY_CODE, 100%); column name carries reference table stem 'CATEGORY'; REFUTED by data — only 0.0% of child rows resolve to a parent
- MENTOR_FEEDBACK -> PEP_CATEGORY: reference lookup into PEP_CATEGORY (26 rows); column names align (CATEGORY ~ CATEGORY, 100%); column name carries reference table stem 'CATEGORY'; REFUTED by data — only 0.0% of child rows resolve to a parent
- MENTOR_FEEDBACK -> PEP_QUESTIONS: reference lookup into PEP_QUESTIONS (150 rows); column names align (CATEGORY ~ CATEGORY_CODE, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- EMPLOYEE_INFO -> PEP_TEMPLATE: reference lookup into PEP_TEMPLATE (4 rows); column names align (DESIGNATION ~ DESIGNATION_CODE, 100%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
- PEP_SCHEDULER -> PEP_TEMPLATE: reference lookup into PEP_TEMPLATE (4 rows); column names align (CREW_DESIGNATION ~ DESIGNATION_CODE, 85%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
- PEP_SCHEDULER -> PEP_TEMPLATE: reference lookup into PEP_TEMPLATE (4 rows); column names align (DESIGNATION ~ DESIGNATION_CODE, 100%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
