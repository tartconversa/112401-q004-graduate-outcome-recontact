"""复访框架、抽中机会与跨轮覆盖。

设计事实（README/frames.csv）：随机拨号在线下按冻结分层名册做无放回
等概率抽取，因此一次随机框架内每个号码的抽中机会为
``实拨数 / 框架号码数``，visits.csv 的 numer/denom 是该机会的留痕；
空白表示非概率的指定复查（mandatory），**只能做个案核实，不能进入
抽样推断**。

跨轮规则：R2 的随机框是在已知 R1 回执后形成的。若某人在 R2 的抽中机会
取决于上一轮是否被抽中或是否答复，最终覆盖必须按真实的条件过程合成，
而不是把各轮结果直接平均。本模块提供两种可解释的过程合成：

- ``independent``：R2 在与 R1 相同的名册上独立重抽；
- ``exclusion``：R2 只在 R1 未抽中（或未答复）的余量中抽取。

每条进入推断的答复都带可追溯的抽中机会与权重（机会倒数）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .classify import VisitFact
from .load import CohortInputs, FrameRow, VisitRow

INDEPENDENT = "independent"
EXCLUSION = "exclusion"
ROUND2_DESIGNS = (INDEPENDENT, EXCLUSION)


@dataclass
class FrameDesign:
    frame: FrameRow
    dispatched: list[VisitRow]
    replies: list[VisitRow]
    probability: float | None       # mandatory 为 None
    recorded_probability: float | None
    probability_consistent: bool
    audit: list[str] = field(default_factory=list)
    cost_used: int = 0

    @property
    def is_probability(self) -> bool:
        return self.frame.kind == "random"


@dataclass
class SurveySnapshot:
    asof: date
    rounds: list[str]
    designs: dict[str, FrameDesign]
    visits_by_student: dict[str, list[VisitRow]]
    audit: list[dict[str, object]] = field(default_factory=list)
    round_budgets: dict[str, dict[str, object]] = field(default_factory=dict)

    def probability_visits(self, student: str) -> list[VisitRow]:
        """该学生所有可追溯抽中机会的随机复访记录。"""
        return [v for v in self.visits_by_student.get(student, [])
                if v.kind == "random" and v.numer is not None]

    def visit_facts(self, student: str) -> list[VisitFact]:
        facts = []
        for v in self.visits_by_student.get(student, []):
            facts.append(VisitFact(
                visit=v.visit, round=v.round, frame=v.frame, kind=v.kind,
                reply=v.reply, replied=v.replied,
                is_probability=v.kind == "random" and v.numer is not None,
                numer=v.numer, denom=v.denom))
        return facts


# 各轮预算与先扣指定复查费用的演练约定（README frames.csv 说明）。
ROUND_BUDGETS = {"R1": 8, "R2": 9}


def build_survey(inputs: CohortInputs, asof: date) -> SurveySnapshot:
    """复现截至 asof 已冻结框架与已通知复访，并核对抽中机会留痕。"""
    designs: dict[str, FrameDesign] = {}
    visits_by_student: dict[str, list[VisitRow]] = {}
    audit: list[dict[str, object]] = []

    rounds = sorted({f.round for f in inputs.frames
                     if f.cutoff <= asof})
    for frame in inputs.frames:
        if frame.cutoff > asof:
            continue
        dispatched = sorted(
            (v for v in inputs.visits
             if v.frame == frame.frame and v.dispatched <= asof),
            key=lambda v: v.visit)
        replies = [v for v in dispatched if v.replied is not None
                   and v.replied <= asof]
        design = FrameDesign(
            frame=frame, dispatched=dispatched, replies=replies,
            probability=None, recorded_probability=None,
            probability_consistent=True)

        if frame.kind == "random":
            n_frame = len(frame.codes)
            n_draw = len(dispatched)
            design.probability = (n_draw / n_frame) if n_frame else None
            for v in dispatched:
                if v.numer is None or v.denom is None:
                    design.probability_consistent = False
                    design.audit.append(
                        f"{v.visit}: 随机复访缺少抽中机会留痕，不能进入推断")
                    audit.append({"visit": v.visit, "frame": frame.frame,
                                  "issue": "missing_inclusion_probability"})
                    continue
                recorded = v.numer / v.denom
                if design.recorded_probability is None:
                    design.recorded_probability = recorded
                if v.denom != n_frame or v.numer != n_draw \
                        or abs(recorded - (design.probability or 0)) > 1e-12:
                    design.probability_consistent = False
                    design.audit.append(
                        f"{v.visit}: 留痕机会 {v.numer}/{v.denom} 与框架事实 "
                        f"{n_draw}/{n_frame} 不一致")
                    audit.append({"visit": v.visit, "frame": frame.frame,
                                  "issue": "inclusion_probability_mismatch",
                                  "recorded": [v.numer, v.denom],
                                  "frame_fact": [n_draw, n_frame]})
        else:
            for v in dispatched:
                if v.numer is not None:
                    design.audit.append(
                        f"{v.visit}: 指定复查却填写了抽中机会，按非概率处理")
                    audit.append({"visit": v.visit, "frame": frame.frame,
                                  "issue": "mandatory_with_probability"})
            design.audit.append(
                f"{frame.frame}: 指定复查层，仅供个案核实，不进入抽样推断")
        design.cost_used = len(dispatched) * frame.cost
        designs[frame.frame] = design

        for v in dispatched:
            visits_by_student.setdefault(v.code, []).append(v)

    budgets = {}
    for round_id in rounds:
        round_frames = [d for d in designs.values()
                        if d.frame.round == round_id]
        mandatory_cost = sum(d.cost_used for d in round_frames
                             if not d.is_probability)
        random_cost = sum(d.cost_used for d in round_frames
                          if d.is_probability)
        budget = ROUND_BUDGETS.get(round_id)
        budgets[round_id] = {
            "budget": budget,
            "mandatory_cost": mandatory_cost,
            "random_cost": random_cost,
            "total_cost": mandatory_cost + random_cost,
            "within_budget": budget is None
            or mandatory_cost + random_cost <= budget,
            "note": "先扣当轮指定复查费用，余额用于随机抽访",
        }

    return SurveySnapshot(
        asof=asof, rounds=rounds, designs=designs,
        visits_by_student=visits_by_student, audit=audit,
        round_budgets=budgets)


def previously_selected(survey: SurveySnapshot,
                        before_round: str) -> set[str]:
    """在 before_round 之前各轮已被随机抽中（已通知）的学生。"""
    return {
        v.code for d in survey.designs.values()
        if d.is_probability and d.frame.round < before_round
        for v in d.dispatched
    }


def cumulative_inclusion(student: str, survey: SurveySnapshot,
                         round_id: str,
                         r2_design: str = EXCLUSION,
                         r2_draws: dict[str, int] | None = None
                         ) -> dict[str, object]:
    """解释一个学生到指定轮次为止的真实抽中过程与最终覆盖。

    返回每轮的条件机会、合成公式与累积抽中概率。r2_draws 给出未来轮次
    各框架计划实拨数（派单后可用实际数替换）；排除式设计下，上一轮
    任意随机框架已抽中的学生本轮条件机会为 0，合格余量按全框架名册
    跨框架扣除，而不是只看同一框架。
    """
    r2_draws = r2_draws or {}
    steps: list[dict[str, object]] = []
    cumulative = 0.0

    for rnd in sorted(set(survey.rounds) | {round_id}):
        if rnd > round_id:
            break
        prior_selected = previously_selected(survey, rnd)
        for design in sorted(survey.designs.values(),
                             key=lambda d: d.frame.frame):
            if design.frame.round != rnd or not design.is_probability:
                continue
            if student not in design.frame.codes:
                continue
            visit = next((v for v in design.dispatched
                          if v.code == student), None)
            planned = r2_draws.get(design.frame.frame)

            if visit is not None:
                p = design.probability or 0.0
                steps.append({
                    "round": rnd, "frame": design.frame.frame,
                    "conditional_probability": p,
                    "note": (f"已通知复访，抽中机会 "
                             f"{len(design.dispatched)}/"
                             f"{len(design.frame.codes)}")})
            elif planned is not None:
                n_frame = len(design.frame.codes)
                if r2_design == EXCLUSION:
                    if student in prior_selected:
                        cond = 0.0
                        formula = "上一轮已被随机抽中，本轮条件机会为 0"
                    else:
                        eligible = max(
                            n_frame - sum(1 for c in design.frame.codes
                                          if c in prior_selected), 1)
                        cond = planned / eligible
                        formula = (f"P({rnd})= {planned}/{eligible}，"
                                   "仅在上一轮未抽中时成立")
                else:
                    cond = planned / n_frame if n_frame else 0.0
                    formula = (f"P({rnd})= {planned}/{n_frame}，"
                               "与上一轮独立重抽")
                p = cond
                steps.append({"round": rnd, "frame": design.frame.frame,
                              "conditional_probability": cond,
                              "formula": formula})
            else:
                p = 0.0
                steps.append({"round": rnd, "frame": design.frame.frame,
                              "conditional_probability": 0.0,
                              "note": "该轮框架内未被抽中且无派单计划"})
            # 条件过程合成：1 - 各轮（1 - 条件机会）连乘，不做轮次平均。
            cumulative = 1 - (1 - cumulative) * (1 - p)

    return {
        "student": student,
        "through_round": round_id,
        "r2_design": r2_design,
        "steps": steps,
        "cumulative_inclusion_probability": cumulative,
        "never_selected_probability": max(1 - cumulative, 0.0),
    }
