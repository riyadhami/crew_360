"""Load generated PEP data into DuckDB and verify the assessment chain.

The reconciliation check is the point of this module, not the loading. It
recomputes every stored MARK/GRADE from the raw question answers through the
template's mark column and the deviation matrix. If that disagrees with what is
stored, the generator's causal chain is broken — and every downstream agent
would then be fitting to a fiction.
"""

from __future__ import annotations

import pandas as pd

from crew_perf import config
from crew_perf.data.executor import DuckDBExecutor
from crew_perf.data.synth import reference as R
from crew_perf.data.synth.generate import GenConfig, generate

# Ground truth is written outside the queryable database so no agent can reach it.
GROUND_TRUTH_DIR = config.DATA_DIR / "ground_truth"


def load_to_duckdb(tables: dict[str, pd.DataFrame], path=None) -> DuckDBExecutor:
    ex = DuckDBExecutor(path or config.DUCKDB_PATH)
    for name, df in tables.items():
        ex.register_df(name, df)
    return ex


def write_ground_truth(latent: pd.DataFrame) -> None:
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    latent.to_parquet(config.LATENT_TRAITS_PATH, index=False)


def reconcile(ex: DuckDBExecutor) -> dict:
    """Recompute MARK and GRADE from question answers; compare against stored.

    Recomputation deliberately goes through the same joins an agent would use:
    scheduler -> employee -> template (by designation + fleet) -> question marks.
    """
    # Which template applied to each assessment: designation + the flight type
    # recorded on the flight row. This is exactly the role x fleet selection
    # that makes the four mark columns meaningful.
    # Template resolution goes through PEP_CATEGORY now: the delivered schema
    # has no PEP_TEMP_CAT_MAPPING and no M_FLIGHT_TYPE, so a question's template
    # is reached via its (template-scoped) category rather than by matching
    # designation and fleet against a lookup.
    mark_column_case = "\n                        ".join(
        f"WHEN c.TEMPLATE_ID = {tid} THEN q.{col}"
        for tid, _code, _name, _desg, _ft, col in R.TEMPLATES
    )
    sql = f"""
    WITH answered AS (
        SELECT
            mf.ID                AS feedback_id,
            mf.MARK              AS stored_mark_text,
            mf.GRADE             AS stored_grade,
            c.TEMPLATE_ID        AS template_id,
            COUNT(*)             AS answered_q,
            SUM(
                CASE WHEN qf.FEEDBACK = 'true' THEN
                    CASE {mark_column_case}
                    END
                ELSE 0 END
            ) AS recomputed_mark
        FROM PEP_QUESTION_FEEDBACK qf
        JOIN PEP_QUESTIONS q  ON q.QUESTION_ID = qf.QUESTION_ID
        JOIN PEP_CATEGORY  c  ON c.CATEGORY_ID = q.CATEGORY_ID
        JOIN MENTOR_FEEDBACK mf ON mf.ID = qf.FEEDBACK_ID
        WHERE qf.P_IS_CURRENT = TRUE AND q.P_IS_CURRENT = TRUE
          AND c.P_IS_CURRENT = TRUE AND mf.P_IS_CURRENT = TRUE
        GROUP BY 1, 2, 3, 4
    ),
    -- Questions each template should have answered. Assessments answering fewer
    -- lost children to the deliberate orphan-FK injection and cannot reconcile
    -- by construction, so they are reported rather than counted as a break.
    tmpl_qcount AS (
        SELECT c.TEMPLATE_ID, COUNT(*) AS expected_q
        FROM PEP_QUESTIONS q
        JOIN PEP_CATEGORY c ON c.CATEGORY_ID = q.CATEGORY_ID
        WHERE q.P_IS_CURRENT = TRUE AND c.P_IS_CURRENT = TRUE AND q.ACTIVE = TRUE
        GROUP BY 1
    )
    SELECT
        a.feedback_id,
        a.stored_mark_text,
        TRY_CAST(a.stored_mark_text AS DOUBLE) AS stored_mark,
        a.recomputed_mark,
        a.stored_grade,
        dm.GRADE AS recomputed_grade,
        a.answered_q,
        tq.expected_q,
        (a.answered_q = tq.expected_q) AS intact
    FROM answered a
    JOIN tmpl_qcount tq ON tq.TEMPLATE_ID = a.template_id
    -- Highest band the mark reaches, not a closed interval: marks are
    -- fractional (91.3 is ordinary), and integer BETWEEN ranges leave a gap
    -- between every adjacent pair of bands for such a mark to fall through.
    LEFT JOIN LATERAL (
        SELECT dm.GRADE
        FROM PEP_DEVIATION_MATRIX dm
        WHERE dm.TEMPLATE_ID = a.template_id
          AND dm.P_IS_CURRENT = TRUE
          AND a.recomputed_mark >= dm.START_RANGE
        ORDER BY dm.START_RANGE DESC
        LIMIT 1
    ) dm ON TRUE
    """
    res = ex.execute(sql, limit=1_000_000)
    df = pd.DataFrame(res.rows, columns=res.columns)

    castable = df[df["stored_mark"].notna()]
    intact = castable[castable["intact"]]
    mark_match = (intact["stored_mark"] - intact["recomputed_mark"]).abs() < 0.01
    grade_match = intact["stored_grade"] == intact["recomputed_grade"]

    return {
        "assessments": len(df),
        "castable": len(castable),
        "uncastable": int(df["stored_mark"].isna().sum()),
        "intact": len(intact),
        "orphan_affected": int(len(castable) - len(intact)),
        "mark_match_pct": float(mark_match.mean() * 100) if len(intact) else 0.0,
        "grade_match_pct": float(grade_match.mean() * 100) if len(intact) else 0.0,
        "mark_mismatches": int((~mark_match).sum()),
        "grade_mismatches": int((~grade_match).sum()),
    }


def distribution_report(ex: DuckDBExecutor) -> dict:
    """Mark/grade distribution — checks the G8 calibration actually landed."""
    res = ex.execute("""
        SELECT GRADE, COUNT(*) AS n,
               MIN(TRY_CAST(MARK AS DOUBLE)) AS min_mark,
               MAX(TRY_CAST(MARK AS DOUBLE)) AS max_mark
        FROM MENTOR_FEEDBACK
        WHERE P_IS_CURRENT = TRUE AND TRY_CAST(MARK AS DOUBLE) IS NOT NULL
        GROUP BY GRADE ORDER BY min_mark DESC
    """, limit=100)
    grades = [dict(zip(res.columns, r)) for r in res.rows]

    stats = ex.execute("""
        SELECT COUNT(*) AS n,
               ROUND(AVG(TRY_CAST(MARK AS DOUBLE)), 2) AS mean_mark,
               ROUND(STDDEV(TRY_CAST(MARK AS DOUBLE)), 2) AS sd_mark,
               MIN(TRY_CAST(MARK AS DOUBLE)) AS min_mark,
               MAX(TRY_CAST(MARK AS DOUBLE)) AS max_mark,
               ROUND(QUANTILE_CONT(TRY_CAST(MARK AS DOUBLE), 0.05), 2) AS p05,
               ROUND(QUANTILE_CONT(TRY_CAST(MARK AS DOUBLE), 0.95), 2) AS p95
        FROM MENTOR_FEEDBACK
        WHERE P_IS_CURRENT = TRUE AND TRY_CAST(MARK AS DOUBLE) IS NOT NULL
    """, limit=1)
    return {"by_grade": grades, "stats": dict(zip(stats.columns, stats.rows[0]))}


def discrimination_report(ex: DuckDBExecutor) -> list[dict]:
    """Per-question pass rate — the signal Agent 2's diagnostic depends on.

    Questions everyone passes carry no information regardless of their marks;
    they must show up here so the weighting agent can exclude them.
    """
    res = ex.execute("""
        SELECT q.QUESTION_ID, q.CATEGORY_CODE, q.QUESTION, q.MARKS,
               COUNT(*) AS n,
               ROUND(AVG(CASE WHEN qf.FEEDBACK = 'true' THEN 1.0 ELSE 0.0 END), 4) AS p_pass
        FROM PEP_QUESTION_FEEDBACK qf
        JOIN PEP_QUESTIONS q ON q.QUESTION_ID = qf.QUESTION_ID
        WHERE qf.P_IS_CURRENT = TRUE
        GROUP BY 1, 2, 3, 4
        ORDER BY p_pass DESC
    """, limit=200)
    return [dict(zip(res.columns, r)) for r in res.rows]


def build(cfg: GenConfig | None = None) -> dict:
    """Generate, load, and verify. Returns a summary dict."""
    cfg = cfg or GenConfig(
        seed=config.SYNTH_SEED,
        n_crew=config.SYNTH_CREW_COUNT,
        months=config.SYNTH_MONTHS,
    )
    result = generate(cfg)

    # CLMS and CrewPortal are generated from the SAME latent crew frame, so a
    # crew member's leave, complaints and appreciations are caused by the same
    # hidden competence that produced their assessment answers. Generated
    # independently they would only add noise for the weighting agent to fit to.
    from crew_perf.data.synth.generate_ops import generate_ops

    ops = generate_ops(result["latent"], cfg)
    result["tables"].update(ops["tables"])
    result["latent"] = ops["latent"]
    result["coverage"] = ops["coverage"]

    ex = load_to_duckdb(result["tables"])
    write_ground_truth(result["latent"])

    return {
        "coverage": result.get("coverage", {}),
        "tables": {name: len(df) for name, df in result["tables"].items()},
        "mess": result["mess"],
        "reconciliation": reconcile(ex),
        "distribution": distribution_report(ex),
        "discrimination": discrimination_report(ex),
        "executor": ex,
    }
