"""覆盖、加权估计、部分识别区间与院校比较。

估计原则（与需求逐条对应）：

1. 分母只含主校已确认的学籍学生；争议学生不进分母，只在争议影响中列出。
2. 结论分三类证据来源：
   - 书面/企业回执等普查式证据（census）：权重 1，覆盖即事实；
   - 随机复访答复（probability）：按可追溯抽中机会的倒数加权；
   - 指定复查（mandatory）：只作个案核实，权重不进推断；
   - 未覆盖：保持 unknown，**不并入失业**。
3. 区间同时容纳抽样误差与部分识别：未知人群可能全部达标或全部不达标，
   形成部分识别界，再叠加抽样标准误。
4. 院校/专业比较若区间重合或差值区间含 0，结论保留"无法判定先后"。
5. 总体与细分共用同一版分类结果与权重，不存在两套口径。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .classify import LABELS, QUALIFIED, UNKNOWN, Classification
from .roster import Roster, StudentRecord
from .survey import SurveySnapshot

Z_95 = 1.96


@dataclass
class StudentOutcome:
    student: str
    school: str | None
    area: str | None
    program: str
    label: str
    source: str                 # census | survey | mandatory_only | unknown
    weight: float
    probability_frames: list[str]
    reasons: list[str]
    classification: Classification | None


@dataclass
class GroupEstimate:
    level: str                  # school | school_program
    school: str
    program: str | None
    area: str
    n: int
    coverage: float
    coverage_detail: dict[str, int]
    label_counts: dict[str, int]
    qualified_rate: float | None
    interval: list[float]
    partial_bounds: list[float]
    sampling_se: float
    missing_sources: list[dict[str, object]]
    uncovered_students: list[str]
    dispute_candidates: list[str]
    frame_contributions: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def build_outcomes(roster: Roster,
                   classifications: dict[str, Classification],
                   survey: SurveySnapshot,
                   withdrawn_pairs: set[tuple[str, str]] | None = None
                   ) -> dict[str, StudentOutcome]:
    """把分类结果与抽样留痕合成为每名学生的可估计结论。

    withdrawn_pairs：因身份/主校更正而退回的 (学生, 框架) 机会对，
    这些框架既不赋权重也不计入可追溯框架清单。
    """
    withdrawn_pairs = withdrawn_pairs or set()
    outcomes: dict[str, StudentOutcome] = {}
    for student, rec in roster.students.items():
        cls = classifications.get(student)
        label = cls.label if cls else UNKNOWN
        prob_visits = [v for v in survey.probability_visits(student)
                       if (student, v.frame) not in withdrawn_pairs]
        mand_visits = [v for v in survey.visits_by_student.get(student, [])
                       if v.kind != "random" or v.numer is None]
        frames_used = sorted({v.frame for v in prob_visits})
        reasons = cls.unknown_reasons if cls else []

        if label != UNKNOWN:
            documentary = bool(cls and cls.used_evidence)
            if documentary:
                source, weight = "census", 1.0
            elif prob_visits:
                # 跨多个随机框架时用合成累积机会的倒数作权重。
                p_eff = 1.0
                seen_frames = {v.frame for v in prob_visits}
                for frame_id in seen_frames:
                    design = survey.designs[frame_id]
                    p_eff *= 1 - (design.probability or 0.0)
                weight = 1.0 / (1.0 - p_eff)
                source = "survey"
            else:
                # 仅指定复查支撑的事实不能赋推断权重，退回未知但保留留痕。
                source, weight = "mandatory_only", 1.0
                if mand_visits:
                    reasons = reasons + [
                        f"{v.visit}: 仅指定复查，不进入抽样推断"
                        for v in mand_visits]
                label = UNKNOWN
        else:
            source, weight = "unknown", 0.0

        outcomes[student] = StudentOutcome(
            student=student, school=rec.main_school, area=rec.area,
            program=rec.program, label=label, source=source, weight=weight,
            probability_frames=frames_used, reasons=reasons,
            classification=cls)
    return outcomes


def _frame_qualified_variance(outcomes: dict[str, StudentOutcome],
                              survey: SurveySnapshot,
                              frame_id: str,
                              members: set[str]) -> tuple[float, float, int]:
    """单个随机框架对合格人数总量的估计与无放回方差。

    用该框架中实际由随机复访定性的成员做 SRS 无放回估计；
    n=1 时样本方差为 0，会在 notes 中提示方差被低估。
    """
    design = survey.designs[frame_id]
    n_frame = len(design.frame.codes)
    p = design.probability or 0.0
    contributors = [
        outcomes[v.code] for v in design.dispatched
        if v.code in members and v.code in outcomes
        and outcomes[v.code].source == "survey"
        and frame_id in outcomes[v.code].probability_frames
    ]
    n = len(contributors)
    if n == 0:
        return 0.0, 0.0, 0
    y = [1.0 if c.label == QUALIFIED else 0.0 for c in contributors]
    weights = [1.0 / p] * n
    q_hat = sum(w * yi for w, yi in zip(weights, y))
    mean = sum(y) / n
    if n > 1:
        s2 = sum((yi - mean) ** 2 for yi in y) / (n - 1)
        var = (n_frame ** 2) * (1 - n / n_frame) * s2 / n
    else:
        var = 0.0
    return q_hat, var, n


def _missing_reasons(outcomes: dict[str, StudentOutcome],
                     members: list[str]) -> list[dict[str, object]]:
    buckets: dict[str, list[str]] = {}
    for student in members:
        out = outcomes[student]
        if out.label != UNKNOWN:
            continue
        key = "；".join(out.reasons) if out.reasons else "无任何有效证据"
        buckets.setdefault(key, []).append(student)
    return [{"reason": reason, "count": len(ids), "students": sorted(ids)}
            for reason, ids in sorted(buckets.items())]


def estimate_group(level: str, school: str, program: str | None,
                   area: str, members: list[StudentRecord],
                   outcomes: dict[str, StudentOutcome],
                   survey: SurveySnapshot,
                   dispute_candidates: list[str]) -> GroupEstimate:
    member_ids = {m.student for m in members}
    n = len(members)
    detail = {"census": 0, "survey": 0, "mandatory_only": 0, "unknown": 0}
    label_counts = {label: 0 for label in LABELS}
    q_census = 0.0
    q_survey = 0.0
    variance = 0.0
    frame_ids = sorted({
        f for m in member_ids
        for f in (outcomes[m].probability_frames if m in outcomes else [])
    })
    frame_contrib: list[dict[str, object]] = []

    for student in sorted(member_ids):
        out = outcomes[student]
        detail[out.source] += 1
        label_counts[out.label] += 1
        if out.source == "census" and out.label == QUALIFIED:
            q_census += 1.0

    for frame_id in frame_ids:
        q_hat, var, n_used = _frame_qualified_variance(
            outcomes, survey, frame_id, member_ids)
        q_survey += q_hat
        variance += var
        frame_contrib.append({
            "frame": frame_id,
            "survey_qualified_estimate": round(q_hat, 4),
            "variance": round(var, 4),
            "respondents_used": n_used,
        })
        if n_used == 1:
            frame_contrib[-1]["note"] = "仅 1 个随机答复，抽样方差记 0（低估不确定性）"

    known = detail["census"] + detail["survey"]
    unknown_n = detail["unknown"] + detail["mandatory_only"]
    q_known = q_census + q_survey
    rate = q_known / n if n else None
    se = math.sqrt(variance) / n if n else 0.0
    lo_pi, hi_pi = q_known / n, (q_known + unknown_n) / n
    lo = max(0.0, lo_pi - Z_95 * se) if n else 0.0
    hi = min(1.0, hi_pi + Z_95 * se) if n else 1.0

    notes: list[str] = []
    if unknown_n:
        notes.append("未知去向不记失业；部分识别界假设未知人群达标率在 0~1 之间")
    if detail["mandatory_only"]:
        notes.append("含仅由指定复查接触的学生，其答复不赋推断权重")

    return GroupEstimate(
        level=level, school=school, program=program, area=area, n=n,
        coverage=known / n if n else 0.0, coverage_detail=detail,
        label_counts=label_counts,
        qualified_rate=round(rate, 4) if rate is not None else None,
        interval=[round(lo, 4), round(hi, 4)],
        partial_bounds=[round(lo_pi, 4), round(hi_pi, 4)],
        sampling_se=round(se, 4),
        missing_sources=_missing_reasons(outcomes, sorted(member_ids)),
        uncovered_students=sorted(
            s for s in member_ids if outcomes[s].label == UNKNOWN),
        dispute_candidates=dispute_candidates,
        frame_contributions=frame_contrib, notes=notes)


def build_estimates(roster: Roster,
                    outcomes: dict[str, StudentOutcome],
                    survey: SurveySnapshot) -> dict[str, GroupEstimate]:
    """院校层与院校×专业层，同一版口径一次产出。"""
    confirmed = [r for r in roster.comparable()]
    schools = sorted({r.main_school for r in confirmed})  # type: ignore[arg-type]
    estimates: dict[str, GroupEstimate] = {}

    dispute_by_cell: dict[tuple[str, str], list[str]] = {}
    for impact in roster.disputed_impact:
        for cell in impact["affected_denominators"]:
            key = (cell["school"], cell["program"])
            dispute_by_cell.setdefault(key, []).append(impact["student"])

    for school in schools:
        members = [r for r in confirmed if r.main_school == school]
        area = members[0].area
        candidates = sorted({
            s for (sch, _prog), ids in dispute_by_cell.items()
            if sch == school for s in ids})
        estimates[f"school:{school}"] = estimate_group(
            "school", school, None, area, members, outcomes, survey,
            candidates)
        for program in sorted({r.program for r in members}):
            prog_members = [r for r in members if r.program == program]
            estimates[f"school_program:{school}:{program}"] = estimate_group(
                "school_program", school, program, area, prog_members,
                outcomes, survey,
                sorted(dispute_by_cell.get((school, program), [])))
    return estimates


def compare(a: GroupEstimate, b: GroupEstimate,
            survey: SurveySnapshot) -> dict[str, object]:
    """两个可比较单元的差值比较；区间重合即无法判定先后。"""
    member_frames = {f["frame"] for f in a.frame_contributions}
    shared_frames = sorted(member_frames &
                           {f["frame"] for f in b.frame_contributions})
    if shared_frames:
        # 共用框架时两组抽样不独立，用保守上界（完全相关）处理。
        se_delta = a.sampling_se + b.sampling_se
        covariance_note = f"共用抽样框架 {shared_frames}，差值标准误取保守上界"
    else:
        se_delta = math.sqrt(a.sampling_se ** 2 + b.sampling_se ** 2)
        covariance_note = "抽样框架相互独立"

    point = round((a.qualified_rate or 0) - (b.qualified_rate or 0), 4)
    lo_delta = (a.partial_bounds[0] - b.partial_bounds[1]) - Z_95 * se_delta
    hi_delta = (a.partial_bounds[1] - b.partial_bounds[0]) + Z_95 * se_delta
    overlap = not (a.interval[1] < b.interval[0]
                   or b.interval[1] < a.interval[0])
    if overlap or lo_delta <= 0 <= hi_delta:
        verdict = "indeterminate"
        reason = "区间重合（或差值区间含 0），无法判定先后"
    elif point > 0:
        verdict = "higher"
        reason = f"{a.school} 的达标率区间整体高于 {b.school}"
    else:
        verdict = "lower"
        reason = f"{a.school} 的达标率区间整体低于 {b.school}"

    return {
        "left": _cell_id(a), "right": _cell_id(b),
        "delta_qualified_rate": point,
        "delta_interval": [round(max(lo_delta, -1), 4),
                           round(min(hi_delta, 1), 4)],
        "left_interval": a.interval, "right_interval": b.interval,
        "shared_frames": shared_frames,
        "covariance_note": covariance_note,
        "verdict": verdict, "reason": reason,
    }


def _cell_id(g: GroupEstimate) -> str:
    return f"{g.school}/{g.program}" if g.program else g.school


def all_comparisons(estimates: dict[str, GroupEstimate],
                    survey: SurveySnapshot) -> list[dict[str, object]]:
    """同层级单元两两比较（院校层、同专业的院校×专业层）。"""
    results: list[dict[str, object]] = []
    school_level = [g for g in estimates.values() if g.level == "school"]
    for i, a in enumerate(school_level):
        for b in school_level[i + 1:]:
            results.append(compare(a, b, survey))
    programs = sorted({g.program for g in estimates.values()
                       if g.level == "school_program" and g.program})
    for program in programs:
        cells = [g for g in estimates.values()
                 if g.level == "school_program" and g.program == program]
        for i, a in enumerate(cells):
            for b in cells[i + 1:]:
                results.append(compare(a, b, survey))
    return results
