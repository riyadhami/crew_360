"""Latent-trait synthetic PEP data generator.

Why not random values (PLAN.md §6.2): the weighting agent measures correlation
between attributes and the assessment outcome. If columns are filled
independently at random, every correlation is noise and Agent 2 is fitting to
nothing. Here the outcome is *caused* by hidden per-crew competence, so there is
a real signal to recover and a ground truth to score the pipeline against.

Causal chain, in order:
    latent traits -> per-question pass probability -> boolean answers
                  -> summed marks (via the template's mark column)
                  -> deviation matrix -> grade

Nothing writes a mark or grade directly; both fall out of the answers, exactly
as production would compute them.

Calibration (PLAN.md G8): pass probabilities are tuned so marks land in the
observed 92-100 band across A+/A/B+/B, with a modest tail below. Reproducing that
compression is deliberate — a generator with a comfortable 40-100 spread would
let the weighting agent pass here and fail on real data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from crew_perf.data.synth import fleet
from crew_perf.data.synth import reference as R

# ─── Model parameters ───────────────────────────────────────────────────────

# Tuned by sweep (see PLAN.md §6.2). The loading is deliberately modest: at 0.6
# every trait becomes a proxy for every other, all weightings score alike, and a
# weighting agent has nothing to discover. At 0.30 the traits are distinct enough
# that correctly weighting them beats the native mark by ~0.08 Spearman.
GENERAL_FACTOR_LOADING = 0.30  # shared competence across all traits
TRAIT_EFFECT = 0.90            # how strongly a trait moves the pass logit
MENTOR_SEVERITY_SD = 0.30      # some mentors mark harder than others
ASSESSMENT_NOISE_SD = 0.35     # day-to-day variation within a crew member

P_LEAD = 0.25                  # share of crew on the Lead designation
P_ATR = 0.20                   # share of crew on the ATR fleet
ASSESSMENTS_PER_CREW = 7.5     # mean over the whole window

STATUS_SUBMITTED, STATUS_PLANNED, STATUS_CANCELLED = 2, 1, 3

# Data-quality mess (PLAN.md §6.2.8)
P_MISSING_TEXT = 0.05
P_UNCASTABLE_MARK = 0.015      # MARK is TEXT in the real schema (G4)
P_ORPHAN_FK = 0.004
P_SCD2_HISTORY = 0.06

# Keyed by latent trait. The trait vocabulary follows the real form's category
# codes, so these changed wholesale when the hand-written bank was replaced —
# `communication` and `safety` have no category in the delivered data at all.
STRENGTH_TEMPLATES = {
    "service_delivery": "Service flow was well paced and trolley timings were met.",
    "cabin_standards": "Cabin and galley presentation were maintained to standard.",
    "customer_focus": "Read the cabin well and pre-empted customer needs.",
    "punctuality": "Reported on time and kept every ground milestone to schedule.",
    "ground_duties": "Post-landing checks completed thoroughly and without prompting.",
    "demeanour": "Warm and composed through all passenger interactions.",
    "coaching": "Junior crew were briefed well and supported through the sector.",
}
IMPROVEMENT_TEMPLATES = {
    "service_delivery": "Work on starting the service promptly after the seatbelt sign is off.",
    "cabin_standards": "Increase frequency of lavatory and cabin checks on longer sectors.",
    "customer_focus": "Engage more proactively with customers during boarding.",
    "punctuality": "Arrive at briefing with more margin before report time.",
    "ground_duties": "Complete the post-landing checklist before leaving the aircraft.",
    "demeanour": "Maintain positive facial expression during peak workload.",
    "coaching": "Distribute workload more evenly and brief the cabin before departure.",
}
INITIATIVE_TEMPLATES = [
    "Assisted an unaccompanied minor beyond standard requirement.",
    "Volunteered for an additional sector at short notice.",
    "Suggested a galley stowage improvement adopted by the base.",
    "Handled a medical situation calmly and by the book.",
    "",
]


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _logit(p):
    return np.log(p / (1.0 - p))


def _row_hash(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


@dataclass
class GenConfig:
    seed: int = 20260731
    n_crew: int = 800
    months: int = 6
    end_date: datetime = datetime(2026, 7, 31)
    inject_mess: bool = True
    # Model knobs. The general-factor loading matters more than it looks: push it
    # high and every trait becomes a proxy for every other, so all weightings
    # score alike and there is nothing for a weighting agent to discover.
    general_factor_loading: float = GENERAL_FACTOR_LOADING
    trait_effect: float = TRAIT_EFFECT
    assessments_per_crew: float = ASSESSMENTS_PER_CREW

    @property
    def start_date(self) -> datetime:
        return self.end_date - timedelta(days=int(self.months * 30.44))


class PepGenerator:
    def __init__(self, cfg: GenConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.tables: dict[str, pd.DataFrame] = {}
        self.latent: pd.DataFrame | None = None

    # ── audit column helper ────────────────────────────────────────────────

    def _audit(self, n: int, key_series=None, current: bool = True) -> dict:
        """Standard SCD-2 + audit columns present on every PEP table."""
        load = self.cfg.end_date
        return {
            "LOAD_DATE": [load] * n,
            "P_CREATED_BY": ["ETL_PEP"] * n,
            "P_CREATED_DT": [load] * n,
            "P_MODIFIED_BY": ["ETL_PEP"] * n,
            "P_MODIFIED_DT": [load] * n,
            "P_IS_CURRENT": [current] * n,
            "ROW_HASH": [_row_hash(k) for k in (key_series if key_series is not None else range(n))],
        }

    def _df(self, name: str, rows: list[dict], current_flags: list[bool] | None = None) -> pd.DataFrame:
        df = pd.DataFrame(rows)
        n = len(df)
        audit = self._audit(n, key_series=list(range(n)))
        if current_flags is not None:
            audit["P_IS_CURRENT"] = current_flags
        for col, vals in audit.items():
            df[col] = vals
        self.tables[name] = df
        return df

    # ── reference tables ───────────────────────────────────────────────────

    def build_reference(self) -> None:
        """Emit only the tables the delivered schema declares.

        The 10-table PEP export has no PEP_TEMP_CAT_MAPPING, PEP_STATUS,
        M_FLIGHT_TYPE or PEP_DESIGNATION, so nothing may reference them.
        Categories are template-scoped and flight type / status are carried as
        plain values on the rows that use them.
        """
        problems = R.validate_reference()
        if problems:
            raise RuntimeError("Reference data is inconsistent:\n  " + "\n  ".join(problems))

        self._df("PEP_TEMPLATE", [
            {"TEMPLATE_ID": tid, "TEMPLATE_CODE": code, "TEMPLATE": name, "ACTIVE": True,
             "CALCULATION_TYPE": "SUM_OF_MARKS", "DESIGNATION_CODE": desg, "FLIGHT_TYPE": ftype}
            for tid, code, name, desg, ftype, _mc in R.TEMPLATES
        ])
        self._df("PEP_CATEGORY", [
            {"CATEGORY_ID": cid, "TEMPLATE_ID": tid, "TEMPLATE_CODE": tcode,
             "CATEGORY_CODE": ccode, "CATEGORY": name, "PRINTABLE": True,
             "POSITION_AT": pos, "ACTIVE": True}
            for cid, tid, tcode, ccode, name, pos in R.category_rows()
        ])
        self._df("PEP_QUESTIONS", [
            {"QUESTION_ID": qid, "CATEGORY_ID": cid, "QUESTION": q[2],
             "CRITICAL": q[7], "POSITION_AT": (i % 40) + 1, "CRUCIAL": q[8],
             "I_DISPLAY_I_CONNECT": bool(q[9]), "CATEGORY_CODE": ccode,
             "SAFETY_ACTION_PARAMETER": q[9], "ACTIVE": True,
             "COLOUR": "RED" if q[7] else ("AMBER" if q[8] else "GREEN"),
             "MARKS": float(q[3]), "LD_MARKS": float(q[4]),
             "ATRLD": float(q[6]), "ATRCA": float(q[5])}
            for i, (qid, cid, _tid, ccode, q) in enumerate(R.question_rows())
        ])
        self._df("PEP_GRADE", [
            {"GRADE_ID": gid, "GRADE_CODE": code, "GRADE": grade, "ACTIVE": True}
            for gid, code, grade in R.GRADES
        ])
        dev_rows, aid = [], 1
        for tid, _code, *_ in R.TEMPLATES:
            for grade, lo, hi in R.DEVIATION_BANDS:
                dev_rows.append({
                    "CREW_ASSES_ID": aid, "TEMPLATE_ID": tid, "MARKS": float(hi),
                    "START_RANGE": lo, "END_RANGE": hi, "GRADE": grade, "IS_ACTIVE": True,
                })
                aid += 1
        self._df("PEP_DEVIATION_MATRIX", dev_rows)

    # ── crew & mentors ─────────────────────────────────────────────────────

    def build_crew(self) -> None:
        cfg, rng = self.cfg, self.rng
        n = cfg.n_crew

        # The fleet is drawn from the extracts rather than invented — see
        # `synth/fleet.py`. `n` follows from what they support, so it is read back
        # off the resolved list rather than trusted from the config.
        igas, self.fleet_provenance = fleet.resolve(cfg.n_crew, cfg.seed)
        n = self.cfg.n_crew = len(igas)

        # No names are generated, and nothing downstream wants one. Personal data
        # is hashed behind the identifier in the warehouse, so the exports drop
        # every name and email at parse time (`graph.schema.PII_COLUMNS`) — which
        # left this generator producing columns no query could ever read.
        #
        # It was also the ceiling on the fleet. Names were drawn unique from
        # 40 first x 24 last = 960 combinations by rejection sampling, so asking
        # for 961 crew did not fail, it looped forever. The extracts support
        # 10,479, and every one of them was unreachable because of a column that
        # is discarded before anything sees it.
        designation = np.where(rng.random(n) < P_LEAD, "LD", "CA")
        fleet_type = np.where(rng.random(n) < P_ATR, 2, 1)  # `fleet` is the module
        base = rng.choice(R.BASES, size=n)

        # Latent competence: a general factor plus trait-specific variation.
        trait_names = [c[4] for c in R.CATEGORIES]
        g = rng.normal(0, 1, n)
        traits = {}
        for t in trait_names:
            specific = rng.normal(0, 1, n)
            gfl = cfg.general_factor_loading
            traits[t] = gfl * g + np.sqrt(1 - gfl**2) * specific

        joined = [cfg.start_date - timedelta(days=int(d))
                  for d in rng.integers(30, 3650, n)]
        active = rng.random(n) > 0.04
        released = [
            (cfg.start_date + timedelta(days=int(rng.integers(0, cfg.months * 30))))
            if not a else None
            for a in active
        ]

        self._df("EMPLOYEE_INFO", [
            {"EMPLOYEE_ID": 100000 + i, "IGA": igas[i],
             "BASE": base[i], "DESIGNATION": designation[i],
             "EG_DESCRIPTION": "Cabin Crew", "ACTIVE": bool(active[i]),
             "CREATED_DATE": joined[i], "AS_DEGN": designation[i],
             "OP_DATE": joined[i], "DO_REL": released[i], "UPDATED_ON": cfg.end_date}
            for i in range(n)
        ])

        self.latent = pd.DataFrame({
            "IGA": igas, "DESIGNATION": designation,
            "BASE": base, "FLIGHT_TYPE": fleet_type, "ACTIVE": active,
            "general_factor": g,
            **{f"trait_{t}": traits[t] for t in trait_names},
        })
        # Overall competence: the single number Agent 5 scores the pipeline
        # against. Weighted by TRAIT_IMPORTANCE, which deliberately differs from
        # the template's mark allocation — so recovering it requires finding
        # better weights than production uses, rather than just averaging.
        self.latent["true_competence"] = sum(
            R.TRAIT_IMPORTANCE[t] * self.latent[f"trait_{t}"] for t in trait_names
        )

        # Mentors, drawn from the Lead population plus dedicated assessors.
        # Identified by login, not by name — for the same two reasons the crew are.
        # `MENTOR_LOGIN` is what the assessment chain actually joins on and what
        # `mentor_count` counts; `MENTOR_NAME` is dropped from the exports as
        # personal data, so generating one produced a column nothing could read.
        # Enumerated rather than sampled, so this cannot become the ceiling the
        # crew names were: one mentor per twenty crew is 524 at the fleet the
        # extracts support, and rejection sampling would have started to struggle
        # against the same 960 names not far beyond it.
        n_mentors = max(30, n // 20)
        self.mentors = pd.DataFrame({
            "login": [f"mentor.{i:05d}" for i in range(n_mentors)],
            "severity": rng.normal(0, MENTOR_SEVERITY_SD, n_mentors),
            "base": rng.choice(R.BASES, size=n_mentors),
        })
    # ── assessments ────────────────────────────────────────────────────────

    def build_assessments(self) -> None:
        cfg, rng = self.cfg, self.rng
        latent = self.latent
        n_days = (cfg.end_date - cfg.start_date).days

        reason_names = [r[1] for r in R.PEP_REASONS]
        reason_w = np.array([r[2] for r in R.PEP_REASONS])
        reason_w = reason_w / reason_w.sum()

        sched_rows, flight_rows, feedback_rows, qfb_rows = [], [], [], []
        planned_rows, wf_rows, change_rows, interchange_rows = [], [], [], []

        sched_id, flight_id, fb_id, qfb_id = 1, 1, 1, 1
        wf_id, change_id, inter_id, planned_id = 1, 1, 1, 1

        # Question rows are template-scoped now, so each assessment answers the
        # rows belonging to its own template rather than a shared question bank.
        self._template_question_ids = {}
        self._source_question = {}
        for qid, _cid, tid, _ccode, q in R.question_rows():
            self._template_question_ids.setdefault(tid, []).append(qid)
            self._source_question[qid] = q

        n_assessments = rng.poisson(cfg.assessments_per_crew, len(latent))

        for idx, crew in enumerate(latent.itertuples()):
            template_id = self._template_for(crew.DESIGNATION, crew.FLIGHT_TYPE)
            mark_col = R.TEMPLATE_BY_ID[template_id][5]
            qids = self._template_question_ids[template_id]

            for _ in range(n_assessments[idx]):
                day_offset = int(rng.integers(0, n_days))
                pep_date = cfg.start_date + timedelta(days=day_offset)
                mentor = self.mentors.iloc[int(rng.integers(0, len(self.mentors)))]
                pep_id = f"PEP{sched_id:07d}"
                reason = str(rng.choice(reason_names, p=reason_w))

                roll = rng.random()
                status = (STATUS_SUBMITTED if roll < 0.85
                          else STATUS_PLANNED if roll < 0.93 else STATUS_CANCELLED)

                # Flight context
                sector = R.SECTORS[int(rng.integers(0, len(R.SECTORS)))]
                flight_no = f"6E{int(rng.integers(100, 9999))}"
                std = pep_date + timedelta(hours=int(rng.integers(5, 22)),
                                           minutes=int(rng.choice([0, 15, 30, 45])))
                sta = std + timedelta(minutes=int(rng.integers(60, 210)))
                flight_rows.append({
                    "ID": flight_id, "PEP_ID": pep_id, "FLIGHT_NO": flight_no,
                    "SECTOR": f"{sector[0]}-{sector[1]}", "FLIGHT_DATE": pep_date,
                    "STD": std, "STA": sta, "STA_SLA": sta + timedelta(hours=48),
                    "EMAIL_SLA": 48,
                    "FLIGHT_TYPE": R.FLIGHT_TYPES[crew.FLIGHT_TYPE - 1][1],
                })
                flight_id += 1

                sched_rows.append({
                    "PEP_SCHDULER_ID": sched_id, "PEP_ID": pep_id, "IGA": crew.IGA,
                    "PEP_REASON": reason,
                    "MENTOR_LOGIN": mentor["login"], "PLANNED_FOR": True,
                    "PEP_DATE": pep_date.date(),
                    "CANCEL_REASON": (R.CANCEL_REASONS[int(rng.integers(0, len(R.CANCEL_REASONS)))]
                                      if status == STATUS_CANCELLED else None),
                    "CANCEL_REMARK": None,
                    "STATUS": status,
                    "NEW_JOINEE": reason == "New Joinee",
                    "REMARK": None,
                    "SUBMITTED_ON": (pep_date + timedelta(days=int(rng.integers(0, 4)))
                                     if status == STATUS_SUBMITTED else None),
                    "CREW_DESIGNATION": crew.DESIGNATION, "MODIFIED_DATE": pep_date,
                    "BASE": crew.BASE, "DESIGNATION": crew.DESIGNATION,
                    "ALLOCATION": R.CART_TYPES[int(rng.integers(0, len(R.CART_TYPES)))],
                })

                planned_rows.append({
                    "ID": planned_id, "IGA": crew.IGA, "BASE": crew.BASE,
                    "DESIGNATION": crew.DESIGNATION,
                    "FLIGHT_NO": flight_no, "PEP_DATE": pep_date, "PEP_REASON": reason,
                    "MENTOR_LOGIN": mentor["login"],
                })
                planned_id += 1

                # Workflow trail
                for action in ["PEP_SCHEDULED", "MENTOR_ASSIGNED"] + (
                        ["FEEDBACK_SUBMITTED", "PEP_CLOSED"] if status == STATUS_SUBMITTED else []):
                    wf_rows.append({
                        "TRACK_ID": wf_id, "PEP_ID": pep_id, "FROM_USER": "pepadmin",
                        "TO_USER": mentor["login"], "TYPE_OF_ACTION": action,
                        "COMMENT": None, "ACTION_TAKEN": pep_date,
                        "TYPE_OF_REQUEST": 1, "EMAIL_STATUS": True,
                    })
                    wf_id += 1

                if status != STATUS_SUBMITTED:
                    sched_id += 1
                    continue

                # ── The causal core: answers -> marks -> grade ──
                answers, mark = self._answer_questions(crew, qids, mark_col, mentor["severity"])
                grade = R.grade_for_mark(mark)

                worst_trait = self._worst_trait(crew, qids)
                best_trait = self._best_trait(crew, qids)

                feedback_rows.append({
                    "ID": fb_id, "PEP_SCHDULER_ID": sched_id, "PEP_ID": pep_id,
                    "IGA": crew.IGA, "PEP_NO": f"PN{fb_id:07d}",
                    "MARK": f"{mark:.2f}", "GRADE": grade,
                    "STRENGHTH": STRENGTH_TEMPLATES[best_trait],
                    "IMPROVEMENT_AREAS": IMPROVEMENT_TEMPLATES[worst_trait],
                    "IMPROVEMENT_LAST_FLIGHT": None,
                    "FLIGHT_NO": flight_no, "PEP_DATE": pep_date,
                    "CREATED_BY": mentor["login"], "CREATED_ON": pep_date,
                    "MODIFIED_BY": mentor["login"], "MODIFIED_ON": pep_date,
                    "CATEGORY": crew.DESIGNATION,
                    "INNOVATIVE_INITIATIVES": INITIATIVE_TEMPLATES[
                        int(rng.integers(0, len(INITIATIVE_TEMPLATES)))],
                    "ALLOCATION": R.CART_TYPES[int(rng.integers(0, len(R.CART_TYPES)))],
                })

                for qid, passed in answers.items():
                    qfb_rows.append({
                        "ID": qfb_id,
                        "FEEDBACK_ID": fb_id,          # -> MENTOR_FEEDBACK.ID (G1)
                        "QUESTION_ID": qid,
                        "FEEDBACK": "true" if passed else "false",
                        "REMARKS": None if passed else self._remark_for(qid),
                    })
                    qfb_id += 1

                fb_id += 1
                sched_id += 1

        # Occasional crew/mentor changes and flight interchanges
        n_changes = len(sched_rows) // 40
        for _ in range(n_changes):
            s = sched_rows[int(rng.integers(0, len(sched_rows)))]
            change_rows.append({
                "CHANGE_ID": change_id, "PEP_SCHDULER_ID": s["PEP_SCHDULER_ID"],
                "IGA": s["IGA"], "CREW_STATUS": str(rng.choice(["REPLACED", "SWAPPED", "ADDED"])),
                "CHANGE_DATE": s["PEP_DATE"], "MENTOR_LOGIN": s["MENTOR_LOGIN"],
            })
            change_id += 1
        for _ in range(n_changes // 2):
            s = sched_rows[int(rng.integers(0, len(sched_rows)))]
            m = self.mentors.iloc[int(rng.integers(0, len(self.mentors)))]
            interchange_rows.append({
                "FLIGHT_ID": inter_id, "PEP_SCHEDULER_ID": s["PEP_SCHDULER_ID"],
                "PEP_ID_TO_CHANGE": s["PEP_ID"], "MENTOR_LOGIN_TO_CHANGE": m["login"],
                "MODIFIED_ON": self.cfg.end_date,
            })
            inter_id += 1

        # Only the ten tables the export declares. Planned crews, workflow
        # tracking, crew-change and interchange records exist in the wider PEP
        # system but were not delivered, so generating them would put rows in the
        # database that no schema vouches for.
        self._df("PEP_SCHEDULER", sched_rows)
        self._df("PEP_FLIGHT_DETAILS", flight_rows)
        self._df("MENTOR_FEEDBACK", feedback_rows)
        self._df("PEP_QUESTION_FEEDBACK", qfb_rows)

    # ── mechanics ──────────────────────────────────────────────────────────

    @staticmethod
    def _template_for(designation: str, flight_type: int) -> int:
        for tid, _code, _name, desg, ftype, _mc in R.TEMPLATES:
            if desg == designation and ftype == flight_type:
                return tid
        raise KeyError(f"no template for {designation}/{flight_type}")

    def _answer_questions(self, crew, qids, mark_col, mentor_severity):
        """Draw boolean answers from latent competence and sum the marks earned."""
        rng = self.rng
        answers, mark = {}, 0.0
        for qid in qids:
            q = self._source_question[qid]
            trait_name = R.CATEGORY_TRAIT[q[1]]
            trait = getattr(crew, f"trait_{trait_name}")
            base_pass = q[10]

            logit_p = (
                _logit(base_pass)
                + self.cfg.trait_effect * trait
                - mentor_severity
                + rng.normal(0, ASSESSMENT_NOISE_SD)
            )
            passed = bool(rng.random() < _sigmoid(logit_p))
            answers[qid] = passed
            if passed:
                mark += R.question_mark(q[0], mark_col)
        return answers, round(mark, 2)

    def _worst_trait(self, crew, qids) -> str:
        traits = {R.CATEGORY_TRAIT[self._source_question[q][1]] for q in qids}
        return min(traits, key=lambda t: getattr(crew, f"trait_{t}"))

    def _best_trait(self, crew, qids) -> str:
        traits = {R.CATEGORY_TRAIT[self._source_question[q][1]] for q in qids}
        return max(traits, key=lambda t: getattr(crew, f"trait_{t}"))

    def _remark_for(self, qid: int) -> str:
        trait = R.CATEGORY_TRAIT[self._source_question[qid][1]]
        return IMPROVEMENT_TEMPLATES[trait]

    # ── realistic mess (PLAN.md §6.2.8) ────────────────────────────────────

    def inject_mess(self) -> dict:
        """Introduce the data-quality problems the agents must survive."""
        rng = self.rng
        report = {}

        # 1. MARK is TEXT in the real schema, so some values don't cast (G4).
        mf = self.tables["MENTOR_FEEDBACK"]
        if len(mf):
            mask = rng.random(len(mf)) < P_UNCASTABLE_MARK
            bad = ["N/A", "PENDING", "-", "NOT ASSESSED"]
            mf.loc[mask, "MARK"] = [bad[i % len(bad)] for i in range(int(mask.sum()))]
            report["uncastable_marks"] = int(mask.sum())

        # 2. Missing free text.
        for col in ["STRENGHTH", "IMPROVEMENT_AREAS", "INNOVATIVE_INITIATIVES"]:
            mask = rng.random(len(mf)) < P_MISSING_TEXT
            mf.loc[mask, col] = None
        report["missing_free_text_rate"] = P_MISSING_TEXT

        # 3. Orphan FKs — question feedback pointing at a non-existent parent.
        qfb = self.tables["PEP_QUESTION_FEEDBACK"]
        if len(qfb):
            mask = rng.random(len(qfb)) < P_ORPHAN_FK
            qfb.loc[mask, "FEEDBACK_ID"] = 9_999_999
            report["orphan_question_feedback"] = int(mask.sum())

        # 4. SCD-2 history rows: superseded copies with P_IS_CURRENT = FALSE.
        #    Any query that forgets the currency filter now double-counts (G7).
        sched = self.tables["PEP_SCHEDULER"]
        if len(sched):
            n_hist = int(len(sched) * P_SCD2_HISTORY)
            picks = rng.choice(len(sched), size=n_hist, replace=False)
            hist = sched.iloc[picks].copy()
            hist["P_IS_CURRENT"] = False
            hist["STATUS"] = STATUS_PLANNED
            hist["ROW_HASH"] = [_row_hash("hist", i) for i in range(n_hist)]
            self.tables["PEP_SCHEDULER"] = pd.concat([sched, hist], ignore_index=True)
            report["scd2_history_rows"] = n_hist

        return report

    # ── orchestration ──────────────────────────────────────────────────────

    def run(self) -> dict:
        self.build_reference()
        self.build_crew()
        self.build_assessments()
        mess = self.inject_mess() if self.cfg.inject_mess else {}
        return {
            "tables": self.tables,
            "latent": self.latent,
            "mess": mess,
            "fleet": self.fleet_provenance,
        }


def generate(cfg: GenConfig | None = None) -> dict:
    return PepGenerator(cfg or GenConfig()).run()
