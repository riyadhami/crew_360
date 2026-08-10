"""The generator's causal chain is what makes every downstream number meaningful.

If marks stop being caused by latent competence, the weighting agent is fitting
to noise and the evaluation harness has nothing to measure — so these tests
check causality and recoverability, not just row counts.
"""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from crew_perf.data.synth import reference as R
from crew_perf.data.synth.generate import GenConfig, generate

SMALL = GenConfig(n_crew=250, months=6, inject_mess=False)


@pytest.fixture(scope="module")
def data():
    return generate(SMALL)


def test_only_declared_tables_are_generated(data):
    import csv

    from crew_perf import config

    with open(config.SCHEMAS_DIR / "pep_schema.csv") as f:
        expected = {r["TABLE_NAME"] for r in csv.DictReader(f)}
    assert set(data["tables"]) == expected, "generated a table no schema declares"


def test_generated_columns_match_the_real_schema(data):
    import csv
    from collections import defaultdict

    from crew_perf import config

    schema = defaultdict(set)
    with open(config.SCHEMAS_DIR / "pep_schema.csv") as f:
        for r in csv.DictReader(f):
            schema[r["TABLE_NAME"]].add(r["COLUMN_NAME"])

    for name, df in data["tables"].items():
        assert set(df.columns) == schema[name], f"{name} column mismatch"


def test_marks_are_caused_by_question_answers(data):
    """Recompute every mark from the raw answers; it must match exactly."""
    mf = data["tables"]["MENTOR_FEEDBACK"]
    qfb = data["tables"]["PEP_QUESTION_FEEDBACK"]
    questions = data["tables"]["PEP_QUESTIONS"].set_index("QUESTION_ID")
    categories = data["tables"]["PEP_CATEGORY"].set_index("CATEGORY_ID")
    answers = {fid: g for fid, g in qfb.groupby("FEEDBACK_ID")}
    mark_col_by_template = {t[0]: t[5] for t in R.TEMPLATES}

    checked = 0
    for row in mf.itertuples():
        g = answers[row.ID]
        expected = 0.0
        for qid, fb in zip(g.QUESTION_ID, g.FEEDBACK):
            if fb != "true":
                continue
            tid = categories.loc[questions.loc[qid, "CATEGORY_ID"], "TEMPLATE_ID"]
            expected += float(questions.loc[qid, mark_col_by_template[tid]])
        assert abs(float(row.MARK) - expected) < 1e-6
        assert row.GRADE == R.grade_for_mark(expected)
        checked += 1
    assert checked > 100


def test_every_assessment_answers_its_full_template(data):
    mf = data["tables"]["MENTOR_FEEDBACK"]
    qfb = data["tables"]["PEP_QUESTION_FEEDBACK"]
    counts = qfb.groupby("FEEDBACK_ID").size()
    # Counts follow the real form: every assessment answers exactly the
    # question set its (role, fleet) variant defines, and Leads answer more
    # because of the LEADSONLY add-on.
    import crew_perf.data.synth.reference as R
    expected = {len(R.template_questions(t[0])) for t in R.TEMPLATES}
    assert set(counts.unique()) <= expected, f"expected one of {sorted(expected)}"
    assert len(counts) == len(mf)


def test_latent_competence_is_recoverable(data):
    """The whole point: observable signal must track hidden competence."""
    lat = data["latent"].set_index("IGA")
    mf = data["tables"]["MENTOR_FEEDBACK"]
    mark = mf.assign(m=pd.to_numeric(mf.MARK, errors="coerce")).groupby("IGA").m.mean()
    d = lat.join(mark.rename("mark")).dropna(subset=["mark"])
    rho = stats.spearmanr(d["mark"], d["true_competence"])[0]
    assert rho > 0.5, f"latent signal too weak to recover (rho={rho:.3f})"


def test_correct_weights_beat_the_native_mark(data):
    """If true weights can't beat production's mark, Agent 2 has no job to do
    and the evaluation harness measures nothing."""
    lat = data["latent"].set_index("IGA")
    mf = data["tables"]["MENTOR_FEEDBACK"]
    qfb = data["tables"]["PEP_QUESTION_FEEDBACK"].merge(
        mf[["ID", "IGA"]], left_on="FEEDBACK_ID", right_on="ID"
    ).merge(data["tables"]["PEP_QUESTIONS"][["QUESTION_ID", "CATEGORY_CODE"]], on="QUESTION_ID")
    qfb["passed"] = (qfb.FEEDBACK == "true").astype(float)

    # Derived from the active reference rather than hardcoded: the category
    # codes came from the real form and a literal list here silently broke when
    # they did. LEADSONLY is excluded because Cabin Attendants never answer it,
    # so it is null for ~78% of crew.
    ca_template = next(t[0] for t in R.TEMPLATES if t[3] == "CA")
    cats = [c for c in R.TEMPLATE_CATEGORIES[ca_template] if c != "LEADSONLY"]
    piv = qfb.pivot_table(index="IGA", columns="CATEGORY_CODE", values="passed", aggfunc="mean")
    mark = mf.assign(m=pd.to_numeric(mf.MARK, errors="coerce")).groupby("IGA").m.mean()
    d = piv.join(lat[["true_competence"]]).join(mark.rename("mark")).dropna(subset=cats + ["mark"])

    z = (d[cats] - d[cats].mean()) / d[cats].std()
    w = np.array([R.TRAIT_IMPORTANCE[R.CATEGORY_TRAIT[c]] for c in cats])
    w = w / w.sum()

    native = stats.spearmanr(d["mark"], d.true_competence)[0]
    oracle = stats.spearmanr(z @ w, d.true_competence)[0]
    assert oracle - native > 0.03, f"no headroom for weighting (native={native:.3f}, oracle={oracle:.3f})"


def test_marks_cluster_high_like_the_real_data(data):
    """G8: reproducing the compressed high range is deliberate. Marks are
    fractional now — the real form's questions are worth values like 3.20 and
    2.09 — so the distribution is smoother than the integer bank produced."""
    marks = pd.to_numeric(data["tables"]["MENTOR_FEEDBACK"].MARK, errors="coerce").dropna()
    assert 92 <= marks.mean() <= 97
    assert marks.quantile(0.50) >= 93
    assert marks.max() == 100
    grades = set(data["tables"]["MENTOR_FEEDBACK"].GRADE)
    assert grades == {"A", "B", "C"}, "the real scale is A/B/C"


def test_generation_is_deterministic():
    a = generate(GenConfig(n_crew=60, months=6, inject_mess=False))
    b = generate(GenConfig(n_crew=60, months=6, inject_mess=False))
    pd.testing.assert_frame_equal(a["tables"]["MENTOR_FEEDBACK"], b["tables"]["MENTOR_FEEDBACK"])


def test_mess_injection_creates_the_problems_agents_must_survive():
    res = generate(GenConfig(n_crew=250, months=6, inject_mess=True))
    mf = res["tables"]["MENTOR_FEEDBACK"]
    sched = res["tables"]["PEP_SCHEDULER"]

    assert pd.to_numeric(mf.MARK, errors="coerce").isna().any(), "no uncastable MARK values"
    assert (~sched.P_IS_CURRENT).any(), "no SCD-2 history rows"
    assert (res["tables"]["PEP_QUESTION_FEEDBACK"].FEEDBACK_ID == 9_999_999).any()
    # The SCD-2 trap must actually bite: ignoring the currency filter changes counts.
    assert len(sched) > sched.P_IS_CURRENT.sum()
