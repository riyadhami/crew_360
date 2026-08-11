"""Re-verify the business question/answer pairs against the live dataset.

The pairs are a deliverable people quote from, which makes them the most
dangerous artifact in the repo: a stale number reads exactly like a fresh one.
So every pair carries the computation that produced it, and this runner reruns
them all. A pair that no longer reproduces fails loudly rather than quietly
becoming folklore.

Two kinds of check:

  sql        the answer is a fact about the warehouse — rerun the query and
             compare the named columns
  mechanism  the answer is about the scoring agents themselves (how far the two
             weightsets disagree, whether the data-derived one reproduces the
             assessment mark) — recomputed from the saved weightsets and the
             attribute frame, because those numbers cannot be expressed in SQL
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from crew_perf import config

QA_PATH = config.EVAL_DIR / "business_qa.yaml"
DIFFICULTIES = ("easy", "medium", "hard")


@dataclass
class PairResult:
    id: str
    difficulty: str
    question: str
    passed: bool = False
    checked: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    skipped: str = ""


def load_pairs() -> list[dict]:
    return yaml.safe_load(QA_PATH.read_text())["pairs"]


def _compare(name: str, actual, rule: dict) -> str | None:
    """One expectation. Returns a failure message, or None when it holds."""
    if actual is None:
        return f"{name}: no value returned"
    if "equals" in rule:
        expected = rule["equals"]
        if isinstance(expected, str):
            if str(actual).strip() != expected:
                return f"{name}: expected {expected!r}, got {str(actual)!r}"
        elif abs(float(actual) - float(expected)) > 1e-9:
            return f"{name}: expected {expected}, got {actual}"
    if "close_to" in rule:
        tolerance = float(rule.get("tolerance", 0.01))
        if abs(float(actual) - float(rule["close_to"])) > tolerance:
            return (f"{name}: expected {rule['close_to']} +/- {tolerance}, "
                    f"got {round(float(actual), 4)}")
    if "at_least" in rule and float(actual) < float(rule["at_least"]):
        return f"{name}: expected >= {rule['at_least']}, got {round(float(actual), 4)}"
    if "at_most" in rule and float(actual) > float(rule["at_most"]):
        return f"{name}: expected <= {rule['at_most']}, got {round(float(actual), 4)}"
    return None


# ─── Mechanism metrics ──────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def mechanism_metrics() -> dict:
    """Everything the `mechanism` checks can assert about, computed once.

    Deliberately recomputed from the saved weightsets rather than from a stored
    report: the question these pairs answer is whether the mechanisms still
    behave as documented, and re-reading a number the agent wrote down would
    only confirm that a file exists.
    """
    import numpy as np
    from scipy import stats

    from crew_perf.agents import dynamic, weighting
    from crew_perf.agents.attributes import build_frame, discover
    from crew_perf.data.executor import get_executor
    from crew_perf.graph.store import get_store

    store, executor = get_store(), get_executor()
    frame = build_frame(executor, discover(store, executor))

    business_path = config.GRAPHS_DIR / "weightset__all__v1.json"
    dyn_path = config.GRAPHS_DIR / "dynamic__v1.json"
    if not (business_path.exists() and dyn_path.exists()):
        raise FileNotFoundError(
            "both weightsets must exist — run `crewperf score <IGA> --refit` and "
            "`crewperf dynamic` first"
        )
    business, dyn = weighting.load(business_path), weighting.load(dyn_path)

    def rank(ws):
        cols = [a.attribute for a in ws.attributes if a.attribute in frame.columns]
        z = frame[cols].astype(float)
        z = (z - z.mean()) / z.std()
        for a in ws.attributes:
            if a.attribute in z.columns and a.direction == "lower_is_better":
                z[a.attribute] = -z[a.attribute]
        w = np.array([a.weight for a in ws.attributes if a.attribute in cols])
        return (z.fillna(0.0) @ (w / w.sum())).sort_values()

    r_business, r_dyn = rank(business), rank(dyn)
    common = r_business.index.intersection(r_dyn.index)

    both = frame.dropna(subset=["sn_service_response_rate", "mean_mark"])
    rho_issue = stats.spearmanr(both["sn_service_response_rate"], both["mean_mark"]).statistic

    leave = frame.dropna(subset=["leave_days_taken", "mean_mark"])
    rho_leave = stats.spearmanr(leave["leave_days_taken"].astype(float),
                                leave["mean_mark"].astype(float)).statistic

    # Crew strong on the assessment and heavily named on service reports. Named
    # means they acted on it, not that they caused it, so this counts crew the
    # two sources describe very differently — not crew with "bad outcomes".
    marks = both["mean_mark"].rank(pct=True)
    issues = both["sn_service_response_rate"].rank(pct=True)
    divergent = int(((marks >= 0.75) & (issues >= 0.75)).sum())

    evaluation = dynamic.evaluate(dyn, frame)
    sources = set()
    for attribute in dyn.attributes:
        for table in _tables_for(attribute.attribute, store, executor):
            node = store.table(table)
            if node:
                sources.add(node.source)

    return {
        "rank_agreement": float(stats.spearmanr(r_business[common], r_dyn[common]).statistic),
        "weakest_5_overlap": len(set(r_business.index[:5]) & set(r_dyn.index[:5])),
        "weakest_20_overlap": len(set(r_business.index[:20]) & set(r_dyn.index[:20])),
        "rho_issue_vs_mark": float(rho_issue),
        "n_measurable_on_both": int(len(both)),
        "rho_leave_vs_mark": float(rho_leave),
        "n_leave_and_mark": int(len(leave)),
        "divergent_crew": divergent,
        "rho_native": float(evaluation.get("rho_native") or 0.0),
        "rank_stability": float((evaluation.get("stability") or {}).get("agreement", 0.0)),
        "sources_reached": len(sources),
    }


@lru_cache(maxsize=None)
def _tables_for(attribute: str, store, executor) -> tuple[str, ...]:
    from crew_perf.agents.attributes import discover

    match = discover(store, executor).by_name(attribute)
    return tuple(match.source_tables) if match else ()


# ─── Runner ─────────────────────────────────────────────────────────────────


def run_pair(pair: dict, executor=None) -> PairResult:
    out = PairResult(id=pair["id"], difficulty=pair.get("difficulty", "?"),
                     question=pair["question"])
    verify = pair.get("verify") or {}
    expect = verify.get("expect") or {}
    if not expect:
        out.skipped = "no verification declared"
        return out

    if verify.get("kind") == "mechanism":
        try:
            actual = mechanism_metrics()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            out.failures.append(f"could not recompute: {type(exc).__name__}: {exc}")
            return out
    else:
        from crew_perf.data.executor import get_executor

        executor = executor or get_executor()
        res = executor.execute(verify["sql"], limit=5)
        if not res.rows:
            out.failures.append("verification query returned no rows")
            return out
        actual = dict(zip([c.lower() for c in res.columns], res.rows[0]))

    for name, rule in expect.items():
        problem = _compare(name, actual.get(name.lower(), actual.get(name)), rule)
        (out.failures if problem else out.checked).append(problem or name)

    out.passed = not out.failures
    return out


def run_all(difficulty: str | None = None, only: str | None = None) -> list[PairResult]:
    pairs = [
        p for p in load_pairs()
        if (not difficulty or p.get("difficulty") == difficulty)
        and (not only or p["id"] == only)
    ]
    return [run_pair(p) for p in pairs]
