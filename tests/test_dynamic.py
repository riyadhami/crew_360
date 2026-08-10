"""Agent 7 — the mechanism it designs, and the rules it must not break.

Runs without an LLM: the semantic pass is skipped, everything arithmetic is
exercised. The properties asserted here are the ones that make the mechanism
*data-derived* rather than a restatement of the business's weighting or of the
assessment form.
"""

from __future__ import annotations

import pandas as pd
import pytest

from crew_perf.agents import dynamic
from crew_perf.agents.attributes import build_frame, discover
from crew_perf.graph.store import get_store


@pytest.fixture(scope="module")
def store():
    return get_store()


@pytest.fixture(scope="module")
def designed(store, executor):
    return dynamic.design(store=store, executor=executor, use_llm=False)


@pytest.fixture(scope="module")
def frame(store, executor):
    return build_frame(executor, discover(store, executor))


def test_a_mechanism_is_produced(designed):
    ws, mech = designed
    assert ws.attributes, "no signal survived admission"
    assert mech.constructs
    # Tolerance tracks the 6dp rounding on each weight, not float epsilon.
    assert abs(sum(a.weight for a in ws.attributes) - 1.0) < 1e-4


def test_no_business_policy_is_read(designed):
    """The whole point of this agent. A weight traceable to a declared prior would
    make it Agent 2 with extra steps."""
    ws, _ = designed
    assert all(a.prior == 0.0 for a in ws.attributes)
    assert all(a.rule_ref is None for a in ws.attributes)


def test_policy_module_is_not_imported_by_the_agent():
    """Structural guard: declared importance must not reach this agent, and an
    import is the first way that creeps back in. Checked over the parsed imports
    rather than the text, so the module can still *discuss* the policy it refuses
    to read."""
    import ast

    tree = ast.parse(open(dynamic.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported += [f"{node.module}.{a.name}" for a in node.names if node.module]
    assert not any("policy" in name for name in imported), imported


def test_the_recorded_verdict_is_never_an_input(designed):
    """`mean_mark` is the weighted sum of the question answers. Scoring with it and
    then reporting the score against it is circular."""
    ws, mech = designed
    scored = {a.attribute for a in ws.attributes}
    assert not (dynamic.RECORDED_VERDICT & scored)
    rejected = {r["signal"] for r in mech.rejected}
    assert dynamic.RECORDED_VERDICT <= rejected


def test_correlation_with_the_mark_is_reported_not_weighted(designed):
    """Two signals with very different agreement with the mark must be able to
    hold similar weight — otherwise the mechanism is fitted to the form."""
    ws, _ = designed
    by_rho = [(a.rho_label, a.weight) for a in ws.attributes if a.rho_label is not None]
    assert by_rho, "no correlation was recorded at all"
    strongest = max(by_rho, key=lambda p: abs(p[0]))
    weakest = min(by_rho, key=lambda p: abs(p[0]))
    assert abs(strongest[0]) - abs(weakest[0]) > 0.2, "no spread to test against"
    assert weakest[1] > 0, "a signal was zeroed for disagreeing with the mark"


def test_redundant_signals_share_one_construct():
    """Signals that move together are one construct measured several times. Left
    unclustered a family of near-copies takes a share of the weight each, and the
    mechanism becomes a vote on how many times something was recorded.

    Built rather than measured: whether any two real signals happen to correlate
    is a property of today's data, and the behaviour under test is not."""
    import numpy as np

    rng = np.random.default_rng(0)
    base = rng.normal(size=400)
    frame = pd.DataFrame({
        "copy_a": base + rng.normal(scale=0.15, size=400),
        "copy_b": base + rng.normal(scale=0.15, size=400),
        "copy_c": base + rng.normal(scale=0.15, size=400),
        "independent": rng.normal(size=400),
    })
    clusters = dynamic.cluster_signals(frame, list(frame.columns))
    grouped = {frozenset(c) for c in clusters}
    assert frozenset({"copy_a", "copy_b", "copy_c"}) in grouped
    assert frozenset({"independent"}) in grouped


def test_one_construct_measured_three_times_does_not_outweigh_one_measured_once():
    """The reason clustering exists at all: weight belongs to the construct, and
    is shared inside it."""
    import numpy as np

    rng = np.random.default_rng(1)
    base = rng.normal(size=400)
    frame = pd.DataFrame({
        "copy_a": base + rng.normal(scale=0.1, size=400),
        "copy_b": base + rng.normal(scale=0.1, size=400),
        "copy_c": base + rng.normal(scale=0.1, size=400),
        "independent": rng.normal(size=400),
    })
    clusters = dynamic.cluster_signals(frame, list(frame.columns))
    assert len(clusters) == 2


def test_clustering_is_stable_and_partitions_exactly_once(frame):
    names = [c for c in frame.columns if frame[c].notna().sum() > dynamic.MIN_N][:12]
    a = dynamic.cluster_signals(frame, names)
    b = dynamic.cluster_signals(frame, names)
    assert sorted(sorted(c) for c in a) == sorted(sorted(c) for c in b)
    flat = [n for c in a for n in c]
    assert sorted(flat) == sorted(names)


def test_an_uninformative_signal_is_rejected(designed):
    """A check almost nobody fails cannot separate crew, however meaningful it is."""
    _, mech = designed
    reasons = {r["signal"]: r["reason"] for r in mech.rejected}
    assert any("almost everyone scores the same" in r for r in reasons.values())


def test_a_signal_too_sparse_to_rank_on_is_rejected(designed):
    _, mech = designed
    reasons = " ".join(r["reason"] for r in mech.rejected)
    assert "too few to rank on" in reasons


def test_environmental_signals_are_discounted_not_deleted():
    """A catering failure on someone's flight is a fact about the flight. It should
    count for less, not for nothing — deleting it would lose real information."""
    assert dynamic.ATTRIBUTION_FACTOR["environmental"] < dynamic.ATTRIBUTION_FACTOR["shared"]
    assert dynamic.ATTRIBUTION_FACTOR["shared"] < dynamic.ATTRIBUTION_FACTOR["direct"]
    assert dynamic.ATTRIBUTION_FACTOR["environmental"] > 0


def test_partial_coverage_is_flagged_on_every_affected_weight(designed):
    ws, _ = designed
    for a in ws.attributes:
        assert a.partial_population == (a.coverage < 0.30)


def test_the_coverage_audit_names_what_was_not_reached(designed):
    """"Designed from all the data points provided" is only checkable if the agent
    says which ones it actually used."""
    _, mech = designed
    audit = mech.coverage_audit
    assert audit["signals_used"] > 0
    assert audit["by_source"], "no per-source accounting"
    assert set(audit["by_source"]) >= {"PEP", "CLMS", "CrewPortal"}


def test_the_mechanism_separates_crew(designed, frame):
    ws, _ = designed
    result = dynamic.evaluate(ws, frame)
    assert result["crew_scored"] > 100
    # A mechanism that cannot tell most crew apart is not a ranking.
    assert result["distinct_scores"] > 0.9 * result["crew_scored"]


def test_it_does_not_reproduce_the_assessment_mark(designed, frame):
    """If it agreed with the mark almost perfectly it would be the PEP form again,
    and there would be no reason to run it."""
    ws, _ = designed
    result = dynamic.evaluate(ws, frame)
    if result.get("rho_native") is not None:
        assert abs(result["rho_native"]) < 0.95


def test_it_round_trips_as_a_weightset(designed, tmp_path):
    """Every downstream agent takes a WeightSet. If this is not one, scoring, the
    audit and the orchestrator all need a second code path."""
    import json

    from crew_perf.agents import weighting

    ws, _ = designed
    path = tmp_path / "dynamic.json"
    path.write_text(json.dumps(ws.to_dict(), default=str))
    loaded = weighting.load(path)
    assert loaded.weights() == ws.weights()


def test_scoring_accepts_the_dynamic_weightset(designed, store, executor, frame):
    from crew_perf.agents import evaluator, scoring

    ws, _ = designed
    iga = next(i for i in frame.index if pd.notna(frame.loc[i].get("mean_mark")))
    card = scoring.score(iga, ws, store=store, executor=executor, population=frame)
    assert card.composite_score is not None
    audit = evaluator.audit_score(card, ws, store=store, population=frame)
    assert not audit.errors(), [f.message for f in audit.errors()]
