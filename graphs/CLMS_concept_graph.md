# Concept Graph — CLMS

*Generated 2026-08-11T03:43:54.834491+00:00*

| Metric | Count |
|---|---|
| Tables | 15 |
| Concepts | 13 |
| Edges | 83 |
| Data-verified joins | 21 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | Balance Update | One scheduler balance update per crew per leave year | yes | 7 | Records the scheduler-driven update of a crew member’s leave balances for a specific leave year, capturing old vs updated balances for SL and URTI. Each row represents one balance update event for one crew and leave year, including eligibility context and whether the update was revoked. |
| `M_CLMS_CREW` | Crew Master | One crew profile version per crew member | yes | 10 | Holds the crew master profile used by CLMS, including designation, base, employment dates, and current status/activation. Each row represents one crew member’s master record version, supporting downstream leave and related crew-context decisions. |
| `M_CLMS_GND_CODE` | Ground Code | One ground-duty code reference | yes | 0 | Provides the reference mapping for ground-duty codes used in CLMS. Each row represents one ground-duty code value that can be referenced by crew leave/ground-duty related logic. |
| `M_CLMS_LEAVE_BALANCE` | Leave Balance | One crew leave-type balance period | yes | 2 | Stores the leave balance per crew member and leave type over a validity period, including the current balance and the last TMU balance snapshot. Each row represents one crew’s balance for a specific leave type within a leave year and date range. |
| `M_DESIGNATIONS` | Designation | One designation reference | yes | 0 | Reference table for crew designations. Each row represents one designation label that can be used to interpret crew role context in CLMS. |
| `M_LMS_LEAVE_TYPE` | Leave Type | One leave type definition | yes | 1 | Defines the catalog of leave types available in the system, including descriptive text and ordering metadata. Each row represents one leave type used to categorize leave balances and leave-related processing for crew. |
| `M_LMS_STATUS_TYPE` | Leave Status Type | One status type definition | yes | 0 | Defines the master list of leave-management status types used to categorize the state of leave requests or leave-related workflows. It provides the standardized dimension values that other leave records reference. |
| `M_UMS_STATION` | Station | One station (base) master record | yes | 3 | Represents an operational base/station with identifying codes, location context, and activation flag. It supports crew base/region context used when evaluating or processing crew leave and related operational movements. |
| `T_CLMS_ATTRITION` | Attrition Record | One crew attrition event | yes | 2 | Stores one attrition event for a specific crew member for a given month and year, including designation, reason, and effective dates (DOJ/DOL). It is used to reflect crew loss/exit context that can affect leave planning and operational availability. |
| `T_CLMS_CREW_SWAP_DETAIL` | Crew Swap Detail | One crew swap transaction detail | yes | 6 | Captures one crew swap application/transaction detail for a crew member, including swap partner, bases, priority, status, and key timeline dates. It records the operational relocation/swap context that can change where a crew member is assigned around leave and scheduling. |
| `T_CLMS_CREW_TRANSFER_DETAIL` | Crew Transfer Detail | One crew transfer transaction detail | yes | 10 | Stores one crew transfer request detail for a crew member, including current vs requested base, reason, priority, approval/relocation dates, and swap-related fields when applicable. It provides the movement context that can influence leave eligibility and operational planning. |
| `T_CLMS_GND_CODES` | Ground Duty Code | One ground-duty activity segment | yes | 2 | Represents one ground-duty leg/activity code assigned to an employee over a time window, with leg status and approval/payability indicators. It supports operational context for crew availability and scheduling decisions that interact with leave management. |
| `T_CLMS_LEAVE_REQ_DETAIL` | Leave Request Detail | One leave-type line item | yes | 3 | Stores the per-type breakdown of a crew member’s leave request, including the number of leave units and the leave balance/current leave values for that leave type over a date range. Each row represents one leave-type line item within a leave request. |
| `T_CLMS_LEAVE_REQ_MASTER` | Leave Request | One leave request per crew | yes | 1 | Represents the main leave request record for a specific crew member, including request timing, leave type, workflow status, action/audit fields, and associated ground-duty code. Each row is one leave request instance that ties to one or more leave detail lines. |
| `T_SPL_APPRECIATION` | Special Appreciation | One appreciation record per crew/base | yes | 1 | Tracks special appreciation/recognition for a crew member for a given base over a validity period, including the expected future appreciation date and whether an award has been received. Each row represents one appreciation record tied to a crew member and base. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `M_CLMS_CREW`, `CREW_BALANCE_UPDATE_BY_SCHEDULER`, `T_CLMS_ATTRITION`, `T_CLMS_CREW_SWAP_DETAIL`, `T_CLMS_CREW_TRANSFER_DETAIL`, `T_CLMS_GND_CODES`, `T_CLMS_LEAVE_REQ_MASTER`, `T_CLMS_LEAVE_REQ_DETAIL`, `T_SPL_APPRECIATION` | Core identity and master profile linkage used by CLMS to associate leave, balances, attrition, transfers/swaps, ground-duty, and recognitions to a specific crew member. Includes crew identifiers and master-version context. |
| **Employment & Crew Status Context** | `M_CLMS_CREW`, `T_CLMS_ATTRITION`, `M_DESIGNATIONS` | Crew employment dates and current status/activation context used to interpret leave eligibility and operational availability. Also includes designation context when present in the crew master. |
| **Base / Station Context** | `M_UMS_STATION`, `T_CLMS_CREW_TRANSFER_DETAIL`, `T_CLMS_CREW_SWAP_DETAIL`, `T_SPL_APPRECIATION`, `T_CLMS_LEAVE_REQ_MASTER` | Operational base/station context used across leave requests, transfers/swaps, and special appreciation. Provides station identity and activation context. |
| **Leave Type Catalog** | `M_LMS_LEAVE_TYPE`, `T_CLMS_LEAVE_REQ_DETAIL`, `M_CLMS_LEAVE_BALANCE`, `CREW_BALANCE_UPDATE_BY_SCHEDULER` | Reference catalog of leave types used to categorize balances and leave request line items, including ordering/metadata. |
| **Ground Duty Code Context** | `M_CLMS_GND_CODE`, `T_CLMS_GND_CODES`, `T_CLMS_LEAVE_REQ_MASTER` | Ground-duty code assignments and reference mapping used to connect operational duty context with leave requests and crew availability. Includes payability/approval indicators for ground-duty segments. |
| **Leave Request Master & Workflow Status** | `T_CLMS_LEAVE_REQ_MASTER`, `M_LMS_STATUS_TYPE` | The core leave request record tying a crew to a leave request instance, including request timing, workflow status, and master-level audit/action fields. This concept unifies the master attributes and decision/workflow state (no separate audit projection). |
| **Leave Request Line Items & Balance Coverage** | `T_CLMS_LEAVE_REQ_DETAIL`, `M_CLMS_LEAVE_BALANCE` | Per-type breakdown of a leave request, including units requested and the balance/current leave values covered by each line item over its date range. Captures how each request line consumes/uses balance at the time of request (as represented by stored balance fields). |
| **Leave Balance Master Records** | `M_CLMS_LEAVE_BALANCE` | Stored leave balances per crew, leave type, and validity period, including current balance and last TMU snapshot. Provides the accounting baseline used for leave processing and reporting. |
| **Scheduler-Driven Balance Updates & Revocations** | `CREW_BALANCE_UPDATE_BY_SCHEDULER` | Audit trail of automated balance updates for a crew and leave year, capturing old vs updated values for specific leave types (e.g., SL and URTI), eligibility context, and whether the update was revoked. |
| **Crew Attrition Events** | `T_CLMS_ATTRITION` | Monthly/yearly attrition events for crew members, including designation, reason coding, and effective dates (DOJ/DOL). Used to model crew exit context affecting leave planning and availability. |
| **Crew Base Transfers & Swaps** | `T_CLMS_CREW_TRANSFER_DETAIL`, `T_CLMS_CREW_SWAP_DETAIL` | Operational movement transactions that relocate crew between bases, including transfer and swap details such as partner crew, priority, status, and key timeline dates. Used to interpret base context around leave and availability. |
| **Ground-Duty Segments (Operational Availability)** | `T_CLMS_GND_CODES` | Time-windowed ground-duty code segments assigned to crew, including leg status and approval/payability indicators. Supports operational availability context that may interact with leave requests. |
| **Special Appreciation / Recognition** | `T_SPL_APPRECIATION` | Recognition records tied to a crew member and base over a validity period, including expected future appreciation date and whether the award has been received. |

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
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 97% | inferred | many-to-many |
| `M_CLMS_CREW` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 99% | inferred | many-to-many |
| `M_CLMS_LEAVE_BALANCE` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 99% | inferred | many-to-many |
| `T_CLMS_ATTRITION` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 96% | inferred | many-to-many |
| `T_SPL_APPRECIATION` | `T_CLMS_LEAVE_REQ_MASTER` | `CREW_ID` -> `CREW_ID` | 0.80 | 99% | inferred | many-to-many |
| `M_CLMS_CREW` | `M_LMS_LEAVE_TYPE` | `LEAVE_TYPE_ID` -> `LEAVE_TYPE_ID` | 0.75 | — | inferred | many-to-one |
| `CREW_BALANCE_UPDATE_BY_SCHEDULER` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `M_CLMS_CREW` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
| `T_CLMS_ATTRITION` | `M_CLMS_LEAVE_BALANCE` | `CREW_ID` -> `CREW_ID` | 0.45 | 100% | inferred | many-to-many |
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

- concept review round 1: 1) Duplicate/near-duplicate concepts: `leave_request_header` and `leave_request_line_items` are strongly related but not duplicates; however, `leave_request_header` overlaps semant
- concept review round 2: 1. Duplicate/near-duplicate concepts: `leave_request_core` and `leave_request_audit_and_decision` are both sourced only from `T_CLMS_LEAVE_REQ_MASTER` and together cover master att

## Rejected join candidates

- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 4.7% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 5.1% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 7.9% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 52.3% of child rows resolve to a parent
- M_CLMS_CREW -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 27.3% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 3.1% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.0% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 10.5% of child rows resolve to a parent
- M_CLMS_CREW -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 53.1% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 27.3% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 3.1% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.0% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 10.5% of child rows resolve to a parent
- M_CLMS_LEAVE_BALANCE -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 53.1% of child rows resolve to a parent
- T_CLMS_ATTRITION -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 41.7% of child rows resolve to a parent
- T_CLMS_ATTRITION -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 4.2% of child rows resolve to a parent
- T_CLMS_ATTRITION -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 8.3% of child rows resolve to a parent
- T_CLMS_ATTRITION -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 54.2% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 23.4% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 2.1% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 14.9% of child rows resolve to a parent
- T_CLMS_CREW_SWAP_DETAIL -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 42.6% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 20.7% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 2.4% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 8.5% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 46.3% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 28.1% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 2.9% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 6.2% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 10.6% of child rows resolve to a parent
- T_CLMS_LEAVE_REQ_MASTER -> T_SPL_APPRECIATION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 49.3% of child rows resolve to a parent
- T_SPL_APPRECIATION -> CREW_BALANCE_UPDATE_BY_SCHEDULER: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 26.2% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_ATTRITION: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 2.7% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_CREW_SWAP_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 3.9% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_CREW_TRANSFER_DETAIL: registry-declared identity key CREW_ID for source CLMS; master table resolved by uniqueness during data verification; matching data type (TEXT); REFUTED by data — only 9.7% of child rows resolve to a parent
- CREW_BALANCE_UPDATE_BY_SCHEDULER -> M_CLMS_LEAVE_BALANCE: shared key-like column LEAVE_YEAR_ID; direction unresolved from names — pending data verification; matching data type (NUMBER); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_LEAVE_BALANCE -> CREW_BALANCE_UPDATE_BY_SCHEDULER: shared key-like column LEAVE_YEAR_ID; direction unresolved from names — pending data verification; matching data type (NUMBER); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- M_LMS_STATUS_TYPE -> T_CLMS_LEAVE_REQ_MASTER: shared key-like column STATUS_TYPE_ID; name evidence favoured M_LMS_STATUS_TYPE as the owner; this direction is proposed so data verification can settle it; matching data type (NUMBER); REFUTED by data — only 25.0% of child rows resolve to a parent
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: shared key-like column T_ID; direction unresolved from names — pending data verification; matching data type (NUMBER); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: shared key-like column CURRENT_BASE_ID; direction unresolved from names — pending data verification; matching data type (TEXT); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_CREW_TRANSFER_DETAIL -> T_CLMS_CREW_SWAP_DETAIL: shared key-like column REQUESTED_BASE_ID; direction unresolved from names — pending data verification; matching data type (TEXT); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_LEAVE_BALANCE -> CREW_BALANCE_UPDATE_BY_SCHEDULER: reference lookup into CREW_BALANCE_UPDATE_BY_SCHEDULER (214 rows); column names align (LEAVE_TYPE_ID ~ LEAVE_YEAR_ID, 78%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_LEAVE_REQ_DETAIL -> CREW_BALANCE_UPDATE_BY_SCHEDULER: reference lookup into CREW_BALANCE_UPDATE_BY_SCHEDULER (214 rows); column names align (LEAVE_TYPE_ID ~ LEAVE_YEAR_ID, 78%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_LEAVE_REQ_MASTER -> CREW_BALANCE_UPDATE_BY_SCHEDULER: reference lookup into CREW_BALANCE_UPDATE_BY_SCHEDULER (214 rows); column names align (LEAVE_TYPE_ID ~ LEAVE_YEAR_ID, 78%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_SPL_APPRECIATION -> M_CLMS_GND_CODE: reference lookup into M_CLMS_GND_CODE (5 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 0.7% of child rows resolve to a parent
- M_CLMS_CREW -> M_LMS_STATUS_TYPE: reference lookup into M_LMS_STATUS_TYPE (12 rows); column names align (STATUS ~ STATUS_TYPE, 75%); REFUTED by data — only 0.0% of child rows resolve to a parent
- T_SPL_APPRECIATION -> T_CLMS_ATTRITION: reference lookup into T_CLMS_ATTRITION (24 rows); column names align (ID ~ ID, 100%); REFUTED by data — only 3.5% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_ATTRITION: reference lookup into T_CLMS_ATTRITION (24 rows); column names align (DESIGNATION ~ DESIGNATION, 100%); DEGENERATE — the key has only 2 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_CREW -> T_CLMS_ATTRITION: reference lookup into T_CLMS_ATTRITION (24 rows); column names align (CATEGORY ~ CATEGORY, 100%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- M_CLMS_CREW -> T_CLMS_CREW_SWAP_DETAIL: reference lookup into T_CLMS_CREW_SWAP_DETAIL (47 rows); column names align (STATUS ~ STATUS, 100%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_GND_CODES -> T_CLMS_CREW_SWAP_DETAIL: reference lookup into T_CLMS_CREW_SWAP_DETAIL (47 rows); column names align (LEG_STATUS ~ STATUS, 80%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_SPL_APPRECIATION -> T_CLMS_CREW_SWAP_DETAIL: reference lookup into T_CLMS_CREW_SWAP_DETAIL (47 rows); column names align (ID ~ T_ID, 100%); DEGENERATE — the key has only 0 distinct value(s), so this join multiplies rows rather than relating them
- T_SPL_APPRECIATION -> T_CLMS_CREW_TRANSFER_DETAIL: reference lookup into T_CLMS_CREW_TRANSFER_DETAIL (82 rows); column names align (ID ~ T_ID, 100%); REFUTED by data — only 11.8% of child rows resolve to a parent
- M_CLMS_CREW -> T_CLMS_CREW_TRANSFER_DETAIL: reference lookup into T_CLMS_CREW_TRANSFER_DETAIL (82 rows); column names align (STATUS ~ STATUS, 100%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
- T_CLMS_GND_CODES -> T_CLMS_CREW_TRANSFER_DETAIL: reference lookup into T_CLMS_CREW_TRANSFER_DETAIL (82 rows); column names align (LEG_STATUS ~ STATUS, 80%); DEGENERATE — the key has only 1 distinct value(s), so this join multiplies rows rather than relating them
