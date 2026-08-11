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

# Recoverability is a correlation, and a correlation needs a population. At 250
# crew Spearman's standard error is ~0.063, so the 0.03 margin this suite exists
# to protect sits inside the noise — the measurement passed or failed on which
# way the RNG fell, not on whether the data had any headroom. 2,000 brings the
# error to ~0.022 and the estimate stops moving (0.055 at 1k, 0.056 at 3k).
RECOVERY = GenConfig(n_crew=2000, months=6, inject_mess=False)


@pytest.fixture(scope="module")
def data():
    return generate(SMALL)


@pytest.fixture(scope="module")
def recovery_data():
    return generate(RECOVERY)


def test_only_declared_tables_are_generated(data):
    import csv

    from crew_perf import config

    with open(config.SCHEMAS_DIR / "pep_schema.csv") as f:
        expected = {r["TABLE_NAME"] for r in csv.DictReader(f)}
    assert set(data["tables"]) == expected, "generated a table no schema declares"


def test_generated_columns_match_the_real_schema(data):
    """Every declared column is generated, except the personal ones.

    The export still declares `EMPLOYEE_NAME` and `EMAIL_ID` because the real
    warehouse still has them — it hashes the values, it does not drop the
    columns, and an export that hid them would misdescribe the system it
    documents. The generator does not produce them: nothing can read them
    (`graph.schema.PII_COLUMNS` removes them at parse), and generating personal
    data that no query can reach is how a local database ends up holding what the
    warehouse went to the trouble of protecting.
    """
    import csv
    from collections import defaultdict

    from crew_perf import config
    from crew_perf.graph.schema import PII_COLUMNS

    schema = defaultdict(set)
    with open(config.SCHEMAS_DIR / "pep_schema.csv") as f:
        for r in csv.DictReader(f):
            schema[r["TABLE_NAME"]].add(r["COLUMN_NAME"])

    for name, df in data["tables"].items():
        expected = {c for c in schema[name] if c.upper() not in PII_COLUMNS}
        assert set(df.columns) == expected, f"{name} column mismatch"
        assert not {c for c in df.columns if c.upper() in PII_COLUMNS}, \
            f"{name} generated personal data"


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


def test_correct_weights_beat_the_native_mark(recovery_data):
    """If true weights can't beat production's mark, Agent 2 has no job to do
    and the evaluation harness measures nothing.

    **The margin is real but modest — ~0.055 — and two structural caps are
    accepted rather than tuned away.**

    A quarter of `true_competence` is not in PEP at all. `reliability` (0.15) has
    no assessment category — that is precisely why CLMS and CrewPortal are worth
    having — and `coaching` (0.10) is LEADSONLY, unobservable for the ~75% of
    crew who are Cabin Attendants. A PEP-only oracle is answering 75% of the
    question before it starts.

    And importance runs against measurability. The two traits carrying 53% of
    competence — punctuality and ground duties — are assessed by 24 of the 138
    questions, while the form spends 69% of its marks on the two categories that
    matter least. So the oracle leans on the noisiest pass rates while the
    recorded mark leans on the most precise ones. Fixing that would mean
    rebalancing a question bank transcribed from the real form, which would
    falsify the one part of this data that is not invented.

    Injected mess costs roughly half of what is left: ~0.028 on a fleet carrying
    the SCD-2 history, uncastable marks and orphans that production has.
    """
    data = recovery_data
    lat = data["latent"].set_index("IGA")
    # Current rows only, as every query in the system reads them. Counting
    # superseded SCD-2 history averages a crew member's answers with their own
    # earlier ones and depresses both correlations — the G7 trap, in the test
    # that is supposed to measure the data's headroom.
    mf = data["tables"]["MENTOR_FEEDBACK"]
    mf = mf[mf.P_IS_CURRENT]
    q = data["tables"]["PEP_QUESTIONS"]
    qfb = data["tables"]["PEP_QUESTION_FEEDBACK"]
    qfb = qfb[qfb.P_IS_CURRENT].merge(
        mf[["ID", "IGA"]], left_on="FEEDBACK_ID", right_on="ID"
    ).merge(q[q.P_IS_CURRENT][["QUESTION_ID", "CATEGORY_CODE"]], on="QUESTION_ID")
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
    assert oracle - native > 0.03, (
        f"no headroom for weighting (native={native:.3f}, oracle={oracle:.3f}) — "
        f"correct weights no longer beat the recorded mark, so there is nothing "
        f"for the mechanism to find"
    )


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
