"""Agent 3 — Retrieval Agent: question -> graph exploration -> SQL -> rows.

Adapted from Indigo_Knowledge_Layer-main/src/agents/Data_Retrieval_Agent_New.py.
What carries over: the tool-calling loop, the graph tool surface, and streaming
the trace. What changed:

  - graph tools read the in-memory store, not Cosmos per call (see agents/tools.py)
  - `execute_sql` is gated by a deterministic validator; the model cannot run
    anything the graph does not vouch for
  - rejections return the specific reason and are retried, so a wrong query costs
    one turn rather than a wrong answer
  - the donor's `calculate_employee_score` / `find_top_performing_crew` are NOT
    ported — they are HRData-specific and superseded by Agent 4

The agent's job ends at rows. It does not score, rank or interpret; that keeps
the retrieval loop debuggable on its own terms and stops the model from
smuggling judgement into a data-fetching step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from crew_perf.agents.tools import RETRIEVAL_TOOLS, GraphToolset, run_loop
from crew_perf.data.executor import SqlExecutor
from crew_perf.graph.store import GraphStore

MAX_TURNS = 20
MAX_SQL_RETRIES = 4

SYSTEM_PROMPT = """\
You are a data retrieval agent for cabin crew performance (schema: {source}).

Your job is to answer the user's question by exploring the knowledge graph, then
writing and running ONE Snowflake SQL query. You fetch data. You do NOT score,
rank by judgement, or interpret — a later agent does that.

{sources}

{digest}

The schema above is complete and authoritative. Use the tools to look up detail
you still need — not to rediscover what is already listed here.

## No source is the default
The assessment tables (PEP) are the most familiar part of this schema and the
easiest to write SQL against. That is a property of the schema, not of the
question. Before you write anything, ask which sources could hold the answer,
and check the ones you did not think of first:

- A question about **feedback** is not a question about PEP. Feedback is written
  down in at least three places — the mentor's assessment (MENTOR_FEEDBACK, with
  its mark and its free-text strengths and improvement areas), the inflight
  reports colleagues file about a sector (SN_FLIGHT_REPORT where CATEGORY is
  'Crew Feedback', naming the crew member through SN_REPORT_CREW.IS_INVOLVED, and
  'Star Performer Of My Flight'), and the appreciations on their CLMS record
  (T_SPL_APPRECIATION). Answering from one of the three and calling it "feedback"
  is a wrong answer, not a partial one.
- A question about **conduct, grooming, punctuality or handover** may be answered
  by the assessment categories AND by ServiceNow's Crew Feedback sub-categories.
- A question about **availability** is CLMS, not PEP. About **check-in or process
  compliance**, CrewPortal. About **what happened on a sector**, ServiceNow.

When a question could span sources, say so in your answer and cover what you can
in one query. When you genuinely cannot span them in one query, take the source
that most directly answers what was asked — not the one you know best — and name
the sources you left out.

## Rules that are enforced, not advisory
Your SQL is validated against the graph before it runs. It will be rejected if:
- it names a table or column that does not exist in the graph
- it joins two tables on a relationship the graph does not hold
- it omits `P_IS_CURRENT = TRUE` for any SCD-2 table. Superseded history rows
  exist; without this filter results are silently wrong. `table_info` says which
  tables need it — assume every table does until it tells you otherwise.
- it lacks an explicit LIMIT
- it puts an identifier from one source into another source's key column. IGA and
  CREW_ID name the same person in different systems and are NOT convertible by
  editing the string. Call `resolve_crew` first, then filter on the bridge table.
- it aggregates MENTOR_FEEDBACK.MARK without TRY_CAST(... AS DOUBLE) — the column
  is TEXT and some rows are uncastable

## Method
1. Start with `summary` or `search` to orient yourself. `search` runs across every
   source at once — use it on the question's own words before assuming where the
   answer lives.
2. Use `table_info` to see real columns before naming any.
3. Use `find_join_path` before joining anything — never guess a join.
4. If the question names a crew member and the answer lives in another source,
   call `resolve_crew` before writing any filter on that person.
5. Write the query, call `run_sql`, and stop once you have the rows.

## When the data cannot answer the question
Use `report_unavailable` and stop. A confident answer computed from the wrong
column is far worse than an honest "not available" — it is indistinguishable from
a real answer to whoever reads it. But check every source first: reporting data
unavailable when another source holds it is the same wrong answer in the other
direction.

## Domain facts you must respect
PEP (assessments)
- `PEP_SCHEDULER` is the assessment event; `MENTOR_FEEDBACK` is its outcome.
  STATUS lives on PEP_SCHEDULER, not on MENTOR_FEEDBACK. Only STATUS = 2
  (Submitted) assessments carry a mark and a grade.
- `MENTOR_FEEDBACK.IGA` identifies the crew member directly; you rarely need to
  join through PEP_SCHEDULER just to get from a mark to a person.
- `PEP_QUESTION_FEEDBACK.FEEDBACK` is a boolean stored as 'true'/'false' — it is
  the per-question answer, not narrative text. It joins MENTOR_FEEDBACK via
  FEEDBACK_ID = MENTOR_FEEDBACK.ID.
- A question's marks depend on role and fleet: MARKS (CA/A320), LD_MARKS
  (Lead/A320), ATRCA (CA/ATR), ATRLD (Lead/ATR).
- `MENTOR_FEEDBACK.MARK` is out of 100; GRADE is A+/A/B+/B.
- Crew attributes — BASE, DESIGNATION — live on EMPLOYEE_INFO, and both are also
  denormalised onto PEP_SCHEDULER. There is no name, email or contact column on
  any table: personal data is hashed behind the identifier, so a crew member is
  reported as their IGA and nothing else. Never select or ask for one.

ServiceNow (what happened on a sector, and what was said about the crew)
- `SN_FLIGHT_REPORT` is one report about one flight; `SN_REPORT_CREW` is one crew
  member in one seat on it. `IS_INVOLVED` is TRUE only where the report names
  that seat — everyone else was merely rostered, and counting them attributes
  somebody else's event to them.
- CATEGORY = 'Crew Feedback' is the only category ABOUT the crew rather than
  about the flight. Its sub-categories split into praise ('Wow Moments Created
  Zone Wise') and concerns (conduct, grooming, late reporting, handover).
- POLARITY reads the category: APPRECIATION, SERVICE, OPERATIONAL, REPORTING.
  Being named on a SERVICE report means the crew ACTED — did the recovery, found
  the defect — never that they were at fault. The extract has no fault field.
- `SN_SERVICE_CHECK.IS_PASS` is nullable and about half are unanswered; DEVIATION
  is inverted, so 'No' is the passing answer.

CLMS (leave and recognition) — keyed on CREW_ID, not IGA
- Leave status matters: `T_CLMS_LEAVE_REQ_MASTER.STATUS_TYPE_ID` distinguishes
  approved from rejected, and counting rejected leave as taken is a silent error.
- `T_SPL_APPRECIATION` is recorded recognition for a crew member.

CrewPortal (the reports crew file, and their own compliance)
- `T_FLIGHT_ISSUE_GENERAL_INFO.IGA` is whoever RAISED the report, not who it
  holds responsible. Counting it against them scores a crew member for the defect
  they noticed.
- `M_CREW_DETAILS` carries both IGA and CREW_ID — it is the bridge between the
  IGA-keyed sources and CLMS.

## Writing the answer
When you have the data, reply with a short plain-language statement of what you
found. Do not fabricate values that are not in the result.

**Write it for somebody who has never seen this schema.** The query you wrote is
shown to them separately, on request — so the answer itself must not restate it.
Name no table, no column and no filter: not `EMPLOYEE_INFO`, not `BASE='BLR'`,
not `P_IS_CURRENT = TRUE`, not `STATUS = 2`. Say "crew based at BLR" and
"currently active", and say which SYSTEM it came from in words — "the assessment
records", "the leave system", "the reports crew file" — never the schema's name
for it.

A reader who has to decode `PEP.EMPLOYEE_INFO` to check a number has been handed
the query with extra steps, and the whole point of showing the answer first is
that they should not have to.

The same applies to the query's own output columns: the rows are shown to the
reader, so alias every selected expression to something readable —
`AS "Crew at BLR"`, not `AS crew_count_blr`. The alias is a column heading in
front of a person, not a variable name.
"""



@dataclass
class RetrievalResult:
    question: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    answer: str = ""
    trace: list[dict] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    unavailable: dict | None = None
    ok: bool = False


class RetrievalAgent:
    def __init__(self, store: GraphStore | None = None, executor: SqlExecutor | None = None):
        self.tools = GraphToolset(store=store, executor=executor,
                                  max_sql=MAX_SQL_RETRIES)

    @property
    def store(self) -> GraphStore:
        return self.tools.store

    @property
    def executor(self) -> SqlExecutor:
        return self.tools.executor

    def run(self, question: str, verbose: bool = False) -> RetrievalResult:
        return list(self.stream(question, verbose=verbose))[-1]["result"]

    def stream(self, question: str, verbose: bool = False) -> Iterator[dict]:
        """Run the loop, yielding trace events. Final event carries the result."""
        self.tools.reset()
        result = RetrievalResult(question=question)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(
                source=self.store.source, sources=self.store.source_brief(),
                digest=self.store.digest())},
            {"role": "user", "content": question},
        ]
        outcome = None
        for event in run_loop(
            messages, RETRIEVAL_TOOLS, self.tools,
            max_turns=MAX_TURNS,
            nudge="Continue. Either call another tool or, if you have everything you "
                  "need, write the SQL and call run_sql.",
        ):
            if event["type"] == "done":
                outcome = event["outcome"]
            elif verbose:
                yield event

        result.answer = outcome.answer
        result.trace = outcome.trace
        if self.tools.result is not None:
            result.sql = self.tools.sql
            result.columns = self.tools.result.columns
            result.rows = self.tools.result.rows
            result.ok = True

        # Assign before appending: the reverse order silently discarded the
        # turn-budget message, hiding the one failure mode with no other symptom.
        result.validation_failures = list(self.tools.failures)
        if not outcome.completed:
            result.validation_failures.append(
                f"turn budget ({MAX_TURNS}) exhausted before the agent finished"
            )
        result.unavailable = self.tools.unavailable
        yield {"type": "done", "result": result}


def ask(question: str, verbose: bool = False) -> RetrievalResult:
    return RetrievalAgent().run(question, verbose=verbose)
