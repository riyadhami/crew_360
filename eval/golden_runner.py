"""Run the golden retrieval prompts and check properties of what came back.

Two things are being measured, and they are different:

  correctness — did the query answer the question (right tables, non-empty,
                values in a sane range)?
  safety      — did it obey the rules that cannot be checked by looking at the
                answer (SCD-2 currency, verified joins, TRY_CAST on the TEXT
                mark)? A query can return a confident number and still be wrong
                on all three.

The safety checks re-run the validator on whatever SQL the agent actually
executed, so a rule that stops firing shows up as a failure here rather than as
quietly-wrong numbers downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from crew_perf import config
from crew_perf.agents.retrieval import RetrievalAgent
from crew_perf.data.validator import validate

GOLDEN_PATH = config.EVAL_DIR / "golden_queries.yaml"

# The grade scale is whatever PEP_GRADE says it is. Hardcoding it froze the
# A+/A/B+/B scale that was assumed before the real extract arrived, so a correct
# answer on the delivered A/B/C scale was reported as an unexpected value —
# the check was failing the agent for being right about the data.
_FALLBACK_GRADES = {"A", "B", "C"}


def known_grades(executor=None) -> set[str]:
    try:
        from crew_perf.data.executor import get_executor

        executor = executor or get_executor()
        res = executor.execute(
            "SELECT DISTINCT GRADE_CODE FROM PEP_GRADE WHERE P_IS_CURRENT = TRUE LIMIT 20",
            limit=20,
        )
        found = {str(r[0]).strip() for r in res.rows if r[0]}
        return found or _FALLBACK_GRADES
    except Exception:  # noqa: BLE001 - an unreadable reference table is not a test failure
        return _FALLBACK_GRADES


@dataclass
class CaseResult:
    id: str
    question: str
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    sql: str | None = None
    row_count: int = 0
    rejections: int = 0
    answer: str = ""
    unavailable: dict | None = None

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


def load_cases() -> list[dict]:
    return yaml.safe_load(GOLDEN_PATH.read_text())["queries"]


def _check_named(name: str, result) -> list[str]:
    """Case-specific assertions."""
    problems: list[str] = []
    rows = result.rows
    if name == "single_numeric_in_mark_range":
        val = next((v for v in rows[0] if isinstance(v, (int, float))), None)
        if val is None:
            problems.append("no numeric value in the result")
        elif not 0 <= float(val) <= 100:
            problems.append(f"mark {val} outside 0-100")
    elif name == "grades_are_known_bands":
        grades = known_grades()
        found = {str(v) for row in rows for v in row if isinstance(v, str)}
        if not (found & grades):
            problems.append(f"no known grade band in result (saw {sorted(found)[:6]}, "
                            f"PEP_GRADE holds {sorted(grades)})")
        unknown = {g for g in found if g and g not in grades and len(g) <= 3}
        if unknown:
            problems.append(f"unexpected grade values: {sorted(unknown)}")
    elif name == "rates_are_proportions":
        # A pass rate is as correctly returned as 0.87 or as 87. Demanding one
        # scale failed an answer for its presentation rather than its arithmetic.
        #
        # Only rate-like COLUMNS are examined. A first version looked at every
        # number in the result and failed a correct answer because it also
        # returned the question count alongside the rate.
        #
        # One rate-like column has to hold proportions; the answer is free to
        # return counts beside it. Requiring EVERY rate-named column to be in
        # range failed a correct result whose `passed_questions` tally happened
        # to contain the word "pass".
        def _is_proportion(index: int) -> bool:
            values = [float(row[index]) for row in rows
                      if isinstance(row[index], (int, float))]
            return bool(values) and all(0 <= v <= 100 for v in values)

        rate_cols = [
            i for i, c in enumerate(result.columns)
            if any(w in str(c).lower() for w in ("rate", "pass", "pct", "percent", "share"))
        ]
        if not rate_cols:
            problems.append(f"no rate-like column in the result (saw {list(result.columns)})")
        elif not any(_is_proportion(i) for i in rate_cols):
            named = [result.columns[i] for i in rate_cols]
            problems.append(f"no rate-like column holds proportions (checked {named})")
    return problems


def run_case(case: dict, agent: RetrievalAgent) -> CaseResult:
    out = CaseResult(id=case["id"], question=case["question"])
    result = agent.run(case["question"])
    out.sql = result.sql
    out.row_count = len(result.rows)
    out.rejections = len(result.validation_failures)
    out.answer = result.answer
    out.unavailable = result.unavailable

    # ── Unanswerable cases: the right behaviour is to return nothing ──
    if case.get("unanswerable"):
        if not result.unavailable:
            out.failures.append(
                "did not call report_unavailable — an honest refusal must be explicit, "
                "not merely an empty result"
            )
        if result.rows:
            out.failures.append(
                f"returned {len(result.rows)} rows for a question the data cannot "
                f"answer — should have reported the gap instead"
            )
        for table in case.get("forbid_tables", []):
            if result.sql and re.search(rf"\b{table}\b", result.sql, re.IGNORECASE):
                out.failures.append(f"used {table} as a proxy for unavailable data")
        out.passed = not out.failures
        return out

    # ── Did it answer? ──
    if not result.sql:
        out.failures.append("no SQL was executed")
        out.passed = False
        return out

    for table in case.get("must_reference", []):
        if not re.search(rf"\b{table}\b", result.sql, re.IGNORECASE):
            out.failures.append(f"never referenced {table}")

    # Some questions have more than one defensible source table — counting
    # submitted assessments from PEP_SCHEDULER is as correct as from
    # MENTOR_FEEDBACK. Pinning one would fail the agent for being right.
    any_of = case.get("must_reference_any", [])
    if any_of and not any(
        re.search(rf"\b{t}\b", result.sql, re.IGNORECASE) for t in any_of
    ):
        out.failures.append(f"referenced none of {', '.join(any_of)}")
    for table in case.get("forbid_tables", []):
        if re.search(rf"\b{table}\b", result.sql, re.IGNORECASE):
            out.failures.append(f"referenced forbidden table {table}")

    min_rows = case.get("min_rows", 1)
    if len(result.rows) < min_rows:
        out.failures.append(f"got {len(result.rows)} rows, expected >= {min_rows}")
    if "max_rows" in case and len(result.rows) > case["max_rows"]:
        out.failures.append(f"got {len(result.rows)} rows, expected <= {case['max_rows']}")

    if case.get("check") and result.rows:
        out.failures.extend(_check_named(case["check"], result))

    # ── Safety: re-validate the SQL that actually ran ──
    verdict = validate(result.sql, agent.store)
    if not verdict.ok:
        out.failures.append(f"executed SQL fails validation: {verdict.reason()[:120]}")

    out.passed = not out.failures
    return out


def run_all(only: str | None = None) -> list[CaseResult]:
    agent = RetrievalAgent()
    cases = [c for c in load_cases() if not only or c["id"] == only]
    return [run_case(c, agent) for c in cases]
