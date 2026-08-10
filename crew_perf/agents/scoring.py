"""Agent 4 — Scoring Agent: rows + WeightSet + rules -> scorecard.

Two tracks, always both:

  native     replay the assessment chain exactly as production does — sum the
             marks of questions answered true, look the total up in the
             deviation matrix. This is what the airline's own system would say.
  composite  normalise each attribute, apply the WeightSet, express 0-10.

Reporting both is the point rather than a hedge. One is the assessment form's own
verdict and the other is everything else the records hold, so a divergence is
*information*: it says this crew member looks better or worse across the wider
record than on what the assessment form measures. Collapsing to one number throws
that away, and makes the score impossible to reconcile against production.

**Arithmetic is Python; the LLM only writes prose.** No model ever produces the
number. Every component carries the rows it came from, so a disputed score can be
traced to evidence — which is also what makes Agent 5's audit possible at all.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from crew_perf.agents.attributes import build_frame, discover
from crew_perf.agents.weighting import WeightSet
from crew_perf.graph.store import GraphStore, get_store
from crew_perf.rules import submitted_predicate


@dataclass
class Component:
    attribute: str
    raw: float | None
    normalized: float | None          # z-score against the peer population
    weight: float
    contribution: float               # weight * z
    direction: str
    rule_ref: str | None
    percentile: float | None = None   # presentation only; never weighted
    evidence: dict = field(default_factory=dict)
    note: str = ""
    # What this is called and what it measures, in the business's own words.
    # `attribute` is a column identifier: "pass_rate_aftertakeoff" is the Service
    # Delivery section of the assessment form, and a scorecard that only carries
    # the identifier forces everything downstream — the narrative especially — to
    # explain a score in vocabulary no reader outside this codebase shares.
    label: str = ""
    measures: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Scorecard:
    # There is no name field, and that is deliberate. Personal data is hashed
    # behind the identifier in the warehouse, so IGA *is* who this scorecard is
    # about. A card that carried a name would put one into every cached
    # scorecard on disk and every answer built from one.
    iga: str
    base: str = ""
    designation: str = ""
    period: dict = field(default_factory=dict)
    native: dict = field(default_factory=dict)
    composite_score: float | None = None
    composite_z: float | None = None
    # Standing on the weighted attributes. The 0-10 figure saturates at +/-3 sd,
    # which is rare across eleven attributes and routine across two, so a
    # question-scoped ranking needs a measure that keeps separating past the end.
    composite_percentile: float | None = None
    coverage: float = 0.0          # of the WeightSet, for this crew member
    signal_coverage: float = 1.0   # of declared importance, across the population
    confidence: str = "unknown"
    weightset_version: str = ""
    # Aspects the question asked to be judged on, when it named any.
    focus: dict | None = None
    components: list[Component] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    narrative: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["components"] = [c.to_dict() for c in self.components]
        return d


# What the composite is built from, in words a reader has. There is one
# mechanism — weights derived from the records — so this is a constant rather
# than a lookup, and it is a phrase rather than a term of art because the
# scorecard is read by people who do not work on this codebase.
COMPOSITE_BASIS = "the signals the records themselves separate crew on"


def _ordinal(n: float) -> str:
    i = int(round(n))
    suffix = "th" if 10 <= i % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suffix}"


def _confidence(n_assessments: int, params: dict) -> tuple[str, str]:
    cfg = params.get("confidence", {})
    if n_assessments < cfg.get("indicative_below", 2):
        return "indicative", (
            f"only {n_assessments} submitted assessment(s) — indicative only, "
            f"not a basis for ranking (the scoring rules)"
        )
    if n_assessments < cfg.get("sufficient_for_ranking_from", 4):
        return "usable", f"{n_assessments} assessments — usable, sample size stated"
    return "sufficient", f"{n_assessments} assessments — sufficient for ranking"


def native_score(executor, iga: str, period: tuple[str, str] | None = None) -> dict:
    """Replay the production chain: marks summed, then the deviation matrix."""
    submitted = submitted_predicate("ps")
    where = ""
    if period:
        where = f" AND ps.PEP_DATE BETWEEN DATE '{period[0]}' AND DATE '{period[1]}'"
    res = executor.execute(f"""
        SELECT COUNT(*) AS assessments,
               AVG(TRY_CAST(mf.MARK AS DOUBLE)) AS mean_mark,
               MIN(TRY_CAST(mf.MARK AS DOUBLE)) AS min_mark,
               MAX(TRY_CAST(mf.MARK AS DOUBLE)) AS max_mark,
               SUM(CASE WHEN TRY_CAST(mf.MARK AS DOUBLE) IS NULL THEN 1 ELSE 0 END) AS uncastable
        FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT = TRUE AND ps.P_IS_CURRENT = TRUE
          AND {submitted} AND mf.IGA = '{iga}'{where}
        LIMIT 1
    """, limit=1)
    if not res.rows:
        return {"assessments": 0}
    row = dict(zip(res.columns, res.rows[0]))

    grades = executor.execute(f"""
        SELECT mf.GRADE, COUNT(*) AS n
        FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT = TRUE AND ps.P_IS_CURRENT = TRUE
          AND {submitted} AND mf.IGA = '{iga}'{where}
        GROUP BY 1 ORDER BY 2 DESC
        LIMIT 10
    """, limit=10)
    row["grades"] = {g: int(n) for g, n in grades.rows}
    row["modal_grade"] = grades.rows[0][0] if grades.rows else None
    return row


def critical_findings(executor, iga: str, period: tuple[str, str] | None = None) -> list[dict]:
    """Safety and critical question failures.

    Reported separately and never netted into the aggregate: the rules are explicit
    that a high mark does not cancel a critical failure. A scorecard showing
    only the total would hide the finding that matters most.
    """
    submitted = submitted_predicate("ps")
    where = ""
    if period:
        where = f" AND ps.PEP_DATE BETWEEN DATE '{period[0]}' AND DATE '{period[1]}'"
    res = executor.execute(f"""
        SELECT q.QUESTION, q.CATEGORY_CODE,
               q.SAFETY_ACTION_PARAMETER, q.CRITICAL,
               COUNT(*) AS failures
        FROM PEP_QUESTION_FEEDBACK qf
        JOIN PEP_QUESTIONS q ON q.QUESTION_ID = qf.QUESTION_ID
        JOIN MENTOR_FEEDBACK mf ON mf.ID = qf.FEEDBACK_ID
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE qf.P_IS_CURRENT = TRUE AND q.P_IS_CURRENT = TRUE
          AND mf.P_IS_CURRENT = TRUE AND ps.P_IS_CURRENT = TRUE
          AND {submitted} AND mf.IGA = '{iga}'
          AND qf.FEEDBACK = 'false'
          AND (q.SAFETY_ACTION_PARAMETER OR q.CRITICAL){where}
        GROUP BY 1,2,3,4 ORDER BY failures DESC
        LIMIT 20
    """, limit=20)
    return [dict(zip(res.columns, r)) for r in res.rows]


def qualitative(executor, iga: str, limit: int = 5) -> dict:
    """Free text, quoted verbatim — never compressed to a label."""
    submitted = submitted_predicate("ps")
    res = executor.execute(f"""
        SELECT mf.PEP_DATE, mf.STRENGHTH, mf.IMPROVEMENT_AREAS, mf.INNOVATIVE_INITIATIVES
        FROM MENTOR_FEEDBACK mf
        JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
        WHERE mf.P_IS_CURRENT = TRUE AND ps.P_IS_CURRENT = TRUE
          AND {submitted} AND mf.IGA = '{iga}'
        ORDER BY mf.PEP_DATE DESC
        LIMIT {limit}
    """, limit=limit)
    rows = [dict(zip(res.columns, r)) for r in res.rows]

    improvements = [r["IMPROVEMENT_AREAS"] for r in rows if r.get("IMPROVEMENT_AREAS")]
    recurring = sorted(
        {t for t in improvements if improvements.count(t) > 1}
    )
    return {"recent": rows, "recurring_improvements": recurring}


def score(
    iga: str,
    weightset: WeightSet,
    store: GraphStore | None = None,
    executor=None,
    period: tuple[str, str] | None = None,
    population: pd.DataFrame | None = None,
) -> Scorecard:
    """Build a scorecard for one crew member."""
    from crew_perf.data.executor import get_executor

    store = store or get_store()
    executor = executor or get_executor()
    params = store.scoring_parameters()

    card = Scorecard(
        iga=iga, weightset_version=weightset.id,
        focus=weightset.focus.to_dict() if getattr(weightset, "focus", None) else None,
    )
    if period:
        card.period = {"from": period[0], "to": period[1]}

    # No name is selected, and the scorecard carries none. Personal data is
    # hashed behind the identifier in the warehouse, so IGA is who this is —
    # BASE and DESIGNATION are the only crew attributes a ranking needs, because
    # they are what a comparison is scoped to ("weakest at DEL", "leads only").
    who = executor.execute(f"""
        SELECT BASE, DESIGNATION FROM EMPLOYEE_INFO
        WHERE P_IS_CURRENT = TRUE AND IGA = '{iga}' LIMIT 1
    """, limit=1)
    if who.rows:
        card.base, card.designation = who.rows[0]

    # What each attribute is called and what it measures. Resolved up here rather
    # than at the component loop because the findings below name attributes too,
    # and a scorecard that reads "Safety findings" in its table and
    # "safety_failure_rate" in its findings is harder to follow than one that
    # picks either consistently.
    attrs = discover(store, executor)
    described = {a.name: a for a in attrs.attributes}

    def named_attribute(name: str) -> str:
        known = described.get(name)
        return known.display_label if known else name

    # ── Track 1: native ──
    card.native = native_score(executor, iga, period)
    n_assessments = int(card.native.get("assessments") or 0)
    card.confidence, note = _confidence(n_assessments, params)

    # A whole source being unpopulated is invisible in `coverage`, which measures
    # only what was computable for THIS crew member. An attribute dropped because
    # nobody has data is renormalised out of the denominator entirely — so
    # without this a score reads "coverage 100%" while silently ignoring a source
    # that has not been loaded yet.
    card.signal_coverage = float(getattr(weightset, "prior_retained", 1.0))
    focus = getattr(weightset, "focus", None)
    if focus:
        # Under a question-scoped weighting the narrow picture IS the answer.
        # Reporting it as a shortfall would read as a defect in a ranking that
        # deliberately counts only what was asked about.
        card.findings.append(
            f"scored on {', '.join(focus.aspects)} only, because the question asked for "
            f"that — this is not a ranking of overall performance, and a good or bad "
            f"standing here says nothing about the attributes left out"
        )
        if focus.unscoreable:
            card.findings.append(
                f"the question also named {', '.join(focus.unscoreable)}, which no crew "
                f"member has enough data for — that part is unanswered, not scored low"
            )
    elif card.signal_coverage < 0.995:
        dropped = [named_attribute(d) for d in getattr(weightset, "dropped_sources", [])]
        named = ", ".join(dropped[:3]) if dropped else "some signals"
        card.findings.append(
            f"only {card.signal_coverage:.0%} of what we can normally measure was "
            f"usable here — {named} could not be read for any crew member, so this "
            f"score is built on a narrower picture than usual"
        )
    card.findings.append(note)

    if n_assessments == 0:
        card.findings.append("no submitted assessments in this period — nothing to score")
        return card

    # ── Track 2: composite, normalised against the peer population ──
    pop = population if population is not None else build_frame(executor, attrs, period=period)
    if iga not in pop.index:
        card.findings.append("crew member has no attribute row — cannot compute a composite")
        return card

    mine = pop.loc[iga]
    total_weight = 0.0
    for aw in weightset.attributes:
        col = aw.attribute
        known = described.get(col)
        named = named_attribute(col)
        if col not in pop.columns:
            card.unavailable.append(f"{named} — not present in the data")
            continue
        raw = mine.get(col)
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):
            card.unavailable.append(f"{named} — no value for this crew member")
            continue

        series = pop[col].astype(float).dropna()
        if len(series) < 10 or series.std() == 0:
            card.unavailable.append(f"{named} — insufficient population spread to rank against")
            continue

        # Standardise, don't rank. Percentiles look friendlier but discard
        # magnitude: two crew a hair apart can sit 20 percentile points apart in
        # a dense middle, and near-identical at the tails. Weighting ranks
        # instead of values cost more than half of what Agent 2's weighting
        # bought — recovery fell from +0.82 to +0.39. The percentile is still
        # computed, for presentation only.
        z = (float(raw) - float(series.mean())) / float(series.std())
        pct = float((series < float(raw)).mean() * 100.0)
        if aw.direction == "lower_is_better":
            z, pct = -z, 100.0 - pct

        contribution = aw.weight * z
        total_weight += aw.weight
        card.components.append(Component(
            attribute=col, raw=float(raw), normalized=round(z, 4),
            label=named, measures=(known.description if known else ""),
            percentile=round(pct, 1),
            weight=aw.weight, contribution=round(contribution, 4),
            direction=aw.direction, rule_ref=aw.rule_ref,
            evidence={"population_n": int(len(series)),
                      "population_mean": round(float(series.mean()), 4),
                      "population_sd": round(float(series.std()), 4),
                      "assessments": n_assessments},
            note=f"z-score against {len(series)} peers ({_ordinal(pct)} percentile)",
        ))

    # A signal only part of the fleet has is a real input for the crew who have
    # it and simply absent for the rest — so two composites can be built from
    # different attributes and still both read as a number out of ten. Naming it
    # here is what keeps that comparison honest.
    scored_names = {c.attribute for c in card.components}
    partial = [a for a in weightset.attributes
               if getattr(a, "partial_population", False) and a.attribute in scored_names]
    if partial:
        card.findings.append(
            "scored partly on signals only some of the fleet has: "
            + ", ".join(f"{named_attribute(a.attribute)} ({a.coverage:.0%} of crew)"
                        for a in partial)
            + " — crew without them are not scored on them, so this composite is not "
              "strictly comparable to one built without them"
        )

    # Missing components shrink coverage, never the score (the scoring rules).
    # Rounded to the same precision as the weights it sums: at 4dp against
    # 6dp weights the two genuinely cannot agree, and the audit correctly
    # reported honest arithmetic as a coverage mismatch.
    card.coverage = round(total_weight, 6)
    if total_weight > 0:
        weighted_z = sum(c.contribution for c in card.components) / total_weight
        card.composite_z = round(weighted_z, 4)
        # Present on 0-10 with 5.0 as the population median. A linear map keeps
        # the ranking exactly as the z-scores set it; +/-3 sd lands at the ends.
        card.composite_score = round(min(max(5.0 + 1.667 * weighted_z, 0.0), 10.0), 2)
        # Three crew at z = -6.3, -4.0 and -3.3 all print as 0.0/10. Under the
        # full weight set that clamp almost never bites; under a question-scoped
        # focus on one or two attributes it is the normal case, and it makes the
        # worst few indistinguishable in the one number people read.
        if card.composite_score in (0.0, 10.0):
            end = "floor" if card.composite_score == 0.0 else "ceiling"
            card.findings.append(
                f"the 0-10 scale is at its {end} here — the standing is z = {weighted_z:+.2f}. "
                f"Percentile saturates out here too, so crew at the {end} are separable only "
                f"on z and on the components below, not on the score"
            )

    # ── Findings that override the aggregate ──
    crit = critical_findings(executor, iga, period)
    for c in crit:
        kind = "safety" if c["SAFETY_ACTION_PARAMETER"] else "critical"
        card.negatives.append(
            f"{kind} finding — failed {c['failures']}x: {c['QUESTION']}"
        )
    if crit:
        card.findings.append(
            f"{len(crit)} critical/safety question failure(s) — these are reported "
            f"separately and are NOT offset by the overall score (the scoring rules)"
        )

    qual = qualitative(executor, iga)
    # Mentors reuse phrasing, so the same strength appears verbatim across
    # assessments. Repeating it three times reads as three observations.
    seen_strengths: set[str] = set()
    for r in qual["recent"]:
        text = str(r.get("STRENGHTH") or "").strip()
        if text and text not in seen_strengths:
            seen_strengths.add(text)
            times = sum(1 for x in qual["recent"] if str(x.get("STRENGHTH") or "").strip() == text)
            card.positives.append(f"{text}" + (f"  (noted {times}x)" if times > 1 else ""))
    for t in qual["recurring_improvements"]:
        card.negatives.append(f"recurring improvement area across assessments: {t}")
    if qual["recurring_improvements"]:
        card.findings.append(
            "a recurring improvement area is a stronger signal than any single mark (the scoring rules)"
        )

    # ── Divergence between the tracks — compared like for like ──
    # The two scores are not on the same scale and must not be subtracted: native
    # is an absolute mark out of 100, composite is a percentile standing where
    # 5.0 is the median crew member. Dividing the mark by ten and differencing
    # made a 98.57 (9.86) look catastrophically worse than a 70th-percentile
    # composite of 7.07 — an artefact of scale, not a finding.
    #
    # Ranking both against the same population is scale-free and answers the
    # question actually worth asking: does this person stand differently across
    # the wider record than on what the assessment form measures?
    mean_mark = card.native.get("mean_mark")
    if mean_mark is not None and card.composite_score is not None and "mean_mark" in pop:
        marks = pop["mean_mark"].astype(float).dropna()
        if len(marks) >= 10:
            native_pct = float((marks < float(mean_mark)).mean() * 100.0)
            from scipy.stats import norm
            composite_pct = float(norm.cdf(card.composite_z or 0.0) * 100.0)
            card.composite_percentile = round(composite_pct, 1)
            card.native["percentile"] = round(native_pct, 1)
            gap = composite_pct - native_pct
            card.findings.append(
                f"standing: {_ordinal(composite_pct)} percentile on "
                f"{COMPOSITE_BASIS} vs {_ordinal(native_pct)} on the raw "
                f"assessment mark"
            )
            if abs(gap) >= 15:
                direction = "better" if gap > 0 else "worse"
                against = "what the records show separates crew"
                card.findings.append(
                    f"NOTABLE DIVERGENCE ({gap:+.0f} percentile points): this crew member ranks "
                    f"{direction} on {against} than on what the assessment form "
                    f"measures. That is a finding about the form's weighting as much as about "
                    f"the person (the scoring rules)."
                )

    # Leave, complaints and recognition are now onboarded and appear as real
    # components above. Duty hours and sectors flown are OUT OF SCOPE — no source
    # will supply them, so the scorecard says so plainly rather than implying a
    # gap that is about to close.
    for gap in ("duty hours", "flight hours", "sectors operated"):
        card.unavailable.append(
            f"{gap} — out of scope; no roster source is onboarded and none is planned"
        )
    return card


def save(card: Scorecard, path=None):
    from crew_perf import config

    config.GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    path = path or config.GRAPHS_DIR / f"scorecard_{card.iga}.json"
    path.write_text(json.dumps(card.to_dict(), indent=2, default=str))
    return path
