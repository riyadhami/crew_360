"""Agent 8 — the conversational layer: reasoning, and follow-up questions.

The pipeline before this agent answers one question at a time and forgets it.
That is fine for "what is the average mark at BOM", and wrong for the way people
actually use a scorecard: they ask for one, read it, and then ask *why*. Without
memory the second question is treated as a first question, so "why is their leave
score low?" gets routed to retrieval, finds no crew member named, and comes back
with a fleet-wide table — an answer to a question nobody asked.

Two jobs, and they are the same job seen from two sides:

  **reason**   a scorecard is a set of numbers, and numbers do not explain
               themselves. `explain()` writes the reasoning for a scoring answer
               *from the computed card* — which component moved the composite,
               what the audit found, what the confidence rests on.

  **follow up** `FollowUpAgent` answers the next question with the conversation
               and the computed results in front of it, and with the same graph
               and SQL tools Agent 3 has. It can therefore explain from what is
               already on screen, or go and get what it does not have — and the
               distinction is its decision, not the router's, which is what
               makes a misroute cheap.

**The model still never produces a number.** `explain()` is handed the arithmetic
Agent 4 already did and may only put it into words; the follow-up agent gets new
numbers the one way anything does, by running validated SQL. A prose layer that
could compute would be a second scoring implementation with no audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from crew_perf.agents.tools import GRAPH_TOOLS, RUN_SQL_TOOL, GraphToolset, run_loop
from crew_perf.data.executor import SqlExecutor
from crew_perf.graph.store import GraphStore
from crew_perf.llm import call_llm

MAX_TURNS = 14
MAX_HISTORY_TURNS = 8          # older turns are dropped, not summarised — see trim()

# Wording that only makes sense against something already said. "Why?" has no
# referent on a cold start, and a question that carries one of these after an
# answer is on screen is a follow-up whatever else it contains.
FOLLOWUP_HINTS = (
    "why", "how come", "explain", "elaborate", "what does that mean", "what do you mean",
    "tell me more", "more detail", "break that down", "walk me through", "how so",
    "is that good", "is that bad", "compared to", "what about", "how about",
    "and the", "what else", "anything else", "based on what", "according to what",
    "are you sure", "how did you", "where did", "which of them", "which of those",
    "same for", "do the same", "instead", "expand on",
)

# A pronoun with no antecedent in the sentence itself is a reference to the last
# answer. Checked only when there IS a last answer, so a cold "how is their
# performance" still routes normally.
#
# The ordinals earn their place: "how many duty hours did the first one fly?"
# opens with an aggregate interrogative and carries no pronoun, so without them
# it routes to retrieval — which has no memory, cannot resolve "the first one",
# and says so. The phrase is a reference to a list only this conversation holds.
_PRONOUN = re.compile(
    r"\b(they|them|their|theirs|he|him|his|she|her|hers|it|its|"
    r"that|those|these|this|the same|both|"
    r"the (?:first|second|third|last|other|rest|remaining)\b|"
    r"(?:one|any|each|all|none|some) of (?:them|those|these))\b",
    re.IGNORECASE,
)

# Asking outright for a score or a ranking. Agent 8 must never compute either —
# it has no weightset and no audit — so a request for one has to reach Agent 4
# however late in a conversation it arrives, or a scorecard turns into a refusal.
_SCORE_REQUEST = re.compile(r"\b(score|scores|rank|ranks|ranking)\b", re.IGNORECASE)

# A question this short after an answer is almost never a new investigation.
_SHORT_TURN_WORDS = 6


def refers_back(question: str) -> bool:
    """True when the wording explicitly points at something already said.

    Separate from `is_followup` because it settles the one collision the other
    rules cannot: "why is that score low?" and "rank the weakest 3 at DEL" both
    contain scoring vocabulary, and only the first is about the last answer.
    """
    return any(h in (question or "").lower() for h in FOLLOWUP_HINTS)


def asks_for_a_score(question: str) -> bool:
    return bool(_SCORE_REQUEST.search(question or ""))


@dataclass
class Turn:
    """One exchange, kept in the shape the next turn needs to reason about it.

    `facts` is deliberately the computed numbers rather than the rendered answer:
    the follow-up agent must reason about what was measured, and re-parsing prose
    to recover a composite score is how a number acquires a second, wrong value.
    """

    question: str
    kind: str                                  # scoring | retrieval | followup
    answer: str = ""
    facts: dict = field(default_factory=dict)


@dataclass
class Conversation:
    """The thread. Bounded, in memory, and owned by whoever is serving it."""

    id: str
    turns: list[Turn] = field(default_factory=list)

    def add(self, turn: Turn) -> Turn:
        self.turns.append(turn)
        self.trim()
        return turn

    def trim(self) -> None:
        """Keep the last N turns verbatim rather than summarising older ones.

        A summary of a scorecard is a paraphrase of numbers, and a paraphrased
        number is the one thing this system must never carry forward. Dropping
        the turn outright is honest: the agent can always re-score.
        """
        if len(self.turns) > MAX_HISTORY_TURNS:
            del self.turns[: len(self.turns) - MAX_HISTORY_TURNS]

    @property
    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    def igas(self) -> list[str]:
        """Everyone the conversation has discussed, most recent first."""
        seen: list[str] = []
        for turn in reversed(self.turns):
            for iga in turn.facts.get("igas", []):
                if iga not in seen:
                    seen.append(iga)
        return seen

    def brief(self) -> str:
        """The transcript, as the follow-up agent sees it.

        Facts are rendered as JSON-ish lines rather than prose so the model reads
        them as data it may cite exactly, not as text it may rephrase.
        """
        import json

        out = []
        for i, turn in enumerate(self.turns, 1):
            out.append(f"--- turn {i} ---")
            out.append(f"USER ASKED: {turn.question}")
            out.append(f"ROUTED TO: {turn.kind}")
            if turn.facts:
                out.append("COMPUTED:")
                out.append(json.dumps(turn.facts, default=str, indent=1)[:4000])
            if turn.answer:
                out.append(f"ANSWERED: {turn.answer[:1200]}")
        return "\n".join(out) if out else "(no previous turns)"


def is_followup(question: str, conversation: Conversation | None) -> bool:
    """Rule-first, and deliberately generous — a misroute here is cheap.

    The follow-up agent holds the same SQL and graph tools as Agent 3, so a new
    question sent here is answered rather than refused. The reverse mistake is
    the expensive one: a genuine follow-up sent to retrieval loses the context
    that gave it meaning and answers something else entirely.
    """
    if conversation is None or not conversation.turns:
        return False
    q = (question or "").strip().lower()
    if not q:
        return False
    if any(h in q for h in FOLLOWUP_HINTS):
        return True
    # "and DEL?" / "the leads too" — too short to stand alone.
    if len(q.split()) <= _SHORT_TURN_WORDS and not q.startswith(("how many", "what is")):
        return True
    return bool(_PRONOUN.search(q))


# ─── Reasoning over a computed scorecard ────────────────────────────────────

EXPLAIN_SYSTEM = """\
You are briefing a cabin-crew manager on scores that have ALREADY been computed.
Write for someone who runs the operation, not for an analyst. They want three
things and nothing else: what you looked at, who came out where, and why those
particular people.

## The shape of the briefing
Three or four short paragraphs, in this order. No headings.

1. **What was looked at, and how.** One or two sentences, first, before any name.
   Say which records the answer came from — `records_this_looked_at` names them —
   and what was counted. If `what_they_were_ranked_on` or
   `question_narrowed_the_scoring_to` is set, this is where you say that the
   ranking is on THAT and is not a ranking of overall performance. A manager who
   reads only this paragraph should know what question was actually answered.

2. **The answer.** The names, the score out of 10, and what that standing means
   against their peers in ordinary words.

3. **Why these people.** The heart of it, and the longest paragraph. For each
   person named, what in the records put them there. Quote the mentors' own words
   from `what_mentors_said_was_good` and `what_mentors_flagged` — those are the
   most business-legible evidence you have and they are usually the real answer
   to "why". `what_helped_most` and `what_hurt_most` give you the measures that
   moved the score, each with a plain-English `measures` line, their reading, how
   they compare to peers, and how much it mattered. Prefer "mentors repeatedly
   noted they anticipate customer needs" over any number, and reach for a number
   only when it is the point.

4. **How far to trust it.** Anything in `caveats_and_findings`, plus how many
   assessments each person has, in plain terms ("only two assessments, so treat
   this as a first read"). A low score on a thin sample is a thin sample, NOT a
   weak crew member — say which it is. Mention what is missing only where it
   changes the decision.

## Write it for the business
- Every measure carries a `measures` line saying what it is in plain English. Use
  those words. NEVER print an internal field name — no `pass_rate_aftertakeoff`,
  no `sn_cx_champion_rate`, no `composite_z`. If you cannot describe something
  without naming a field, leave it out.
- Banned vocabulary: composite, z-score, z, standard deviation, normalised,
  percentile, weight, weighting, coverage, construct, attribute, signal, prior,
  mechanism, variance, correlation, sigma. Say what they mean instead:
    "84th percentile"        -> "ahead of about 8 in 10 of their peers"
    "weight 1.0"             -> "this was the only thing the question asked about"
    "z = 0.98"               -> just say how far ahead they are, in plain words
    "coverage 76%"           -> "about three-quarters of what we normally look at"
- Plain, not dumbed down. Your reader runs a cabin-crew operation and knows what
  a service deviation and a grooming standard are. What they do not know, and do
  not need to, is how the score was computed.
- Numbers stay, jargon goes. A share is a fraction of 1: say "96%", never
  "0.9629". Scores are out of 10 and marks out of 100 — two decimals at most.
- Name at most three measures per person. The full breakdown is on screen beside
  you; listing every one restates the table instead of explaining it.

## Hard rules
- Every number you state must appear in the data below. Never compute, average,
  re-round or infer one.
- Never call a crew member good or bad on evidence the card does not contain.
- Critical and safety findings are never offset by a high score. If the card
  carries one, it leads the briefing, ahead of everything above.
- If several people share the same score, say so and say why — it means nothing
  measured here separates them, which is itself the finding.
- If the card says something is unavailable or out of scope, that is a fact about
  the data, not about the person. Say so; never fill the gap.
- The score comes from patterns found in the records themselves. If you mention
  where it came from, say that — never that it came from what the business
  values or declared, because nothing declared is read.

Plain prose. A short bullet list is fine when naming people. **Bold** the names
and the figures that matter. Answer the question that was actually asked, and
nothing else.
"""


def explain(
    question: str,
    cards: list,
    audits: list,
    *,
    notes: list[str] | None = None,
    rank_basis: str = "",
) -> str:
    """Prose reasoning over scorecards Agent 4 already computed.

    Returns "" if the model is unreachable: an answer with its cards and no
    narrative is degraded, an answer that fails outright over missing prose is
    broken, and the numbers are the part that matters.
    """
    if not cards:
        return ""
    payload = {
        "question": question,
        "how_these_scores_were_decided": (
            "from patterns found in the records themselves — each thing measured "
            "earns its share of the score from how well it actually tells crew apart"
        ),
        "what_they_were_ranked_on": rank_basis,
        "things_the_reader_must_be_told": notes or [],
        "crew": [_card_facts(c, a) for c, a in zip(cards, audits)],
    }
    if len({c.composite_score for c in cards}) == 1 and len(cards) > 1:
        payload["note_on_the_tie"] = (
            "every one of these crew scored identically. That is not a "
            "coincidence to gloss over: it means nothing measured here "
            "separates them, and the ranking cannot say who is strongest"
        )
    import json

    try:
        return call_llm(
            json.dumps(payload, default=str)[:24_000],
            system=EXPLAIN_SYSTEM,
            temperature=0.2,
        ).strip()
    except Exception:  # noqa: BLE001 - prose is an enhancement, never the answer
        return ""


def _standing(percentile: float | None) -> str:
    """A percentile said the way an operations manager would say it.

    Handed over pre-translated rather than left to the model. Asked to avoid the
    word "percentile" it reaches for "z" or "standard deviations" instead, which
    is further from the reader, not closer — the fix is to remove the statistic
    from what it is given, not to forbid the vocabulary and hope.
    """
    if percentile is None:
        return "no standing measured"
    if percentile >= 90:
        return "among the strongest in the fleet"
    if percentile >= 70:
        return f"ahead of about {round(percentile / 10)} in 10 of their peers"
    if percentile >= 55:
        return "a little above the middle of the fleet"
    if percentile > 45:
        return "around the middle of the fleet"
    if percentile > 30:
        return "a little below the middle of the fleet"
    if percentile > 10:
        return f"behind about {round((100 - percentile) / 10)} in 10 of their peers"
    return "among the weakest in the fleet"


def _importance(weight: float, total: float) -> str:
    """How much a measure mattered, as a sentence rather than a coefficient."""
    if total <= 0 or weight <= 0:
        return "not counted in this ranking"
    share = weight / total
    if share >= 0.95:
        return "this was the only thing counted"
    if share >= 0.4:
        return "the largest single factor"
    if share >= 0.15:
        return "a significant factor"
    return "a minor factor"


def _reading(value: float | None, direction: str, measures: str) -> str:
    """A raw measurement rendered the way its own description implies.

    A "share of ... " is a percentage to a reader and a fraction to the code; a
    count of appreciation letters is neither. Deciding here means the model is
    never handed 0.9629629629629629 and asked to be tactful about it.
    """
    if value is None:
        return "not measured"
    looks_like_a_share = ("share" in measures.lower() or "rate" in measures.lower()
                          or 0.0 <= value <= 1.0)
    if looks_like_a_share:
        return f"{value * 100:.0f}%"
    return f"{value:,.1f}".removesuffix(".0")


_ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)")

# The last-resort vocabulary swap, applied to any finding no rule below rewrote.
# Deliberately after the rules and not instead of them: word-for-word substitution
# turns "the standing is z = -3.30" into "the standing is how far from the middle
# = -3.30", which is not English. The rules produce sentences; this only catches
# a stray term in one that was otherwise readable.
_PLAIN_WORDS = (
    ("composite score", "overall score"),
    ("the composite", "the overall score"),
    ("composite", "overall score"),
    ("weightset", "scoring setup"),
    ("declared importance", "the measures that carry the most weight"),
    ("partial-population signals", "measures only part of the fleet has"),
    ("attributes", "measures"),
    ("attribute", "measure"),
    ("signals", "measures"),
    ("signal", "measure"),
)


def _plain_findings(findings: list[str], standing: str) -> list[str]:
    """Rewrite a scorecard's findings into something a manager can act on.

    The findings on the card are the audit trail and stay exactly as computed —
    "standing: 71st percentile on the signals the records themselves separate
    crew on", "z = -3.30", "only 87% of what we can normally measure was usable
    here". They have to: the evaluator re-reads them, and a caveat rounded off
    for readability is a caveat that can no longer be checked.

    They are also the single largest source of jargon in the briefing. The prompt
    forbids the vocabulary, but the model is handed a payload written in it and
    nothing else to describe the crew member with, so it hands some back. The
    translation therefore happens here, on the way into the briefing, rather than
    on the card or in the prompt.

    Anything unrecognised passes through with a light vocabulary swap. Dropping
    it would be worse than a clumsy sentence: several of these are the warnings a
    reader must not be shielded from.
    """
    out: list[str] = []
    for f in findings:
        out.append(_plain_finding(f, standing))
    return [f for f in out if f]


def _plain_finding(f: str, standing: str) -> str:  # noqa: PLR0911 - a lookup table
    low = f.lower()

    if f.startswith("standing:"):
        nums = _ORDINAL.findall(f)
        if len(nums) == 2:
            overall, mark = (_standing(float(n)) for n in nums)
            return (f"Weighing everything up they are {overall}; on the mentor's mark "
                    f"alone they are {mark}.")
        return ""

    if "NOTABLE DIVERGENCE" in f:
        better = "better" if "ranks better" in f else "worse"
        return (f"Worth noting: they come out clearly {better} across the wider "
                f"record than on the mentor's mark on its own. That is as much a "
                f"finding about how the assessment form is weighted as about them.")

    if "declared importance is scoreable" in f:
        pct = re.match(r"(\d+)%", f)
        named = f.split("—", 1)[1].split("could not be used")[0].strip() if "—" in f else ""
        head = (f"Only about {pct.group(1)}% of what we normally look at could actually "
                f"be measured here" if pct else "Part of what we normally look at could "
                "not be measured here")
        tail = f" — nothing is recorded on {named} for anyone" if named else ""
        return f"{head}{tail}, so this is a narrower read than usual."

    if "the 0-10 scale is at its" in f:
        floor = "floor" in low
        end, how = ("bottom", "far enough behind") if floor else ("top", "far enough ahead")
        return (f"They are at the very {end} of the scale — {how} that the score out of "
                f"10 cannot separate them from the others there. The individual measures "
                f"below can.")

    if "scored partly on signals only some of the fleet has" in f:
        named = f.split(":", 1)[1].split("—")[0].strip() if ":" in f else ""
        return (f"Some of what counted here — {named} — is only recorded for part of "
                f"the fleet. Crew without it were not judged on it, so this score is "
                f"not strictly like-for-like against someone scored without it."
                if named else
                "Some of what counted here is only recorded for part of the fleet, so "
                "this score is not strictly like-for-like against everyone else's.")

    if "critical/safety question failure" in f:
        n = re.match(r"(\d+)", f)
        count = f"{n.group(1)} " if n else ""
        return (f"{count}safety or critical failure(s) recorded. These are reported on "
                f"their own and are NOT offset by a good overall score.")

    if "recurring improvement area is a stronger signal" in f:
        return ("The same improvement area coming up across several assessments carries "
                "more weight than any single mark.")

    if "not a basis for ranking" in low or "usable, sample size stated" in low:
        # Already plain, and the assessment count is the whole point of it.
        return f

    return _swap_words(f)


def _swap_words(f: str) -> str:
    for jargon, plain in _PLAIN_WORDS:
        f = re.sub(re.escape(jargon), plain, f, flags=re.IGNORECASE)
    return f


def _records_behind(card) -> list[str]:
    """The systems whose records contributed weight to this score."""
    from crew_perf.agents.weighting import _sources_behind

    counted = [c.attribute for c in card.components if c.weight]
    if card.focus and card.focus.get("sources"):
        return list(card.focus["sources"])
    return _sources_behind(counted)


def _card_facts(card, audit=None) -> dict:
    """One scorecard, restated in the vocabulary a manager already has.

    The translation happens here rather than in the prompt on purpose. A model
    told to avoid statistics but handed a payload full of them will reliably
    hand a few back — it has nothing else to describe the crew member with. Give
    it standings, importance and readings already in words, and the jargon has
    no route to the page.
    """
    counted = sorted(
        (c for c in card.components if c.weight),
        key=lambda c: -abs(c.contribution or 0.0),
    )
    total_weight = sum(c.weight for c in counted) or 1.0
    helped = [c for c in counted if (c.contribution or 0) > 0]
    hurt = [c for c in counted if (c.contribution or 0) < 0]

    def described(c) -> dict:
        return {
            "measure": c.label or c.attribute,
            "measures": c.measures or "",
            "their_reading": _reading(c.raw, c.direction, c.measures or c.attribute),
            "against_peers": _standing(c.percentile),
            "how_much_it_mattered": _importance(c.weight, total_weight),
            "helped_or_hurt": "helped" if (c.contribution or 0) >= 0 else "hurt",
        }

    facts = {
        "who": {"name": card.iga, "id": card.iga,
                "base": card.base, "role": card.designation},
        "score_out_of_10": card.composite_score,
        "standing": _standing(card.composite_percentile),
        "assessments_behind_it": card.native.get("assessments"),
        "how_much_to_trust_it": {
            "sufficient": "enough assessments to rank on",
            "usable": "usable, but a small number of assessments",
            "indicative": "too few assessments to rank on — a first read only",
        }.get(card.confidence, card.confidence),
        "their_assessment_mark_out_of_100": card.native.get("mean_mark"),
        "usual_grade": card.native.get("modal_grade"),
        "what_helped_most": [described(c) for c in helped[:3]],
        "what_hurt_most": [described(c) for c in hurt[:3]],
        "what_mentors_said_was_good": card.positives[:6],
        "what_mentors_flagged": card.negatives[:6],
        # Translated on the way in, never on the card — see `_plain_findings`.
        # Several of these are caveats a reader must not be shielded from, so the
        # rule is rewrite-or-pass-through, never drop.
        "caveats_and_findings": _plain_findings(
            card.findings, _standing(card.composite_percentile)),
        # Which systems this score actually read. The first question anyone asks
        # of a cross-source ranking is where the evidence came from, and a list of
        # measure names does not answer it — "the mentors' assessments and the
        # inflight reports" does.
        "records_this_looked_at": _records_behind(card),
    }
    if card.focus:
        facts["question_narrowed_the_scoring_to"] = [
            a.replace("_", " ") for a in (card.focus.get("aspects") or [])
        ]
        facts["scope_warning"] = (
            "this score ranks them ONLY on what the question asked about, and "
            "says nothing about their overall performance"
        )
    if card.unavailable:
        # One sentence, no count, no field names. A count invites the briefing to
        # recite "8 things for X and 8 things for Y", which tells a reader
        # nothing they can act on; what they need is that roster data is absent
        # and therefore nothing here reflects hours flown.
        facts["not_covered"] = (
            "no roster data exists in any onboarded system, so nothing in this "
            "score reflects hours flown, sectors operated or duty time"
        )
    if audit is not None:
        facts["independent_check"] = (
            "passed" if audit.passed else "FAILED — this score is not trustworthy")
        problems = [f.message for f in audit.findings if f.severity != "info"]
        if problems:
            facts["independent_check_raised"] = problems
    return facts


# ─── Follow-up questions ────────────────────────────────────────────────────

FOLLOWUP_SYSTEM = """\
You are the analyst in an ongoing conversation about cabin crew performance
(schema: {source}). The user has already been given one or more answers. Your job
is the NEXT question.

## The conversation so far
{brief}

## The data you can reach
{sources}

{digest}

## No source is the default
The assessment tables (PEP) are the easiest part of this schema to query. That is
a property of the schema, not of the question. A question about **feedback** spans
the mentor's assessment, the inflight reports colleagues file naming this crew
member, and the appreciations on their leave record — answering from PEP alone and
calling it feedback is a wrong answer, not a partial one. Availability is CLMS;
check-in and process compliance are CrewPortal; what happened on a sector is
ServiceNow. Check the source you did not think of first.

## How to answer
1. If the conversation above already contains what is being asked — a score, a
   component, a finding, a returned row — answer from it and explain. Do not
   re-run a query to re-derive a number you were already given.
2. If the question needs data nobody has fetched yet, use the tools: explore the
   graph, then write and run ONE Snowflake query. `table_info` before naming a
   column; `find_join_path` before joining; `resolve_crew` before putting an
   identifier into another source's key column.
3. If the question asks for a *score* the conversation does not already hold —
   "now score the other three" — say plainly that scoring is a separate step and
   ask the user to request it directly. You must NOT compute a composite score,
   a weighting, a ranking, or a percentile yourself.

## Rules that hold whatever is asked
- Never state a number that is not in the conversation above or in a query result
  you just ran. Do not recompute, re-round, or average numbers you were given.
- Your SQL is validated before it runs: SCD-2 tables need `P_IS_CURRENT = TRUE`,
  every query needs an explicit LIMIT, and joins must exist in the graph.
  Aggregate `MENTOR_FEEDBACK.MARK` only as `TRY_CAST(MARK AS DOUBLE)`.
- What no onboarded system holds is listed above. Say so; never substitute a
  related-but-different table. Equally, never report something as missing without
  checking every source — the answer is often in the one you did not think of.
- A low score on a thin sample is a thin sample. Two scores are not comparable
  if they were decided different ways. If the conversation's numbers cannot
  support the comparison being asked for, say what is missing instead of
  answering.

## Write it for the business
You are talking to a cabin-crew manager, not an analyst.
- Never print an internal field or column name. The conversation gives each
  measure a plain-English name and a line saying what it is; use those.
- Banned vocabulary: composite, z-score, standard deviation, normalised,
  percentile, weight, weighting, coverage, construct, attribute, signal, prior,
  mechanism, correlation. Say what they mean instead — "ahead of about 8 in 10
  of their peers", "the only thing this question counted".
- Shares are percentages to a reader: "96%", never "0.9629". Scores are out of
  10, marks out of 100, two decimals at most.
- The mentors' own words are the most business-legible evidence you have. When
  the question is "why", reach for those before any number.

Reply in plain prose, 1-4 short paragraphs; a short bullet list is fine when you
are naming several people or measures. **Bold** the names and figures that
matter. No headings or tables — any rows you fetch are rendered for you.
"""

FOLLOWUP_TOOLS = [*GRAPH_TOOLS, RUN_SQL_TOOL]


@dataclass
class FollowUpResult:
    question: str
    answer: str = ""
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    used_data: bool = False        # did it go back to the warehouse, or explain?


class FollowUpAgent:
    """Answers the next question with the conversation and the tools in hand."""

    def __init__(self, store: GraphStore | None = None, executor: SqlExecutor | None = None):
        self.tools = GraphToolset(store=store, executor=executor, max_sql=3)

    def run(self, question: str, conversation: Conversation) -> FollowUpResult:
        return list(self.stream(question, conversation))[-1]["result"]

    def stream(self, question: str, conversation: Conversation) -> Iterator[dict]:
        self.tools.reset()
        store = self.tools.store
        result = FollowUpResult(question=question)

        messages = [
            {"role": "system", "content": FOLLOWUP_SYSTEM.format(
                source=store.source, brief=conversation.brief(),
                sources=store.source_brief(), digest=store.digest())},
            {"role": "user", "content": question},
        ]
        outcome = None
        for event in run_loop(
            messages, FOLLOWUP_TOOLS, self.tools,
            max_turns=MAX_TURNS,
            temperature=0.2,
            nudge="Continue. Answer from the conversation above if it already holds "
                  "what was asked, or call a tool to fetch what it does not.",
        ):
            if event["type"] == "done":
                outcome = event["outcome"]
            else:
                yield event

        result.answer = outcome.answer
        result.trace = outcome.trace
        result.validation_failures = list(self.tools.failures)
        if self.tools.result is not None:
            result.sql = self.tools.sql
            result.columns = self.tools.result.columns
            result.rows = self.tools.result.rows
            result.used_data = True
        if not outcome.completed:
            result.validation_failures.append(
                f"turn budget ({MAX_TURNS}) exhausted before the agent finished"
            )
        if not result.answer:
            result.answer = (
                "I could not put that together from the conversation or the data. "
                "Try asking it as a standalone question."
            )
        yield {"type": "done", "result": result}
