"""The graph and SQL tool surface shared by Agent 3 and Agent 8.

The loops themselves need an LLM and are exercised by `crewperf golden`; these
cover the tools they call, which must never crash a loop and must never invite
the model to invent a join. They are tested once, here, because both agents get
them from the same object — a second copy is exactly what this file exists to
prevent.
"""

import pytest

from crew_perf.agents.tools import RETRIEVAL_TOOLS, GraphToolset
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def tools(executor):
    return GraphToolset(store=get_store(), executor=executor)


def test_table_info_returns_columns_and_currency_requirement(tools):
    info = tools.table_info("MENTOR_FEEDBACK")
    assert info["scd2"] is True
    assert info["requires_filter"] == "P_IS_CURRENT = TRUE"
    names = {c["name"] for c in info["columns"]}
    assert {"IGA", "MARK", "GRADE"} <= names
    assert info["joins"], "no joins offered — the model would have to guess"


def test_unknown_table_suggests_alternatives_instead_of_failing(tools):
    info = tools.table_info("MENTOR_FEEDBAK")
    assert "error" in info
    assert info["did_you_mean"]


def test_find_join_path_refuses_to_invent(tools):
    """When no verified path exists the tool must say so explicitly — silence
    invites the model to make one up.

    A grading band and a ServiceNow report category share no key and no route
    through the crew spine; nothing relates them and the tool must say so."""
    out = tools.find_join_path("PEP_GRADE", "M_SN_CATEGORY")
    assert out["path"] is None
    assert "do not invent" in out["note"].lower()


def test_a_question_answer_reaches_its_assessment(tools):
    """`PEP_QUESTION_FEEDBACK.FEEDBACK_ID = MENTOR_FEEDBACK.ID` is the join the
    scoring agent itself writes. While it was missing from the graph the
    validator refused the only correct query for "which crew failed a safety
    question", and the agent reported data unavailable that was sitting there."""
    out = tools.find_join_path("PEP_QUESTION_FEEDBACK", "MENTOR_FEEDBACK")
    assert out.get("path"), out
    assert out["hops"] == 1


def test_find_join_path_returns_the_assessment_chain(tools):
    out = tools.find_join_path("PEP_QUESTION_FEEDBACK", "PEP_TEMPLATE")
    assert out.get("path")
    assert out["hops"] >= 1


def test_sample_rows_filters_history(tools):
    out = tools.sample_rows("PEP_SCHEDULER", limit=3)
    assert out["rows"] and len(out["rows"]) <= 3
    assert not any(c.upper().startswith("P_") for c in out["columns"])


def test_run_sql_rejects_before_executing(tools):
    tools.reset()
    out = tools.run_sql("SELECT IGA, MARK FROM MENTOR_FEEDBACK LIMIT 5")
    assert out["rejected"]
    assert any("P_IS_CURRENT" in e for e in out["errors"])
    assert tools.result is None, "a rejected query must not reach the database"


def test_run_sql_executes_a_valid_query(tools):
    tools.reset()
    out = tools.run_sql(
        "SELECT IGA, MARK FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 5",
        purpose="sample marks",
    )
    assert out.get("ok")
    assert out["row_count"] == 5
    assert tools.result is not None


def test_the_sql_retry_budget_is_finite(tools):
    """A model that keeps rewriting a rejected query must be stopped by the
    toolset, not by the loop around it — otherwise every agent has to remember
    to count, and the one that forgets spends its whole turn budget retrying."""
    tools.reset()
    for _ in range(tools.max_sql):
        tools.run_sql("SELECT IGA FROM MENTOR_FEEDBACK LIMIT 1")   # rejected: no SCD-2 filter
    out = tools.run_sql("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 1")
    assert out["rejected"]
    assert "budget" in " ".join(out["errors"])
    tools.reset()


def test_dispatch_survives_a_failing_tool(tools):
    """One bad lookup must degrade to a message, not end the run."""
    out = tools.dispatch("table_info", {"wrong_arg": 1})
    assert "error" in out
    out = tools.dispatch("no_such_tool", {})
    assert "error" in out and "available" in out


def test_reset_clears_state_between_questions(tools):
    tools.reset()
    tools.run_sql("SELECT IGA FROM MENTOR_FEEDBACK WHERE P_IS_CURRENT = TRUE LIMIT 1")
    assert tools.result is not None
    tools.reset()
    assert tools.result is None and not tools.failures and tools.sql is None


def test_resolve_crew_translates_an_iga_into_every_source_key(tools):
    """Left to reason about it the agent stripped 'IGA' off IGA60406 and filtered
    CLMS on '60406' — a valid query returning nothing, reported as "this crew
    member has no leave". The lookup is one indexed row; making it a tool removes
    the reasoning step rather than trying to improve it."""
    out = tools.resolve_crew("IGA60406")
    assert out["found"] is True
    assert out["keys"]["IGA"] == "IGA60406"
    assert out["keys"]["CREW_ID"] and out["keys"]["CREW_ID"] != "60406"
    assert out["bridge_table"] == "M_CREW_DETAILS"


def test_resolve_crew_refuses_to_guess_for_an_unknown_identifier(tools):
    out = tools.resolve_crew("IGA00000")
    assert out["found"] is False
    assert "do not guess" in out["note"]


def test_every_advertised_tool_has_a_handler(tools):
    """A tool the model can name but the toolset cannot run comes back as
    "unknown tool" mid-loop, which reads to the model as its own mistake and
    sends it round again."""
    advertised = {t["function"]["name"] for t in RETRIEVAL_TOOLS}
    assert advertised <= set(tools.handlers())
    assert "resolve_crew" in advertised
    assert "error" not in tools.dispatch("resolve_crew", {"identifier": "IGA60406"})
