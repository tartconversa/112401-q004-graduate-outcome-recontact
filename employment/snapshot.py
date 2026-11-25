"""把读取、学籍、身份、分类、抽样、估计各环节按同一版口径串成快照。

一次 ``build_snapshot`` 对应"每轮答复收完后"的一次完整更新：
全部院校层与专业层结论使用同一份学籍认定、同一版代号映射、
同一套地区口径和同一份复访设计，杜绝总体与细分口径漂移。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .changes import SnapshotBundle
from .classify import Classification, UNKNOWN, classify_student
from .estimate import (GroupEstimate, StudentOutcome, all_comparisons,
                       build_estimates, build_outcomes)
from .identity import IdentityResolution, resolve_identities
from .load import CohortInputs
from .roster import Roster, build_roster
from .survey import SurveySnapshot, build_survey


@dataclass
class SnapshotResult:
    asof: date
    inputs: CohortInputs
    roster: Roster
    identities: IdentityResolution
    survey: SurveySnapshot
    classifications: dict[str, Classification]
    outcomes: dict[str, StudentOutcome]
    estimates: dict[str, GroupEstimate]
    comparisons: list[dict[str, object]]
    withdrawn_pairs: set[tuple[str, str]]
    audit: list[dict[str, object]] = field(default_factory=list)

    def uncovered_students(self) -> set[str]:
        return {sid for sid, out in self.outcomes.items()
                if out.label == UNKNOWN}

    def to_bundle(self) -> SnapshotBundle:
        return SnapshotBundle(
            asof=self.asof.isoformat(),
            labels={sid: out.label for sid, out in self.outcomes.items()},
            label_detail={
                sid: {
                    **(out.classification.as_dict()
                       if out.classification else {}),
                    "area": out.area,
                    "main_school": out.school,
                    "source": out.source,
                    "weight": out.weight,
                    "probability_frames": out.probability_frames,
                }
                for sid, out in self.outcomes.items()
            },
            estimates=self.estimates,
            comparisons=self.comparisons,
            disputed=list(self.roster.disputed),
            active_mappings=dict(self.identities.external_to_student),
            frame_cutoffs={
                fid: d.frame.cutoff.isoformat()
                for fid, d in self.survey.designs.items()},
            policy_signature={
                p.policy: (p.area, p.window_days, p.days,
                           p.threshold_hours, p.threshold_income)
                for p in self.inputs.policies},
            events=list(self.identities.events),
            inclusion_records=self._inclusion_records(),
        )

    def _inclusion_records(self) -> dict[str, list[dict[str, object]]]:
        records: dict[str, list[dict[str, object]]] = {}
        for sid, visits in self.survey.visits_by_student.items():
            records[sid] = [
                {"visit": v.visit, "round": v.round, "frame": v.frame,
                 "kind": v.kind,
                 "probability": (v.numer / v.denom
                                 if v.numer is not None and v.denom else None),
                 "reply": v.reply,
                 "replied": v.replied.isoformat() if v.replied else None}
                for v in visits]
        return records


def build_snapshot(inputs: CohortInputs, asof: date) -> SnapshotResult:
    """按截至日 asof 复现一版完整的届分析。"""
    window_days = inputs.policies[0].window_days
    if any(p.window_days != window_days for p in inputs.policies):
        raise ValueError("各地区 window_days 不一致，无法定义统一观察窗")

    roster = build_roster(inputs, asof, window_days)
    identities = resolve_identities(inputs, asof)
    survey = build_survey(inputs, asof)

    classifications: dict[str, Classification] = {}
    for sid, rec in roster.students.items():
        evidence = identities.student_evidence.get(sid, [])
        visit_facts = survey.visit_facts(sid)
        if rec.main_school is not None and rec.area is not None:
            policy = inputs.policy_for_area(rec.area)
            classifications[sid] = classify_student(
                rec, evidence, visit_facts, policy)
        else:
            # 主校争议：按每个候选地区的口径各评一次并全部留痕，
            # 但不进入任何院校分母（roster 已将其排除）。
            per_candidate = {}
            for school in rec.school_options:
                area = next((s.area for s in inputs.schools
                             if s.school == school), None)
                if area is None:
                    continue
                policy = inputs.policy_for_area(area)
                per_candidate[school] = classify_student(
                    rec, evidence, visit_facts, policy).as_dict()
            fallback = inputs.policies[0]
            base = classify_student(rec, evidence, visit_facts, fallback)
            base.conflicts.append({
                "type": "disputed_main_school",
                "candidate_assessments": per_candidate,
                "resolution": "主校未定，不进入任何可比较分母",
            })
            classifications[sid] = base

    # 身份/主校与框架冻结地区不一致的机会对，直接退回，不进入权重。
    withdrawn_pairs: set[tuple[str, str]] = set()
    for sid, rec in roster.students.items():
        if rec.area is None:
            continue
        for design in survey.designs.values():
            if not design.is_probability or sid not in design.frame.codes:
                continue
            if design.frame.area != rec.area:
                withdrawn_pairs.add((sid, design.frame.frame))

    outcomes = build_outcomes(roster, classifications, survey,
                              withdrawn_pairs)
    estimates = build_estimates(roster, outcomes, survey)
    comparisons = all_comparisons(estimates, survey)

    audit = list(inputs.load_warnings)
    audit.extend({"survey": item} for item in survey.audit)
    for sid, frame_id in sorted(withdrawn_pairs):
        audit.append({"withdrawn_inclusion": {"student": sid,
                                              "frame": frame_id}})
    return SnapshotResult(
        asof=asof, inputs=inputs, roster=roster, identities=identities,
        survey=survey, classifications=classifications, outcomes=outcomes,
        estimates=estimates, comparisons=comparisons,
        withdrawn_pairs=withdrawn_pairs,
        audit=[{"message": w} if isinstance(w, str) else w
               for w in audit])
