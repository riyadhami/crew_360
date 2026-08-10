# Concept Graph — CLMS

*Generated 2026-08-09T17:37:30.438885+00:00*

| Metric | Count |
|---|---|
| Tables | 15 |
| Concepts | 11 |
| Edges | 83 |
| Data-verified joins | 18 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | Balance Update | One automated balance update event per crew and leave year | yes | 7 | Stores automated, scheduler-driven updates to a crew member’s leave balances for a specific leave year. Each row captures the old vs updated balances (e.g., SL and URTI), eligibility context, and whether the update was revoked. |
| `M_CLMS_CREW` | Crew Master | One crew member profile record | yes | 10 | Holds the crew master record with designation, base, employment dates, and status/activation attributes used by the leave management system. Each row represents one crew member’s current (or versioned) profile context that leave and balance logic can reference. |
| `M_CLMS_GND_CODE` | Ground Codes | One ground-duty code reference | yes | 0 | Provides a reference list of ground-duty codes used to categorize or interpret ground-duty related leave/crew contexts. Each row is one code value that other records can map to. |
| `M_CLMS_LEAVE_BALANCE` | Leave Balance | One crew leave-type balance for a leave year and validity window | yes | 2 | Stores a crew member’s leave balance by leave type and leave year, including the active balance and the validity window. Each row represents the balance amount for one crew + leave type + leave year during a defined period. |
| `M_DESIGNATIONS` | Designations | One designation reference | yes | 0 | Reference table mapping designation identifiers to designation names used to describe crew roles. Each row is one designation value that crew master records can reference. |
| `M_LMS_LEAVE_TYPE` | Leave Types | One leave type definition | yes | 1 | Defines the set of leave types available in the system, including a human-readable name and description. Each row represents one leave type that balances and leave requests can be categorized under. |
| `M_LMS_STATUS_TYPE` | Status Type | One status type definition | yes | 0 | Defines the master list of leave-management status types used to classify the state of leave-related workflows. It provides the standardized dimension values that other CLMS records reference for consistent status reporting. |
| `M_UMS_STATION` | Station | One station master record | yes | 3 | Stores station master data (code, name, location attributes) used to contextualize crew operations and base-related decisions. It supports reporting and filtering by station and base/region characteristics for crew movements and assignments. |
| `T_CLMS_ATTRITION` | Attrition Record | One crew attrition event (month/year) | yes | 2 | Captures one crew member’s attrition event details for a given month and year, including designation, trigger operations, and reasons for leaving. It supports workforce analytics by tracking attrition timing and categorization at the crew level. |
| `T_CLMS_CREW_SWAP_DETAIL` | Crew Swap | One crew swap application detail | yes | 6 | Represents one crew swap application instance for a crew member, including the swap partner crew, bases, priority, status, and key timeline dates. It records the operational and administrative progression of the swap request for crew relocation outcomes. |
| `T_CLMS_CREW_TRANSFER_DETAIL` | Crew Transfer | One crew transfer application detail | yes | 10 | Represents one crew transfer application instance, capturing the crew member, current and requested bases, priority, status, and approval/relocation timeline. It also records whether the transfer is linked to a swap and related flags for pull-back and acceptance outcomes. |
| `T_CLMS_GND_CODES` | Ground Duty Code | One ground-duty leg assignment | yes | 2 | Stores one ground-duty leg entry for an employee, including the leg code, activity code, time window, and approval/payability indicators. It supports operational tracking of ground-duty assignments and whether they are approved and payable. |
| `T_CLMS_LEAVE_REQ_DETAIL` | Leave Request Detail | One leave-type line item | yes | 3 | Stores the per-type breakdown of a crew member’s leave request, including the number of leave days and the leave balance/current leave figures for that leave type over a date range. Each row represents one leave-type line item within a leave request. |
| `T_CLMS_LEAVE_REQ_MASTER` | Leave Request | One leave request instance | yes | 1 | Represents the main workflow record for a crew member’s leave request, tying the request to a crew, leave type, status, and ground-duty code, with auditable action and comments. Each row represents one leave request instance for a crew member (at a specific leave type and status). |
| `T_SPL_APPRECIATION` | Special Appreciation | One appreciation recognition record | yes | 1 | Tracks special appreciation recognition for a crew member at a base over an effective date range, including the future appreciation date and whether the award has been received. Each row represents one appreciation/recognition record for a crew member at a specific base. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `M_CLMS_CREW`, `CREW_BALANCE_UPDATE_BY_SCHEDULER`, `T_CLMS_ATTRITION`, `T_CLMS_CREW_SWAP_DETAIL`, `T_CLMS_CREW_TRANSFER_DETAIL`, `T_CLMS_LEAVE_REQ_MASTER`, `T_CLMS_LEAVE_REQ_DETAIL`, `T_CLMS_GND_CODES`, `T_SPL_APPRECIATION` | Core crew identity used across CLMS joins (CREW_ID) and referenced crew master attributes that anchor all leave, balance, and workforce events. |
| **Employment & Role Context** | `M_CLMS_CREW`, `M_DESIGNATIONS`, `M_UMS_STATION` | Crew master context used to interpret leave and workforce outcomes: designation/role, base/station context, and employment status/dates. |
| **Leave Types & Reference Dimensions** | `M_LMS_LEAVE_TYPE`, `M_LMS_STATUS_TYPE` | Reference dimensions for leave categorization and standardized workflow status classification. |
| **Leave Request Workflow & Context** | `T_CLMS_LEAVE_REQ_MASTER`, `M_CLMS_GND_CODE`, `M_LMS_STATUS_TYPE`, `M_LMS_LEAVE_TYPE` | Leave request master workflow record: ties crew + leave type + ground-duty code + status, with auditable action metadata and comments. |
| **Leave Request Line Items & Accounting** | `T_CLMS_LEAVE_REQ_DETAIL`, `M_LMS_LEAVE_TYPE`, `M_CLMS_LEAVE_BALANCE` | Per-type breakdown of a leave request including requested days and the leave date range, plus line-level balance/current figures for that leave type over the date range. |
| **Leave Balances & Validity Windows** | `M_CLMS_LEAVE_BALANCE`, `CREW_BALANCE_UPDATE_BY_SCHEDULER` | Active leave balance by crew + leave type + leave year, including validity window boundaries used to determine which balance applies. |
| **Automated Balance Update & Revocation Audit** | `CREW_BALANCE_UPDATE_BY_SCHEDULER` | Scheduler-driven balance lifecycle events: old vs updated balances, eligibility context, and whether the update was revoked. |
| **Ground Duty Codes & Legs** | `M_CLMS_GND_CODE`, `T_CLMS_GND_CODES`, `T_CLMS_LEAVE_REQ_MASTER`, `T_CLMS_LEAVE_REQ_DETAIL` | Ground-duty code reference and the crew-level ground-duty leg assignments (time windows, approval, and payability) used to contextualize leave and operational scheduling. |
| **Crew Transfers & Swaps** | `T_CLMS_CREW_TRANSFER_DETAIL`, `T_CLMS_CREW_SWAP_DETAIL`, `M_UMS_STATION` | Operational relocation workflow for crew: transfer and swap application details including partner crew, bases, priority, status, and key timeline dates. |
| **Crew Attrition Events** | `T_CLMS_ATTRITION`, `M_DESIGNATIONS` | Monthly attrition records capturing workforce exit timing and categorization by designation and trigger operations/reasons. |
| **Special Appreciations & Recognition** | `T_SPL_APPRECIATION`, `M_UMS_STATION` | Recognition lifecycle for crew at a base over an effective date range, including future appreciation date and whether the award was received. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `M_CLMS_LEAVE_BALANCE` | `M_LMS_LEAVE_TYPE` | `LEAVE_TYPE_ID` -> `LEAVE_TYPE_ID` | 1.00 | 100% | declared | many-to-one |
| `M_CLMS_LEAVE_BALANCE` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 1.00 | 100% | declared | many-to-one |
| `T_CLMS_LEAVE_REQ_DETAIL` | `T_CLMS_LEAVE_REQ_MASTER` | `LEAVE_DETAIL_ID` -> `LEAVE_DETAIL_ID` | 1.00 | 100% | declared | many-to-one |
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_CLMS_ATTRITION` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_CLMS_CREW_SWAP_DETAIL` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_CLMS_CREW_TRANSFER_DETAIL` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_CLMS_GND_CODES` | `M_CLMS_CREW` | `EMP_NO` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_CLMS_LEAVE_REQ_MASTER` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_SPL_APPRECIATION` | `M_CLMS_CREW` | `CREW_ID` -> `CREW_ID` | 0.95 | 100% | asserted | many-to-one |
| `T_CLMS_LEAVE_REQ_DETAIL` | `M_LMS_LEAVE_TYPE` | `LEAVE_TYPE_ID` -> `LEAVE_TYPE_ID` | 0.95 | 100% | inferred | many-to-one |
| `T_CLMS_LEAVE_REQ_MASTER` | `M_LMS_LEAVE_TYPE` | `LEAVE_TYPE_ID` -> `LEAVE_TYPE_ID` | 0.95 | 100% | inferred | many-to-one |
| `T_CLMS_LEAVE_REQ_MASTER` | `M_LMS_STATUS_TYPE` | `STATUS_TYPE_ID` -> `STATUS_TYPE_ID` | 0.95 | 100% | inferred | many-to-one |
| `T_CLMS_LEAVE_REQ_MASTER` | `T_CLMS_LEAVE_REQ_DETAIL` | `LEAVE_DETAIL_ID` -> `LEAVE_DETAIL_ID` | 0.95 | 100% | inferred | many-to-one |
| `M_CLMS_CREW` | `M_DESIGNATIONS` | `DESIGNATION` -> `DESIGNATION` | 0.95 | 100% | inferred | many-to-one |
| `M_CLMS_LEAVE_BALANCE` | `M_LMS_LEAVE_TYPE` | `LEAVE_YEAR_ID` -> `LEAVE_TYPE_ID` | 0.95 | 100% | inferred | many-to-one |
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 99% | inferred | many-to-many |
| `T_SPL_APPRECIATION` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 99% | inferred | many-to-many |
| `M_CLMS_CREW` | `M_LMS_LEAVE_TYPE` | `LEAVE_TYPE_ID` -> `LEAVE_TYPE_ID` | 0.75 | — | inferred | many-to-one |
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `M_CLMS_CREW` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `M_CLMS_CREW` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.45 | 99% | inferred | many-to-many |
| `M_CLMS_LEAVE_BALANCE` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.45 | 99% | inferred | many-to-many |
| `T_CLMS_ATTRITION` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_ATTRITION` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_CREW_SWAP_DETAIL` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_CREW_SWAP_DETAIL` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_CREW_TRANSFER_DETAIL` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_CREW_TRANSFER_DETAIL` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_LEAVE_REQ_MASTER` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_SPL_APPRECIATION` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_CREW_SWAP_DETAIL` | `T_CLMS_CREW_TRANSFER_DETAIL` | `SWAP_CREW_ID` -> `SWAP_CREW_ID` | 0.40 | — | inferred | many-to-one |
| `T_CLMS_CREW_TRANSFER_DETAIL` | `T_CLMS_CREW_SWAP_DETAIL` | `SWAP_CREW_ID` -> `SWAP_CREW_ID` | 0.40 | — | inferred | many-to-one |
| `T_CLMS_CREW_SWAP_DETAIL` | `T_CLMS_CREW_TRANSFER_DETAIL` | `T_ID` -> `T_ID` | 0.40 | — | inferred | many-to-one |
| `T_CLMS_CREW_SWAP_DETAIL` | `T_CLMS_CREW_TRANSFER_DETAIL` | `CURRENT_BASE_ID` -> `CURRENT_BASE_ID` | 0.40 | — | inferred | many-to-one |
| `T_CLMS_CREW_SWAP_DETAIL` | `T_CLMS_CREW_TRANSFER_DETAIL` | `REQUESTED_BASE_ID` -> `REQUESTED_BASE_ID` | 0.40 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `CREW_BALANCE_UPDATE_BY_SCHEDULER` | `LEAVE_TYPE_ID` -> `LEAVE_YEAR_ID` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `M_UMS_STATION` | `REGION` -> `REGION` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `TRG_OPS` -> `TRG_OPS` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `REASON` -> `REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `SUB_REASON` -> `REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `R_REASON` -> `REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `REASON` -> `SUB_REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `SUB_REASON` -> `SUB_REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_ATTRITION` | `R_REASON` -> `SUB_REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_CREW_TRANSFER_DETAIL` | `REASON` -> `REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_CREW_TRANSFER_DETAIL` | `SUB_REASON` -> `REASON` | 0.35 | — | inferred | many-to-one |
| `M_CLMS_CREW` | `T_CLMS_CREW_TRANSFER_DETAIL` | `R_REASON` -> `REASON` | 0.35 | — | inferred | many-to-one |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1) Table coverage gap: M_CLMS_GND_CODE is not covered by any concept. The concept 'ground_duty_codes_and_legs' lists M_CLMS_GND_CODE in source_tables, but its key_columns reference
- concept review round 2: 1) Table coverage gap: T_CLMS_CREW_SWAP_DETAIL is not covered by any concept that lists it in source_tables. It appears only inside the key_columns of the 'crew_transfers_and_swaps

## Rejected join candidates

- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.4% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 1.8% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 12.3% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 50.9% of child rows resolve to a parent
- M_CLMS_CREW -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 21.8% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 3.8% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.1% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 11.4% of child rows resolve to a parent
- M_CLMS_CREW -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 54.5% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 21.8% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 3.8% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.1% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 11.4% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 54.5% of child rows resolve to a parent
- T_CLMS_ATTRITION -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 36.7% of child rows resolve to a parent
- T_CLMS_ATTRITION -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_CLMS_ATTRITION -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.7% of child rows resolve to a parent
- T_CLMS_ATTRITION -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 43.3% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.2% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 14.6% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 56.2% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 23.6% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 2.2% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 7.9% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 55.1% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 21.3% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 3.3% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.4% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 11.4% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 50.9% of child rows resolve to a parent
- T_SPL_APPRECIATION -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 21.5% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 2.7% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.4% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 11.7% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> M_CLMS_LEAVE_BALANCE: shared key-like column LEAVE_YEAR_ID; direction unresolved from names — pending data verification; matching data type (NUMBER); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_LEAVE_BALANCE -> CREW_BALANCE_UPDATE_BY_SCHEDULER: shared key-like column LEAVE_YEAR_ID; direction unresolved from names — pending data verification; matching data type (NUMBER); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- M_LMS_STATUS_TYPE -> T_CLMS_LEAVE_REQ_MASTER: shared key-like column STATUS_TYPE_ID; name evidence favoured M_LMS_STATUS_TYPE as the owner; this direction is proposed so data verification can settle it; matching data type (NUMBER); REFUTED by data — only 25.0% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: shared key-like column T_ID; direction unresolved from names — pending data verification; matching data type (NUMBER); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: shared key-like column CURRENT_BASE_ID; direction unresolved from names — pending data verification; matching data type (TEXT); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: shared key-like column REQUESTED_BASE_ID; direction unresolved from names — pending data verification; matching data type (TEXT); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_LEAVE_BALANCE -> CREW_BALANCE_UPDATE_BY_SCHEDULER: reference lookup into CREW_BALANCE_UPDATE_BY_SCHEDULER (171 rows); column names align (LEAVE_TYPE_ID ~ LEAVE_YEAR_ID, 78%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_LEAVE_REQ_DETAIL -> CREW_BALANCE_UPDATE_BY_SCHEDULER: reference lookup into CREW_BALANCE_UPDATE_BY_SCHEDULER (171 rows); column names align (LEAVE_TYPE_ID ~ LEAVE_YEAR_ID, 78%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_LEAVE_REQ_MASTER -> CREW_BALANCE_UPDATE_BY_SCHEDULER: reference lookup into CREW_BALANCE_UPDATE_BY_SCHEDULER (171 rows); column names align (LEAVE_TYPE_ID ~ LEAVE_YEAR_ID, 78%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_SPL_APPRECIATION -> M_CLMS_GND_CODE: reference lookup into M_CLMS_GND_CODE (5 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.7% of child rows resolve to a parent
- M_CLMS_CREW -> M_LMS_STATUS_TYPE: reference lookup into M_LMS_STATUS_TYPE (12 rows); column names align (STATUS ~ STATUS_TYPE, 75%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_ATTRITION: reference lookup into T_CLMS_ATTRITION (30 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 4.0% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_ATTRITION: reference lookup into T_CLMS_ATTRITION (30 rows); column names align (DESIGNATION ~ DESIGNATION, 100%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_CREW -> T_CLMS_ATTRITION: reference lookup into T_CLMS_ATTRITION (30 rows); column names align (CATEGORY ~ CATEGORY, 100%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_CREW -> T_CLMS_CREW_SWAP_DETAIL: reference lookup into T_CLMS_CREW_SWAP_DETAIL (48 rows); column names align (STATUS ~ STATUS, 100%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_GND_CODES -> T_CLMS_CREW_SWAP_DETAIL: reference lookup into T_CLMS_CREW_SWAP_DETAIL (48 rows); column names align (LEG_STATUS ~ STATUS, 80%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_SPL_APPRECIATION -> T_CLMS_CREW_SWAP_DETAIL: reference lookup into T_CLMS_CREW_SWAP_DETAIL (48 rows); column names align (ID ~ T_ID, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_SPL_APPRECIATION -> T_CLMS_CREW_TRANSFER_DETAIL: reference lookup into T_CLMS_CREW_TRANSFER_DETAIL (89 rows); column names align (ID ~ T_ID, 100%); REFUTED by data — only 11.9% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_CREW_TRANSFER_DETAIL: reference lookup into T_CLMS_CREW_TRANSFER_DETAIL (89 rows); column names align (STATUS ~ STATUS, 100%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_GND_CODES -> T_CLMS_CREW_TRANSFER_DETAIL: reference lookup into T_CLMS_CREW_TRANSFER_DETAIL (89 rows); column names align (LEG_STATUS ~ STATUS, 80%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
