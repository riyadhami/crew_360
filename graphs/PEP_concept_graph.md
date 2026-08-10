# Concept Graph — PEP

*Generated 2026-08-09T17:34:53.192430+00:00*

| Metric | Count |
|---|---|
| Tables | 10 |
| Concepts | 10 |
| Edges | 60 |
| Data-verified joins | 18 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `EMPLOYEE_INFO` | Employee Identity | One crew member identity record (versioned over time) | yes | 1 | Stores the crew member’s identity and assignment attributes used to drive PEP assessment planning and filtering (e.g., base and designation). Each row represents one crew identity state over time, including whether the employee is active. |
| `MENTOR_FEEDBACK` | Mentor Feedback | One mentor evaluation for one PEP assessment instance | yes | 2 | Captures the mentor’s evaluation outcome for a specific PEP assessment instance, including the overall MARK and derived GRADE plus narrative feedback fields. Each row represents one mentor assessment record tied to a crew member and flight context. |
| `PEP_CATEGORY` | Template Categories | One template-scoped assessment category | yes | 3 | Defines the categories within a PEP template, including display/print behavior and positional ordering within the template. Each row represents one category scoped to a specific template used to structure the assessment. |
| `PEP_DEVIATION_MATRIX` | Grading Rules | One template-scoped mark-range to grade mapping | yes | 4 | Maps mark ranges to grade bands for a given template, providing the rule set used to convert an overall numeric MARK into an ordinal GRADE. Each row represents one grade band range definition for a template. |
| `PEP_FLIGHT_DETAILS` | Flight Context | One flight instance tied to one PEP assessment | yes | 1 | Holds the flight context for a PEP assessment, linking the assessment to a specific flight number, sector, and key operational timestamps. Each row represents one flight instance associated with a PEP assessment. |
| `PEP_GRADE` | Grade Catalog | One grade band definition | yes | 1 | Provides the catalog of possible grade bands used by the PEP system. Each row represents one grade code/label that can be referenced by grading rules and mentor outcomes. |
| `PEP_QUESTIONS` | Questions | One question definition | yes | 10 | Defines the question bank used during a PEP assessment, including which category and template role it applies to and whether it is critical. Stores the per-role mark values for the same question across Cabin Attendant/A320, Lead/ATR, and Cabin Attendant/ATR templates (with 0 indicating the question is not on that template). |
| `PEP_QUESTION_FEEDBACK` | Mentor Feedback | One question outcome for an assessment | yes | 1 | Captures the mentor’s per-question outcome for a specific assessment, recording the boolean answer that drives marks (true awards the question’s marks; false awards zero). Optionally includes remarks when the answer is false to explain the deviation from expected performance. |
| `PEP_SCHEDULER` | Assessment Scheduling | One scheduled PEP assessment instance | yes | 3 | Represents the scheduling and lifecycle of a PEP assessment for a crew member, including planned vs cancelled status and submission timing. Links the assessment to the mentor, crew designation/base, and the flight context via the PEP identifier and date. |
| `PEP_TEMPLATE` | Templates | One assessment template definition | yes | 2 | Defines the assessment templates used to evaluate crew performance, including which designation they target and whether they are currently active. Provides template configuration such as calculation type and flight type to control how scoring and grading are performed for assessments using that template. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `EMPLOYEE_INFO`, `PEP_SCHEDULER` | Crew member identity and assignment attributes used to drive PEP assessment planning and filtering, including whether the employee is active and the crew’s base/designation context over time. |
| **Flight Context** | `PEP_FLIGHT_DETAILS`, `PEP_SCHEDULER`, `MENTOR_FEEDBACK` | The operational flight instance to which a PEP assessment is tied, including flight number/sector and key operational timestamps. |
| **PEP Assessment Instance Lifecycle** | `PEP_SCHEDULER`, `MENTOR_FEEDBACK` | Scheduling, planned vs cancelled status, submission timing, and the PEP identifier that uniquely identifies an assessment instance for a crew member. |
| **Mentor Evaluation Outcome** | `MENTOR_FEEDBACK`, `PEP_GRADE`, `PEP_DEVIATION_MATRIX` | Mentor-level overall evaluation outcome for a specific PEP assessment instance, including overall MARK and derived GRADE plus narrative feedback fields. |
| **Grading Rules & Grade Bands** | `PEP_DEVIATION_MATRIX`, `PEP_GRADE` | Template-scoped deviation matrix mapping overall MARK ranges to grade bands, and the grade catalog taxonomy used for derived GRADE. |
| **PEP Template Selection & Configuration** | `PEP_TEMPLATE`, `PEP_SCHEDULER`, `PEP_DEVIATION_MATRIX` | Template definitions that determine which designation they target and how scoring/grading are configured (e.g., calculation type and flight type). |
| **Template Categories Structure** | `PEP_CATEGORY`, `PEP_TEMPLATE` | Template-scoped category definitions used to organize and present the assessment (display/print behavior and ordering within the template). |
| **Question Bank & Applicability** | `PEP_QUESTIONS`, `PEP_CATEGORY`, `PEP_TEMPLATE` | Question definitions and their applicability to templates/roles, including criticality and per-role mark values (0 indicates the question is not on that template). |
| **Mentor Question Outcomes** | `PEP_QUESTION_FEEDBACK`, `MENTOR_FEEDBACK`, `PEP_SCHEDULER`, `PEP_QUESTIONS` | Per-question mentor evaluation for a specific assessment instance, including the boolean answer that drives marks and optional remarks when the answer is false. |
| **Question Scoring & Mark Contribution** | `PEP_QUESTION_FEEDBACK`, `PEP_QUESTIONS`, `PEP_SCHEDULER`, `PEP_TEMPLATE` | How question-level boolean outcomes translate into numeric marks using the question bank’s per-role mark values (including 0 meaning not applicable on that template). |

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

- concept review round 1: 1) Duplicate/near-duplicate concepts: `question_bank_and_scoring_rules` and `template_categories` are both template-structure related, but they are not duplicates. However, `mentor
- concept review round 2: 1) Table coverage gap: PEP_QUESTION_FEEDBACK is not covered by any concept. (The concept `mentor_question_outcomes` references PEP_QUESTION_FEEDBACK in source_tables, but its key_c

## Rejected join candidates

- MENTOR_FEEDBACK -> PEP_CATEGORY: reference lookup into PEP_CATEGORY (26 rows); column name carries reference table stem 'CATEGORY'; REFUTED by data — only 77.8% of child rows resolve to a parent
- MENTOR_FEEDBACK -> PEP_CATEGORY: reference lookup into PEP_CATEGORY (26 rows); column names align (CATEGORY ~ CATEGORY_CODE, 100%); column name carries reference table stem 'CATEGORY'; REFUTED by data — only 0.0% of child rows resolve to a parent
- MENTOR_FEEDBACK -> PEP_CATEGORY: reference lookup into PEP_CATEGORY (26 rows); column names align (CATEGORY ~ CATEGORY, 100%); column name carries reference table stem 'CATEGORY'; REFUTED by data — only 0.0% of child rows resolve to a parent
- MENTOR_FEEDBACK -> PEP_QUESTIONS: reference lookup into PEP_QUESTIONS (150 rows); column names align (CATEGORY ~ CATEGORY_CODE, 100%); REFUTED by data — only 0.0% of child rows resolve to a parent
- EMPLOYEE_INFO -> PEP_TEMPLATE: reference lookup into PEP_TEMPLATE (4 rows); column names align (DESIGNATION ~ DESIGNATION_CODE, 100%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
- PEP_SCHEDULER -> PEP_TEMPLATE: reference lookup into PEP_TEMPLATE (4 rows); column names align (CREW_DESIGNATION ~ DESIGNATION_CODE, 85%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
- PEP_SCHEDULER -> PEP_TEMPLATE: reference lookup into PEP_TEMPLATE (4 rows); column names align (DESIGNATION ~ DESIGNATION_CODE, 100%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
