"""Orchestrator — routes a user prompt through the agent pipeline.

Three shapes of question, and telling them apart is the whole job:

  scoring   "how is IGA60406 doing", "rank the weakest crew at DEL"
            -> Agent 7 (weights) -> Agent 4 (scorecard) -> Agent 5 (audit)
            -> Agent 8 (reasoning over the computed card)

  retrieval "what is the average mark at DEL", "which questions fail most"
            -> Agent 3 (graph -> SQL -> rows)

  followup  "why is that low?", "how does she compare to the others?"
            -> Agent 8, holding the conversation AND the graph/SQL tools

Routing is a small classification, not a judgement call, so it is done by rules
first. A misroute is cheap to detect and expensive to hide: a scoring question
answered by the retrieval agent returns rows with no audit, no confidence and no
evidence trail, which looks like an answer.

The follow-up branch is checked first and deliberately errs toward taking it,
because its two error costs are not symmetric. Agent 8 carries Agent 3's tools,
so a fresh question sent there is still answered; but a genuine follow-up sent
anywhere else has lost the context that gave it meaning — "why?" with no memory
routes to retrieval, finds nobody named, and returns a fleet-wide table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from crew_perf.agents.conversation import (
    Conversation, Turn, asks_for_a_score, is_followup, refers_back,
)
from crew_perf.graph.store import GraphStore, get_store

IGA_PATTERN = re.compile(r"\bIGA\s*0*(\d{3,})\b", re.IGNORECASE)

SCORING_HINTS = (
    "score", "scoring", "rank", "ranking", "best", "worst", "weakest", "strongest",
    "top ", "bottom ", "how is", "how are", "performing", "performance of",
    "evaluate", "assessment of", "doing",
)
RETRIEVAL_HINTS = (
    "how many", "count", "average", "list", "which questions", "distribution",
    "what is the", "show me", "breakdown",
)


# "the weakest 3 crew", "top 10 leads". Bounded because the caller's default is
# a display limit, not an invitation to score the whole fleet one card at a time.
COUNT_PATTERN = re.compile(
    r"\b(?:top|bottom|weakest|strongest|best|worst|lowest|highest)\s+(\d{1,2})\b"
    r"|\b(\d{1,2})\s+(?:weakest|strongest|best|worst|lowest|highest)\b",
    re.IGNORECASE,
)


def requested_count(question: str) -> int | None:
    """The number of crew the question actually asked for, if it named one.

    Answering "rank the weakest 3" with five scorecards is not a rounding error
    — it is a different answer, and the extra two read as findings the user
    never asked about.
    """
    m = COUNT_PATTERN.search(question)
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 1 <= n <= 50 else None


# Wording that asks to be ranked on the assessment itself rather than on the
# composite built from the data.
#
# This cannot be served by weighting `mean_mark` into the composite, and the
# reason matters: the mentor mark is the label the attributes are judged against.
# Feeding it back in as an input is circular — it correlates 1.0 with itself, so
# it would take almost all the weight and the composite would become the native
# mark with extra steps. The mechanism therefore excludes it as "is the label,
# not an input", which is right. The gap was that the native track was computed
# and displayed but never rankable, so a question about mentor feedback silently
# got a composite ranking instead.
#
# "mentor feedback" is deliberately NOT here any more. A question about feedback
# is a question about everything anybody wrote down — the mentor's assessment,
# the inflight reports colleagues file naming the crew member, the appreciations
# on their record — and answering it on the PEP mark alone silently discarded two
# of the three sources that hold it. `weighting.ASPECTS["feedback"]` spans them,
# and a focus beats the native track in `stream()` below, so these terms are now
# only the ones that name the recorded *number* and nothing broader.
NATIVE_TERMS = (
    "mentor score", "mentor mark", "mentor rating",
    "assessment mark", "assessment score", "assessment result",
    "pep mark", "pep score", "pep result",
    "grade", "grades", "marks", "the mark",
)


def wants_native_track(question: str) -> bool:
    """True when the question asks about the recorded assessment, not the composite."""
    q = (question or "").lower()
    return any(t in q for t in NATIVE_TERMS)


# A superlative attached to a recorded QUANTITY rather than to a person.
#
# "top 5 crew which has taken the lowest leaves" is not a performance question.
# It asks for a fact the warehouse already holds — who took the fewest leave days
# — and the honest way to answer it is a query, ordered by that column.
#
# Scoring it instead was wrong three times over, and the failure was silent:
#
#   1. It inverted the answer. `lowest` set `wanted_worst`, so the pipeline
#      returned the *weakest* five on a leave-scoped composite. Leave is
#      lower-is-better, so those are the crew who took the MOST leave — the exact
#      opposite of what was asked, with no error anywhere.
#   2. It answered a question nobody asked, ranking on a composite when the
#      question named a column.
#   3. The reasoning then explained the result with Check-in reliability, Ground
#      Duties and Poise & Grace — signals carrying zero weight in a
#      leave-focused ranking.
#
# The distinction is which noun the superlative modifies. "lowest LEAVES" ranks a
# quantity; "worst CREW in terms of leave" ranks people and merely scopes what
# counts. Only the first is a data question, so only an ordering word sitting
# next to a measure name routes to retrieval — `worst`, `best`, `top` and
# `bottom` are deliberately absent, because those attach to people.
_QUANTITY_ORDER = r"(?:most|least|fewest|lowest|highest|max(?:imum)?|min(?:imum)?)"


@lru_cache(maxsize=1)
def _measure_terms() -> tuple[str, ...]:
    """The words that name something the warehouse counts, from `ASPECTS`.

    Read off the same table `detect_focus` uses, so a new aspect cannot be
    rankable by the scorer and invisible to this at the same time.
    """
    from crew_perf.agents.weighting import ASPECTS

    terms = {t for spec in ASPECTS.values() for t in spec["terms"] if len(t) > 3}
    return tuple(sorted(terms, key=len, reverse=True))


def asks_for_a_quantity(question: str) -> bool:
    """True when an ordering word sits beside the name of a measure."""
    q = (question or "").lower()
    for term in _measure_terms():
        # `s?` because the question says "leaves" and the aspect says "leave".
        # Without it the one phrasing this exists to catch does not match.
        noun = rf"{re.escape(term)}s?\b"
        # Within a short window either way: "lowest leaves", "leave taken, least".
        window = rf"{_QUANTITY_ORDER}\W+(?:\w+\W+){{0,2}}{noun}"
        reverse = rf"\b{noun}\W+(?:\w+\W+){{0,2}}{_QUANTITY_ORDER}"
        if re.search(window, q) or re.search(reverse, q):
            return True
    return False


@dataclass
class Route:
    kind: str                       # "scoring" | "retrieval" | "followup"
    igas: list[str] = field(default_factory=list)
    reason: str = ""
    count: int | None = None        # crew asked for, when the question says


def classify(question: str, conversation: Conversation | None = None) -> Route:
    """Rule-first routing. Cheap, deterministic, and explains itself."""
    q = question.lower()
    igas = [f"IGA{m}" for m in IGA_PATTERN.findall(question)]
    n = requested_count(question)

    scoring_hit = next((h for h in SCORING_HINTS if h in q), None)
    retrieval_hit = next((h for h in RETRIEVAL_HINTS if h in q), None)

    # "Score IGA60024 too", "rank the weakest 3 at DEL" — a fresh scoring request
    # that happens to arrive mid-conversation. Agent 8 has no weightset and no
    # audit and is forbidden from computing a composite, so one of these swallowed
    # as a follow-up comes back as a refusal instead of a scorecard.
    #
    # Unless the wording explicitly points backwards: "why is that score low?"
    # carries the same vocabulary and is unambiguously about the last answer.
    demands_a_score = (igas and scoring_hit) or asks_for_a_score(question)
    if not (demands_a_score and not refers_back(question)) \
            and is_followup(question, conversation):
        return Route("followup", igas,
                     "follow-up to the previous answer; kept in context", n)

    # An ordering word next to the name of a measure asks for that measure, not
    # for a judgement about people — see `asks_for_a_quantity`. Checked before the
    # scoring branches because "top 5 crew … lowest leaves" satisfies both, and
    # scoring it returns the opposite of what was asked.
    #
    # The recorded assessment is the exception: "highest mark" names the label
    # the scorer already ranks on natively, and that path reports the mark itself
    # rather than a composite, so it is already the right answer.
    if asks_for_a_quantity(question) and not wants_native_track(question):
        return Route("retrieval", igas,
                     "asks to rank a recorded quantity, not to judge crew", n)

    # A named crew member plus a performance word is unambiguous.
    if igas and scoring_hit:
        return Route("scoring", igas, f"names {igas[0]} and asks about {scoring_hit!r}", n)

    # An aggregate interrogative with nobody named is a question about the
    # population, whatever performance vocabulary it also contains: "how many
    # crew scored best" wants a count, not a stack of scorecards. Requiring the
    # absence of scoring words instead would misroute it, because such questions
    # almost always mention scoring.
    if retrieval_hit and not igas:
        return Route("retrieval", igas,
                     f"aggregate question ({retrieval_hit!r}) with no crew member named", n)
    if retrieval_hit and not scoring_hit:
        return Route("retrieval", igas, f"aggregate question ({retrieval_hit!r})", n)
    if scoring_hit:
        return Route("scoring", igas, f"performance question ({scoring_hit!r})", n)
    return Route("retrieval", igas, "no performance wording; treated as a data question", n)


@dataclass
class PipelineResult:
    question: str
    route: Route
    scorecards: list = field(default_factory=list)
    audits: list = field(default_factory=list)
    retrieval: object | None = None
    followup: object | None = None
    notes: list[str] = field(default_factory=list)
    rank_basis: str = ""       # what the ordering was actually computed on
    narrative: str = ""        # Agent 8's reasoning over the numbers above


def run(question: str, store: GraphStore | None = None, executor=None,
        top_n: int = 5, conversation: Conversation | None = None) -> PipelineResult:
    return list(stream(question, store=store, executor=executor, top_n=top_n,
                       conversation=conversation))[-1]["result"]


def stream(question: str, store: GraphStore | None = None, executor=None,
           top_n: int = 5, conversation: Conversation | None = None,
           explain: bool = True) -> Iterator[dict]:
    """Run the pipeline, yielding stage events; the last carries the result.

    Pass a `Conversation` to keep the thread: the route sees what came before,
    and every turn is recorded on it. Without one the pipeline behaves exactly as
    it did — one question, answered and forgotten — which is what the CLI and the
    eval harnesses want.
    """
    from crew_perf.data.executor import get_executor

    store = store or get_store()
    executor = executor or get_executor()

    route = classify(question, conversation)
    result = PipelineResult(question=question, route=route)
    yield {"stage": "route", "detail": f"{route.kind} — {route.reason}"}

    if route.kind == "followup":
        from crew_perf.agents.conversation import FollowUpAgent

        yield {"stage": "agent 8", "detail": "answering in context of the conversation"}
        follow = FollowUpAgent(store=store, executor=executor).run(question, conversation)
        result.followup = follow
        result.narrative = follow.answer
        if follow.used_data:
            yield {"stage": "agent 8", "detail": f"ran a query: {len(follow.rows)} row(s)"}
        _record(conversation, result)
        yield {"stage": "done", "result": result}
        return

    if route.kind == "retrieval":
        from crew_perf.agents.retrieval import RetrievalAgent

        yield {"stage": "agent 3", "detail": "graph exploration -> validated SQL"}
        result.retrieval = RetrievalAgent(store=store, executor=executor).run(question)
        _record(conversation, result)
        yield {"stage": "done", "result": result}
        return

    # ── Scoring path ──
    from crew_perf.agents import dynamic as dynamic_agent
    from crew_perf.agents import evaluator, scoring, weighting
    from crew_perf.agents.attributes import build_frame, discover

    yield {"stage": "agent 7", "detail": "designing a scoring mechanism from the data "
                                         "and the graph"}
    ws, mechanism = dynamic_agent.design(store=store, executor=executor)
    result.notes.append(
        f"These scores come from patterns found in the records themselves. "
        f"The data shows {len(mechanism.constructs)} genuinely separate things "
        f"being measured, and each one earns its share of the score from how "
        f"well it actually tells crew apart."
    )

    # "Worst crew in terms of leave and service" was answered with the standard
    # weights — 25% of them on leave and service. That is a ranking of overall
    # performance wearing the question's label, and it names different people.
    focus = weighting.detect_focus(question, ws)
    # A named aspect wins. "Rank crew by grooming marks" is a question about
    # grooming that happens to use the word "marks" — ranking it on the overall
    # assessment would answer a question nobody asked. The native track is for
    # questions that name the assessment and nothing more specific.
    native_rank = wants_native_track(question) and not focus
    if native_rank:
        # Naming the assessment explicitly is a request for that number, not for
        # a weighted view of the attributes behind it.
        focus = weighting.Focus(reason="ranked on the recorded assessment mark")
    if focus:
        ws = weighting.refocus(ws, focus)
        yield {"stage": "agent 2", "detail": f"weights scoped to {', '.join(focus.aspects)} "
                                             f"({len(focus.attributes)} attributes counted)"}
        result.notes.append(focus.reason)
        if focus.unscoreable:
            result.notes.append(
                f"No crew member has enough data on "
                f"{', '.join(a.replace('_', ' ') for a in focus.unscoreable)}, "
                f"so that part of the question is left unanswered rather than "
                f"scored badly."
            )
    elif focus.aspects:
        # The question named something and NOTHING that measures it is rankable.
        # `Focus` is falsy here, so the ranking silently reverted to the standing
        # weighting and reported itself as an ordinary answer — a general ranking
        # of overall performance handed back under the question's own label,
        # naming people the asker was not asking about. The ranking still runs,
        # because a list is more use than a refusal, but it now says what it is.
        named = ", ".join(a.replace("_", " ") for a in focus.aspects)
        result.notes.append(
            f"Nothing we record measures {named} well enough to rank crew on it, so "
            f"this is NOT a ranking on {named}. What follows is the standing view of "
            f"overall performance, and it may well name different people than a real "
            f"{named} ranking would."
        )

    population = build_frame(executor, discover(store, executor))

    # Hoisted out of the ranking branch: the ordering of the finished cards reads
    # it too, and a question naming two crew directly ("compare IGA1 and IGA2")
    # never enters that branch.
    wanted_worst = any(w in question.lower()
                       for w in ("worst", "weakest", "bottom", "lowest"))

    targets = route.igas
    if not targets:
        # A ranking question: score the population, return the ends of it. The
        # question's own count wins over the caller's display default.
        top_n = route.count or top_n
        yield {"stage": "agent 4", "detail": f"scoring population for a top/bottom {top_n}"}
        subset = _filtered_population(question, population, executor)
        if native_rank:
            ranked = _rank_native(subset)
            result.rank_basis = "the marks their mentors actually gave them"
        else:
            ranked = _rank(subset, ws)
            # Say what the ordering was computed on, in words a reader has.
            # Naming it "the data-derived composite" satisfies that on paper and
            # not in fact: nobody outside this codebase can act on a phrase they
            # cannot read.
            composite = ("an overall score built from what the records themselves "
                         "show separates crew")
            result.rank_basis = (
                f"{composite}, looking only at {', '.join(focus.aspects)}"
                if focus.aspects else composite
            )
        targets = list(ranked.index[: top_n] if wanted_worst else ranked.index[-top_n:][::-1])
        if not targets:
            result.notes.append(
                "Nothing could be ranked: none of the things this question asks "
                "about could be measured for the crew in scope, so there is no "
                "ordering to report."
            )
        result.notes.append(
            f"Ranked {len(ranked)} crew on {result.rank_basis}; "
            f"showing the {'weakest' if wanted_worst else 'strongest'} {len(targets)}."
        )

    for iga in targets:
        card = scoring.score(iga, ws, store=store, executor=executor, population=population)
        audit = evaluator.audit_score(card, ws, store=store, population=population)
        if audit.confidence_downgrade:
            card.confidence = audit.confidence_downgrade
            card.findings.append(
                f"confidence downgraded by audit: {audit.confidence_downgrade}"
            )
        result.scorecards.append(card)
        result.audits.append(audit)
        yield {"stage": "agent 4/5", "detail": f"{iga}: {card.composite_score} "
                                               f"({'audit ok' if audit.passed else 'AUDIT FAILED'})"}

    # Order the answer by the number the reader can see.
    #
    # WHICH crew are returned comes from `_rank`, which scores the whole
    # population with missing signals filled at the population mean — the only
    # way to compare people who do not all have the same signals. Each card is
    # then scored on its own, where a missing signal is renormalised out of that
    # person's denominator instead. The two are close but not identical, so a
    # "best 5" picked by one and displayed with the other came out visibly
    # unsorted: 8.17, 7.64, 8.13. Sorting on the displayed composite makes the
    # list self-consistent.
    #
    # It does NOT make the selection exact: the five returned are the top five by
    # population ranking, which may not be the top five by card score. Closing
    # that would mean scoring the whole population card-by-card, which is the
    # expensive thing `_rank` exists to avoid.
    if len(result.scorecards) > 1:
        pairs = sorted(
            zip(result.scorecards, result.audits),
            key=lambda ca: (ca[0].composite_score is None,
                            ca[0].composite_score or 0.0),
            reverse=not wanted_worst,
        )
        result.scorecards = [c for c, _ in pairs]
        result.audits = [a for _, a in pairs]

    # The numbers are the answer; the reasoning is what makes them usable. Run
    # last and over the *audited* cards, so a confidence the audit downgraded is
    # the one the prose explains — narrating first would describe a card that no
    # longer exists.
    if explain and result.scorecards:
        from crew_perf.agents import conversation as convo

        yield {"stage": "agent 8", "detail": "reasoning over the computed scorecards"}
        result.narrative = convo.explain(
            question, result.scorecards, result.audits,
            notes=result.notes, rank_basis=result.rank_basis,
        )
        # The card carries its own reasoning when it is the only one, so a
        # scorecard handed anywhere else — saved, re-rendered, cited later —
        # arrives with the explanation attached rather than beside it.
        if len(result.scorecards) == 1:
            result.scorecards[0].narrative = result.narrative

    _record(conversation, result)
    yield {"stage": "done", "result": result}


def _record(conversation: Conversation | None, result: PipelineResult) -> None:
    """Append this turn to the thread, as facts rather than as prose.

    What goes in is what the next turn may cite: computed numbers, the SQL that
    produced rows, the notes that qualify them. What stays out is anything that
    would have to be re-derived to be used — a rendered table, a rounded score
    read back off a card.
    """
    if conversation is None:
        return
    from crew_perf.agents.conversation import _card_facts

    facts: dict = {"notes": result.notes}
    if result.rank_basis:
        facts["ranked_on"] = result.rank_basis

    if result.route.kind == "scoring":
        facts["igas"] = [c.iga for c in result.scorecards]
        facts["scorecards"] = [
            _card_facts(c, a) for c, a in zip(result.scorecards, result.audits)
        ]
    elif result.route.kind == "retrieval":
        r = result.retrieval
        facts["igas"] = list(result.route.igas)
        if r is not None:
            facts["sql"] = r.sql
            facts["columns"] = list(r.columns)
            # Rows are capped hard: the transcript is re-sent on every subsequent
            # turn, so a 500-row answer would otherwise cost its full width for
            # the rest of the conversation.
            facts["rows"] = [list(row) for row in r.rows[:30]]
            facts["row_count"] = len(r.rows)
            facts["unavailable"] = r.unavailable
    else:  # followup
        f = result.followup
        facts["igas"] = conversation.igas()[:1]
        if f is not None and f.used_data:
            facts["sql"] = f.sql
            facts["columns"] = list(f.columns)
            facts["rows"] = [list(row) for row in f.rows[:30]]
            facts["row_count"] = len(f.rows)

    answer = result.narrative
    if not answer and result.retrieval is not None:
        answer = result.retrieval.answer
    conversation.add(Turn(question=result.question, kind=result.route.kind,
                          answer=answer, facts=facts))


def _filtered_population(question: str, population, executor):
    """Apply base/designation filters mentioned in the question.

    Ranking crew at DEL against the whole airline would be a different question
    from the one asked, so the filter has to be applied before ranking, not after.
    """
    q = question.upper()
    res = executor.execute(
        "SELECT IGA, BASE, DESIGNATION FROM EMPLOYEE_INFO WHERE P_IS_CURRENT = TRUE",
        limit=10**6,
    )
    import pandas as pd

    who = pd.DataFrame(res.rows, columns=[c.lower() for c in res.columns]).set_index("iga")
    keep = who.index
    bases = [b for b in who["base"].dropna().unique() if re.search(rf"\b{b}\b", q)]
    if bases:
        keep = who[who["base"].isin(bases)].index
    desigs = [d for d in who["designation"].dropna().unique()
              if re.search(rf"\b{d}\b", q)]
    if desigs:
        keep = who.loc[keep][who.loc[keep]["designation"].isin(desigs)].index
    return population.loc[population.index.intersection(keep)]


def _rank_native(population):
    """Ascending ranking on the recorded assessment mark.

    Deliberately not a z-score: the mark is already on one absolute scale that
    every mentor uses, so normalising it would only obscure it. Crew with no
    submitted assessment are dropped rather than sorted to the bottom — having
    no mark is not the same as having a bad one, and the worst-N list is exactly
    where that confusion does damage.
    """
    if "mean_mark" not in population.columns:
        return population.iloc[0:0].assign(_v=0.0)["_v"]
    return population["mean_mark"].astype(float).dropna().sort_values()


def _rank(population, weightset):
    """Ascending weighted-z ranking of a population."""
    import numpy as np

    cols = [a.attribute for a in weightset.attributes if a.attribute in population.columns]
    if not cols:
        return population.iloc[0:0].assign(_v=0.0)["_v"]
    frame = population[cols].astype(float)
    z = (frame - frame.mean()) / frame.std()
    for a in weightset.attributes:
        if a.attribute in z.columns and a.direction == "lower_is_better":
            z[a.attribute] = -z[a.attribute]
    w = np.array([a.weight for a in weightset.attributes if a.attribute in cols])
    if w.sum() <= 0:
        # Every weight zero produces NaN for the whole population, and NaN sorts
        # to the front — so the "weakest three" came back as the first three crew
        # by index, each with a score of None, looking like an answer.
        return population.iloc[0:0].assign(_v=0.0)["_v"]
    return (z.fillna(0.0) @ (w / w.sum())).sort_values()
