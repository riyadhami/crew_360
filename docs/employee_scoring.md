# Employee Weighted Scoring Function

## Overview

The `calculate_employee_score` function provides a comprehensive performance scoring system for Indigo Airlines employees based on multiple weighted criteria.

## Formula Components

| Parameter | Weightage | Data Source | Description |
|-----------|-----------|-------------|-------------|
| **Crew Availability** | 40% | CLMS_Raw_Data | Employee attendance and availability for duty, from real CLMS leave records |
| **Poise & Grace (BMI)** | 10% | HR Data | Physical fitness indicator (Body Mass Index) |
| **Appreciation Letters** | 20% | CAC Portal | Positive recognition and commendations |
| **Non-performance Discussions** | 10% | Disciplinary Records | Caution letters and performance warnings |
| **Curative Training/iCoach** | 5% | Training Logs (LMS) | Coaching sessions and performance improvement initiatives |
| **Extra Initiatives/Recognition** | 5% | 6E Clap & Other Recognitions | Special recognition and awards |
| **Passenger NPS Feedback** | 10% | IndigoNPS_Summary | Passenger Net Promoter Score and qualitative feedback attributed to the crew member |

## Scoring Methodology

### 1. Crew Availability (40%)
- Calculated from real CLMS leave records (`CLMS_Raw_Data`, joined on IGA)
- If the crew member's CLMS status is `Released`, `Active = 'N'`, or a Last Working
  Day (`LWD`) is recorded: availability = 0 (crew member has separated)
- Otherwise, formula: `Score = max(100 - (NoOfLeaves * 10), 0)`, based on the leave
  days requested in the crew member's most recent leave record
- 0 leave days taken = 100% score; each leave day reduces score by 10%

### 2. Poise & Grace - BMI (10%)
- Based on ideal BMI range (18.5-24.9)
- Scoring:
  - 18.5-24.9: 100%
  - 17.0-18.4 or 25.0-27.0: 80%
  - 16.0-16.9 or 27.1-30.0: 60%
  - Outside ranges: 40%

### 3. Appreciation Letters (20%)
- Formula: `Score = min(letters * 10, 100)`
- Each appreciation letter adds 10 points
- Maximum score: 100% (10+ letters)

### 4. Non-performance Discussions (10%)
- **Lower is better** (inverse scoring)
- Formula: `Score = max(100 - (caution_letters * 20), 0)`
- 0 caution letters = 100%
- Each caution letter deducts 20%

### 5. Curative Training/iCoach Sessions (5%)
- Formula: `Score = min(sessions * 20, 100)`
- Each coaching session adds 20 points
- Maximum score: 100% (5+ sessions)

### 6. Extra Initiatives/Recognition (5%)
- Scoring based on recognition type:
  - "6E Clap" recognition: 100%
  - Other recognitions: 80%
  - No recognition: 0%

### 7. Passenger NPS Feedback (10%)
- Calculated from `IndigoNPS_Summary` (joined on IGA), the passenger survey response
  attributed to the crew member operating that flight
- Formula: `Score = NPS_Score * 10` (NPS Score is 0-10, scaled to 0-100)
- The response's free-text rationale (`Please share your reasons for the rating`) is
  surfaced verbatim in the component as `feedback`, alongside the `Crew helpfulness`
  sub-rating for context — neither is separately weighted

## Usage

### Command Line Interface

```bash
# Calculate score by employee name
python test_employee_scoring.py --name "Akash Saxena"

# Calculate score by IGA number
python test_employee_scoring.py --iga 62913

# Get JSON output
python test_employee_scoring.py --name "Akash Saxena" --json
```

### Programmatic Usage

```python
from src.agents.Data_Retrieval_Agent_New import DatabaseConnectionManager, calculate_employee_score

# Initialize database manager
db_manager = DatabaseConnectionManager()

# Calculate score by name
result = calculate_employee_score(
    db_manager=db_manager,
    employee_identifier="Akash Saxena",
    identifier_type="name"
)

# Calculate score by IGA
result = calculate_employee_score(
    db_manager=db_manager,
    employee_identifier="62913",
    identifier_type="iga"
)
```

### Using the Data Retrieval Agent Tool

The function is also available as a tool in the Data Retrieval Agent:

```json
{
  "name": "calculate_employee_score",
  "arguments": {
    "employee_identifier": "Akash Saxena",
    "identifier_type": "name"
  }
}
```

## Response Format

```json
{
  "employee_identifier": "Vivek Rao",
  "identifier_type": "name",
  "employee_details": {
    "name": "Vivek Rao",
    "iga": 63584.0,
    "base": "DEL",
    "designation": "CA"
  },
  "components": [
    {
      "name": "Poise & Grace (BMI)",
      "weight": "10%",
      "raw_value": 0.0,
      "normalized_score": 40.0,
      "weighted_score": 4.0,
      "data_source": "HR Data"
    },
    {
      "name": "Appreciation Letters",
      "weight": "20%",
      "raw_value": 2,
      "normalized_score": 20.0,
      "weighted_score": 4.0,
      "data_source": "CAC Portal"
    },
    {
      "name": "Crew Availability",
      "weight": "40%",
      "raw_value": "2.0 leave days (most recent request), balance 7.7 (SPL)",
      "normalized_score": 80.0,
      "weighted_score": 32.0,
      "data_source": "CLMS_Raw_Data",
      "note": "Calculated from recent leave days requested; lower is better"
    },
    {
      "name": "Passenger NPS Feedback",
      "weight": "10%",
      "raw_value": "NPS Score 9.0/10, Crew helpfulness 5/5",
      "normalized_score": 90.0,
      "weighted_score": 9.0,
      "data_source": "IndigoNPS_Summary",
      "note": "Scaled from passenger Net Promoter Score (0-10 -> 0-100)",
      "feedback": "Good service by ground staff and cabin crew."
    }
    // ... other components
  ],
  "total_score": 54.0,
  "summary": {
    "total_score": 54.0,
    "max_possible_score": 100.0,
    "percentage": "54.0%",
    "components_calculated": 5,
    "total_components": 7,
    "missing_data": 2
  },
  "errors": ["Caution letters data not available", "Coaching sessions data not available"]
}
```

## Example Output

```
================================================================================
👤 EMPLOYEE: Vivek Rao
   IGA: 63584.0
   Base: DEL
   Designation: CA

================================================================================
📊 PERFORMANCE SCORE BREAKDOWN
================================================================================

Poise & Grace (BMI) (10%)
  Raw Value: 0.0
  Normalized Score: 40.00/100
  Weighted Score: 4.00
  Source: HR Data

Appreciation Letters (20%)
  Raw Value: 2
  Normalized Score: 20.00/100
  Weighted Score: 4.00
  Source: CAC Portal

Extra Initiatives/Recognition (5%)
  Raw Value: 6e Clap
  Normalized Score: 100.00/100
  Weighted Score: 5.00
  Source: 6E Clap and Recognitions

Crew Availability (40%)
  Raw Value: 2.0 leave days (most recent request), balance 7.7 (SPL)
  Normalized Score: 80.00/100
  Weighted Score: 32.00
  Source: CLMS_Raw_Data
  Note: Calculated from recent leave days requested; lower is better

Passenger NPS Feedback (10%)
  Raw Value: NPS Score 9.0/10, Crew helpfulness 5/5
  Normalized Score: 90.00/100
  Weighted Score: 9.00
  Source: IndigoNPS_Summary
  Note: Scaled from passenger Net Promoter Score (0-10 -> 0-100)
  Passenger Feedback: "Good service by ground staff and cabin crew."

================================================================================
📈 SUMMARY
================================================================================
Total Score: 54.00 / 100.00
Percentage: 54.0%
Components Calculated: 5 / 7

⚠️  Missing Data: 2 component(s)

================================================================================
⚠️  WARNINGS/ERRORS
================================================================================
  • Caution letters data not available
  • Coaching sessions data not available

================================================================================
```

## Data Sources

The function automatically fetches data from multiple tables:

1. **Indigo_HR_Raw_Data**: Employee master data (Name, IGA, Base, Designation)
2. **IJP_Employee_scores**: Performance metrics (BMI, Appreciation Letters, Caution Letters, Coaching Sessions, Recognition, LWP Days)
3. **CLMS_Raw_Data**: Real crew availability data (Status, Active, LWD, NoOfLeaves, Balance, LeaveType)
4. **IndigoNPS_Summary**: Passenger NPS score, per-stage sub-ratings, and free-text feedback attributed to the crew member

All four tables live in the single `HRData` physical SQL Server database and are joined
on `IGA` in one query.

## Error Handling

The function handles missing data gracefully:
- Reports which components have missing data
- Calculates partial scores when some data is unavailable
- Returns detailed error messages for troubleshooting
- Provides suggestions for data correction

## Notes

- **Extensibility**: Additional scoring components can be easily added following the same pattern.
- **Customization**: Scoring formulas can be adjusted based on business requirements.
- **Data Validation**: All raw values are validated before scoring to prevent errors.

## Future Enhancements

1. **Historical Trends**: Track score changes over time
2. **Comparative Analysis**: Compare scores across teams, bases, or designations
3. **Threshold Alerts**: Notify when scores fall below thresholds
4. **Custom Weights**: Allow configuration of weights based on role or department
5. **On-time Performance**: Incorporate flight punctuality metrics

## Related Documentation

- [Data Retrieval Agent Documentation](../src/agents/Data_Retrieval_Agent_New.py)
- [Database Schema Documentation](../db_schemas_csv/)
- [Knowledge Graph Documentation](../docs/cosmos_db_setup.md)
