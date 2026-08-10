"""Agent 7 — Dynamic Scoring Mechanism Agent.

Designs the scoring mechanism for crew performance from nothing but the data
points the system can actually compute, the relationships the knowledge graph
holds, and what those things mean. **No declared statement of importance is read.**

This is the only mechanism. There used to be a second one that started from a
business's written list of what matters and let the data adjust it, and running
both was the point: where they agreed the declared weighting was corroborated,
where they disagreed the disagreement was the finding. It is gone, and what it
cost was not the second opinion — it was that every score had to say which
mechanism produced it, every comparison had to check the two were on the same
scale, and nobody outside this codebase could act on a number without holding
both in their head.

### Why this cannot be "ask the model for weights"

A model asked to weight crew performance returns the industry's conventional
wisdom — safety first, then service — which is a business judgement smuggled in
through the training data, and precisely what this agent was asked not to do. So
the LLM is used for the one thing it is genuinely better at than any statistic,
and nothing else:

    SEMANTICS   what does this number mean, which direction is good, which
                latent construct is it measuring, and how much of it is
                actually about this person rather than about their flight?

    ARITHMETIC  how much it counts. Derived from measured properties only —
                coverage, discrimination, reliability, and redundancy against
                every other signal.

### How weight is derived

1. **Discover** every per-crew number the system can compute, across all four
   sources, and profile each against the population.
2. **Read** each one through the graph: its table's grain and description, the
   concepts it belongs to, the joins that attribute it to a crew member.
3. **Cluster** the signals by measured correlation. Seven assessment categories
   that move together are one construct measured seven times, not seven
   independent facts; left unclustered they would take 70% of the weight purely
   by being numerous.
4. **Weight** each construct by the mean quality of its members, and split that
   within the construct by each member's own quality. Quality is reliability x
   sample-size shrinkage x attribution — never importance.

The one thing deliberately NOT used as evidence is correlation with the mentor
mark. Fitting to it reproduces the assessment form's own mark allocation (Phase
5 measured this: it cannot beat the thing it is fitted to), and treating it as
truth would make every other source a noisy copy of PEP rather than an
independent view.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from crew_perf import config
from crew_perf.agents.attributes import AttributeSet, build_frame, discover
from crew_perf.agents.weighting import AttributeWeight, WeightSet
from crew_perf.graph.store import GraphStore, get_store

MECHANISM_FILE = "dynamic_mechanism.json"

# A signal has to be computable for enough crew to rank anyone on, has to vary,
# and has to not be a restatement of another signal. These are the only three
# things that decide weight here.
MIN_COVERAGE = 0.10
MIN_N = 30
DISCRIMINATION_BAND = (0.02, 0.98)
SHRINKAGE_K = 50                 # n at which a signal is trusted at half strength
REDUNDANCY_THRESHOLD = 0.60      # |Spearman rho| at which two signals are one construct

# How much of the number is about this crew member. Not importance — measurement
# attribution. A catering shortfall recorded on someone's flight is a fact about
# the flight; counting it at full strength scores them for somebody else's work.
ATTRIBUTION_FACTOR = {"direct": 1.0, "shared": 0.6, "environmental": 0.25}

# The assessment system's own published verdict on a crew member. Scoring with
# these and then reporting the result alongside them is circular: `mean_mark` is
# the weighted sum of the question answers, so it correlates ~1.0 with the
# composite by construction and tells nobody anything.
#
# Excluded STRUCTURALLY rather than by asking the model, and that is the whole
# lesson from the first run of this agent: asked to identify "the system's own
# recorded verdict", it kept `mean_mark` as an input and threw out every
# ServiceNow and CLMS signal as a verdict instead — a complaint is, after all,
# something the system recorded. The mechanism it designed was consequently the
# PEP assessment with extra steps (rho +0.79 against the very mark it was
# supposed to be independent of). Which two columns constitute the verdict is a
# structural fact about the schema, not a judgement, so it is asserted here and
# the model is never offered the choice.
RECORDED_VERDICT = {"mean_mark", "top_grade_share"}

# What the recorded verdict is worth when a question ASKS to be judged on it.
#
# Excluding it from the standing weighting is right and is not the same as
# pretending it does not exist. "Which crew get the best feedback" is a question
# the mentor's mark answers better than anything else in the warehouse — it is
# the only assessment carried out by a trained assessor against a template — and
# answering it from the other signals while silently dropping the mark itself
# leaves out the best-attributed feedback the airline holds.
#
# It is capped at half rather than left to renormalise freely, and the cap is a
# structural stance rather than a business judgement: feedback is not one
# system's word. The mentor said one thing, the colleagues who filed inflight
# reports naming this crew member said another, and the appreciations on the
# record said a third. Letting the mark take whatever share the arithmetic gave
# it drove it to 91% in testing, which is the recorded mark with rounding errors
# attached rather than a ranking that spans the sources.
VERDICT_SHARE_WHEN_ASKED_FOR = 0.5

# Signals that count how often something was REPORTED, or what the flight threw at
# the crew, rather than how well the crew performed. Never scored, in either
# direction.
#
# Asserted structurally for the same reason `RECORDED_VERDICT` is: the semantic
# pass is a language model reading a column description, and asked what a rising
# count of flight issue reports means about a crew member it will answer that
# fewer is better — which is the industry's reflex, is what the previous
# mechanism did (`flight_issue_rate` took the single largest construct weight at
# 11.2%), and is wrong. A crew member who files a report was observant enough to
# notice; one who flew the same aircraft and filed nothing scores better only
# because nothing was written down. Whether a report reflects on the person
# depends on who it NAMES, and that is a structural fact about the schema rather
# than a judgement, so the model is not offered the choice.
REPORTING_OR_ENVIRONMENT = {
    "flight_reports_filed",
    "sn_operational_exposure_rate",
    # Named on a service report = the crew responded. Asserted here rather than
    # asked, because "share of flights where this crew member was named in a
    # customer or cabin service issue" reads to a model as a complaint rate, and
    # it is nothing of the kind — see the note in attributes.py.
    "sn_service_response_rate",
}

SEMANTIC_PROMPT = """\
You are reading a data dictionary for an airline crew-performance system. Your \
job is SEMANTICS ONLY. You must NOT decide how much anything counts — weights are \
computed from measured statistical properties elsewhere, and any importance you \
express will be discarded.

## What the knowledge graph knows about these tables
{graph_context}

## Candidate per-crew signals
Each is a single number computed per crew member, with its measured profile.
{signals_json}

## For each signal decide

1. `role` — one of exactly two:
   - `performance_signal`: the number says something about how well this crew \
member did. A complaint, a commendation, a compliance rate and an assessment \
result are ALL performance signals — they disagree with each other, and that is \
why several are being read, not a reason to discard any of them.
   - `context`: it counts exposure or opportunity rather than quality — how many \
flights the crew member was on, how many assessments they had, how many rows \
exist about them. If a larger value could be either good or bad depending on how \
much they flew, it is context.
   Use `context` sparingly and only when the number genuinely cannot rank two \
crew members.
2. `direction` — `higher_is_better` or `lower_is_better`, from what the number \
MEANS. State it even for `context`.
3. `construct` — the latent aspect of crew performance it measures, in 1-3 \
words, drawn from the concepts above where they fit. Signals measuring the same \
underlying thing MUST get the same construct string.
4. `attribution` — how much of this number is about the person:
   - `direct`: it records this crew member's own action or assessed behaviour
   - `shared`: it records something the crew collectively did or were named in
   - `environmental`: it records a circumstance of the flight (weather, \
catering, engineering, airport, delay) that happened around them
5. `confidence` — 0.0 to 1.0 in your reading.
6. `why` — one short sentence.

Return ONLY JSON:
{{"signals": [{{"name": "...", "role": "...", "direction": "...", \
"construct": "...", "attribution": "...", "confidence": 0.8, "why": "..."}}]}}
"""


@dataclass
class SignalReading:
    """What one candidate number is, before any weight is attached to it."""

    name: str
    description: str
    source_tables: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    role: str = "performance_signal"
    direction: str = "higher_is_better"
    construct: str = ""
    attribution: str = "direct"
    confidence: float = 0.5
    why: str = ""
    read_by: str = "declared"        # llm | declared (fallback)

    # Measured profile
    n: int = 0
    coverage: float = 0.0
    mean: float | None = None
    sd: float | None = None
    reliability: float = 0.0
    p_pass: float | None = None
    rho_native: float | None = None  # reported only; never used to set weight

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Mechanism:
    """The scoring scheme this agent designed, and how it got there."""

    id: str
    generated_at: str
    population_n: int
    signals: list[SignalReading] = field(default_factory=list)
    constructs: list[dict] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    rejected: list[dict] = field(default_factory=list)
    coverage_audit: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    method: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "generated_at": self.generated_at,
            "population_n": self.population_n,
            "method": self.method,
            "signals": [s.to_dict() for s in self.signals],
            "constructs": self.constructs,
            "weights": self.weights,
            "rejected": self.rejected,
            "coverage_audit": self.coverage_audit,
            "findings": self.findings,
        }


# ─── 1. Discovery and profiling ─────────────────────────────────────────────


def profile(frame: pd.DataFrame, attrs: AttributeSet, store: GraphStore) -> list[SignalReading]:
    """Every computable per-crew number, with its measured profile attached.

    Profiling happens before any semantic reading so the model is shown what the
    data actually looks like — a "rate" that is 0.99 everywhere is not a rate
    worth naming a construct for, and saying so in the prompt is cheaper than
    correcting it afterwards.
    """
    out: list[SignalReading] = []
    for attr in attrs.attributes:
        if attr.name not in frame.columns:
            continue
        series = frame[attr.name].astype(float)
        present = series.notna()
        values = series[present]
        n = int(present.sum())
        mean = float(values.mean()) if n else None
        sd = float(values.std()) if n > 1 else 0.0

        # Reliability: spread against the most spread this shape could have had.
        # For a rate that is the binomial ceiling at its own mean; for anything
        # else, dispersion relative to its own scale.
        p_pass = None
        if n and mean is not None and 0.0 <= mean <= 1.0:
            ceiling = float(np.sqrt(max(mean * (1 - mean), 1e-9)))
            reliability = min(sd / ceiling, 1.0) if ceiling > 0 else 0.0
            p_pass = mean
        elif n and mean not in (None, 0):
            reliability = min(abs(sd / mean), 1.0)
        else:
            reliability = 1.0 if sd > 0 else 0.0

        concepts: list[str] = []
        for table in attr.source_tables:
            for concept in store.concepts_of(table):
                node = store.concepts.get(concept)
                label = node.display_label if node else concept
                if label not in concepts:
                    concepts.append(label)

        out.append(SignalReading(
            name=attr.name,
            description=attr.description,
            source_tables=list(attr.source_tables),
            concepts=concepts,
            direction=attr.direction,
            construct=attr.family,
            n=n,
            coverage=float(present.mean()) if len(series) else 0.0,
            mean=mean, sd=sd,
            reliability=float(reliability),
            p_pass=p_pass,
        ))
    return out


def _graph_context(store: GraphStore, signals: list[SignalReading], limit: int = 4000) -> str:
    """What the graph says about the tables these signals are computed from."""
    tables = {t for s in signals for t in s.source_tables}
    lines: list[str] = []
    for name in sorted(tables):
        node = store.table(name)
        if node is None:
            continue
        concepts = ", ".join(store.concepts_of(name)) or "—"
        lines.append(
            f"- {name} ({node.source}) — one row is {node.grain or 'unstated'}. "
            f"{node.description[:180]} [concepts: {concepts}]"
        )
    joins = [
        f"- {j.source_table}.{j.source_column} = {j.target_table}.{j.target_column}"
        + ("  (crosses sources)" if j.cross_source else "")
        for j in store.joins
        if j.confidence >= 0.8 and (j.source_table in tables or j.target_table in tables)
    ]
    body = "\n".join(lines) + "\n\n### How these tables reach a crew member\n" + "\n".join(joins[:40])
    return body[:limit]


# ─── 2. Semantic reading ────────────────────────────────────────────────────


def read_semantics(signals: list[SignalReading], store: GraphStore,
                   use_llm: bool = True) -> list[str]:
    """Attach role, direction, construct and attribution to each signal.

    Returns notes. Without an LLM the declared direction and family stand in —
    those come with the data-point definition rather than from a business rule,
    so the mechanism still reads no declared importance, just less finely constructed.
    """
    notes: list[str] = []
    # Structural first, and not negotiable: the verdict columns are never inputs,
    # and a count of reports filed or of disruptions suffered is never a quality
    # signal in either direction.
    forced = RECORDED_VERDICT | REPORTING_OR_ENVIRONMENT
    for s in signals:
        if s.name in RECORDED_VERDICT:
            s.role = "outcome_label"
            s.why = "the assessment system's own published verdict — an input here would "\
                    "make the score a restatement of it"
        elif s.name in REPORTING_OR_ENVIRONMENT:
            s.role = "context"
            s.attribution = "environmental"
            s.why = "counts reporting activity or flight circumstance, not crew quality — "\
                    "scoring it would make noticing a defect a mark against the person "\
                    "who noticed it"

    if not use_llm:
        for s in signals:
            if s.name in forced:
                continue
            s.read_by = "declared"
            s.role = "context" if s.construct in {"volume"} else "performance_signal"
        notes.append(
            "semantic reading skipped (--no-llm): construct = the signal's declared "
            "family, direction = its declared direction, attribution assumed direct"
        )
        return notes

    from crew_perf.llm import call_llm, parse_llm_json

    payload = [
        {
            "name": s.name,
            "means": s.description,
            "from_tables": s.source_tables,
            "graph_concepts": s.concepts,
            "measured": {
                "crew_with_a_value": s.n,
                "coverage": round(s.coverage, 3),
                "mean": None if s.mean is None else round(s.mean, 4),
                "sd": None if s.sd is None else round(s.sd, 4),
            },
        }
        for s in signals if s.name not in forced
    ]
    reply = call_llm(
        SEMANTIC_PROMPT.format(
            graph_context=_graph_context(store, signals),
            signals_json=json.dumps(payload, indent=1),
        ),
        temperature=0.0,
    )
    parsed = parse_llm_json(reply) or {}
    readings = {r.get("name"): r for r in (parsed.get("signals") or [])
                if isinstance(r, dict)}

    by_name = {s.name: s for s in signals}
    unread: list[str] = []
    for name, s in by_name.items():
        if name in forced:
            continue
        r = readings.get(name)
        if not r:
            unread.append(name)
            s.read_by = "declared"
            s.role = "context" if s.construct == "volume" else "performance_signal"
            continue
        s.read_by = "llm"
        role = str(r.get("role") or "")
        s.role = role if role in {"performance_signal", "context"} else "performance_signal"
        # Direction is the one reading that can silently invert a ranking, so a
        # value the model did not return in the expected vocabulary is refused
        # rather than coerced — the declared direction stands instead.
        direction = str(r.get("direction") or "")
        if direction in {"higher_is_better", "lower_is_better"}:
            s.direction = direction
        s.construct = str(r.get("construct") or s.construct).strip().lower() or s.construct
        attribution = str(r.get("attribution") or "").lower()
        s.attribution = attribution if attribution in ATTRIBUTION_FACTOR else "direct"
        try:
            s.confidence = float(r.get("confidence", 0.5))
        except (TypeError, ValueError):
            s.confidence = 0.5
        s.why = str(r.get("why") or "")

    if unread:
        notes.append(
            f"the semantic pass returned nothing for {', '.join(sorted(unread))} — "
            f"their declared direction and family stand in, and they are weighted "
            f"on the same measured evidence as everything else"
        )

    return notes


# ─── 3. Redundancy clustering ───────────────────────────────────────────────


def cluster_signals(frame: pd.DataFrame, names: list[str],
                    threshold: float = REDUNDANCY_THRESHOLD) -> list[list[str]]:
    """Group signals that move together into one construct, by average linkage.

    Seven assessment-category pass rates are seven views of one assessment. Left
    as seven independent signals they take seven shares of the weight and the
    mechanism becomes "the PEP form, again". Grouping them by measured
    correlation lets a single well-attributed complaint signal stand against the
    whole assessment block instead of being outvoted by it.
    """
    if len(names) < 2:
        return [[n] for n in names]

    rho = frame[names].corr(method="spearman", min_periods=MIN_N).abs()
    rho = rho.fillna(0.0)

    clusters = [[n] for n in names]
    while len(clusters) > 1:
        best, best_score = None, threshold
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                pairs = [rho.at[a, b] for a in clusters[i] for b in clusters[j]]
                score = float(np.mean(pairs)) if pairs else 0.0
                if score >= best_score:
                    best, best_score = (i, j), score
        if best is None:
            break
        i, j = best
        clusters[i] = clusters[i] + clusters[j]
        clusters.pop(j)
    return clusters


# ─── 4. Mechanism design ────────────────────────────────────────────────────


def design(store: GraphStore | None = None, executor=None, use_llm: bool = True,
           version: int = 1, progress=None) -> tuple[WeightSet, Mechanism]:
    """Derive a scoring mechanism from the data points and the graph alone."""
    from crew_perf.data.executor import get_executor

    store = store or get_store()
    executor = executor or get_executor()

    def step(stage: str, detail: str) -> None:
        if progress:
            progress(stage, detail)

    attrs = discover(store, executor)
    frame = build_frame(executor, attrs)
    step("discovery", f"{len(frame.columns)} computable signals over {len(frame)} crew")

    signals = profile(frame, attrs, store)
    notes = read_semantics(signals, store, use_llm=use_llm)
    step("semantics", f"{sum(1 for s in signals if s.read_by == 'llm')}/{len(signals)} "
                      f"read through the graph")

    mechanism = Mechanism(
        id=f"dynamic__v{version}",
        generated_at=datetime.now(timezone.utc).isoformat(),
        population_n=int(len(frame)),
        signals=signals,
        findings=list(notes),
        method=[
            "weights derived from measured properties only — coverage, discrimination, "
            "reliability, redundancy and attribution",
            "no declared business importance was read",
            "correlation with the recorded assessment mark is reported for information "
            "and never used to set a weight",
            f"signals correlating at |rho| >= {REDUNDANCY_THRESHOLD} are treated as one "
            f"construct and share its weight",
        ],
    )

    # The recorded mark is reported against, never fitted to.
    native = frame["mean_mark"].astype(float) if "mean_mark" in frame else pd.Series(dtype=float)

    # ── Admission: three measured tests, no judgement ──
    admitted: list[SignalReading] = []
    for s in signals:
        if s.role == "outcome_label":
            mechanism.rejected.append({
                "signal": s.name, "kind": "by_design",
                "reason": "the system's own recorded verdict — scoring "
                          "with it and then comparing against it is circular"})
            continue
        if s.role == "context":
            mechanism.rejected.append({
                "signal": s.name, "kind": "by_design",
                "reason": s.why if s.name in REPORTING_OR_ENVIRONMENT else
                          "measures exposure or volume, not quality — "
                          "kept for confidence, never scored"})
            continue
        if s.coverage < MIN_COVERAGE or s.n < MIN_N:
            mechanism.rejected.append({
                "signal": s.name, "kind": "on_evidence",
                "reason": f"computable for {s.n} crew ({s.coverage:.0%}) — too few to rank on"})
            continue
        if s.sd is None or s.sd <= 0:
            mechanism.rejected.append({
                "signal": s.name, "kind": "on_evidence",
                "reason": "identical for every crew member — separates nobody"})
            continue
        if s.p_pass is not None and not (
                DISCRIMINATION_BAND[0] <= s.p_pass <= DISCRIMINATION_BAND[1]):
            mechanism.rejected.append({
                "signal": s.name, "kind": "on_evidence",
                "reason": f"almost everyone scores the same ({s.p_pass:.1%}) — carries "
                          f"nearly no information"})
            continue
        if len(native) and s.n >= MIN_N:
            from scipy import stats

            aligned = native.reindex(frame[s.name].dropna().index)
            ok = aligned.notna()
            if int(ok.sum()) >= MIN_N:
                rho, _ = stats.spearmanr(frame[s.name].dropna()[ok], aligned[ok])
                s.rho_native = None if np.isnan(rho) else round(float(rho), 4)
        admitted.append(s)

    step("admission", f"{len(admitted)} signals admitted, {len(mechanism.rejected)} rejected")

    # A source that offered signals and had every one of them rejected is worth
    # saying out loud. The first version of this agent dropped ServiceNow, CLMS
    # and CrewPortal entirely through one mis-read classification and still
    # printed a confident mechanism — the loss was only visible by reading
    # fourteen rejection lines one at a time.
    def _source_of(signal: SignalReading) -> str | None:
        for table in signal.source_tables:
            node = store.table(table)
            if node:
                return node.source
        return None

    offered: dict[str, list[str]] = {}
    for s in signals:
        source = _source_of(s)
        if source:
            offered.setdefault(source, []).append(s.name)
    kept = {_source_of(s) for s in admitted}
    reasons = {r["signal"]: r["reason"] for r in mechanism.rejected}

    for source, names in sorted(offered.items()):
        if source in kept:
            continue
        why = {reasons[n].split(" — ")[0] for n in names if n in reasons}
        mechanism.findings.append(
            f"every one of {source}'s {len(names)} candidate signal(s) was rejected "
            f"({'; '.join(sorted(why))[:150]}) — the mechanism scores nothing from that source"
        )

    if not admitted:
        mechanism.findings.append("no signal passed admission — no mechanism could be designed")
        return _empty_weightset(mechanism), mechanism

    # ── Redundancy: what is actually being measured, and how many times ──
    clusters = cluster_signals(frame, [s.name for s in admitted])
    by_name = {s.name: s for s in admitted}

    quality: dict[str, float] = {}
    for s in admitted:
        shrink = s.n / (s.n + SHRINKAGE_K)
        quality[s.name] = max(
            s.reliability * shrink * ATTRIBUTION_FACTOR.get(s.attribution, 1.0), 1e-6
        )

    cluster_scores = [float(np.mean([quality[n] for n in c])) for c in clusters]
    total_cluster = sum(cluster_scores) or 1.0

    weights: dict[str, float] = {}
    for cluster, score in zip(clusters, cluster_scores):
        share = score / total_cluster
        within = sum(quality[n] for n in cluster) or 1.0
        for name in cluster:
            weights[name] = round(share * quality[name] / within, 6)

    # Normalise once at the end so the reported numbers sum to 1 exactly.
    total = sum(weights.values()) or 1.0
    weights = {k: round(v / total, 6) for k, v in weights.items()}
    mechanism.weights = weights

    for cluster, score in zip(clusters, cluster_scores):
        members = sorted(cluster, key=lambda n: -weights[n])
        constructs = sorted({by_name[n].construct for n in cluster if by_name[n].construct})
        mechanism.constructs.append({
            "label": " / ".join(constructs) or "unnamed",
            "signals": members,
            "weight": round(sum(weights[n] for n in cluster), 6),
            "mean_quality": round(score, 4),
            "semantic_agreement": len(constructs) <= 1,
        })
        # A cluster spanning several named constructs means the data disagrees
        # with the reading: things called different names move together. That is
        # a finding about the measurement, not an error to smooth over.
        if len(constructs) > 1:
            mechanism.findings.append(
                f"signals read as different constructs ({', '.join(constructs)}) move "
                f"together in the data (|rho| >= {REDUNDANCY_THRESHOLD}) — they are "
                f"weighted as one, because measuring the same thing twice should not "
                f"count twice"
            )

    # The mirror image of the finding above: two signals the reading calls the
    # same thing, which the data says are unrelated. `appreciation_count` (CLMS)
    # and `sn_appreciation_count` (ServiceNow) are both "recognition" and move
    # independently, because they are two systems recording different awards over
    # different populations. Counted separately on purpose — collapsing them
    # would assert an equivalence the data refuses.
    seen: dict[str, list[str]] = {}
    for cluster in clusters:
        for label in {by_name[n].construct for n in cluster if by_name[n].construct}:
            seen.setdefault(label, []).append(", ".join(sorted(cluster)))
    for label, groups in seen.items():
        if len(groups) > 1:
            mechanism.findings.append(
                f"{len(groups)} groups of signals are both read as {label!r} but do not move "
                f"together ({' | '.join(groups)}) — they are weighted as separate constructs, "
                f"because the data does not support treating them as one measurement"
            )

    # A signal related to nothing else in the set is either genuinely new
    # information or noise, and no statistic available here separates the two.
    # Saying so beats implying the weight is evidence of the former.
    if len(weights) > 2:
        rho_all = frame[list(weights)].corr(method="spearman", min_periods=MIN_N).abs()
        isolated = [
            n for n in weights
            if float(rho_all[n].drop(index=n).max() or 0.0) < 0.10
        ]
        if isolated:
            mechanism.findings.append(
                "signals correlating with nothing else measured (" + ", ".join(isolated)
                + ") — they may be independent information or they may be noise, and "
                  "nothing in the data distinguishes the two. They hold weight on their "
                  "own reliability alone"
            )

    step("weighting", f"{len(clusters)} independent construct(s) across {len(weights)} signals")

    # ── What the mechanism does NOT see ──
    mechanism.coverage_audit = _coverage_audit(store, admitted, frame)
    partial = [s for s in admitted if s.coverage < 0.5]
    if partial:
        mechanism.findings.append(
            "signals covering less than half the fleet: "
            + ", ".join(f"{s.name} ({s.coverage:.0%})" for s in partial)
            + " — counted for the crew who have them, absent for the rest, never zero-filled"
        )

    verdict = [s for s in signals if s.name in RECORDED_VERDICT]
    # Volume signals are kept out of the standing weighting because exposure is
    # not quality — but "rank the crew on leave" is asking for the exposure, and
    # that objection does not survive the question naming it. They are held the
    # same way the verdict is, so `refocus` can promote one; REPORTING_OR_ENVIRONMENT
    # is deliberately not held, because scoring a report count inverts the ranking
    # whether or not it was asked for.
    volume = [s for s in signals
              if s.role == "context"
              and s.name not in REPORTING_OR_ENVIRONMENT
              and s.name not in RECORDED_VERDICT]
    weightset = _to_weightset(mechanism, admitted, weights, verdict, volume)
    return weightset, mechanism


def _coverage_audit(store: GraphStore, admitted: list[SignalReading],
                    frame: pd.DataFrame) -> dict:
    """Which of the onboarded data the mechanism actually reaches.

    A mechanism designed from "all the data points provided" should be able to
    say which ones it used. Tables and concepts contributing no signal are not
    necessarily a defect — a lookup table cannot produce a per-crew number — but
    an unreached fact table is a blind spot worth naming.
    """
    used_tables = {t for s in admitted for t in s.source_tables}
    by_source: dict[str, dict] = {}
    for node in store.tables.values():
        entry = by_source.setdefault(node.source, {"tables": 0, "used": 0, "unused": []})
        entry["tables"] += 1
        if node.name in used_tables:
            entry["used"] += 1
        else:
            entry["unused"].append(node.name)

    used_concepts = {c for s in admitted for c in s.concepts}
    all_concepts = {c.display_label for c in store.concepts.values()}
    return {
        "signals_used": len(admitted),
        "signals_computable": int(len(frame.columns)),
        "crew_in_population": int(len(frame)),
        "by_source": by_source,
        "concepts_reached": sorted(used_concepts),
        "concepts_unreached": sorted(all_concepts - used_concepts),
    }


def _to_weightset(mechanism: Mechanism, admitted: list[SignalReading],
                  weights: dict[str, float],
                  verdict: list[SignalReading] | None = None,
                  volume: list[SignalReading] | None = None) -> WeightSet:
    """Express the mechanism as a WeightSet so every downstream agent is unchanged."""
    ws = WeightSet(
        id=mechanism.id,
        scope={"designation": "ALL", "fleet": "ALL", "basis": "data-derived"},
        generated_at=mechanism.generated_at,
        label_n=mechanism.population_n,
        notes=[*mechanism.method, *mechanism.findings],
    )
    by_name = {s.name: s for s in admitted}
    for name, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        s = by_name[name]
        ws.attributes.append(AttributeWeight(
            attribute=name,
            weight=weight,
            prior=0.0,                       # there is no prior; that is the point
            evidence=s.reliability,
            reliability=s.reliability,
            coverage=s.coverage,
            n=s.n,
            dispersion=s.sd or 0.0,
            rho_label=s.rho_native,
            p_value=None,
            direction=s.direction,
            family=s.construct or "unnamed",
            rule_ref=None,                    # no rule was consulted
            partial_population=s.coverage < 0.30,
            rationale=(
                f"construct {s.construct!r} ({s.attribution} attribution); reliability "
                f"{s.reliability:.2f} over n={s.n} at {s.coverage:.0%} coverage; weight is "
                f"its share of an independent construct, not a declared importance"
            ),
        ))
    ws.excluded = [{"attribute": r["signal"], "reason": r["reason"],
                    "kind": r.get("kind", "on_evidence")}
                   for r in mechanism.rejected]
    ws.reportable = [*_reportable_verdict(verdict or []),
                     *_reportable_volume(volume or [])]

    # What the mechanism WANTED to read and could not. Nothing declared was lost
    # — there is no declaration — but a signal the schema offers and the data
    # cannot fill is exactly as invisible to a score as a forfeited prior was,
    # and in production that is the common case: a source that is onboarded but
    # not yet populated. Rejections "by_design" are excluded because those are
    # deliberate (the recorded verdict, reporting counts), not missing.
    unusable = [r["signal"] for r in mechanism.rejected
                if r.get("kind") != "by_design"]
    ws.dropped_sources = unusable
    candidates = len(ws.attributes) + len(unusable)
    ws.prior_retained = round(len(ws.attributes) / candidates, 4) if candidates else 1.0
    return ws


def _reportable_volume(volume: list[SignalReading]) -> list[AttributeWeight]:
    """Volume signals, held back for the question that asks to be ranked on one.

    `leave_days_taken` is the case that motivated this. It is excluded from the
    standing weighting on a real principle — days away measure exposure, and a
    crew member who took no leave is not thereby a better one — so a general
    ranking must not spend weight on it. But "rank the crew best in terms of
    leave" is asking for exactly that measure, and the exclusion answered it with
    the standing weighting instead: a ranking of overall performance wearing the
    question's label, with no leave signal anywhere in the breakdown.

    So they are held exactly as the recorded verdict is — present, at zero weight,
    reachable only through `refocus`. `prior` is 0.0 and that is the difference
    that matters: the verdict carries a reserved share because it would otherwise
    swamp a ranking it is only half of, while a volume signal has no such claim
    and is renormalised against whatever else the question named, on the measured
    evidence every attribute already carries.
    """
    return [
        AttributeWeight(
            attribute=s.name,
            weight=0.0,                  # never counted unless a question asks
            prior=0.0,                   # and never with a reserved share
            evidence=s.reliability,
            reliability=s.reliability,
            coverage=s.coverage,
            n=s.n,
            dispersion=s.sd or 0.0,
            rho_label=s.rho_native,
            p_value=None,
            direction=s.direction,
            family=s.construct or "unnamed",
            rule_ref=None,
            partial_population=s.coverage < 0.30,
            held_for="volume",
            rationale=(
                f"measures volume, not quality, so it carries no weight in a general "
                f"ranking; counted only when a question asks to be judged on it, at "
                f"reliability {s.reliability:.2f} over n={s.n} ({s.coverage:.0%} coverage)"
            ),
        )
        for s in volume if s.n > 0
    ]


def _reportable_verdict(verdict: list[SignalReading]) -> list[AttributeWeight]:
    """The recorded assessment, held back for the question that asks for it.

    These carry a `prior` where nothing else does, and it is not a smuggled
    business judgement: it is the share `refocus` reserves when — and only when —
    a question names the recorded assessment. In every other ranking they weigh
    nothing, because scoring with the mark and then reporting the result beside
    it is circular.

    **The share goes to the mark, not to the pair.** `top_grade_share` is how
    often that same mark landed in the top band — a coarser reading of the number
    `mean_mark` already is, not a second opinion about the crew member. Splitting
    the reserved share between them by measured reliability gave the band 0.47
    and the mark itself 0.03, because a near-binary indicator disperses less than
    a mark out of 100; the ranking then turned on the derivative and ignored the
    verdict. It is still reported, at zero, so a reader can see it was considered.
    """
    usable = [s for s in verdict if s.n > 0]
    if not usable:
        return []
    lead = next((s for s in usable if s.name == "mean_mark"), usable[0])
    return [
        AttributeWeight(
            attribute=s.name,
            weight=0.0,                  # never counted unless a question asks
            prior=(VERDICT_SHARE_WHEN_ASKED_FOR if s is lead else 0.0),
            held_for="verdict",
            evidence=s.reliability,
            reliability=s.reliability,
            coverage=s.coverage,
            n=s.n,
            dispersion=s.sd or 0.0,
            rho_label=None,              # it IS the label; correlating it with itself says nothing
            p_value=None,
            direction=s.direction,
            family="recorded_verdict",
            rule_ref="mark_aggregation",
            rationale=(
                "the assessment system's own recorded verdict — kept out of the "
                "standing score because the score is built from the answers it is "
                "computed from, and counted only when a question asks to be judged "
                "on the assessment itself"
            ),
        )
        for s in usable
    ]


def _empty_weightset(mechanism: Mechanism) -> WeightSet:
    return WeightSet(
        id=mechanism.id, scope={"designation": "ALL", "fleet": "ALL"},
        generated_at=mechanism.generated_at, label_n=mechanism.population_n,
        notes=mechanism.findings,
    )


# ─── 5. Self-evaluation ─────────────────────────────────────────────────────


def evaluate(weightset: WeightSet, frame: pd.DataFrame) -> dict:
    """What the mechanism achieves, measured — not asserted.

    Three questions, and none of them is "does it agree with the mentor mark":
    can it separate crew, does the ordering survive its own weights being
    slightly different, and how far does it depart from what the assessment form
    already says.
    """
    from crew_perf.agents.evaluator import rank_stability

    cols = [a.attribute for a in weightset.attributes if a.attribute in frame.columns]
    if not cols:
        return {"separable": 0, "stability": None, "rho_native": None}

    z = frame[cols].astype(float)
    z = (z - z.mean()) / z.std()
    for a in weightset.attributes:
        if a.attribute in z.columns and a.direction == "lower_is_better":
            z[a.attribute] = -z[a.attribute]
    w = np.array([a.weight for a in weightset.attributes if a.attribute in cols])
    score = (z.fillna(0.0) @ (w / w.sum()))

    out = {
        "crew_scored": int(score.notna().sum()),
        "distinct_scores": int(score.round(4).nunique()),
        "stability": rank_stability(weightset, frame),
    }
    if "mean_mark" in frame:
        from scipy import stats

        native = frame["mean_mark"].astype(float)
        ok = native.notna() & score.notna()
        if int(ok.sum()) >= MIN_N:
            rho, p = stats.spearmanr(score[ok], native[ok])
            out["rho_native"] = round(float(rho), 4)
            out["p_native"] = round(float(p), 6)
    return out


# ─── Persistence ────────────────────────────────────────────────────────────


def save(mechanism: Mechanism, weightset: WeightSet) -> tuple:
    from crew_perf.agents import weighting

    config.GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.GRAPHS_DIR / MECHANISM_FILE
    path.write_text(json.dumps(mechanism.to_dict(), indent=2, default=str))
    return path, weighting.save(weightset)


def load(path=None) -> WeightSet:
    from crew_perf.agents import weighting

    path = path or config.GRAPHS_DIR / "dynamic__v1.json"
    return weighting.load(path)
