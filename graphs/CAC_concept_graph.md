# Concept Graph — CAC

*Generated 2026-08-11T03:47:51.474481+00:00*

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
| `CAC_APPRECIATION` | Appreciation | One appreciation letter per crew member per flight | yes | 0 | Stores one Crew Appreciation Centre letter for one crew member, including the flight it relates to and the comment text recorded in the spreadsheet export. It acts as a recognition record that can be compared alongside other crew performance recognition sources. |
| `M_CAC_CREW` | Crew Appreciation Stats | One crew member’s appreciation summary | yes | 2 | Provides aggregated counts of Crew Appreciation Centre letters per crew member, including how many appreciations they have and how many distinct flights were named. It supports ranking and trend analysis of recognition at the crew level. |

## Concepts

| Concept | Tables | Description |
|---|---|---|
| **Crew Identity** | `CAC_APPRECIATION`, `M_CAC_CREW` | Identifies the crew member receiving the appreciation/recognition. This is the join key used to aggregate and compare recognition across sources. |
| **Flight Context** | `CAC_APPRECIATION`, `M_CAC_CREW` | Captures which flight the appreciation letter relates to, enabling flight-level analysis and distinct-flight breadth metrics. |
| **Recognition Event Text** | `CAC_APPRECIATION` | Stores the free-text comment recorded in the spreadsheet export for each appreciation letter. |
| **Crew Recognition Metrics (Counts & Distinct Flights)** | `M_CAC_CREW` | Raw recognition volume and breadth for each crew member: total number of appreciation letters and how many distinct flights they cover. |
| **Recognition Density (Appreciations per Distinct Flight)** | `M_CAC_CREW` | Derived density metric to answer: how frequently a crew member is recognized across the flights they appear in. Typically computed as appreciation_count / distinct_flight_count. |
| **Recognition Coverage Ratio (Distinct Flights Covered)** | `M_CAC_CREW` | Derived coverage metric to answer: what proportion of available/observed flights for a crew member are represented by appreciations. Typically computed as distinct_flight_count / total_flights_available (total_flights_available would come from another dataset). |
| **Recognition Ranking & Top-N** | `M_CAC_CREW` | Supports ranking crew members by recognition volume and/or breadth (e.g., top-N by appreciation_count or distinct_flight_count). |
| **Recognition Workflow & Audit (Workflow Fields Present in Source)** | `CAC_APPRECIATION` | Represents the workflow/audit context of the appreciation record as captured in the export, excluding personal-data fields that are hashed/handled elsewhere. This concept is included to support future linkage to workflow events if non-personal workflow attributes exist. |

## Join edges

| Child | Parent | Column | Conf | Coverage | Source | Cardinality |
|---|---|---|---|---|---|---|
| `CAC_APPRECIATION` | `M_CAC_CREW` | `IGA` -> `IGA` | 0.95 | 100% | asserted | many-to-one |
| `M_CAC_CREW` | `CAC_APPRECIATION` | `IGA` -> `IGA` | 0.45 | 100% | inferred | many-to-many |

## Concept review history

*Intermediate feedback from the generate/review loop. Issues listed here were raised during review; anything that survived into the final graph appears under Warnings below.*

- concept review round 1: 1) Duplicate/near-duplicate concepts: `recognition_metrics` (counts from M_CAC_CREW) and `performance_analytics` (derived analytics fields including distinct flight coverage) overl
- concept review round 2: 1) Duplicate/near-duplicate concepts: `crew_recognition_coverage` and `crew_recognition_analytics` overlap heavily. Both reference `M_CAC_CREW.distinct_flight_count` and `M_CAC_CRE
