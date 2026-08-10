"""Agent 5 — Evaluator: audits a scorecard.

**Score audit**, per scorecard and fully deterministic. Checks the things that make
a score defensible rather than merely plausible: that the arithmetic reproduces,
that every claim traces to evidence, that stated coverage is honest, and — the
one that matters most — that the *ranking is stable*. A score built on weights
that could plausibly have been slightly different, and which would then reorder
the population, is not a ranking; it is a coin flip with a decimal point.

Deliberately not an LLM critic. Everything here is recomputation and comparison,
because an auditor that can be talked out of a finding is not an auditor.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np

from crew_perf import config
from crew_perf.agents.scoring import Scorecard
from crew_perf.agents.weighting import WeightSet
from crew_perf.graph.store import GraphStore, get_store

PERTURBATION = 0.20      # ±20% on each weight (PLAN.md §5, Agent 5)
PERTURBATION_TRIALS = 200
STABLE_TOP_N = 10
RANK_STABILITY_FLOOR = 0.90


@dataclass
class Finding:
    check: str
    severity: str            # error | warning | info
    message: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditResult:
    subject: str
    passed: bool = True
    confidence_downgrade: str | None = None
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, severity: str, message: str, **detail) -> None:
        self.findings.append(Finding(check, severity, message, detail))
        if severity == "error":
            self.passed = False

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def to_dict(self) -> dict:
        return {
            "subject": self.subject, "passed": self.passed,
            "confidence_downgrade": self.confidence_downgrade,
            "findings": [f.to_dict() for f in self.findings],
        }


# ─── 5a: score audit ────────────────────────────────────────────────────────


def audit_score(
    card: Scorecard, weightset: WeightSet, store: GraphStore | None = None,
    population=None,
) -> AuditResult:
    """Recompute and cross-check a scorecard. No LLM involved."""
    store = store or get_store()
    audit = AuditResult(subject=card.iga)

    if card.composite_score is None:
        audit.add("computable", "info", "no composite score to audit")
        return audit

    # ── Arithmetic reproduces ──
    if card.coverage > 0:
        recomputed_z = sum(c.contribution for c in card.components) / card.coverage
        expected = min(max(5.0 + 1.667 * recomputed_z, 0.0), 10.0)
        if abs(expected - card.composite_score) > 0.01:
            audit.add("arithmetic", "error",
                      f"composite {card.composite_score} does not reproduce from its "
                      f"components (recomputed {expected:.2f})",
                      recomputed=round(expected, 3))
    for c in card.components:
        if abs(c.contribution - c.weight * (c.normalized or 0)) > 1e-3:
            audit.add("arithmetic", "error",
                      f"{c.attribute}: contribution does not equal weight x z")

    # ── Weights match the named WeightSet ──
    declared = weightset.weights()
    if card.weightset_version != weightset.id:
        audit.add("provenance", "error",
                  f"scorecard cites {card.weightset_version} but was audited against "
                  f"{weightset.id}")
    for c in card.components:
        if c.attribute not in declared:
            audit.add("provenance", "error",
                      f"{c.attribute} is not in the WeightSet at all")
        elif abs(declared[c.attribute] - c.weight) > 1e-6:
            audit.add("provenance", "error",
                      f"{c.attribute} weighted {c.weight} but the WeightSet says "
                      f"{declared[c.attribute]}")

    # ── Coverage honesty ──
    # Tolerance tracks the 6dp weight rounding, not float epsilon: a tighter
    # bound reports correct arithmetic as a coverage lie.
    if abs(sum(c.weight for c in card.components) - card.coverage) > 1e-5:
        audit.add("coverage", "error", "stated coverage does not match component weights")
    if card.coverage < 0.7:
        audit.add("coverage", "warning",
                  f"score computed from {card.coverage:.0%} of the weight — a "
                  f"{card.composite_score} from partial coverage is not comparable to a "
                  f"full one", coverage=card.coverage)

    # ── Every claim is grounded ──
    for c in card.components:
        if not c.evidence.get("population_n"):
            audit.add("grounding", "error", f"{c.attribute} carries no evidence")
        # A component must cite a rule that exists, so a number can always be
        # traced back to what produced it.
        from crew_perf import policy

        if c.rule_ref and not policy.rule_exists(c.rule_ref):
            audit.add("grounding", "error",
                      f"{c.attribute} cites rule {c.rule_ref!r}, which does not exist")

    # ── Sample size ──
    n = int(card.native.get("assessments") or 0)
    if n < 2:
        audit.add("sample_size", "warning",
                  f"scored on {n} assessment(s) — not a basis for ranking (the confidence rule)")
        audit.confidence_downgrade = "indicative"
    elif n < 4 and card.confidence == "sufficient":
        audit.add("sample_size", "error",
                  f"confidence reported as 'sufficient' on {n} assessments")

    # ── Rank stability: the check that decides whether this is a ranking ──
    if population is not None:
        stability = rank_stability(weightset, population)
        audit.add("rank_stability",
                  "warning" if stability["agreement"] < RANK_STABILITY_FLOOR else "info",
                  f"top-{STABLE_TOP_N} membership survives ±{int(PERTURBATION*100)}% weight "
                  f"perturbation {stability['agreement']:.0%} of the time",
                  **stability)
        if stability["agreement"] < RANK_STABILITY_FLOOR:
            audit.confidence_downgrade = "unstable_ranking"

    # ── Divergence between the tracks is a finding, not an error ──
    if "percentile" in card.native and card.composite_z is not None:
        from scipy.stats import norm

        gap = float(norm.cdf(card.composite_z) * 100.0) - float(card.native["percentile"])
        if abs(gap) >= 15:
            audit.add("track_divergence", "info",
                      f"{gap:+.0f} percentile points between the composite and the raw mark — "
                      f"the assessment form and the wider record disagree about this "
                      f"crew member", gap=round(gap, 1))

    # ── Critical failures must be surfaced, not absorbed ──
    safety = [n for n in card.negatives if "safety finding" in n or "critical" in n.lower()]
    if safety and not any("NOT offset" in f for f in card.findings):
        audit.add("criticality", "error",
                  "safety/critical failures present but the scorecard does not state "
                  "that they are not offset by the overall score (the criticality rule)")
    return audit


def rank_stability(weightset: WeightSet, population, trials: int = PERTURBATION_TRIALS,
                   top_n: int = STABLE_TOP_N, seed: int = 7) -> dict:
    """Would the top-N change if the weights were slightly different?

    Weights are a judgement with error bars, not measurements. If perturbing them
    within plausible bounds reshuffles who is in the top ten, the ordering is an
    artefact of the exact numbers chosen — and presenting it as a ranking would
    be misleading regardless of how carefully each weight was derived.
    """
    rng = np.random.default_rng(seed)
    cols = [a.attribute for a in weightset.attributes if a.attribute in population.columns]
    if not cols:
        return {"agreement": 1.0, "trials": 0, "note": "no attributes to perturb"}

    frame = population[cols].astype(float)
    z = (frame - frame.mean()) / frame.std()
    for a in weightset.attributes:
        if a.attribute in z.columns and a.direction == "lower_is_better":
            z[a.attribute] = -z[a.attribute]
    z = z.fillna(0.0)

    base_w = np.array([a.weight for a in weightset.attributes if a.attribute in cols])
    baseline = set((z @ (base_w / base_w.sum())).nlargest(top_n).index)

    overlaps = []
    for _ in range(trials):
        jitter = rng.uniform(1 - PERTURBATION, 1 + PERTURBATION, size=len(base_w))
        w = base_w * jitter
        top = set((z @ (w / w.sum())).nlargest(top_n).index)
        overlaps.append(len(top & baseline) / top_n)

    return {
        "agreement": float(np.mean(overlaps)),
        "worst_trial": float(np.min(overlaps)),
        "trials": trials,
        "top_n": top_n,
        "perturbation": PERTURBATION,
    }




# ─── Persistence ────────────────────────────────────────────────────────────


def save(audit: AuditResult, name: str | None = None):
    config.GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    slug = name or audit.subject.replace(" ", "_").lower()
    path = config.GRAPHS_DIR / f"audit_{slug}.json"
    path.write_text(json.dumps(
        {**audit.to_dict(), "generated_at": datetime.now(timezone.utc).isoformat()},
        indent=2, default=str,
    ))
    return path
