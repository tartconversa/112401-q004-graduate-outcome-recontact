"""抽访经费分配与派单计划。

学校先处理必须复核的矛盾记录（mandatory 层，费用先扣），剩余经费按
群组的成本、未覆盖人数与对关键院校比较的影响分配。每个进入推断的
群组都必须挂在一个冻结随机框架上，派单时即可写出每人的抽中机会，
保证后续推断的可追溯性。

优先级（0~1 标准化后等权合成）：
- 影响分：该群组服务的关键比较中，当前最宽的差值不确定度；
- 效率分：每单位话务额度能覆盖的未覆盖人数。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .estimate import GroupEstimate
from .load import CohortInputs, FrameRow
from .roster import Roster
from .survey import (EXCLUSION, ROUND_BUDGETS, SurveySnapshot,
                     cumulative_inclusion, previously_selected)


@dataclass
class StratumPlan:
    frame: str
    round: str
    area: str
    stratum: str
    kind: str
    cost: int
    capacity: int
    frame_size: int
    uncovered_in_frame: list[str]
    already_drawn: list[str]
    eligible_for_redraw: list[str]
    impact_score: float
    efficiency_score: float
    priority: float
    recommended_draws: int = 0
    planned_probability_note: str = ""
    budget_used: int = 0


@dataclass
class FollowUpPlan:
    round: str
    budget: int
    mandatory_reserved: int
    random_budget: int
    plans: list[StratumPlan]
    greedy_trace: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def _impact_for_frame(frame: FrameRow, roster: Roster,
                      estimates: dict[str, GroupEstimate],
                      key_schools: set[str]) -> float:
    """框架覆盖的学生所属院校中，关键院校当前最宽的区间宽度。"""
    widths: list[float] = []
    touched_schools = set()
    for code in frame.codes:
        rec = roster.students.get(code)
        if rec and rec.main_school:
            touched_schools.add(rec.main_school)
    for school in touched_schools:
        cell = estimates.get(f"school:{school}")
        if cell is None:
            continue
        if not key_schools or school in key_schools:
            widths.append(cell.interval[1] - cell.interval[0])
    return max(widths, default=0.0)


def plan_followup(inputs: CohortInputs, round_id: str,
                  roster: Roster, survey: SurveySnapshot,
                  estimates: dict[str, GroupEstimate],
                  uncovered: set[str],
                  key_schools: set[str] | None = None,
                  r2_design: str = EXCLUSION) -> FollowUpPlan:
    """生成下一轮派单计划。

    uncovered：截至当前快照仍未覆盖（unknown）的学生集合；
    key_schools：优先缩小不确定性的院校，空集表示全部同等关键。
    """
    key_schools = key_schools or set()
    frames = [f for f in inputs.frames if f.round == round_id]
    budget = ROUND_BUDGETS[round_id]
    mandatory = [f for f in frames if f.kind != "random"]
    # 指定复查先扣：按容量上限预留费用。
    mandatory_reserved = sum(f.capacity * f.cost for f in mandatory)
    random_budget = budget - mandatory_reserved
    # 排除式设计：上一轮任何随机框架已抽中的人本轮不再有条件机会。
    prior_selected = previously_selected(survey, round_id)

    plans: list[StratumPlan] = []
    for frame in frames:
        prior_design = survey.designs.get(frame.frame)
        prior_draws = ([v.code for v in prior_design.dispatched]
                       if prior_design else [])
        uncovered_here = [c for c in frame.codes if c in uncovered]
        eligible = [c for c in frame.codes if c not in prior_selected]
        plans.append(StratumPlan(
            frame=frame.frame, round=frame.round, area=frame.area,
            stratum=frame.stratum, kind=frame.kind, cost=frame.cost,
            capacity=frame.capacity, frame_size=len(frame.codes),
            uncovered_in_frame=sorted(uncovered_here),
            already_drawn=sorted(prior_draws),
            eligible_for_redraw=sorted(eligible),
            impact_score=_impact_for_frame(frame, roster, estimates,
                                           key_schools),
            efficiency_score=0.0, priority=0.0))

    random_plans = [p for p in plans if p.kind == "random"]
    if random_plans:
        max_impact = max((p.impact_score for p in random_plans), default=1) or 1
        max_eff = max((len(p.uncovered_in_frame) / p.cost
                       for p in random_plans), default=1) or 1
        for p in random_plans:
            p.impact_score = round(p.impact_score / max_impact, 4)
            p.efficiency_score = round(
                (len(p.uncovered_in_frame) / p.cost) / max_eff, 4)
            p.priority = round(
                0.5 * p.impact_score + 0.5 * p.efficiency_score, 4)

    # 贪心：每单位预算投给当前优先级最高且仍有容量/可联系余量的框架。
    remaining = random_budget
    trace: list[str] = []
    ordered = sorted(random_plans, key=lambda p: (-p.priority, p.frame))
    while remaining > 0:
        candidates = [
            p for p in ordered
            if p.recommended_draws < p.capacity
            and p.cost <= remaining
            and p.recommended_draws < len(p.eligible_for_redraw)]
        if not candidates:
            trace.append(
                f"余额 {remaining}：各群组或达容量、或已无未覆盖的合格联系人，"
                "停止（余额不强行摊派，避免对已覆盖者重复联系）")
            break
        pick = candidates[0]
        pick.recommended_draws += 1
        pick.budget_used += pick.cost
        remaining -= pick.cost
        trace.append(
            f"向 {pick.frame}（{pick.stratum}，优先级 {pick.priority}）"
            f"拨 1 次，累计 {pick.recommended_draws}/{pick.capacity}")

    skipped: list[dict[str, str]] = []
    for p in random_plans:
        if p.recommended_draws == 0:
            skipped.append({"frame": p.frame,
                            "reason": "经费或容量约束下未入选"})
        eligible_n = max(len(p.eligible_for_redraw), 1)
        if r2_design == EXCLUSION:
            p.planned_probability_note = (
                f"条件机会 = {p.recommended_draws}/{eligible_n}，"
                "仅对上一轮未抽中者成立；最终覆盖按条件过程合成")
        else:
            p.planned_probability_note = (
                f"独立重抽机会 = {p.recommended_draws}/{p.frame_size}，"
                "与上一轮独立")

    return FollowUpPlan(
        round=round_id, budget=budget,
        mandatory_reserved=mandatory_reserved,
        random_budget=random_budget, plans=plans, greedy_trace=trace,
        skipped=skipped)


def planned_inclusion_table(plan: FollowUpPlan, survey: SurveySnapshot,
                            roster: Roster,
                            r2_design: str = EXCLUSION) -> list[dict[str, object]]:
    """把计划落到每个未覆盖学生：写出到该轮为止的累积抽中机会轨迹。"""
    draws = {p.frame: p.recommended_draws for p in plan.plans
             if p.kind == "random"}
    table: list[dict[str, object]] = []
    seen: set[str] = set()
    # 直接遍历框架名册，保证每个进入推断的号码都有轨迹。
    for p in plan.plans:
        if p.kind != "random":
            continue
        design = survey.designs.get(p.frame)
        codes = design.frame.codes if design is not None else []
        for student in codes:
            seen.add(student)
            table.append(cumulative_inclusion(
                student, survey, plan.round, r2_design=r2_design,
                r2_draws=draws))
    return table
