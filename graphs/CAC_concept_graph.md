# Concept Graph — CAC

*Generated 2026-08-09T17:41:08.443862+00:00*

| Metric | Count |
|---|---|
| Tables | 2 |
| Concepts | 8 |
| Edges | 12 |
| Data-verified joins | 1 |
| Excluded by relevance gate | 0 |

## Tables

| Table | Label | Grain | SCD-2 | Measures | Description |
|---|---|---|---|---|---|
| `CAC_APPRECIATION` | Crew Appreciation | One appreciation letter per crew member per flight | yes | 0 | Stores one Crew Appreciation Centre letter for a specific crew member, linked to the flight it relates to and the appreciation comment text. It provides a recognition record that can be analyzed alongside other crew performance acknowledgements. |
| `M_CAC_CREW` | Appreciation Metrics | One crew member’s appreciation summary | yes | 2 | Holds aggregated metrics per crew member for CAC appreciation letters, including how many appreciations they have received and how many distinct flights were named. It supports performance reporting and ranking based on recognition volume and flight coverage. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `CAC_APPRECIATION`, `M_CAC_CREW` | Links each appreciation record to the crew member being appreciated (hashed crew identifier). Used as the primary join key for crew-level performance and metrics. |
| **Recognition Record Identity** | `CAC_APPRECIATION` | Provides a unique identity for each appreciation letter record so qualitative analysis (comment text) can be tied to the correct row/letter instance. |
| **Flight Context** | `CAC_APPRECIATION`, `M_CAC_CREW` | Captures the flight the appreciation relates to, enabling flight-level slicing and crew coverage across distinct flights. |
| **Appreciation Comment (Qualitative Text)** | `CAC_APPRECIATION` | Stores the free-text comment written in the CAC appreciation letter for qualitative analysis (themes, sentiment, keywords). |
| **Appreciation Date / Time Period** | `CAC_APPRECIATION` | Captures when the appreciation letter was recorded (and supports derived time slicing such as month/quarter if available). Note: only appreciation_date is explicitly available from the source load; no flight date/route/aircraft fields are present in the provided schema. |
| **Crew Role / Position** | `CAC_APPRECIATION` | Stores the role/position context associated with the crew appreciation record, enabling breakdowns by role. |
| **Workflow & Audit Context (Non-Personal)** | `CAC_APPRECIATION` | Captures workflow-related fields for the appreciation record excluding hashed personal data (initiator/approver are not loaded). This concept groups any non-person workflow/audit columns present in CAC_APPRECIATION. |
| **Appreciation Metrics (Aggregated)** | `M_CAC_CREW` | Aggregated recognition volume and flight coverage per crew member for CAC appreciation letters. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `CAC_APPRECIATION` | `M_CAC_CREW` | `IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `M_CAC_CREW` | `CAC_APPRECIATION` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1) Duplicate/near-duplicate concepts: `recognition_volume` and `analytics_ready_metrics` overlap heavily (both use `M_CAC_CREW.appreciation_count`), and `recognition_coverage` over
- concept review round 2: 1) Table coverage: CAC_APPRECIATION is covered, but M_CAC_CREW is only covered by crew_identity, flight_context, crew_role_position, and recognition_metrics. This is fine, but chec
