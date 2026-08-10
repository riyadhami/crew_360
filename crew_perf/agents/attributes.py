"""Candidate scoring attributes, discovered from the graph.

An attribute is a per-crew number that could plausibly say something about
performance. Raw columns are not attributes — `MENTOR_FEEDBACK.MARK` is one row
per assessment, not one number per person — so each attribute carries the SQL
that aggregates it to crew grain, along with the direction that counts as good.

Discovery is graph-driven rather than hardcoded: the category list comes from
`PEP_CATEGORY`, so a new assessment category appears as a candidate attribute
without a code change. What stays fixed is the *shape* of the aggregation, since
that follows from the schema's grain, not from its contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_perf.graph.store import GraphStore

# Only submitted assessments carry a mark (the validity rule). Every attribute inherits this.
# Emitted from the rule rather than written as a literal, so the status code
# cannot drift between the declared rule and the queries that apply it.
from crew_perf.rules import submitted_predicate

SUBMITTED = submitted_predicate("ps")
CURRENT = "mf.P_IS_CURRENT = TRUE AND ps.P_IS_CURRENT = TRUE"

# ServiceNow's `Crew Feedback` category — inflight feedback about a crew member,
# as opposed to every other category, which is about the flight. Split by what
# the sub-category actually says, and enumerated rather than pattern-matched: a
# rule like "anything with 'issues' in the name is bad" would have swept in
# `Crew to Crew Handover Issues` correctly and `All world Passport` wrongly, and
# a new sub-category would join a scored signal without anyone deciding it should.
#
# Anything not listed here is administrative filing under the same category and
# is deliberately in neither list.
INFLIGHT_FEEDBACK_PRAISE = ("CREW_FEEDBACK__WOW_MOMENTS_CREATED_ZONE_WISE",)
INFLIGHT_FEEDBACK_CONCERN = (
    "CREW_FEEDBACK__CONDUCT_OF_CABIN_CREW",
    "CREW_FEEDBACK__GROOMING_OF_CABIN_CREW",
    "CREW_FEEDBACK__LATE_REPORTING_OF_CABIN_CREW",
    "CREW_FEEDBACK__CREW_TO_CREW_HANDOVER_ISSUES",
    "CREW_FEEDBACK__CONDUCT_OF_ACM_ON_BOARD",
    "CREW_FEEDBACK__NPSD",
)
INFLIGHT_FEEDBACK_PRAISE_SQL = ", ".join(f"'{c}'" for c in INFLIGHT_FEEDBACK_PRAISE)
INFLIGHT_FEEDBACK_CONCERN_SQL = ", ".join(f"'{c}'" for c in INFLIGHT_FEEDBACK_CONCERN)


@dataclass
class Attribute:
    name: str
    description: str
    expression: str                 # aggregate over the joined assessment frame
    direction: str                  # higher_is_better | lower_is_better
    source_tables: list[str]
    family: str                     # category_pass_rate | outcome | volume | qualitative
    rule_ref: str | None = None
    prior: float = 0.0              # declared baseline weight, normalised later
    requires_questions: bool = False  # needs the question-feedback join
    # What to call this in front of a reader. `name` is a column identifier and
    # reads like one: "pass_rate_aftertakeoff" is the Service Delivery section of
    # the assessment form, and nobody outside this codebase could know that.
    label: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()

    def to_dict(self) -> dict:
        return {
            "name": self.name, "label": self.display_label,
            "description": self.description,
            "direction": self.direction, "family": self.family,
            "source_tables": self.source_tables, "rule_ref": self.rule_ref,
            "prior": self.prior,
        }


@dataclass
class AttributeSet:
    attributes: list[Attribute] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [a.name for a in self.attributes]

    def by_name(self, name: str) -> Attribute | None:
        return next((a for a in self.attributes if a.name == name), None)


def _category_codes(executor) -> list[tuple[str, str]]:
    """(code, name) for every active assessment category, from the data."""
    res = executor.execute(
        "SELECT DISTINCT CATEGORY_CODE, CATEGORY FROM PEP_CATEGORY "
        "WHERE P_IS_CURRENT = TRUE AND ACTIVE = TRUE ORDER BY CATEGORY_CODE",
        limit=100,
    )
    return [(r[0], r[1]) for r in res.rows]


def _category_priors(executor) -> dict[str, float]:
    """Marks-derived prior per category, derived from the marks the template already assigns.

    PLAN.md §5: the prior is *derived, not invented* — `PEP_QUESTIONS.MARKS` is
    already a per-question weight, so summing it per category gives the weighting
    the business itself encoded. Safety-flagged questions get a documented
    multiplier on top.
    """
    res = executor.execute(
        """
        SELECT q.CATEGORY_CODE,
               SUM(q.MARKS) AS ca_marks,
               SUM(CASE WHEN q.SAFETY_ACTION_PARAMETER THEN q.MARKS ELSE 0 END) AS safety_marks
        FROM PEP_QUESTIONS q
        WHERE q.P_IS_CURRENT = TRUE AND q.ACTIVE = TRUE
        GROUP BY 1
        """,
        limit=100,
    )
    raw: dict[str, float] = {}
    for code, ca_marks, safety_marks in res.rows:
        marks = float(ca_marks or 0)
        # Leadership questions carry no Cabin Attendant mark (0), so a
        # marks-only prior would drop the category entirely. Fall back to an
        # equal share so it stays a candidate rather than vanishing silently.
        raw[code] = marks + 0.15 * float(safety_marks or 0)
    total = sum(raw.values())
    if total <= 0:
        return {code: 0.0 for code in raw}
    return {code: v / total for code, v in raw.items()}


def discover(store: GraphStore, executor) -> AttributeSet:
    """Build the candidate attribute pool for crew performance."""
    out = AttributeSet()
    categories = _category_codes(executor)
    priors = _category_priors(executor)

    zero_prior = [c for c, _ in categories if priors.get(c, 0.0) <= 0.0]
    if zero_prior:
        share = 1.0 / max(len(categories), 1)
        for code in zero_prior:
            priors[code] = share * 0.5
        out.notes.append(
            f"categories with no Cabin Attendant marks given a fallback prior: "
            f"{', '.join(zero_prior)} — they are assessed for Leads only, so a "
            f"marks-derived prior would drop them entirely"
        )

    total = sum(priors.values()) or 1.0
    priors = {k: v / total for k, v in priors.items()}

    # ── Category pass rates: the primary, high-variance signal (G8) ──
    for code, label in categories:
        out.attributes.append(Attribute(
            name=f"pass_rate_{code.lower()}",
            label=label,
            description=f"Share of {label} questions answered true",
            expression=(
                f"AVG(CASE WHEN q.CATEGORY_CODE = '{code}' "
                f"THEN (CASE WHEN qf.FEEDBACK = 'true' THEN 1.0 ELSE 0.0 END) END)"
            ),
            direction="higher_is_better",
            source_tables=["PEP_QUESTION_FEEDBACK", "PEP_QUESTIONS"],
            family="category_pass_rate",
            rule_ref=None,
            prior=priors.get(code, 0.0),
            requires_questions=True,
        ))

    # ── Safety and criticality: these override the aggregate ──
    out.attributes.append(Attribute(
        name="safety_failure_rate",
        label="Safety findings",
        description="Share of safety-action-parameter questions failed",
        expression=(
            "AVG(CASE WHEN q.SAFETY_ACTION_PARAMETER "
            "THEN (CASE WHEN qf.FEEDBACK = 'true' THEN 0.0 ELSE 1.0 END) END)"
        ),
        direction="lower_is_better",
        source_tables=["PEP_QUESTION_FEEDBACK", "PEP_QUESTIONS"],
        family="outcome",
        rule_ref="criticality",
        prior=0.10,
        requires_questions=True,
    ))
    out.attributes.append(Attribute(
        name="critical_failure_rate",
        label="Critical findings",
        description="Share of questions flagged CRITICAL that were failed",
        expression=(
            "AVG(CASE WHEN q.CRITICAL "
            "THEN (CASE WHEN qf.FEEDBACK = 'true' THEN 0.0 ELSE 1.0 END) END)"
        ),
        direction="lower_is_better",
        source_tables=["PEP_QUESTION_FEEDBACK", "PEP_QUESTIONS"],
        family="outcome",
        rule_ref="criticality",
        prior=0.06,
        requires_questions=True,
    ))

    # ── Aggregate outcome: what production reports ──
    out.attributes.append(Attribute(
        name="mean_mark",
        label="Assessment mark",
        description="Mean assessment mark across submitted assessments",
        expression="AVG(TRY_CAST(mf.MARK AS DOUBLE))",
        direction="higher_is_better",
        source_tables=["MENTOR_FEEDBACK"],
        family="outcome",
        rule_ref="mark_aggregation",
        prior=0.0,   # the label itself; carried for reporting, never self-scored
    ))
    out.attributes.append(Attribute(
        name="top_grade_share",
        label="Top grades",
        description="Share of assessments graded A+ or A",
        expression="AVG(CASE WHEN mf.GRADE IN ('A+','A') THEN 1.0 ELSE 0.0 END)",
        direction="higher_is_better",
        source_tables=["MENTOR_FEEDBACK"],
        family="outcome",
        rule_ref="grading",
        prior=0.0,
    ))

    # ── Volume and qualitative context: reported, never scored ──
    out.attributes.append(Attribute(
        name="assessment_count",
        label="Assessments completed",
        description="Number of submitted assessments in the period",
        expression="COUNT(DISTINCT mf.ID)",
        direction="higher_is_better",
        source_tables=["MENTOR_FEEDBACK"],
        family="volume",
        rule_ref="confidence",
        prior=0.0,
    ))
    out.attributes.append(Attribute(
        name="mentor_count",
        label="Mentors who assessed them",
        description="Distinct mentors who assessed this crew member",
        expression="COUNT(DISTINCT ps.MENTOR_LOGIN)",
        direction="higher_is_better",
        source_tables=["PEP_SCHEDULER"],
        family="volume",
        rule_ref="fairness",
        prior=0.0,
    ))
    out.attributes.append(Attribute(
        name="initiative_rate",
        label="Initiative shown",
        description="Share of assessments recording an innovative initiative",
        expression=("AVG(CASE WHEN mf.INNOVATIVE_INITIATIVES IS NOT NULL "
                    "AND mf.INNOVATIVE_INITIATIVES <> '' THEN 1.0 ELSE 0.0 END)"),
        direction="higher_is_better",
        source_tables=["MENTOR_FEEDBACK"],
        family="qualitative",
        rule_ref="fairness",
        prior=0.04,
    ))

    out.notes.append(
        "attributes in the `volume` family are never scored — assessment count "
        "governs confidence, not performance (the confidence rule); mentor count exists to "
        "detect single-mentor bias (the fairness rules)"
    )

    out.attributes.extend(_cross_source_attributes(executor))
    return out


# ─── Cross-source attributes (CLMS + CrewPortal) ────────────────────────────
# Derived from the `crew_performance_features` the domain guidance names, rather
# than invented here. Each is computed on its own grain and joined to the crew
# afterwards, because a single query spanning leave rows, issue rows and
# assessment rows multiplies every one of them.


def _cross_source_attributes(executor) -> list[Attribute]:
    available = set(executor.list_tables()) if executor else set()
    out: list[Attribute] = []

    if {"T_CLMS_LEAVE_REQ_MASTER", "T_CLMS_LEAVE_REQ_DETAIL", "M_CREW_DETAILS"} <= available:
        out.append(Attribute(
            name="leave_days_taken",
            label="Leave taken",
            description="Leave days requested over the period (lower indicates availability)",
            expression="", direction="lower_is_better",
            source_tables=["T_CLMS_LEAVE_REQ_MASTER", "T_CLMS_LEAVE_REQ_DETAIL"],
            family="availability", rule_ref=None, prior=0.0,
        ))
    # The third record of recognition, beside CLMS appreciations and ServiceNow
    # star-performer reports. Counted separately rather than added to either: a
    # letter raised through the Crew Appreciation Centre is a different act by a
    # different person from an appreciation keyed into CLMS, and `dynamic`
    # decides from the data whether the two move together — where they do it
    # clusters them into one construct, and where they do not, treating them as
    # one would have hidden that the sources disagree about who is recognised.
    if {"CAC_APPRECIATION"} <= available:
        out.append(Attribute(
            name="cac_appreciation_count",
            label="Appreciation letters",
            description="Appreciation letters raised for this crew member through the "
                        "Crew Appreciation Centre",
            expression="", direction="higher_is_better",
            source_tables=["CAC_APPRECIATION"],
            family="recognition", rule_ref=None, prior=0.0,
        ))
    if {"T_SPL_APPRECIATION", "M_CREW_DETAILS"} <= available:
        out.append(Attribute(
            name="appreciation_count",
            label="Appreciations received",
            description="Special appreciations recorded for this crew member",
            expression="", direction="higher_is_better",
            source_tables=["T_SPL_APPRECIATION"],
            family="recognition", rule_ref=None, prior=0.0,
        ))
    # Reports FILED, not issues attributed. `T_FLIGHT_ISSUE_GENERAL_INFO.IGA` is
    # the report header's crew — the person who raised it — so counting it as a
    # negative scored the reporter for the defect they found. That is the exact
    # inversion the design brief forbids: a crew member who notices a broken tray
    # and files it is more observant, not worse, and the one who flew the same
    # aircraft and said nothing scores better for the silence.
    #
    # Attribution has to come from who the report NAMES, not from who typed it.
    # In the delivered extract it cannot: `T_FLIGHT_ISSUE_DETAILS.CREW_INVOLVED`
    # and every position in `T_FLIGHT_ISSUE_WORK_POSITION_DETAILS` equal the filer
    # on all 995 reports, so the source carries no way to separate the two. Until
    # it does, CrewPortal flight issues are exposure, not quality: kept in the
    # `volume` family, reported for context, never scored.
    if {"T_FLIGHT_ISSUE_GENERAL_INFO"} <= available:
        out.append(Attribute(
            name="flight_reports_filed",
            label="Flight reports raised",
            description="Flight issue reports this crew member raised (reporting volume, "
                        "not fault — the source cannot attribute an issue to a person)",
            expression="", direction="higher_is_better",
            source_tables=["T_FLIGHT_ISSUE_GENERAL_INFO", "T_FLIGHT_ISSUE_DETAILS"],
            family="volume", rule_ref="confidence", prior=0.0,
        ))
    if {"T_FLIGHT_PROCESS_COMPLIANCE", "T_FLIGHT_ISSUE_GENERAL_INFO"} <= available:
        out.append(Attribute(
            name="process_compliance_rate",
            label="Process compliance",
            description="Share of flight process-compliance checks passed",
            expression="", direction="higher_is_better",
            source_tables=["T_FLIGHT_PROCESS_COMPLIANCE"],
            family="compliance", rule_ref=None, prior=0.0,
        ))
    if {"T_CHECKIN"} <= available:
        out.append(Attribute(
            name="checkin_failure_rate",
            label="Check-in reliability",
            description="Share of crew check-in attempts that failed",
            expression="", direction="lower_is_better",
            source_tables=["T_CHECKIN"],
            family="reliability", rule_ref=None, prior=0.0,
        ))

    # ── ServiceNow crew feedback ──
    # Every rate below is over the flights this crew member actually operated in
    # the extract, not over all flights: the denominator has to be their own
    # exposure or a crew who flew twice looks flawless next to one who flew forty
    # times. Involvement — not presence — is what attributes a report to a
    # person; being rostered on a flight where catering failed says nothing about
    # the crew, and counting it would score them for somebody else's shortfall.
    if {"SN_REPORT_CREW", "SN_FLIGHT_REPORT"} <= available:
        out += [
            Attribute(
                name="sn_reports_operated",
                label="Reported flights operated",
                description="Reported flights this crew member operated (ServiceNow extract)",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW"],
                family="volume", rule_ref="confidence", prior=0.0,
            ),
            # Being named involved is only a negative when the report says the
            # crew fell short. `sn_involvement_rate` used to average IS_INVOLVED
            # across every polarity, which counted 12,511 OPERATIONAL rows
            # (catering, engineering, airport, delay), 2,424 REPORTING rows (the
            # crew filing a report — the process working) and 882 APPRECIATION
            # rows against the crew member. The last of those was scored as a
            # positive by `sn_appreciation_count` and a negative here from the
            # same flag. Filtered to the polarities that actually say something
            # about the crew, it became a 99.4% duplicate of
            # `sn_service_issue_rate` (16,675 SERVICE vs 104 UNCLASSIFIED
            # involvements), so that signal now carries this alone.
            #
            # The operational rows are not deleted — they are exposure, and the
            # environment a crew flew in is worth seeing next to their score. They
            # are kept in the `volume` family and never scored.
            Attribute(
                name="sn_operational_exposure_rate",
                label="Disrupted flights flown",
                description="Share of their reported flights disrupted by an upstream "
                            "operational failure (catering, engineering, airport, delay) "
                            "— a fact about the flight, not about the crew",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW", "SN_FLIGHT_REPORT"],
                family="volume", rule_ref="confidence", prior=0.0,
            ),
            # Named in a service report means the crew ACTED, not that they were
            # at fault, and the free text is unambiguous about it. Read across
            # the SERVICE polarity: 6,674 reports are "Customer Issues - Service
            # Recovery Done", 2,982 are "Cabin Events / Safety reporting", and
            # the crew named on them are the ones who did the recovery or found
            # the defect — "Lav F fire extinguisher viewing window was found
            # broken while doing pre flight check" names L4, the crew member who
            # found it; "crew Ms Mahin khan (r1) intervened to separate" names
            # R1, who broke up a physical assault; "Hot beverages couldn't be
            # served because of turbulence" names the whole cabin crew.
            #
            # Scored `lower_is_better` this penalised service recovery, defect
            # detection, and handling an unruly passenger. The extract carries no
            # field that says a crew member CAUSED anything, so no direction can
            # honestly be claimed here: it goes in `volume`, reported beside a
            # score and never inside one. `sn_service_deviation_rate` — the
            # operating lead's own recorded judgement that the service standard
            # was missed — is the only ServiceNow signal that attributes a
            # shortfall, and it belongs to a different table for that reason.
            Attribute(
                name="sn_service_response_rate",
                label="Named as acting on a report",
                description="Share of their reported flights where they were named as "
                            "acting on a customer- or cabin-facing issue (response and "
                            "detection activity — the extract never records fault)",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW", "SN_FLIGHT_REPORT"],
                family="volume", rule_ref="confidence", prior=0.0,
            ),
            # Detection quality: of the reports this crew member acted on, how
            # many were substantiated afterwards. This is the signal that lets
            # reporting count FOR someone rather than merely not against them —
            # a crew member whose reports repeatedly turn out to be real defects
            # is more observant, which is invisible to any count of reports.
            #
            # Two forms of substantiation, both recorded by somebody other than
            # the reporting crew:
            #   CDLB_ENTRY_MADE = 'Yes'   a Cabin Defect Log Book entry — the
            #                             defect was written into the aircraft's
            #                             log and went to engineering
            #   resolved by a person      IS_AUTO_RESOLVED marks closure by the
            #                             Crew Portal integration, which is an
            #                             acknowledgement rather than an
            #                             investigation; a human resolver means
            #                             the report was actually worked
            Attribute(
                name="sn_validated_report_rate",
                label="Issues they raised that proved real",
                description="Share of the reports this crew member acted on that were "
                            "substantiated afterwards — a cabin defect log entry was "
                            "raised, or a person worked the incident rather than the "
                            "portal auto-acknowledging it",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW", "SN_FLIGHT_REPORT"],
                family="detection", rule_ref="attribute_importance", prior=0.0,
            ),
            Attribute(
                name="sn_appreciation_count",
                label="Star-performer mentions",
                description="Star-performer reports naming this crew member",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW", "SN_FLIGHT_REPORT"],
                family="recognition", rule_ref="attribute_importance", prior=0.0,
            ),
            # ── Inflight feedback: what was said about this crew member ──
            # The `Crew Feedback` category is the only place in ServiceNow where
            # a report is ABOUT the crew rather than about the flight. Its
            # sub-categories split cleanly in two — "Wow Moments Created Zone
            # Wise" is praise, while conduct, grooming, late reporting and
            # handover are concerns raised about them — and the two must stay
            # separate signals. Averaged into one they cancel: a crew member
            # praised twice and flagged twice reads as unremarkable, when in fact
            # two different people wrote down two strong and opposite things.
            #
            # `All world Passport` and `International Layover Sign In` are
            # administrative filings under the same category and say nothing
            # about how anyone performed; both are deliberately outside either
            # signal rather than silently swept into the concern side.
            #
            # Rates over their OWN reported flights, like every other ServiceNow
            # signal here: a count would make a crew member who flew forty
            # reported sectors look worse than one who flew two.
            Attribute(
                name="sn_inflight_feedback_praise_rate",
                label="Praised in inflight feedback",
                description="Share of their reported flights where inflight feedback "
                            "singled this crew member out for creating a wow moment",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW", "SN_FLIGHT_REPORT"],
                family="recognition", rule_ref="attribute_importance", prior=0.0,
            ),
            Attribute(
                name="sn_inflight_feedback_concern_rate",
                label="Concerns raised in inflight feedback",
                description="Share of their reported flights where inflight feedback "
                            "raised a concern about this crew member — conduct, "
                            "grooming, late reporting or handover",
                expression="", direction="lower_is_better",
                source_tables=["SN_REPORT_CREW", "SN_FLIGHT_REPORT"],
                family="conduct", rule_ref="attribute_importance", prior=0.0,
            ),
            Attribute(
                name="sn_cx_champion_rate",
                label="Customer-experience nominations",
                description="Share of their reported flights where they were nominated "
                            "customer-experience champion",
                expression="", direction="higher_is_better",
                source_tables=["SN_REPORT_CREW"],
                family="recognition", rule_ref="attribute_importance", prior=0.0,
            ),
        ]
    if {"SN_SERVICE_CHECK", "SN_REPORT_CREW"} <= available:
        out.append(Attribute(
            name="sn_service_deviation_rate",
            label="Service deviations recorded",
            description="Share of their reported flights the lead recorded a service "
                        "deviation on",
            expression="", direction="lower_is_better",
            source_tables=["SN_SERVICE_CHECK", "SN_REPORT_CREW"],
            family="compliance", rule_ref="attribute_importance", prior=0.0,
        ))
    return out


# Each cross-source attribute is its own query at its own grain, keyed to the
# canonical IGA. Mixing grains in one statement is how a leave row silently
# multiplies an issue count.
_CROSS_SQL = {
    "leave_days_taken": """
        SELECT c.IGA AS iga, COALESCE(SUM(d.NO_OF_LEAVES), 0) AS leave_days_taken
        FROM M_CREW_DETAILS c
        JOIN T_CLMS_LEAVE_REQ_MASTER m ON m.CREW_ID = c.CREW_ID AND m.P_IS_CURRENT = TRUE
        JOIN T_CLMS_LEAVE_REQ_DETAIL d ON d.LEAVE_DETAIL_ID = m.LEAVE_DETAIL_ID
                                      AND d.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND m.STATUS_TYPE_ID = 1
        GROUP BY 1
    """,
    "appreciation_count": """
        SELECT c.IGA AS iga, COUNT(*) AS appreciation_count
        FROM M_CREW_DETAILS c
        JOIN T_SPL_APPRECIATION a ON a.CREW_ID = c.CREW_ID AND a.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE
        GROUP BY 1
    """,
    # Read straight off the extract rather than through a crew master. The letter
    # already carries the IGA it was raised for, and routing it through
    # M_CREW_DETAILS would make this a two-engine query in Snowflake mode — the
    # extract is local and the crew master is in the warehouse, which is refused
    # by name. Crew with no letter are absent here, never zero-filled.
    "cac_appreciation_count": """
        SELECT a.IGA AS iga, COUNT(*) AS cac_appreciation_count
        FROM CAC_APPRECIATION a
        WHERE a.P_IS_CURRENT = TRUE AND a.IGA IS NOT NULL
        GROUP BY 1
    """,
    # g.IGA is the crew member who RAISED the report, not one it holds
    # responsible. Named accordingly so no later reader can mistake it.
    "flight_reports_filed": """
        SELECT g.IGA AS iga, COUNT(*) AS flight_reports_filed
        FROM T_FLIGHT_ISSUE_GENERAL_INFO g
        WHERE g.P_IS_CURRENT = TRUE AND g.IS_ACTIVE = 1
        GROUP BY 1
    """,
    "process_compliance_rate": """
        SELECT g.IGA AS iga,
               AVG(CASE WHEN p.PROCESS_COMP_VALUE = 1 THEN 1.0 ELSE 0.0 END)
                   AS process_compliance_rate
        FROM T_FLIGHT_PROCESS_COMPLIANCE p
        JOIN T_FLIGHT_ISSUE_GENERAL_INFO g ON g.REPORT_ID = p.REPORT_ID
                                          AND g.P_IS_CURRENT = TRUE
        WHERE p.P_IS_CURRENT = TRUE
        GROUP BY 1
    """,
    "checkin_failure_rate": """
        SELECT t.IGA_CODE AS iga,
               AVG(CASE WHEN t.IS_FAIL THEN 1.0 ELSE 0.0 END) AS checkin_failure_rate
        FROM T_CHECKIN t
        WHERE t.P_IS_CURRENT = TRUE
        GROUP BY 1
    """,
    # ServiceNow. Cabin seats only — the extract also names the flight deck, and
    # a captain has no cabin-crew scorecard to contribute to.
    "sn_reports_operated": """
        SELECT c.IGA AS iga, COUNT(*) AS sn_reports_operated
        FROM SN_REPORT_CREW c
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    "sn_operational_exposure_rate": """
        SELECT c.IGA AS iga,
               AVG(CASE WHEN r.POLARITY = 'OPERATIONAL' THEN 1.0 ELSE 0.0 END)
                   AS sn_operational_exposure_rate
        FROM SN_REPORT_CREW c
        JOIN SN_FLIGHT_REPORT r ON r.SN_REPORT_ID = c.SN_REPORT_ID AND r.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    "sn_service_response_rate": """
        SELECT c.IGA AS iga,
               AVG(CASE WHEN c.IS_INVOLVED AND r.POLARITY = 'SERVICE' THEN 1.0 ELSE 0.0 END)
                   AS sn_service_response_rate
        FROM SN_REPORT_CREW c
        JOIN SN_FLIGHT_REPORT r ON r.SN_REPORT_ID = c.SN_REPORT_ID AND r.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    # Denominator is the reports this crew member was NAMED on, not every report
    # on their flights: substantiation is a property of a report they acted on,
    # and diluting it by flights they were never named in would just measure how
    # much they flew. Crew named on no report at all get no value rather than a
    # zero — never having filed is not the same as filing and being wrong.
    "sn_validated_report_rate": """
        SELECT c.IGA AS iga,
               AVG(CASE WHEN r.CDLB_ENTRY_MADE = 'Yes'
                          OR (r.IS_RESOLVED AND NOT r.IS_AUTO_RESOLVED)
                        THEN 1.0 ELSE 0.0 END) AS sn_validated_report_rate
        FROM SN_REPORT_CREW c
        JOIN SN_FLIGHT_REPORT r ON r.SN_REPORT_ID = c.SN_REPORT_ID AND r.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
          AND c.IS_INVOLVED = TRUE
        GROUP BY 1
    """,
    "sn_appreciation_count": """
        SELECT c.IGA AS iga,
               SUM(CASE WHEN c.IS_INVOLVED AND r.POLARITY = 'APPRECIATION' THEN 1 ELSE 0 END)
                   AS sn_appreciation_count
        FROM SN_REPORT_CREW c
        JOIN SN_FLIGHT_REPORT r ON r.SN_REPORT_ID = c.SN_REPORT_ID AND r.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    # Inflight feedback about this crew member. Involvement is what makes the
    # report about THEM — a `Crew Feedback` report on a flight they operated but
    # were not named on is somebody else's feedback.
    "sn_inflight_feedback_praise_rate": f"""
        SELECT c.IGA AS iga,
               AVG(CASE WHEN c.IS_INVOLVED
                         AND r.SUB_CATEGORY_CODE IN ({INFLIGHT_FEEDBACK_PRAISE_SQL})
                        THEN 1.0 ELSE 0.0 END) AS sn_inflight_feedback_praise_rate
        FROM SN_REPORT_CREW c
        JOIN SN_FLIGHT_REPORT r ON r.SN_REPORT_ID = c.SN_REPORT_ID AND r.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    "sn_inflight_feedback_concern_rate": f"""
        SELECT c.IGA AS iga,
               AVG(CASE WHEN c.IS_INVOLVED
                         AND r.SUB_CATEGORY_CODE IN ({INFLIGHT_FEEDBACK_CONCERN_SQL})
                        THEN 1.0 ELSE 0.0 END) AS sn_inflight_feedback_concern_rate
        FROM SN_REPORT_CREW c
        JOIN SN_FLIGHT_REPORT r ON r.SN_REPORT_ID = c.SN_REPORT_ID AND r.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    "sn_cx_champion_rate": """
        SELECT c.IGA AS iga,
               AVG(CASE WHEN c.IS_CX_CHAMPION THEN 1.0 ELSE 0.0 END) AS sn_cx_champion_rate
        FROM SN_REPORT_CREW c
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
        GROUP BY 1
    """,
    # Only answered deviations count. Roughly half the reports leave the check
    # blank, and treating a blank as "no deviation" would score a crew member for
    # a question the lead never answered.
    "sn_service_deviation_rate": """
        SELECT c.IGA AS iga,
               AVG(CASE WHEN s.IS_PASS THEN 0.0 ELSE 1.0 END) AS sn_service_deviation_rate
        FROM SN_REPORT_CREW c
        JOIN SN_SERVICE_CHECK s ON s.SN_REPORT_ID = c.SN_REPORT_ID AND s.P_IS_CURRENT = TRUE
        WHERE c.P_IS_CURRENT = TRUE AND c.IS_CABIN_CREW = TRUE
          AND s.CHECK_CODE = 'DEVIATION' AND s.IS_PASS IS NOT NULL
        GROUP BY 1
    """,
}


def build_cross_source_frame(executor, attrs: AttributeSet):
    """Materialise the cross-source attributes, one query per grain."""
    import pandas as pd

    frames = []
    # A cross-source attribute is exactly one with no inline expression — it is
    # computed by its own query at its own grain. Selecting by family instead
    # meant every new family had to be added here too, and one that was not
    # silently produced a column of nulls rather than an error.
    wanted = {a.name for a in attrs.attributes if not a.expression}
    for name, sql in _CROSS_SQL.items():
        if name not in wanted:
            continue
        try:
            res = executor.execute(sql, limit=10**6)
        except Exception:  # noqa: BLE001 - a missing source must not break scoring
            continue
        if res.rows:
            frames.append(pd.DataFrame(res.rows, columns=res.columns).set_index("iga"))

    if not frames:
        return pd.DataFrame()
    frame = frames[0]
    for extra in frames[1:]:
        frame = frame.join(extra, how="outer")
    return frame.apply(pd.to_numeric, errors="coerce")


def build_frame(executor, attrs: AttributeSet, period: tuple[str, str] | None = None):
    """Materialise every attribute per crew member as a DataFrame indexed by IGA.

    Two queries rather than one: attributes needing the question-feedback join
    fan out to ~21 rows per assessment, and mixing that grain with per-assessment
    aggregates in a single query silently multiplies the latter.
    """
    import pandas as pd

    where_period = ""
    if period:
        where_period = f" AND ps.PEP_DATE BETWEEN DATE '{period[0]}' AND DATE '{period[1]}'"

    # Cross-source attributes carry no inline expression — they are computed by
    # their own query at their own grain (see `build_cross_source_frame`) and
    # joined afterwards. Including them here would splice an empty expression
    # into the PEP select list.
    pep_attrs = [a for a in attrs.attributes if a.expression]
    assessment_attrs = [a for a in pep_attrs if not a.requires_questions]
    question_attrs = [a for a in pep_attrs if a.requires_questions]

    frames = []
    if assessment_attrs:
        cols = ",\n       ".join(f"{a.expression} AS {a.name}" for a in assessment_attrs)
        sql = f"""
            SELECT mf.IGA AS iga,
                   {cols}
            FROM MENTOR_FEEDBACK mf
            JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
            WHERE {CURRENT} AND {SUBMITTED}{where_period}
            GROUP BY 1
        """
        res = executor.execute(sql, limit=10**6)
        frames.append(pd.DataFrame(res.rows, columns=res.columns).set_index("iga"))

    if question_attrs:
        cols = ",\n       ".join(f"{a.expression} AS {a.name}" for a in question_attrs)
        sql = f"""
            SELECT mf.IGA AS iga,
                   {cols}
            FROM PEP_QUESTION_FEEDBACK qf
            JOIN PEP_QUESTIONS q ON qf.QUESTION_ID = q.QUESTION_ID
            JOIN MENTOR_FEEDBACK mf ON mf.ID = qf.FEEDBACK_ID
            JOIN PEP_SCHEDULER ps ON mf.PEP_SCHDULER_ID = ps.PEP_SCHDULER_ID
            WHERE {CURRENT} AND qf.P_IS_CURRENT = TRUE AND q.P_IS_CURRENT = TRUE
              AND {SUBMITTED}{where_period}
            GROUP BY 1
        """
        res = executor.execute(sql, limit=10**6)
        frames.append(pd.DataFrame(res.rows, columns=res.columns).set_index("iga"))

    if not frames:
        return pd.DataFrame()
    frame = frames[0]
    for extra in frames[1:]:
        frame = frame.join(extra, how="outer")

    # CLMS and CrewPortal attributes arrive on their own grains, keyed to the
    # canonical IGA. Left-joined, so a crew member absent from one source keeps
    # their PEP attributes and simply has nulls there — which shows up as reduced
    # coverage on the scorecard rather than as a missing crew member.
    cross = build_cross_source_frame(executor, attrs)
    if not cross.empty:
        frame = frame.join(cross, how="left")
    return frame.apply(pd.to_numeric, errors="coerce")
