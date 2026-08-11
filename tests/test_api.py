"""The HTTP surface.

What is worth pinning here is not that the endpoints return 200 — it is that the
web surface cannot drift away from the CLI. Two paths to the same number is two
places for them to disagree, and a page that quietly disagrees with the tool
operations actually runs is worse than no page.

The surface is deliberately small: one question box, one streaming answer. The
crew directory, the weights table and the graph explorer are gone, and with them
the second way of reaching a score. The heaviest tests here are on the split
between what the answer SHOWS and what it carries underneath — that is now a
property of the payload, not of a stylesheet.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="API extra not installed")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client(duckdb_snapshot, monkeypatch_session):
    from crew_perf.api.app import app

    return TestClient(app)


@pytest.fixture(scope="module")
def monkeypatch_session(duckdb_snapshot):
    """Point the whole module at the snapshot, never the working database."""
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    mp.setattr("crew_perf.config.DUCKDB_PATH", duckdb_snapshot)
    import crew_perf.data.executor as ex_mod

    mp.setattr(ex_mod, "_executor", None, raising=False)
    yield mp
    mp.undo()


def test_ask_streams_progress_before_the_result(client):
    """The pipeline takes tens of seconds. If the response only flushed at the
    end, the stage events would be decoration — the whole reason for SSE is that
    they arrive while the work is still happening."""
    seen: list[str] = []
    with client.stream("GET", "/api/ask", params={"q": "How is IGA60406 performing?"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event: "):
                seen.append(line.removeprefix("event: "))

    assert "result" in seen, "no result event"
    assert seen.index("stage") < seen.index("result"), "stages must precede the result"
    assert seen[-1] == "result", "the result must be last"


def test_the_server_survives_more_than_one_question(client):
    """It did not, and the failure mode was the worst kind: the first question
    worked, the second segfaulted the process. Handing the pipeline to the event
    loop's default executor builds a fresh thread per request, and pyarrow's
    allocator dies re-initialising in one. A long-lived worker fixes it — and
    also serialises access to the DuckDB connection, which is a shared singleton
    and not safe to drive from two threads at once."""
    for _ in range(3):
        with client.stream("GET", "/api/ask", params={"q": "How is IGA60406 performing?"}) as r:
            assert r.status_code == 200
            assert any(line.startswith("event: result") for line in r.iter_lines())


def test_the_synthetic_benchmark_is_not_exposed(client):
    """`recovery` is measured against latent traits that exist only because the
    data is generated. In production that harness has nothing to read, so an
    endpoint for it would be a permanently broken tab."""
    assert client.get("/api/benchmark").status_code == 404
    paths = client.get("/openapi.json").json()["paths"]
    assert not any("benchmark" in p for p in paths)


def test_overview_separates_the_graph_from_the_warehouse(client):
    """The reasoning graph covers one source; the warehouse holds all three.
    Reporting both counts unlabelled reads as a contradiction."""
    o = client.get("/api/overview").json()
    assert o["graph_source"]
    assert o["tables_loaded"] >= o["graph"]["tables"]
    assert o["graph"]["verified_joins"] <= o["graph"]["join_edges"]


def test_rule_provenance_is_reported(client):
    """The UI distinguishes rules read from the warehouse from rules pinned in
    config. That distinction is only possible if the API carries it."""
    rules = client.get("/api/overview").json()["rules"]
    assert rules["grading"].startswith(("data", "declared"))
    assert "PEP_DEVIATION_MATRIX" in rules["grading"] or rules["grading"].startswith("declared")


# ─── scoring mechanism ──────────────────────────────────────────────────────


# ─── the plain/technical split ──────────────────────────────────────────────


def _ask(client, question: str) -> dict:
    """Run one question and return the result payload."""
    payload = None
    with client.stream("GET", "/api/ask", params={"q": question}) as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and '"detail"' in line:
                payload = json.loads(line.removeprefix("data: "))
    assert payload, f"no result for {question!r}"
    return payload


# Column identifiers that used to reach the page. Any of these in the visible
# half of an answer is the bug this split exists to prevent.
IDENTIFIERS = ("sn_service_response_rate", "pass_rate_", "checkin_failure_rate",
               "sn_inflight_feedback", "mean_mark", "top_grade_share",
               "process_compliance_rate", "appreciation_count", "_rate", "_count")


def test_a_scoring_answer_is_plain_english_with_the_apparatus_underneath(client, sample_iga):
    """The reader gets names, a score and reasons. `sn_service_response_rate` is
    a column, and handing it to somebody who has to act on a crew member's
    performance tells them nothing — but it must still be one click away, or the
    score cannot be checked."""
    payload = _ask(client, f"How is {sample_iga} performing?")
    assert payload["kind"] == "scoring"
    assert payload["crew"], "a scoring answer must name the crew it scored"

    visible = json.dumps({k: v for k, v in payload.items() if k != "detail"})
    for identifier in IDENTIFIERS:
        assert identifier not in visible, f"{identifier} leaked into the visible answer"

    for member in payload["crew"]:
        assert member["iga"] and member["name"]
        assert member["score"] is None or 0 <= member["score"] <= 10
        assert member["confidence"]

    detail = payload["detail"]
    assert detail["signals"], "the apparatus must still be carried"
    assert all(s["identifier"] and s["signal"] for s in detail["signals"]), (
        "every signal must carry both its identifier and a readable name"
    )


def test_a_scoring_answer_carries_its_own_audit(client, sample_iga):
    """A score without its audit is a number with no warranty. It moved into
    `detail` when the page stopped showing it by default — it must not have been
    dropped on the way."""
    payload = _ask(client, f"How is {sample_iga} performing?")
    detail = payload["detail"]
    assert "audit" in detail
    assert detail["reasoning"], "the reasoning behind a score must survive"
    assert detail["unavailable"], "out-of-scope data must still be stated"


def test_a_data_question_returns_its_rows_and_hides_its_sql(client):
    """The rows ARE the answer to a data question, so they stay visible. The
    query that produced them is apparatus: useful to check, noise to read."""
    payload = _ask(client, "how many crew are based at DEL?")
    assert payload["kind"] == "retrieval"
    if payload["table"]:
        assert payload["table"]["columns"]
    assert "sql" in payload["detail"]
    visible = json.dumps({k: v for k, v in payload.items() if k != "detail"})
    assert "SELECT" not in visible.upper(), "SQL must not reach the visible answer"


def test_the_removed_pages_are_gone_from_the_surface(client):
    """Crew directory, weights and graph were three more ways to reach a score,
    each with its own rendering of it. One question box is the whole product now,
    and a route left behind is a second answer waiting to disagree."""
    for path in ("/api/crew", "/api/weights", "/api/graph",
                 "/api/filters", "/api/score/IGA60406"):
        assert client.get(path).status_code == 404, f"{path} still answers"
