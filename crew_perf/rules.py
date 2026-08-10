"""Runtime rule resolution — read from the data wherever the data is the authority.

The distinction this module enforces:

  **In the data.**  Grading bands live in `PEP_DEVIATION_MATRIX`. The template a
  designation and fleet map to lives in `PEP_TEMPLATE`. Which mark column applies
  follows from that template. These are facts the source system already records,
  so they are read from it — not restated in config where they would silently
  diverge the moment the real matrix differs from the one we assumed.

  **Not in the data.**  How much each dimension *matters*, and how many
  assessments constitute enough to rank someone, are judgements. Nothing
  observable settles them, so they stay in `crew_perf/policy.py`.

Why this matters more than it looks: `policy.py` previously hardcoded grading
bands of A+ 98-100 / A 95-97 / B+ 92-94. If a real deviation matrix says A+
starts at 96, a system reading config would keep grading by the old boundaries
and report nothing wrong. Every value here therefore carries its provenance, and
a fallback to the declared default is a reported event rather than a silent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_perf import policy


@dataclass
class Rule:
    """A resolved rule plus where it came from."""

    name: str
    value: object
    source: str                      # "data" | "declared"
    detail: str = ""

    @property
    def from_data(self) -> bool:
        return self.source == "data"


@dataclass
class ResolvedRules:
    grading: Rule
    templates: Rule
    counted_status: Rule
    confidence: Rule
    warnings: list[str] = field(default_factory=list)

    def grade_for_mark(self, mark: float, template_id: int | None = None) -> str | None:
        """Look a mark up in the bands that actually govern it.

        Resolved as a threshold — the highest band whose lower bound the mark
        reaches — rather than a closed interval. Real marks are fractional, so
        a total of 91.3 sits in the gap between an integer band ending at 91 and
        the next starting at 92, and a closed-interval lookup returns no grade
        at all for it.
        """
        bands = self.grading.value
        scoped = [b for b in bands
                  if template_id is None or b.get("template_id") in (None, template_id)]
        candidates = [b for b in (scoped or bands) if mark >= b["start"]]
        if not candidates:
            return None
        return max(candidates, key=lambda b: b["start"])["grade"]

    def mark_column_for(self, designation: str, fleet: str | int) -> str | None:
        """Which of the four mark columns applies to this role and fleet."""
        for t in self.templates.value:
            if t["designation"] == designation and str(t["fleet"]) == str(fleet):
                return t["mark_column"]
        return None

    def provenance(self) -> dict[str, str]:
        return {
            r.name: f"{r.source} — {r.detail}"
            for r in (self.grading, self.templates, self.counted_status, self.confidence)
        }


# Mark column by template ordinal. This mapping is an assumption about the
# schema's four parallel mark columns, not a fact any table records — the ATR
# pair in particular is inferred from column naming alone.
_MARK_COLUMN_BY_FLEET_ROLE = {
    ("CA", 1): "MARKS", ("LD", 1): "LD_MARKS",
    ("CA", 2): "ATRCA", ("LD", 2): "ATRLD",
}


def _grading_from_data(executor) -> Rule | None:
    try:
        res = executor.execute(
            "SELECT TEMPLATE_ID, GRADE, START_RANGE, END_RANGE "
            "FROM PEP_DEVIATION_MATRIX "
            "WHERE P_IS_CURRENT = TRUE AND IS_ACTIVE = TRUE "
            "ORDER BY TEMPLATE_ID, START_RANGE DESC",
            limit=1000,
        )
    except Exception:  # noqa: BLE001 - absent table falls back, does not crash
        return None
    if not res.rows:
        return None

    bands = [
        {"template_id": int(t), "grade": str(g), "start": float(lo), "end": float(hi)}
        for t, g, lo, hi in res.rows
    ]
    distinct = {(b["grade"], b["start"], b["end"]) for b in bands}
    return Rule(
        "grading", bands, "data",
        f"{len(bands)} bands across {len({b['template_id'] for b in bands})} templates "
        f"({len(distinct)} distinct) from PEP_DEVIATION_MATRIX",
    )


def _templates_from_data(executor) -> Rule | None:
    try:
        res = executor.execute(
            "SELECT TEMPLATE_ID, TEMPLATE_CODE, DESIGNATION_CODE, FLIGHT_TYPE "
            "FROM PEP_TEMPLATE WHERE P_IS_CURRENT = TRUE AND ACTIVE = TRUE "
            "ORDER BY TEMPLATE_ID",
            limit=200,
        )
    except Exception:  # noqa: BLE001
        return None
    if not res.rows:
        return None

    out, unmapped = [], []
    for tid, code, desg, fleet in res.rows:
        mark_col = _MARK_COLUMN_BY_FLEET_ROLE.get((str(desg), int(fleet)))
        if mark_col is None:
            unmapped.append(str(code))
            continue
        out.append({
            "template_id": int(tid), "template_code": str(code),
            "designation": str(desg), "fleet": int(fleet), "mark_column": mark_col,
        })
    if not out:
        return None
    detail = f"{len(out)} templates from PEP_TEMPLATE"
    if unmapped:
        detail += f"; no mark column known for {', '.join(unmapped)}"
    return Rule("templates", out, "data", detail)


def resolve(executor=None) -> ResolvedRules:
    """Resolve every rule, preferring the database over declared defaults."""
    from crew_perf.data.executor import get_executor

    executor = executor or get_executor()
    warnings: list[str] = []

    grading = _grading_from_data(executor)
    if grading is None:
        grading = Rule(
            "grading",
            [{"template_id": None, **b} for b in policy.GRADING["bands"]],
            "declared",
            "PEP_DEVIATION_MATRIX unavailable; using the declared bands",
        )
        warnings.append(
            "grading bands could not be read from PEP_DEVIATION_MATRIX — falling back to "
            "the declared defaults, which may not match how this data was actually graded"
        )

    templates = _templates_from_data(executor)
    if templates is None:
        templates = Rule(
            "templates",
            [{"template_id": None, "template_code": v["template_code"],
              "designation": k.split("|")[0], "fleet": k.split("|")[1],
              "mark_column": v["mark_column"]}
             for k, v in policy.TEMPLATE_SELECTION["mapping"].items()],
            "declared",
            "PEP_TEMPLATE unavailable; using the declared mapping",
        )
        warnings.append(
            "template selection could not be read from PEP_TEMPLATE — falling back to the "
            "declared mapping"
        )

    return ResolvedRules(
        grading=grading,
        templates=templates,
        counted_status=Rule(
            "counted_status", policy.ASSESSMENT_VALIDITY["counted_status"], "declared",
            "no status lookup table in the delivered schema; 2 = Submitted is an assumption",
        ),
        confidence=Rule(
            "confidence", policy.CONFIDENCE, "declared",
            "sample-size thresholds are a policy choice, not a fact in the data",
        ),
        warnings=warnings,
    )


_cached: ResolvedRules | None = None


def get(executor=None, fresh: bool = False) -> ResolvedRules:
    global _cached
    if _cached is None or fresh:
        _cached = resolve(executor)
    return _cached


def submitted_predicate(alias: str = "ps") -> str:
    """The SQL predicate for a countable assessment.

    Emitted from one place so the literal cannot drift between the rule and the
    dozen queries that filter on it.
    """
    return f"{alias}.STATUS = {policy.ASSESSMENT_VALIDITY['counted_status']}"
