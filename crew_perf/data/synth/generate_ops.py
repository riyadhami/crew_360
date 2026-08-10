"""Synthetic CLMS and CrewPortal data, driven by the same latent crew traits.

Generated from the crew frame the PEP generator already produced, so a crew
member's leave behaviour, flight issues and appreciations are *caused by the
same hidden competence* that produced their assessment answers. Independent
random fills would let Agent 2 "discover" cross-source correlations that are
pure noise — the failure this whole design exists to avoid (PLAN.md §6.2).

Two traits carry most of the new signal:

    reliability   drives leave taken, check-in failures, late acknowledgements
    safety        (already used by PEP) drives flight issues and compliance misses

Identity is deliberately imperfect. CLMS keys on `CREW_ID`, CrewPortal on `IGA`,
and `M_CREW_DETAILS` bridges them — but a few crew are missing from each source,
so the join-coverage reporting (G9) has something real to measure rather than a
tidy 100% that would never occur in production.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from crew_perf.data.synth import reference_data

# The master mapping is what makes a leave code interpretable — an agent reading
# LEAVE_TYPE_ID = 7 has no way to know that is unpaid leave without it, and the
# The IJP promotion policy scores that one type specifically while ignoring the rest.
# LWP carries no balance (you do not accrue unpaid leave), so it is deliberately
# excluded from the balance loop below.
# No extract exists for the ground-duty codes, so these stay invented — and are
# only ever written as codes, never resolved to a meaning by any scorer.
GND_CODES = [(1, "TRG"), (2, "OFC"), (3, "MED"), (4, "STBY"), (5, "DUTY")]
STATIONS = ["DEL", "BOM", "BLR", "HYD", "MAA", "CCU", "AMD", "PNQ"]
AIRCRAFT = [(1, "A320", "Airbus A320"), (2, "A321", "Airbus A321"), (3, "ATR", "ATR 72")]

# Deliberate identity gaps, so join coverage is measured rather than assumed.
P_MISSING_FROM_CLMS = 0.03
P_MISSING_FROM_PORTAL = 0.02


def _audit(n: int, load_date, prefix: str) -> dict:
    return {
        "LOAD_DATE": [load_date] * n,
        "P_CREATED_BY": [f"ETL_{prefix}"] * n,
        "P_CREATED_DT": [load_date] * n,
        "P_MODIFIED_BY": [f"ETL_{prefix}"] * n,
        "P_MODIFIED_DT": [load_date] * n,
        "P_IS_CURRENT": [True] * n,
        "ROW_HASH": [f"{prefix}{i:012d}" for i in range(n)],
    }


class OpsGenerator:
    """Builds CLMS + CrewPortal frames from an existing crew latent frame."""

    def __init__(self, latent: pd.DataFrame, cfg, schema: dict[str, list[str]]):
        self.latent = latent.reset_index(drop=True)
        self.cfg = cfg
        self.schema = schema          # table -> ordered column list, from the CSVs
        self.rng = np.random.default_rng(cfg.seed + 991)
        self.tables: dict[str, pd.DataFrame] = {}

        # Reliability is new; derive it from the general factor so it correlates
        # with competence without being a copy of it.
        n = len(self.latent)
        self.reliability = (
            0.55 * self.latent["general_factor"].to_numpy()
            + 0.83 * self.rng.normal(0, 1, n)
        )
        self.latent["trait_reliability"] = self.reliability

        # true_competence must include reliability, or the CLMS/CrewPortal
        # signals correlate with the target only through their shared general
        # factor — decorative rather than informative. PEP cannot observe this
        # trait at all; that is precisely why the other sources are worth having.
        from crew_perf.data.synth import reference as _R

        weights = _R.TRAIT_IMPORTANCE
        self.latent["true_competence"] = sum(
            w * self.latent[f"trait_{t}"] for t, w in weights.items()
            if f"trait_{t}" in self.latent.columns
        )

        # Identity: CREW_ID is a different identifier from IGA, related by lookup.
        self.crew_id = np.array([f"C{60000 + i}" for i in range(n)])
        self.latent["CREW_ID"] = self.crew_id
        self.in_clms = self.rng.random(n) > P_MISSING_FROM_CLMS
        self.in_portal = self.rng.random(n) > P_MISSING_FROM_PORTAL

    # ── helpers ────────────────────────────────────────────────────────────

    def _emit(self, table: str, rows: list[dict], prefix: str) -> pd.DataFrame:
        """Build a schema-conformant frame: declared columns, in order, no extras."""
        cols = self.schema[table]
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
        for col, vals in _audit(len(df), self.cfg.end_date, prefix).items():
            if col in cols:
                df[col] = vals
        for col in cols:
            if col not in df.columns:
                df[col] = None
        self.tables[table] = df[cols]
        return self.tables[table]

    def _dates(self, count: int):
        span = (self.cfg.end_date - self.cfg.start_date).days
        return [self.cfg.start_date + timedelta(days=int(d))
                for d in self.rng.integers(0, span, count)]

    # ── CLMS ───────────────────────────────────────────────────────────────

    def build_clms(self) -> None:
        rng, lat = self.rng, self.latent
        crew_rows, idx_in = [], []
        for i, c in enumerate(lat.itertuples()):
            if not self.in_clms[i]:
                continue
            idx_in.append(i)
            joined = self.cfg.start_date - timedelta(days=int(rng.integers(200, 3600)))
            crew_rows.append({
                "CREW_ID": self.crew_id[i], "CREW_NAME": c.EMPLOYEE_NAME,
                "EMAIL": c.EMPLOYEE_NAME.lower().replace(" ", ".") + "@airline.com",
                "DESIGNATION": c.DESIGNATION, "BASE": c.BASE, "DOJ": joined,
                "ACTIVE": bool(c.ACTIVE), "STATUS": "Active" if c.ACTIVE else "Released",
                "CATEGORY": "Cabin Crew",
                "IGO_EXP": round(float((self.cfg.end_date - joined).days) / 365.0, 2),
                "TOTAL_EXP": round(float((self.cfg.end_date - joined).days) / 365.0 + 1.5, 2),
                "MEDICAL": self.cfg.end_date + timedelta(days=int(rng.integers(-60, 400))),
                "IS_BASE_MGR": 0,
            })
        self._emit("M_CLMS_CREW", crew_rows, "CLMS")

        leave_type_rows = reference_data.LEAVE_TYPES
        self._emit("M_LMS_LEAVE_TYPE", leave_type_rows, "CLMS")
        status_rows = reference_data.LEAVE_STATUS_TYPES
        self._emit("M_LMS_STATUS_TYPE", status_rows, "CLMS")

        # Requests reference the mapping that was actually emitted, so a real
        # extract shifts the generated ids with it. Hardcoding 1..4 here would
        # produce leave rows pointing at CarryForword and Earned — balance
        # states, not request states.
        by_code = {r["LEAVE_TYPE"]: r["LEAVE_TYPE_ID"] for r in leave_type_rows}
        status_by_name = {r["STATUS_TYPE"]: r["STATUS_TYPE_ID"] for r in status_rows}
        lwp_id = by_code["LWP"]
        paid_ids = [by_code[c] for c in ("SL", "PL", "URTI", "CL") if c in by_code] or [1, 2, 3, 4]
        approved_id = status_by_name.get("Approved", 1)
        refused_id = status_by_name.get("Not Approved", status_by_name.get("Rejected", 2))
        pending_id = status_by_name.get("Applied", status_by_name.get("Pending", 3))
        self._emit("M_CLMS_GND_CODE", [
            {"ID": i, "CODE": c} for i, c in GND_CODES], "CLMS")
        self._emit("M_DESIGNATIONS", [
            {"DES_ID": 1, "DESIGNATION": "CA"}, {"DES_ID": 2, "DESIGNATION": "LD"}], "CLMS")
        self._emit("M_UMS_STATION", [
            {"STATION_ID": i + 1, "STATION_CODE": s, "STATION_NAME": f"{s} Airport",
             "IATA_CODE": s, "ACTIVE": True, "BASE_STATION": 1, "REGION": (i % 4) + 1}
            for i, s in enumerate(STATIONS)], "CLMS")

        # ── Leave: fewer days when reliability is high ──
        bal_rows, req_rows, det_rows, upd_rows = [], [], [], []
        bal_id = req_id = det_id = 1
        for i in idx_in:
            rel = self.reliability[i]
            for tid in paid_ids:
                bal_rows.append({
                    "BALANCE_ID": bal_id, "CREW_ID": self.crew_id[i], "LEAVE_TYPE_ID": tid,
                    "BALANCE": round(float(max(0, rng.normal(12 + 2 * rel, 3))), 1),
                    "START_DATE": self.cfg.start_date, "END_DATE": self.cfg.end_date,
                    "LEAVE_YEAR_ID": 1,
                })
                bal_id += 1

            n_req = int(max(0, rng.poisson(max(0.5, 6.0 - 1.6 * rel))))
            for _ in range(n_req):
                start = self._dates(1)[0]
                days = int(max(1, rng.poisson(max(1.0, 3.0 - 0.6 * rel))))
                # LWP is rare and concentrated in less reliable crew: unpaid
                # leave is what someone takes once paid balance is exhausted.
                # Keeping it rare matters — the IJP formula bands at 0/1-2/3-4/
                # 5-6/7+, so a type that everyone accrues would flatten the
                # component to a constant and the 50% weight would discriminate
                # nothing.
                p_lwp = float(np.clip(0.10 - 0.035 * rel, 0.01, 0.20))
                rest = 1.0 - p_lwp
                share = [0.35, 0.35, 0.15, 0.15][: len(paid_ids)]
                share = [x / sum(share) * rest for x in share]
                ltype = int(rng.choice(paid_ids + [lwp_id], p=share + [p_lwp]))
                status = int(rng.choice([approved_id, refused_id, pending_id],
                                        p=[0.82, 0.10, 0.08]))
                req_rows.append({
                    "LEAVE_DETAIL_ID": req_id, "CREW_ID": self.crew_id[i],
                    "REQUESTED_DATE": start - timedelta(days=7), "LEAVE_TYPE_ID": ltype,
                    "STATUS_TYPE_ID": status, "FROM_DT": start,
                    "TO_DT": start + timedelta(days=days),
                    "CREW_COMMENT": None, "REMARKS": None,
                    "CREATED_DTM": start - timedelta(days=7),
                })
                det_rows.append({
                    "LEAVE_REQ_SUB_ID": det_id, "LEAVE_DETAIL_ID": req_id,
                    "LEAVE_TYPE_ID": ltype, "NO_OF_LEAVES": days,
                    "BALANCE": round(float(max(0, rng.normal(10, 3))), 1),
                    "CURRENT_LEAVE": days, "FROM_DT": start,
                    "TO_DT": start + timedelta(days=days),
                })
                req_id += 1
                det_id += 1

            if rng.random() < 0.25:
                upd_rows.append({
                    "CREW_ID": self.crew_id[i],
                    "OLD_SL_BALANCE": round(float(rng.uniform(4, 12)), 1),
                    "UPDATED_SL_BALANCE": round(float(rng.uniform(4, 12)), 1),
                    "OLD_URTI_BALANCE": round(float(rng.uniform(0, 6)), 1),
                    "UPDATED_URTI_BALANCE": round(float(rng.uniform(0, 6)), 1),
                    "IS_SCHEDULER_UPDATE": True, "ELIGIBLE_MONTHS": 12,
                    "LEAVE_YEAR_ID": 1, "UPDATED_TIME": self.cfg.end_date,
                    "IS_REVOKED": False, "CREATED_DTM": self.cfg.end_date,
                })
        self._emit("M_CLMS_LEAVE_BALANCE", bal_rows, "CLMS")
        self._emit("T_CLMS_LEAVE_REQ_MASTER", req_rows, "CLMS")
        self._emit("T_CLMS_LEAVE_REQ_DETAIL", det_rows, "CLMS")
        self._emit("CREW_BALANCE_UPDATE_BY_SCHEDULER", upd_rows, "CLMS")

        # ── Ground duty, appreciations, attrition, movement ──
        gnd_rows = []
        for i in idx_in:
            for _ in range(int(rng.poisson(3))):
                start = self._dates(1)[0]
                gnd_rows.append({
                    "EMP_NO": self.crew_id[i], "LEG_CD": f"L{rng.integers(100, 999)}",
                    "START_TIME": start, "END_TIME": start + timedelta(hours=6),
                    "LEG_STATUS": "CLOSED",
                    "GND_ACT_CODE": GND_CODES[int(rng.integers(0, len(GND_CODES)))][1],
                    "APPROVED_STATUS": 1, "IS_PAYABLE": 1, "REMARK": None,
                })
        self._emit("T_CLMS_GND_CODES", gnd_rows, "CLMS")

        # Appreciations track overall competence — a genuine positive signal.
        appr_rows, aid = [], 1
        for i in idx_in:
            comp = float(lat["true_competence"].iloc[i])
            for _ in range(int(rng.poisson(max(0.05, 0.9 + 1.4 * comp)))):
                start = self._dates(1)[0]
                appr_rows.append({
                    "ID": aid, "CREW_ID": self.crew_id[i],
                    "CREW_NAME": lat["EMPLOYEE_NAME"].iloc[i], "BASE": lat["BASE"].iloc[i],
                    "FROM_DT": start, "TO_DT": start + timedelta(days=30),
                    "IS_AWARD_RECEIVED": bool(rng.random() < 0.6),
                    "CREATED_DTM": start,
                })
                aid += 1
        self._emit("T_SPL_APPRECIATION", appr_rows, "CLMS")

        att_rows, tid_ = [], 1
        for i in idx_in:
            if lat["ACTIVE"].iloc[i]:
                continue
            left = self._dates(1)[0]
            att_rows.append({
                "ID": tid_, "CREW_ID": self.crew_id[i],
                "CREW_NAME": lat["EMPLOYEE_NAME"].iloc[i],
                "MONTH": left.month, "YEAR": left.year,
                "DESIGNATION": lat["DESIGNATION"].iloc[i], "DOL": left,
                "LOCATION": lat["BASE"].iloc[i], "REASON": "Resigned",
                "SUB_REASON": "Personal", "CREATED_DT": left, "CATEGORY": "Voluntary",
            })
            tid_ += 1
        self._emit("T_CLMS_ATTRITION", att_rows, "CLMS")

        tr_rows, sw_rows, t_id, s_id = [], [], 1, 1
        for i in idx_in:
            if rng.random() < 0.10:
                tr_rows.append({
                    "T_ID": t_id, "CREW_ID": self.crew_id[i],
                    "CURRENT_BASE_ID": lat["BASE"].iloc[i],
                    "REQUESTED_BASE_ID": STATIONS[int(rng.integers(0, len(STATIONS)))],
                    "REASON": "Personal", "APPLY_DATE": self._dates(1)[0],
                    "PRIORITY_NO": int(rng.integers(1, 40)), "STATUS": "Pending",
                })
                t_id += 1
            if rng.random() < 0.06:
                sw_rows.append({
                    "SWAP_ID": s_id, "CREW_ID": self.crew_id[i],
                    "SWAP_APPLY_DATE": self._dates(1)[0], "SWAP_OR_PRIORITY": "SWAP",
                    "PRIORITY_NO": int(rng.integers(1, 40)), "STATUS": "Pending",
                    "SWAP_BASE": STATIONS[int(rng.integers(0, len(STATIONS)))],
                })
                s_id += 1
        self._emit("T_CLMS_CREW_TRANSFER_DETAIL", tr_rows, "CLMS")
        self._emit("T_CLMS_CREW_SWAP_DETAIL", sw_rows, "CLMS")

    # ── CrewPortal ─────────────────────────────────────────────────────────

    def build_crewportal(self) -> None:
        rng, lat = self.rng, self.latent

        # The bridge: this is the ONLY table carrying both identifiers.
        details, idx_in = [], []
        for i, c in enumerate(lat.itertuples()):
            if not self.in_portal[i]:
                continue
            idx_in.append(i)
            first, _, last = c.EMPLOYEE_NAME.partition(" ")
            details.append({
                "IGA": c.IGA, "CREW_ID": self.crew_id[i], "CREW_CAT": "CC",
                "CREW_NAME": c.EMPLOYEE_NAME, "FIRST_NAME": first, "LAST_NAME": last,
                "EMAIL_ID": c.EMPLOYEE_NAME.lower().replace(" ", ".") + "@airline.com",
                "IS_CREW": "Y", "CREW_TYPE": c.DESIGNATION, "CREW_BASE": c.BASE,
                "CITY": c.BASE, "COUNTRY": "IN",
            })
        self._emit("M_CREW_DETAILS", details, "CP")

        cat_rows = reference_data.ISSUE_CATEGORIES
        # The real extract ships two rows with a null name and no GUID. They are
        # kept (that is a fact about the source) but cannot be referenced as a
        # category, so issue rows draw only from the usable ones.
        self._emit("M_ISSUE_CATEGORY", cat_rows, "CP")
        cat_rows = [c for c in cat_rows if c["CATEGORY_UNIQUE_ID"]] or cat_rows
        self._emit("M_CREW_POSITION", reference_data.CREW_POSITIONS, "CP")
        self._emit("M_STATION", [
            {"STATION_ID": i + 1, "STATION_UNIQUE_ID": f"ST{i+1:03d}", "STATION_CODE": s,
             "STATION_NAME": f"{s} Airport", "IS_ACTIVE": True}
            for i, s in enumerate(STATIONS)], "CP")
        self._emit("M_AIRPORT_NAMES", [
            {"AIRPORT_ID": i + 1, "AIRPORT_CODE": s, "AIRPORT_NAME": f"{s} Intl",
             "CITY_NAME": s, "IS_ACTIVE": True, "CITY_TYPE": 1,
             "IS_DEFENCE_STATION": False, "IS_LIFE_VEST": False}
            for i, s in enumerate(STATIONS)], "CP")
        self._emit("M_AIRCRAFT", reference_data.AIRCRAFT, "CP")
        compliance_rows = reference_data.PROCESS_COMPLIANCE_CHECKS
        self._emit("M_FR_PROCESS_COMPLIANCE", compliance_rows, "CP")
        compliance_ids = [r["COMPLIANCE_ID"] for r in compliance_rows]
        self._emit("M_FR_PROCESS_COMPLIANCE_VALUE", [
            {"COMPLIANCE_VALUE_ID": 1, "COMPLIANCE_VALUE": "1", "COMPLIANCE_TEXT": "Yes",
             "DESCRIPTION": "Complied", "IS_ACTIVE": True},
            {"COMPLIANCE_VALUE_ID": 2, "COMPLIANCE_VALUE": "0", "COMPLIANCE_TEXT": "No",
             "DESCRIPTION": "Not complied", "IS_ACTIVE": True}], "CP")
        self._emit("DOCUMENT", [
            {"DOCUMENT_ID": i + 1, "DOC_NAME": n} for i, n in
            enumerate(["Safety Circular", "Service Bulletin", "Grooming Checklist"])], "CP")
        self._emit("DOCUMENT_DETAILS", [
            {"DOC_DETAIL_ID": i + 1, "DOCUMENT_ID": i + 1, "CHECK_LIST_NAME": f"CL-{i+1}",
             "AIRCRAFT": "A320", "ADDED_BY": "admin",
             "FROM_DATE": self.cfg.start_date, "TO_DATE": self.cfg.end_date,
             "UPLOADED_FILE_PATH": f"/docs/{i+1}.pdf"} for i in range(3)], "CP")

        # ── Flight issues: more when procedural care / reliability are low ──
        # Driven by whichever trait expresses procedural discipline in the
        # active reference. The hand-written bank had `safety`; the real form
        # has no safety category at all, so `ground_duties` (post-landing and
        # checklist compliance) carries that role now. Resolved rather than
        # hardcoded so either reference works.
        procedural_col = next(
            (c for c in ("trait_ground_duties", "trait_safety", "general_factor")
             if c in lat.columns), None)
        gen, det, pos, comp, api, checkin, alert, ack = [], [], [], [], [], [], [], []
        rid = 1
        for i in idx_in:
            safety = float(lat[procedural_col].iloc[i]) if procedural_col else 0.0
            rel = self.reliability[i]

            for _ in range(int(rng.poisson(max(0.05, 1.3 - 0.55 * safety)))):
                when = self._dates(1)[0]
                gen.append({
                    "REPORT_ID": rid, "IGA": lat["IGA"].iloc[i],
                    "FLIGHT_DATE": when.strftime("%Y-%m-%d"),
                    "START_TIME": "08:00", "FLIGHT_NO": f"6E{rng.integers(100, 9999)}",
                    "DEP": STATIONS[int(rng.integers(0, len(STATIONS)))],
                    "ARR": STATIONS[int(rng.integers(0, len(STATIONS)))],
                    "FLIGHT_LEAD_BASE": lat["BASE"].iloc[i],
                    "FLIGHT_REG": f"VT-{rng.integers(100,999)}",
                    "CREATED_ON": when, "IS_ACTIVE": 1, "FLIGHT_TYPE_ID": 1,
                    "SOURCE_APPLICATION_ID": 1,
                })
                cat = cat_rows[int(rng.integers(0, len(cat_rows)))]
                det.append({
                    "ID": rid, "REPORT_ID": rid,
                    "ISSUE_CATEGORY_GUID": cat["CATEGORY_UNIQUE_ID"],
                    "CREW_INVOLVED": lat["IGA"].iloc[i],
                    "STATION": STATIONS[int(rng.integers(0, len(STATIONS)))],
                    "IS_CRM_PROVIDED": "N",
                    "COMMENTS": f"{cat['CATEGORY_NAME'] or 'Unclassified'} reported on the sector",
                    "REQUEST_NUMBER": f"REQ{rid:07d}", "CREATED_ON": when, "IS_ACTIVE": 1,
                    "CRN_NO": f"CRN{rid:06d}", "ACTION": "Reviewed", "OUTCOME": "Closed",
                    "IS_SEND_MAIL": True, "IS_RETRY": 0,
                })
                pos.append({
                    "REPORT_ID": rid, "L1_IGA": lat["IGA"].iloc[i],
                    "L2_IGA": None, "R1_IGA": None, "R2_IGA": None,
                    "CAPT_IGA": None, "FO_IGA": None, "ACM_IGA": None,
                })
                for cid in compliance_ids:
                    complied = rng.random() < 1 / (1 + np.exp(-(2.0 + 0.7 * safety)))
                    comp.append({
                        "REPORT_ID": rid, "PROCESS_COMP_ID": cid,
                        "PROCESS_COMP_VALUE": 1 if complied else 0,
                        "PROCESS_COMP_REMARKS": None if complied else "Not complied",
                        "CREATED_ON": when, "IS_ACTIVE": 1,
                    })
                api.append({"REPORT_ID": rid, "CREATED_ON": when, "CREATED_BY": "api",
                            "DETAILS_INSERTED_STATUS": 1, "ERROR_MSG": None})
                rid += 1

            # Check-in: failures track reliability.
            for _ in range(int(rng.poisson(9))):
                when = self._dates(1)[0]
                failed = rng.random() < 1 / (1 + np.exp(-(-2.6 - 0.6 * rel)))
                checkin.append({
                    "CHECKIN_ID": len(checkin) + 1, "CREW_CHECKIN_MODEL": "MOBILE",
                    "IGA_CODE": lat["IGA"].iloc[i],
                    "EMAIL_ID": lat["EMPLOYEE_NAME"].iloc[i].lower().replace(" ", ".") + "@airline.com",
                    "IS_RUN": True, "IS_FAIL": bool(failed),
                    "CREATED_DATE": when, "UPDATED_DATE": when,
                })
            for _ in range(int(rng.poisson(4))):
                when = self._dates(1)[0]
                alert.append({
                    "ID": len(alert) + 1, "CREW_IGA": lat["IGA"].iloc[i],
                    "ALERT_CODE": f"ALT{rng.integers(1, 9)}", "ALERT_VALUE": "READ",
                    "CREATED_ON": when, "UPDATE_ON": when,
                    "ACKNOWLEDGED_COUNTER": int(max(1, rng.poisson(max(1.0, 2.2 - 0.5 * rel)))),
                    "USER_EMAIL_ID": lat["EMPLOYEE_NAME"].iloc[i].lower().replace(" ", ".") + "@airline.com",
                })
            for d in range(1, 4):
                when = self._dates(1)[0]
                ack.append({
                    "ID": len(ack) + 1, "CREW_IGA": lat["IGA"].iloc[i],
                    "CREW_EMAIL_ID": lat["EMPLOYEE_NAME"].iloc[i].lower().replace(" ", ".") + "@airline.com",
                    "CREW_BASE": lat["BASE"].iloc[i], "DOCUMENT_ID": d,
                    "DEPARTMENT_ID": 1, "DEPARTMENT_NAME": "InFlight",
                    "DOCUMENT_NAME": f"Doc {d}", "CIRCULAR_TYPE": "Safety",
                    "CREATED_DATE": when, "DOCUMENT_EXPIRY": self.cfg.end_date,
                    "MESSAGE": None, "PATH": f"/docs/{d}.pdf", "TYPE": "CIRCULAR",
                    "IS_SYNC": 1,
                    "STATUS": "ACK" if rng.random() < 1 / (1 + np.exp(-(1.9 + 0.6 * rel))) else "PENDING",
                    "UPDATED_DATE_TIME": when,
                })

        self._emit("T_FLIGHT_ISSUE_GENERAL_INFO", gen, "CP")
        self._emit("T_FLIGHT_ISSUE_DETAILS", det, "CP")
        self._emit("T_FLIGHT_ISSUE_WORK_POSITION_DETAILS", pos, "CP")
        self._emit("T_FLIGHT_PROCESS_COMPLIANCE", comp, "CP")
        self._emit("T_FLIGHT_REPORTS_FILLED_THROUGH_API", api, "CP")
        self._emit("T_CHECKIN", checkin, "CP")
        self._emit("T_ALERT_ACKNOWLEDGED_DATA", alert, "CP")
        self._emit("T_CREW_PAL_NOTIFICATION_ACKNOWLEDGEMENT", ack, "CP")

        wp = []
        for r in gen[: max(1, len(gen) // 2)]:
            wp.append({
                "FLIGHT_REPORT_WORK_POSITION_DETAILS_ID": len(wp) + 1,
                "REPORT_ID": r["REPORT_ID"], "CREW_POSITION_ID": "1",
                "CREW_IGA": r["IGA"], "CREATED_BY": "api",
                "CREATED_DATE": r["CREATED_ON"], "ACTIVE": True,
            })
        self._emit("T_FLIGHT_REPORT_WORK_POSITION_DETAILS", wp, "CP")
        self._emit("T_MISSING_FLIGHT_REPORT_GENERATION_REQUEST", [
            {"ID": 1, "INPUT": "{}", "EMAIL": "ops@airline.com",
             "PROCESS_START_TIME": self.cfg.end_date, "IS_GENERATED_REPORT": True,
             "IS_MAIL_SENT": True, "REQUESTED_BY": "ops",
             "REQUESTED_ON": self.cfg.end_date}], "CP")

    def run(self) -> dict:
        self.build_clms()
        self.build_crewportal()
        return {
            "tables": self.tables,
            "latent": self.latent,
            "coverage": {
                "crew": len(self.latent),
                "in_clms": int(self.in_clms.sum()),
                "in_crewportal": int(self.in_portal.sum()),
            },
        }


def schema_columns(source_names=("CLMS", "CrewPortal")) -> dict[str, list[str]]:
    """Ordered column lists straight from the registry, so frames conform exactly."""
    from crew_perf.sources import load_registry

    registry = load_registry()
    out: dict[str, list[str]] = {}
    for name in source_names:
        src = registry.sources.get(name)
        if src and src.available:
            for table, schema in src.tables.items():
                out[table] = schema.column_names
    return out


def generate_ops(latent: pd.DataFrame, cfg) -> dict:
    return OpsGenerator(latent, cfg, schema_columns()).run()
