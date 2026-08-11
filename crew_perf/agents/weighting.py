"""The weighting vocabulary: what a set of weights *is*, and how to scope it.

The mechanism that produces weights lives in `agents/dynamic.py` — it derives
them from the data and the graph alone, and it is the only one. This module owns
the shared parts around it:

    WeightSet / AttributeWeight   the structure a mechanism produces
    save / load / push            versioned persistence
    detect_focus / refocus        scoping a weighting to what a question asked

**Why the declared-prior mechanism is gone.** There used to be a second one that
started from a business's written statement of what matters and let the data
adjust it. Two mechanisms meant every score had to say which produced it, every
comparison had to check the two were on the same scale, and a reader had to hold
both in their head to act on a number. One mechanism, derived from the records,
is the whole system now.

**Refocus is not a second mechanism.** "Rank the weakest crew on leave and
service" is a question about two aspects, and answering it with a weighting that
spends three-quarters of itself elsewhere is a ranking of overall performance
wearing the question's label — it names different people. `refocus` moves only
the allocation of importance; the evidence behind each signal is a property of
the data, not of the question, and re-deriving it per question would let a
rephrasing quietly change what counts as reliable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace

from crew_perf import config
from crew_perf.graph.schema import VertexLabel

# Below this share of the population a signal covers only part of the fleet. The
# ServiceNow extract is a two-month window naming 18% of the modelled crew, and
# dropping it outright would make a whole onboarded source invisible to every
# score while the scorecard still read "coverage 100%". Kept signals show up in
# each crew member's own `coverage` figure — present for those who have rows,
# absent (never zero-filled) for those who do not.
MIN_COVERAGE = 0.30


@dataclass
class AttributeWeight:
    attribute: str
    weight: float
    prior: float
    evidence: float
    reliability: float
    coverage: float
    n: int
    dispersion: float            # sd of the attribute across crew
    rho_label: float | None      # correlation with the recorded mark (direction only)
    p_value: float | None
    direction: str
    family: str
    rule_ref: str | None
    rationale: str
    # True when fewer than MIN_COVERAGE of crew have this signal at all. Carried
    # onto the scorecard so a composite built partly on a partially-covered
    # source says so, instead of looking like a full one.
    partial_population: bool = False
    # Why this attribute sits in `reportable` rather than in the standing
    # weighting: "verdict" (circular to score, so it takes a reserved share when
    # asked for) or "volume" (exposure rather than quality, so it takes no
    # reserved share and is ranked against whatever else was named). Empty for a
    # fitted attribute. Stated rather than inferred from `prior`, because
    # `top_grade_share` is a verdict signal whose prior is legitimately 0.0 and
    # reading it as volume would hand a feedback ranking to the derivative of the
    # mark instead of the mark — see `dynamic._reportable_verdict`.
    held_for: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WeightSet:
    id: str
    scope: dict
    generated_at: str
    label_n: int
    attributes: list[AttributeWeight] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Share of declared importance that survived into the fit. Distinct from a
    # scorecard's `coverage`, which measures what was computable for one crew
    # member. An attribute dropped because NOBODY has data disappears from the
    # denominator entirely — so without this a score reads "coverage 100%" while
    # silently ignoring a whole source that has not been populated yet.
    prior_retained: float = 1.0
    dropped_sources: list[str] = field(default_factory=list)
    # Set when the weights were scoped to a question's named aspects.
    focus: "Focus | None" = None
    # The recorded assessment verdict, profiled but never counted in the standing
    # weighting. `fit` excludes `mean_mark` and `top_grade_share` because the
    # composite is built from the question answers they are computed from, so
    # scoring them would be circular. That reasoning holds for a general ranking
    # and stops holding the moment somebody asks to be ranked ON the assessment —
    # "who scores best on feedback" wants the mentor's mark counted, not routed
    # around. Keeping them here, with their evidence measured the same way as
    # everything else, lets `refocus` promote them for exactly those questions
    # without them leaking into any other ranking.
    reportable: list[AttributeWeight] = field(default_factory=list)

    def weights(self) -> dict[str, float]:
        return {a.attribute: a.weight for a in self.attributes}

    def to_dict(self) -> dict:
        return {
            "id": self.id, "scope": self.scope, "generated_at": self.generated_at,
            "label_n": self.label_n,
            "attributes": [a.to_dict() for a in self.attributes],
            "excluded": self.excluded, "notes": self.notes,
            "prior_retained": self.prior_retained,
            "dropped_sources": self.dropped_sources,
            "focus": self.focus.to_dict() if self.focus else None,
            "reportable": [a.to_dict() for a in self.reportable],
        }


# ─── Persistence ────────────────────────────────────────────────────────────


def save(ws: WeightSet) -> "object":
    config.GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.GRAPHS_DIR / f"{ws.id}.json"
    path.write_text(json.dumps(ws.to_dict(), indent=2, default=str))
    return path


def push(ws: WeightSet, client=None) -> dict:
    """Upsert the WeightSet as a graph vertex.

    The rule each attribute cites (`rule_ref`) is carried as a property on the
    vertex, not as an edge to a rule vertex. Scoring rules are configuration in
    `crew_perf/policy.py`, read there by the code that applies them; a second
    copy of them in the graph could disagree with the one in force and nothing
    would notice.
    """
    from crew_perf.graph.cosmos import (
        close_cosmos_client, get_cosmos_client, upsert_vertex,
    )

    config.assert_graph_writable()
    own = client is None
    client = client or get_cosmos_client()
    counts = {"weightsets": 0, "edges": 0}
    try:
        upsert_vertex(client, ws.id, VertexLabel.WEIGHTSET, {
            "source": "WeightSet",
            "name": ws.id,
            "scope": ws.scope,
            "generated_at": ws.generated_at,
            "label_n": ws.label_n,
            "attributes": [a.to_dict() for a in ws.attributes],
            "excluded": ws.excluded,
            "notes": ws.notes,
        })
        counts["weightsets"] += 1
    finally:
        if own:
            close_cosmos_client(client)
    return counts


def _attribute_weight(raw: dict) -> AttributeWeight:
    """Rebuild one AttributeWeight from a saved file, tolerating an older shape.

    These JSONs are generated caches that outlive the code that wrote them: a
    weightset fitted last week is read by today's build, and `crewperf dynamic`
    is not re-run on every rename. Passing the dict straight into the dataclass
    meant one renamed field turned every cached artifact into a TypeError at
    load — a crash on stale cache rather than a refit, in a system whose whole
    contract is that a cache can always be regenerated.

    Unknown keys are dropped and missing ones take their defaults; the one
    rename so far is translated by name so a file written before it still says
    which rule each weight cites.
    """
    known = {f.name for f in fields(AttributeWeight)}
    data = dict(raw)
    if "sop_ref" in data and "rule_ref" not in data:
        data["rule_ref"] = data.pop("sop_ref")
    return AttributeWeight(**{k: v for k, v in data.items() if k in known})


def load(path) -> WeightSet:
    """Rehydrate a saved WeightSet."""
    raw = json.loads(path.read_text())
    ws = WeightSet(
        id=raw["id"], scope=raw["scope"], generated_at=raw["generated_at"],
        label_n=raw["label_n"], excluded=raw.get("excluded", []),
        notes=raw.get("notes", []),
        prior_retained=raw.get("prior_retained", 1.0),
        dropped_sources=raw.get("dropped_sources", []),
    )
    ws.attributes = [_attribute_weight(a) for a in raw["attributes"]]
    # Absent from weightsets written before the recorded verdict was profiled.
    # A cached file missing it degrades to "the question named the assessment and
    # we cannot rank on it", which is a worse answer than refitting but not a
    # wrong one — so it loads rather than failing.
    ws.reportable = [_attribute_weight(a) for a in raw.get("reportable", [])]
    return ws


# ─── Question-scoped emphasis ────────────────────────────────────────────────
#
# "Who is the worst crew in terms of leave and service" was answered with the
# standard weights: 25% of them on leave and service, 57% on safety and
# communication. That is a ranking of overall performance wearing the question's
# label — the two people it names are probably not the two the asker wanted.
#
# The fix is a lens, not a rewrite. Declared importance still decides the
# relative standing of the attributes in focus; the question decides which
# attributes are in focus at all. Weights are never invented, and an attribute
# the question did not raise is reported at zero weight rather than deleted, so
# the scorecard still shows what it chose not to count.

ASPECTS: dict[str, dict] = {
    # Attribute names follow the real form's category codes, so these were
    # re-keyed wholesale when the invented bank (SAF/COM/SVC/CAB/GRM/LED) was
    # replaced. `safety` is kept deliberately even though nothing scores it —
    # asking about safety must report the aspect as unanswerable rather than
    # silently falling back to a general ranking.
    "leave": {
        "terms": ("leave", "absence", "absent", "time off", "availability", "sick"),
        "attributes": ("leave_days_taken",),
    },
    "service": {
        "terms": ("service", "service delivery", "onboard sales", "hospitality",
                  "food", "beverage", "meal"),
        "attributes": ("pass_rate_aftertakeoff",),
    },
    "customer": {
        "terms": ("customer", "customer focus", "passenger", "in-flight experience",
                  "inflight experience", "guest"),
        "attributes": ("pass_rate_inflightexp", "sn_cx_champion_rate"),
    },
    "punctuality": {
        "terms": ("punctual", "punctuality", "time management", "on time",
                  "late", "delay", "report time"),
        "attributes": ("pass_rate_timemanagement",),
    },
    "grooming": {
        "terms": ("grooming", "groomed", "appearance", "uniform", "poise", "grace"),
        "attributes": ("pass_rate_grooming",),
    },
    "cabin": {
        "terms": ("cabin", "cleanliness", "clean", "galley", "lavatory",
                  "cabin management"),
        "attributes": ("pass_rate_cleanliness",),
    },
    "ground_duties": {
        "terms": ("ground duties", "ground duty", "post landing", "post-landing",
                  "checklist", "pre-flight", "preflight"),
        "attributes": ("pass_rate_plchecklist",),
    },
    "coaching": {
        "terms": ("coaching", "coach", "mentoring", "leadership", "guidance",
                  "inspiring leadership"),
        "attributes": ("pass_rate_leadsonly",),
    },
    "safety": {
        "terms": ("safety", "safe", "emergency", "drill", "critical"),
        "attributes": ("safety_failure_rate", "critical_failure_rate"),
    },
    "compliance": {
        "terms": ("compliance", "compliant", "process", "procedure",
                  "process adherence", "standard operating procedure"),
        "attributes": ("process_compliance_rate",),
    },
    "reliability": {
        "terms": ("reliability", "reliable", "check-in", "checkin",
                  "operational behaviour", "operational behavior"),
        "attributes": ("checkin_failure_rate",),
    },
    # Note what is NOT here: report and issue COUNTS, and involvement in them.
    # Ranking crew on how many issues they raised or responded to answers "who
    # reports and handles the most", which is not the same question as "who
    # performs worst" and is closer to its opposite. Only a signal that names a
    # crew member in a shortfall belongs in this aspect, which currently leaves
    # exactly one — the lead's own recorded service deviation. When that is too
    # rare to rank on, "who is worst on issues" is reported as unanswerable
    # rather than quietly answered with a general ranking.
    "issues": {
        "terms": ("issue", "issues", "incident", "incidents", "report", "reports",
                  "complaint", "complaints"),
        "attributes": ("sn_service_deviation_rate",),
    },
    "detection": {
        "terms": ("detection", "detect", "observant", "attention to detail",
                  "spotted", "spots", "reporting quality", "defect", "defects",
                  "vigilance", "vigilant"),
        "attributes": ("sn_validated_report_rate",),
    },
    # Recognition and achievement are the same question asked in two vocabularies,
    # so they are one aspect rather than two overlapping ones. "Top crew on their
    # achievements and service" matched only `service` while `achievement` was
    # missing here, and an aspect that matches nothing is not reported as
    # unanswerable — it simply does not appear, so the one aspect that *did*
    # match took the entire ranking. Service Delivery at 100% was the answer to
    # half the question wearing the whole question's label.
    #
    # All three sources are named because an achievement is recorded wherever
    # somebody happened to record it: a letter through the Appreciation Centre,
    # an appreciation keyed into CLMS, a star-performer or customer-experience
    # nomination in ServiceNow. Ranking on one of them ranks on which system the
    # person's manager uses.
    "recognition": {
        "terms": ("appreciation", "appreciations", "recognition", "recognised",
                  "recognized", "commendation", "compliment", "star performer",
                  "champion", "achievement", "achievements", "achieved",
                  "accomplishment", "accomplishments", "award", "awards",
                  "accolade", "accolades"),
        "attributes": ("appreciation_count", "cac_appreciation_count",
                       "sn_appreciation_count", "sn_cx_champion_rate"),
    },
    # ── Feedback: everything anybody wrote down about this crew member ──
    #
    # "Top performing crew in terms of feedback" used to match no aspect at all
    # and fall through to the standing weighting — a ranking of overall
    # performance handed back under the word "feedback". It named people who may
    # have had no feedback recorded about them whatsoever.
    #
    # Feedback is not one source's word. It is:
    #   PEP          MENTOR_FEEDBACK — a trained assessor's mark and written
    #                strengths/improvement areas. Best-attributed, widest
    #                coverage, and the mark itself has to be promoted out of
    #                `reportable` to be counted (see `refocus`).
    #   ServiceNow   the `Crew Feedback` category — inflight feedback naming this
    #                crew member for a wow moment or for a conduct concern — plus
    #                star-performer reports and customer-experience nominations.
    #   CLMS         T_SPL_APPRECIATION — appreciations recorded against them.
    #
    # Deliberately NOT here: flight issue reports and validated-detection rates.
    # Those measure what a crew member reported about the operation, not what
    # anyone said about them, and folding them in would rank the loudest reporter
    # as the best-regarded colleague.
    "feedback": {
        "terms": ("feedback", "feed back", "what people say", "what mentors say",
                  "praise", "praised", "spoken about", "written about"),
        "attributes": (
            "mean_mark",                          # PEP — the mentor's own verdict
            "sn_inflight_feedback_praise_rate",   # ServiceNow — praised by name
            "sn_inflight_feedback_concern_rate",  # ServiceNow — flagged by name
            "sn_appreciation_count",              # ServiceNow — star performer
            "sn_cx_champion_rate",                # ServiceNow — CX champion
            "appreciation_count",                 # CLMS — recorded appreciations
            "cac_appreciation_count",             # CAC — appreciation letters
        ),
    },
    # The crew's own reports of what happened on a sector, as distinct from what
    # anyone said about them. Kept separate from `feedback` on purpose: these
    # answer "who reports and handles the most", which is a different question.
    "crew_reports": {
        "terms": ("servicenow", "service now", "crew report", "crew reports",
                  "flight report", "reported flight"),
        "attributes": ("sn_validated_report_rate", "sn_service_deviation_rate"),
    },
    # Conduct as recorded by the people who flew with them.
    "conduct": {
        "terms": ("conduct", "behaviour", "behavior", "attitude", "professionalism",
                  "handover", "late reporting"),
        "attributes": ("sn_inflight_feedback_concern_rate",),
    },
    "service_deviation": {
        "terms": ("deviation", "deviations", "service standard", "service standards",
                  "cabin service", "service completed"),
        "attributes": ("sn_service_deviation_rate",),
    },
}


@dataclass
class Focus:
    """Which aspects a question asked to be judged on."""

    aspects: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    unscoreable: list[str] = field(default_factory=list)   # named but not available
    reason: str = ""
    # Sources the focused attributes actually come from. Carried so the answer
    # can say "this looked at the assessments, the inflight reports and the leave
    # system" — a manager's first question about a cross-source ranking is which
    # records it read, and a list of attribute names does not answer it.
    sources: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.attributes)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_focus(question: str, weightset: "WeightSet | None" = None) -> Focus:
    """Aspects named in the question, mapped to attributes that exist.

    Keyword matching rather than a model call: which metrics a ranking is built
    on is the single most consequential thing about it, and a silent
    misclassification would change who gets named as worst. A rule that misses
    an aspect degrades to the standard weighting, which is the safe direction.
    """
    q = (question or "").lower()
    # An aspect may name the recorded assessment verdict, which `fit` deliberately
    # leaves out of the standing weighting. It is scoreable for a question that
    # asks for it — `refocus` promotes it — so it counts as available here, or
    # "rank crew on feedback" would report the mentor's own mark as missing data.
    promotable = {a.attribute for a in getattr(weightset, "reportable", [])} if weightset else set()
    scoreable = ({a.attribute for a in weightset.attributes} | promotable) if weightset else None
    dropped = {e["attribute"] for e in weightset.excluded} - promotable if weightset else set()

    hit_aspects: list[str] = []
    attributes: list[str] = []
    unscoreable: list[str] = []
    partly: list[str] = []
    for name, spec in ASPECTS.items():
        if not any(t in q for t in spec["terms"]):
            continue
        hit_aspects.append(name)
        usable = [a for a in spec["attributes"]
                  if scoreable is None or a in scoreable]
        if usable:
            attributes.extend(a for a in usable if a not in attributes)
            # An aspect that spans sources can be answered from some of them and
            # not others — feedback especially, where a two-month ServiceNow
            # extract sits beside years of assessments. Saying which part is
            # missing is the difference between a ranking a manager can act on
            # and one they have to take on trust.
            if len(usable) < len(spec["attributes"]):
                partly.append(name)
        elif any(a in dropped for a in spec["attributes"]):
            unscoreable.append(name)

    if not hit_aspects:
        return Focus(reason="no aspect named; judged on the standard weighting")
    # This reason is shown to whoever reads the answer, not only written to the
    # trace — so it says what was done and what it costs, rather than naming the
    # match that triggered it.
    named = ", ".join(a.replace("_", " ") for a in hit_aspects)
    sources = _sources_behind(attributes)
    reason = (f"The question asked about {named}, so the crew are ranked on that "
              f"alone — this is not a ranking of overall performance.")
    if sources:
        reason += f" It draws on {_readable_list(sources)}."
    if unscoreable:
        reason += (f" There is no usable data on "
                   f"{', '.join(a.replace('_', ' ') for a in unscoreable)}.")
    if partly:
        reason += (" Some of what would normally count towards "
                   f"{', '.join(a.replace('_', ' ') for a in partly)} is not recorded "
                   "for enough crew to rank on, so it is left out rather than "
                   "counted as a zero.")
    return Focus(aspects=hit_aspects, attributes=attributes,
                 unscoreable=unscoreable, reason=reason, sources=sources)


# Which system a signal's evidence physically comes from. Keyed by attribute
# name prefix rather than read off `Attribute.source_tables`, because a Focus is
# built from names alone — the attribute objects need an executor to discover.
_SIGNAL_SOURCE = {
    "pass_rate_": "the mentors' assessments",
    "mean_mark": "the mentors' assessments",
    "top_grade_share": "the mentors' assessments",
    "safety_failure_rate": "the mentors' assessments",
    "critical_failure_rate": "the mentors' assessments",
    "initiative_rate": "the mentors' assessments",
    "sn_": "the inflight reports crew file after a sector",
    "leave_days_taken": "the leave system",
    "appreciation_count": "the leave system's appreciation records",
    "cac_appreciation_count": "the appreciation letters raised through the Crew "
                              "Appreciation Centre",
    "process_compliance_rate": "the flight-reporting portal",
    "checkin_failure_rate": "the flight-reporting portal",
    "flight_reports_filed": "the flight-reporting portal",
}


def _sources_behind(attributes: list[str]) -> list[str]:
    out: list[str] = []
    for attr in attributes:
        for prefix, label in _SIGNAL_SOURCE.items():
            if attr == prefix or attr.startswith(prefix):
                if label not in out:
                    out.append(label)
                break
    return out


def _readable_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def refocus(ws: WeightSet, focus: Focus) -> WeightSet:
    """Re-weight a fitted WeightSet onto the aspects a question asked about.

    Refocusing rather than refitting is deliberate. The evidence step — coverage,
    dispersion, correlation with the recorded mark — is a property of the data,
    not of the question, and re-running it per question would let a phrasing
    change quietly alter what counts as a reliable signal. Only the allocation
    of importance moves.
    """
    if not focus:
        return ws

    keep = set(focus.attributes)
    # Promote the recorded assessment verdict when — and only when — the question
    # asked for it. `fit` keeps it out of the standing weighting because the
    # composite is built from the answers it is computed from, which makes it
    # circular there. It is not circular in a ranking that names it: "best crew
    # on feedback" is asking for the mentor's mark, and answering that from the
    # attribute breakdown while silently dropping the mark itself would leave out
    # the single best-attributed piece of feedback the airline holds.
    promoted = [a for a in ws.reportable
                if a.attribute in keep
                and a.attribute not in {x.attribute for x in ws.attributes}]
    pool = [*ws.attributes, *promoted]

    in_focus = [a for a in pool if a.attribute in keep]
    if not in_focus:
        return ws

    # The two kinds of share are not on the same scale and must not be pooled. A
    # promoted attribute's `prior` is a share of THIS question — the recorded
    # verdict at 0.5 means "half of a ranking that asked for the assessment".
    # Everything else carries the standing the mechanism derived. Pooling them
    # read 0.5 against 0.04+0.03+0.02 and gave the mark 91% of the answer, which
    # is the native mark with three rounding errors attached rather than a
    # ranking that spans sources. So the promoted signals take their reserved
    # share of the focus and the rest divide what is left, each by its own
    # standing.
    # Two kinds of promotion, and only one of them reserves a share. The verdict
    # carries a `prior` because it would otherwise swamp a ranking it is only one
    # source's half of. A volume signal — `leave_days_taken` — carries none: it
    # was held back for being exposure rather than quality, not for being
    # overwhelming, so it is ranked alongside whatever else the question named.
    # A weightset cached before `held_for` existed carries only verdict signals in
    # `reportable`, so defaulting the unmarked to the reserved path leaves an old
    # file behaving exactly as it did.
    volume_names = {a.attribute for a in promoted if a.held_for == "volume"}
    reserved_names = {a.attribute for a in promoted if a.attribute not in volume_names}
    reserved = min(sum(a.prior for a in in_focus if a.attribute in reserved_names), 0.95)
    fitted_in_focus = [a for a in in_focus if a.attribute not in reserved_names]

    # Renormalise the non-promoted attributes on whatever THEY treat as
    # importance — decided from those attributes alone, never from the pool.
    #
    # Reading it off the pool is a bug that has now happened twice. The derived
    # mechanism gives every attribute a prior of 0.0, so a single promoted signal
    # carrying a prior flips this to "prior" and every other attribute in the
    # focus renormalises to zero: total weight 0.03, one signal carrying the
    # whole ranking, and five crew tied on it because they all have full marks.
    # The promoted signals are already handled by `reserved` and never consult
    # `basis` — so only the fitted ones may decide it.
    # A promoted volume signal has no derived weight — the standing mechanism
    # never scored it, so its `weight` is zero by construction and renormalising
    # on it hands the signal 0% of the ranking it was promoted to carry. Such a
    # focus renormalises on `evidence` instead: the measured reliability every
    # attribute carries, fitted or held back, so the two are on one scale. It is
    # read from the weightset, not recomputed, so this is still not a refit.
    if any(a.prior > 0 for a in fitted_in_focus):
        basis = "prior"
    elif any(a.attribute in volume_names for a in fitted_in_focus):
        basis = "evidence"
    else:
        basis = "weight"
    if not fitted_in_focus:
        reserved = 1.0          # nothing else was asked about; the verdict is the answer

    promoted_total = sum(a.prior for a in in_focus if a.attribute in reserved_names) or 1.0
    fitted_total = sum(getattr(a, basis) for a in fitted_in_focus) or 1.0

    def share_of(a: AttributeWeight) -> float:
        if a.attribute not in keep:
            return 0.0
        if a.attribute in reserved_names:
            return reserved * a.prior / promoted_total
        return (1.0 - reserved) * getattr(a, basis) / fitted_total

    focused: list[AttributeWeight] = []
    for a in pool:
        share = round(share_of(a), 6)
        if a.attribute in reserved_names:
            why = (f"in focus ({', '.join(focus.aspects)}): the recorded assessment "
                   f"verdict, which the question asked to be judged on — given "
                   f"{share:.0%} of this ranking by declared importance")
        elif a.attribute in volume_names and share:
            why = (f"in focus ({', '.join(focus.aspects)}): a volume measure, held out "
                   f"of the standing weighting because exposure is not quality — counted "
                   f"here at {share:.0%} because the question asked to be judged on it, "
                   f"{'fewer' if a.direction == 'lower_is_better' else 'more'} ranking better")
        elif share:
            why = (f"in focus ({', '.join(focus.aspects)}): {basis} "
                   f"{getattr(a, basis):.3f} renormalised over the {1 - reserved:.0%} "
                   f"of this ranking not reserved for the recorded verdict"
                   if reserved else
                   f"in focus ({', '.join(focus.aspects)}): {basis} "
                   f"{getattr(a, basis):.3f} renormalised over the aspects asked about")
        else:
            why = "not in focus — reported for context, not counted"
        focused.append(replace(a, weight=share, rationale=why))

    out = replace(
        ws,
        id=f"{ws.id}__focus_{'_'.join(focus.aspects)}",
        attributes=focused,
        notes=[*ws.notes,
               f"weights scoped to the question: {focus.reason}. Attributes outside "
               f"that focus are shown at zero weight, not removed — this is a ranking "
               f"on {', '.join(focus.aspects)}, not on overall performance."],
    )
    out.focus = focus
    if focus.unscoreable:
        out.notes.append(
            f"asked about {', '.join(focus.unscoreable)}, which no crew member has "
            f"enough data for — that part of the question is unanswered, not scored low"
        )
    return out
