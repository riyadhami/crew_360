"""Ground-truth evaluation harness.

Measures how well an estimator recovers each crew member's hidden competence.
Reads `latent_traits.parquet`, which lives outside the queryable database — no
agent can reach it; only this harness can.

The headline number is *relative*, not absolute. Absolute Spearman is capped by
how much signal the data physically contains (~0.71 here, set by assessment
volume and answer noise), so an absolute target like "> 0.8" is unreachable no
matter how good the agents are. What is meaningful is where a score lands
between two reference points:

    native   — mean MENTOR_FEEDBACK.MARK, i.e. what production already reports
    oracle   — the same observable signals combined with the generator's true
               importance weights: the best any weighting can do here

`recovery` reports the fraction of the native->oracle gap that an estimator
closes. 0.0 means "no better than production"; 1.0 means "found the true
weights". That is the number Agent 2 is actually judged on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from crew_perf import config
from crew_perf.data.executor import DuckDBExecutor
from crew_perf.data.synth import reference as R

# Categories every crew member is assessed on — derived from the reference,
# not hardcoded. A literal list here silently broke when the real form's codes
# replaced the invented ones, and the benchmark cannot detect that itself.
# LEADSONLY is excluded: Cabin Attendants never answer it, so it is null for
# ~78% of crew and would drop them from the comparison entirely.
_CA_TEMPLATE = next(t[0] for t in R.TEMPLATES if t[3] == "CA")
SHARED_CATEGORIES = [c for c in R.TEMPLATE_CATEGORIES[_CA_TEMPLATE] if c != "LEADSONLY"]
CATEGORY_TRAIT = {c[1]: c[4] for c in R.CATEGORIES}


def load_ground_truth() -> pd.DataFrame:
    if not config.LATENT_TRAITS_PATH.exists():
        raise FileNotFoundError(
            f"No ground truth at {config.LATENT_TRAITS_PATH}. Run `crewperf synth` first."
        )
    return pd.read_parquet(config.LATENT_TRAITS_PATH)


def category_pass_rates(ex: DuckDBExecutor) -> pd.DataFrame:
    """Per-crew pass rate for each assessment category."""
    res = ex.execute(
        """
        SELECT mf.IGA AS iga, q.CATEGORY_CODE AS cat,
               AVG(CASE WHEN qf.FEEDBACK = 'true' THEN 1.0 ELSE 0.0 END) AS pass_rate,
               COUNT(*) AS n
        FROM PEP_QUESTION_FEEDBACK qf
        JOIN PEP_QUESTIONS q  ON q.QUESTION_ID = qf.QUESTION_ID
        JOIN MENTOR_FEEDBACK mf ON mf.ID = qf.FEEDBACK_ID
        WHERE mf.P_IS_CURRENT = TRUE AND qf.P_IS_CURRENT = TRUE
        GROUP BY 1, 2
        """,
        limit=10**6,
    )
    df = pd.DataFrame(res.rows, columns=res.columns)
    return df.pivot(index="iga", columns="cat", values="pass_rate")


def native_marks(ex: DuckDBExecutor) -> pd.Series:
    """Mean MENTOR_FEEDBACK.MARK per crew — the production baseline."""
    res = ex.execute(
        """
        SELECT IGA AS iga, AVG(TRY_CAST(MARK AS DOUBLE)) AS mean_mark
        FROM MENTOR_FEEDBACK
        WHERE P_IS_CURRENT = TRUE AND TRY_CAST(MARK AS DOUBLE) IS NOT NULL
        GROUP BY 1
        """,
        limit=10**6,
    )
    df = pd.DataFrame(res.rows, columns=res.columns)
    return df.set_index("iga")["mean_mark"]


# Cross-source proxies for traits PEP cannot observe. `reliability` shows up as
# leave taken and check-in failures, both inverted (more leave = less available).
CROSS_TRAIT_PROXIES = {
    "reliability": [("leave_days_taken", -1), ("checkin_failure_rate", -1)],
}


def _cross_source_frame(ex: DuckDBExecutor):
    """Cross-source attribute values per crew, if those sources are onboarded."""
    from crew_perf.agents.attributes import build_cross_source_frame, discover
    from crew_perf.graph.store import get_store

    try:
        return build_cross_source_frame(ex, discover(get_store(), ex))
    except Exception:  # noqa: BLE001 - the benchmark must work on PEP alone
        return pd.DataFrame()


def benchmark(ex: DuckDBExecutor | None = None) -> dict:
    """Compute the native and oracle reference points.

    The oracle is "the best any weighting could do with what is *observable*".
    When a trait is only visible through another source — `reliability` lives in
    CLMS/CrewPortal, not PEP — its proxies have to enter the oracle too, or the
    benchmark quietly understates what is achievable and every estimator scores
    against a ceiling that is lower than the real one.
    """
    ex = ex or DuckDBExecutor()
    latent = load_ground_truth().set_index("IGA")
    piv = category_pass_rates(ex)
    marks = native_marks(ex)

    d = piv.join(latent[["true_competence", "DESIGNATION"]], how="inner").join(
        marks.rename("native_mark"), how="inner"
    )
    d = d.dropna(subset=SHARED_CATEGORIES + ["native_mark"])

    signals = list(SHARED_CATEGORIES)
    weights = [R.TRAIT_IMPORTANCE[CATEGORY_TRAIT[c]] for c in SHARED_CATEGORIES]

    cross = _cross_source_frame(ex)
    for trait, proxies in CROSS_TRAIT_PROXIES.items():
        importance = R.TRAIT_IMPORTANCE.get(trait)
        if not importance or cross.empty:
            continue
        usable = [(c, sign) for c, sign in proxies if c in cross.columns]
        if not usable:
            continue
        share = importance / len(usable)
        for col, sign in usable:
            d[col] = sign * cross[col].reindex(d.index)
            # A crew member missing from a source keeps their PEP signals; the
            # gap becomes the population mean rather than an implicit zero.
            d[col] = d[col].fillna(d[col].mean())
            signals.append(col)
            weights.append(share)

    # Every other observable attribute belongs in the ceiling too, not just the
    # trait proxies — otherwise an agent using more signals than the oracle scores
    # above 1.0, and "recovery" stops meaning anything.
    for col in cross.columns:
        if col not in signals:
            d[col] = cross[col].reindex(d.index)
            d[col] = d[col].fillna(d[col].mean())
            signals.append(col)
            weights.append(0.0)          # weighted by the fit below, not by hand

    z = (d[signals] - d[signals].mean()) / d[signals].std()
    z = z.fillna(0.0)

    native = stats.spearmanr(d["native_mark"], d["true_competence"])[0]
    equal = stats.spearmanr(z.mean(axis=1), d["true_competence"])[0]

    # The oracle is the best ANY linear combination of the observable signals
    # could achieve — obtained by fitting against the hidden truth, which no
    # agent can do. A hand-weighted combination is not a ceiling: an agent with
    # access to more attributes will beat it, and did (recovery 1.10), which
    # made the metric meaningless rather than impressive.
    truth = d["true_competence"].to_numpy()
    design = np.column_stack([np.ones(len(z)), z.to_numpy()])
    coef, *_ = np.linalg.lstsq(design, truth, rcond=None)
    oracle = stats.spearmanr(design @ coef, truth)[0]

    return {
        "n_crew": len(d),
        "native": float(native),
        "equal_weight": float(equal),
        "oracle": float(oracle),
        "headroom": float(oracle - native),
        "signals": signals,
        "_frame": d,
        "_z": z,
    }


def recovery(scores: pd.Series, bench: dict | None = None) -> dict:
    """Score an estimator against the native/oracle reference points.
    `scores` maps IGA -> a composite score (higher = better crew member).
    """
    bench = bench or benchmark()
    d = bench["_frame"]
    aligned = scores.reindex(d.index).dropna()
    if len(aligned) < 30:
        raise ValueError(f"only {len(aligned)} crew overlap with the ground truth")

    truth = d["true_competence"].reindex(aligned.index)
    rho = float(stats.spearmanr(aligned, truth)[0])
    span = bench["oracle"] - bench["native"]
    return {
        "n": len(aligned),
        "rho": rho,
        "native": bench["native"],
        "oracle": bench["oracle"],
        "recovery": float((rho - bench["native"]) / span) if abs(span) > 1e-9 else float("nan"),
    }
