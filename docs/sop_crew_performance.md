# SOP: Crew Performance, Leave & Passenger Feedback Management

## Purpose & Scope
Governs leave administration, weighted performance scoring, passenger feedback integration, Internal Job Posting (IJP) eligibility, and disciplinary escalation for crew designated **CA** (Captain) and **SCA** (Senior Captain).

## Definitions
- **IGA** — unique employee identifier, format `IGA#####`
- **CLMS** — Crew Leave Management System
- **IJP** — Internal Job Posting
- **NPS** — Net Promoter Score, passenger satisfaction, 0–10
- **LWD** — Last Working Day, set only on separation
- **COC** — Certificate of Competency

## Roles
- Base Manager: approves leave requests, reviews caution letters
- HR Operations: maintains employee records, runs eligibility checks
- CLMS Administrator: processes leave balance updates and attrition records
- Performance Review Committee: quarterly scoring review, IJP sign-off

## Leave Management (CLMS)
- Leave types: `SL` Sick Leave, `PL` Privilege Leave, `URTI`, `CL` Casual Leave, `GND` Ground Duty, `SPL` Special Leave.
- A leave request specifies type and duration (`NoOfLeaves`); balance is checked against the leave-year allocation (`LeavesAllotted`).
- Scheduler-driven SL/URTI balance adjustments are logged automatically.
- On separation: `Status = Released`, `Active = 'N'`, `LWD` recorded. This zeroes the crew member's Availability score regardless of prior leave history.
- Base transfers are logged (`Curr_Base`/`Pre_Base`/`Trans_Date`) for leave-year continuity.

## Weighted Performance Scoring
Every crew member's score is 7 components, each normalized 0–100, summing to a weighted total out of 100. Missing components are excluded from the total (partial scores are valid) and reported individually rather than blocking the score.

| Component | Weight | Formula | Source table |
|---|---|---|---|
| Crew Availability | 40% | 0 if released/inactive; else `max(100 - NoOfLeaves*10, 0)` using the most recent leave record | CLMS_Raw_Data |
| Appreciation Letters | 20% | `min(letters*10, 100)` | IJP_Employee_scores |
| Poise & Grace (BMI) | 10% | 100 if 18.5–24.9; 80 if 17.0–18.4 or 25.0–27.0; 60 if 16.0–16.9 or 27.1–30.0; else 40 | Indigo_HR_Raw_Data |
| Non-performance Discussions | 10% | `max(100 - caution_letters*20, 0)` — lower is better | IJP_Employee_scores |
| Passenger NPS Feedback | 10% | `NPS_Score * 10` (NPS is 0–10) | IndigoNPS_Summary |
| Curative Training/iCoach | 5% | `min(sessions*20, 100)` | IJP_Employee_scores |
| Extra Initiatives/Recognition | 5% | 100 for "6E Clap"; 80 for other recognition; 0 for none | IJP_Employee_scores |

All source tables live in one `HRData` SQL Server database and join on `IGA`. Available as a single-employee score or ranked across all crew (best or worst, optionally filtered by base/designation).

## Passenger NPS Feedback Integration
- Post-flight survey covers six journey stages: booking, pre-travel, check-in, boarding, on-board, arrival.
- Up to 4 operating crew are attributed to each response via `IGA Code`.
- Free-text feedback is retained verbatim and used as-is in reviews — never compressed into a qualitative label.
- On-board sub-ratings (crew helpfulness, announcement clarity, cabin cleanliness) provide context but aren't separately weighted.

## IJP Eligibility
- HR evaluates `Eligibility Check as per HR` against LWP days, caution letters, and tenure (`Months in IndiGo`).
- Eligible crew may submit up to 3 ranked preferred locations before `IJP End Date`.
- Ineligible applicants receive a system-generated message citing the specific disqualifying criterion.

## Disciplinary Escalation
Non-Performance Discussion → Caution Letter (−20pts in scoring, each) → Curative Training/iCoach Session (+20pts in scoring, each) → Review Committee escalation if unresolved after 2 cycles.

## Data Sources
| Domain | Table |
|---|---|
| Employee master data | Indigo_HR_Raw_Data |
| Performance metrics | IJP_Employee_scores |
| Leave & attrition | CLMS_Raw_Data |
| Passenger feedback | IndigoNPS_Summary |
