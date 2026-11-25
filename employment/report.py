"""结果序列化与人工复核视图。

输出目录包含：

- ``snapshot_<asof>.json``：全量机器可读结果；
- ``frozen_rosters.md``：各轮冻结的名单范围与抽中机会留痕；
- ``case_verification.md``：只作个案核实的指定复查联系；
- ``inference_replies.md``：能进入抽样推断的答复及其权重；
- ``uncovered.md``：仍未覆盖的人群、原因与所属框架；
- ``comparison.md``：院校/专业层覆盖、估计区间与比较结论；
- ``changes.md``（存在上一轮快照时）：结论变化归因与退回说明。
"""
from __future__ import annotations

import json
from pathlib import Path

from .changes import ChangeRecord, Rollback
from .planning import FollowUpPlan
from .snapshot import SnapshotResult


def _estimate_dict(result: SnapshotResult):
    return {
        key: {
            "level": g.level, "school": g.school, "program": g.program,
            "area": g.area, "denominator": g.n,
            "coverage": round(g.coverage, 4),
            "coverage_detail": g.coverage_detail,
            "label_counts": g.label_counts,
            "qualified_rate_point": g.qualified_rate,
            "qualified_rate_interval_95": g.interval,
            "partial_identification_bounds": g.partial_bounds,
            "sampling_standard_error": g.sampling_se,
            "missing_sources": g.missing_sources,
            "uncovered_students": g.uncovered_students,
            "dispute_candidates_excluded": g.dispute_candidates,
            "frame_contributions": g.frame_contributions,
            "notes": g.notes,
        }
        for key, g in result.estimates.items()
    }


def snapshot_payload(result: SnapshotResult) -> dict[str, object]:
    return {
        "asof": result.asof.isoformat(),
        "cohort_window": _window_info(result),
        "disputed_main_school": result.roster.disputed_impact,
        "identity": {
            "external_to_student": result.identities.external_to_student,
            "duplicate_codes": result.identities.duplicate_codes,
            "events": result.identities.events,
            "unresolved_evidence": result.identities.unresolved_evidence,
        },
        "students": [
            {
                "student": sid,
                **(cls.as_dict()),
                "source": result.outcomes[sid].source,
                "inference_weight": result.outcomes[sid].weight,
                "main_school": result.roster.students[sid].main_school,
            }
            for sid, cls in sorted(result.classifications.items())
        ],
        "survey": {
            "rounds": result.survey.rounds,
            "round_budgets": result.survey.round_budgets,
            "frames": [
                {
                    "frame": d.frame.frame, "round": d.frame.round,
                    "area": d.frame.area, "stratum": d.frame.stratum,
                    "kind": d.frame.kind,
                    "frozen_codes": d.frame.codes,
                    "dispatched_codes": [v.code for v in d.dispatched],
                    "inclusion_probability": d.probability,
                    "recorded_probability": d.recorded_probability,
                    "probability_consistent": d.probability_consistent,
                    "audit": d.audit,
                    "cost_used": d.cost_used,
                }
                for d in sorted(result.survey.designs.values(),
                                key=lambda x: x.frame.frame)
            ],
        },
        "estimates": _estimate_dict(result),
        "comparisons": result.comparisons,
        "withdrawn_inclusion_pairs": [
            {"student": sid, "frame": frame_id}
            for sid, frame_id in sorted(result.withdrawn_pairs)],
        "audit": result.audit,
    }


def _window_info(result: SnapshotResult) -> dict[str, str]:
    any_rec = next(iter(result.roster.students.values()))
    return {"start": any_rec.window_start.isoformat(),
            "end": any_rec.window_end.isoformat(),
            "rule": "自毕业次日起 90 天（含首尾）"}


def plan_payload(plan: FollowUpPlan,
                 inclusion_table: list[dict[str, object]]) -> dict[str, object]:
    return {
        "round": plan.round, "budget": plan.budget,
        "mandatory_reserved": plan.mandatory_reserved,
        "random_budget": plan.random_budget,
        "allocation_trace": plan.greedy_trace,
        "strata": [
            {
                "frame": p.frame, "stratum": p.stratum, "kind": p.kind,
                "cost_per_contact": p.cost, "capacity": p.capacity,
                "frame_size": p.frame_size,
                "uncovered_in_frame": p.uncovered_in_frame,
                "already_drawn": p.already_drawn,
                "eligible_for_redraw": p.eligible_for_redraw,
                "impact_score": p.impact_score,
                "efficiency_score": p.efficiency_score,
                "priority": p.priority,
                "recommended_draws": p.recommended_draws,
                "budget_used": p.budget_used,
                "planned_probability_note": p.planned_probability_note,
            }
            for p in plan.plans
        ],
        "skipped": plan.skipped,
        "planned_inclusion_table": inclusion_table,
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8")


def render_markdown(result: SnapshotResult,
                    changes: list[ChangeRecord] | None = None,
                    rollbacks: list[Rollback] | None = None) -> dict[str, str]:
    """返回 文件名 -> Markdown 文本 的映射。"""
    views: dict[str, str] = {}
    views["frozen_rosters.md"] = _frozen_rosters(result)
    views["case_verification.md"] = _case_verification(result)
    withdrawn = {(student, frame)
                 for rb in (rollbacks or [])
                 for frame in rb.unusable_frames
                 for student in rb.students}
    views["inference_replies.md"] = _inference_replies(result, withdrawn)
    views["uncovered.md"] = _uncovered(result)
    views["comparison.md"] = _comparison(result)
    if changes is not None:
        views["changes.md"] = _changes(result, changes, rollbacks or [])
    return views


def _frozen_rosters(result: SnapshotResult) -> str:
    lines = ["# 各轮冻结名单范围与抽中机会", ""]
    for d in sorted(result.survey.designs.values(),
                    key=lambda x: (x.frame.round, x.frame.frame)):
        lines.append(f"## {d.frame.frame}（{d.frame.round} / "
                     f"{d.frame.area} / {d.frame.stratum} / {d.frame.kind}）")
        lines.append(f"- 冻结截止：{d.frame.cutoff}")
        lines.append(f"- 名册号码（{len(d.frame.codes)}）："
                     f"{'、'.join(d.frame.codes)}")
        if d.is_probability:
            lines.append(f"- 抽中机会：{len(d.dispatched)}/"
                         f"{len(d.frame.codes)} = {d.probability:.4f}")
            lines.append(f"- 留痕一致性：{'通过' if d.probability_consistent else '不一致'}")
        else:
            lines.append("- 非概率指定复查，无抽中机会，不进入抽样推断")
        lines.append(f"- 已通知：{'、'.join(v.code for v in d.dispatched) or '无'}")
        lines.append(f"- 费用：{d.cost_used} 单位")
        for note in d.audit:
            lines.append(f"- 审计：{note}")
        lines.append("")
    lines.append("## 轮次预算")
    for round_id, info in result.survey.round_budgets.items():
        lines.append(
            f"- {round_id}：预算 {info['budget']}，指定复查 {info['mandatory_cost']}，"
            f"随机抽访 {info['random_cost']}，合计 {info['total_cost']}，"
            f"{'未超支' if info['within_budget'] else '超支'}")
    return "\n".join(lines) + "\n"


def _case_verification(result: SnapshotResult) -> str:
    lines = ["# 只作个案核实的联系（不进入抽样推断）", ""]
    any_row = False
    for d in result.survey.designs.values():
        for v in d.dispatched:
            if v.kind == "random" and v.numer is not None:
                continue
            any_row = True
            lines.append(f"- {v.visit}｜{v.frame}｜{v.code}｜答复：{v.reply or '待回'}"
                         f"｜派单 {v.dispatched}｜回复 {v.replied or '—'}")
    if not any_row:
        lines.append("（无）")
    return "\n".join(lines) + "\n"


def _inference_replies(result: SnapshotResult,
                       withdrawn: set[tuple[str, str]] | None = None) -> str:
    withdrawn = withdrawn or set()
    lines = ["# 可进入抽样推断的答复与权重", "",
             "所有带抽中机会的随机复访都在此留痕（含未接通与重复答复）；",
             "权重为该学生在已发生各轮的合成累积机会倒数，unknown 表示",
             "该答复未产生事实定性，但其抽中机会仍计入覆盖过程。",
             "标“已退回”的机会因身份/主校更正无法映射新分母，不参与推断。", "",
             "| 复访 | 轮次 | 框架 | 学生 | 答复 | 本次机会 | 累积权重 | 归类 | 状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for sid, out in sorted(result.outcomes.items()):
        for visit in result.survey.visits_by_student.get(sid, []):
            if visit.kind != "random" or visit.numer is None:
                continue
            is_withdrawn = (sid, visit.frame) in withdrawn
            lines.append(
                f"| {visit.visit} | {visit.round} | {visit.frame} | {sid} | "
                f"{visit.reply} | {visit.numer}/{visit.denom} | "
                f"{out.weight if out.source == 'survey' else '—'} | "
                f"{out.label} | {'已退回，不参与推断' if is_withdrawn else '有效'} |")
    return "\n".join(lines) + "\n"


def _uncovered(result: SnapshotResult) -> str:
    lines = ["# 仍未覆盖人群（保持未知，不计失业）", "",
             "| 学生 | 院校 | 专业 | 原因 | 所在框架 |",
             "|---|---|---|---|---|"]
    for sid in sorted(result.uncovered_students()):
        out = result.outcomes[sid]
        rec = result.roster.students[sid]
        frames = sorted({v.frame for v in
                         result.survey.visits_by_student.get(sid, [])})
        reason = "；".join(out.reasons) or "无任何有效证据"
        lines.append(f"| {sid} | {rec.main_school or '争议未定'} | "
                     f"{rec.program} | {reason} | {'、'.join(frames) or '未入框'} |")
    lines.append("")
    lines.append("## 缺失来源汇总")
    for key, g in result.estimates.items():
        for item in g.missing_sources:
            lines.append(f"- {key}：{item['reason']}（{item['count']} 人："
                         f"{'、'.join(item['students'])}）")
    return "\n".join(lines) + "\n"


def _comparison(result: SnapshotResult) -> str:
    lines = ["# 院校与专业就业质量对比（同一版口径）", "",
             "## 单元估计", "",
             "| 单元 | 地区 | 分母 | 覆盖率 | 达标率点估 | 95% 区间 | 部分识别界 | 未覆盖 |",
             "|---|---|---|---|---|---|---|---|"]
    for key, g in result.estimates.items():
        cell = f"{g.school}/{g.program}" if g.program else g.school
        lines.append(
            f"| {cell} | {g.area} | {g.n} | {g.coverage:.2%} | "
            f"{g.qualified_rate if g.qualified_rate is not None else '—'} | "
            f"[{g.interval[0]:.4f}, {g.interval[1]:.4f}] | "
            f"[{g.partial_bounds[0]:.4f}, {g.partial_bounds[1]:.4f}] | "
            f"{len(g.uncovered_students)} 人 |")
    lines.extend(["", "## 两两比较", "",
                  "| 左 | 右 | 达标率差 | 差值区间 | 结论 | 依据 |",
                  "|---|---|---|---|---|---|"])
    verdict_text = {"indeterminate": "无法判定先后", "higher": "左高于右",
                    "lower": "左低于右"}
    for c in result.comparisons:
        lines.append(
            f"| {c['left']} | {c['right']} | {c['delta_qualified_rate']} | "
            f"[{c['delta_interval'][0]:.4f}, {c['delta_interval'][1]:.4f}] | "
            f"{verdict_text[c['verdict']]} | {c['reason']}；{c['covariance_note']} |")
    lines.extend(["", "## 被排除的争议分母"])
    if result.roster.disputed_impact:
        for item in result.roster.disputed_impact:
            cells = "、".join(f"{d['school']}/{d['program']}"
                              for d in item["affected_denominators"])
            lines.append(f"- {item['student']}（{item['program']}）："
                         f"候选主校 {item['candidate_schools']}，受影响分母 {cells}")
    else:
        lines.append("- （无）")
    return "\n".join(lines) + "\n"


def _changes(result: SnapshotResult, changes: list[ChangeRecord],
             rollbacks: list[Rollback]) -> str:
    reason_text = {
        "data_completion": "数据补齐", "identity_review": "身份/主校复核",
        "caliber_review": "口径复核", "coverage_review": "抽样覆盖判断变化"}
    lines = ["# 跨轮结论变化归因", "",
             "| 学生 | 上轮标签 | 本轮标签 | 变化原因 |",
             "|---|---|---|---|"]
    for c in changes:
        lines.append(f"| {c.student} | {c.previous_label} | "
                     f"{c.current_label} | "
                     f"{'、'.join(reason_text.get(r, r) for r in c.reasons)} |")
    lines.extend(["", "## 抽样机会退回说明"])
    if rollbacks:
        for rb in rollbacks:
            lines.append(f"- {rb.comparison}：{rb.reason}")
            lines.append(f"  - 不可继续使用：{rb.unusable_frames}")
            lines.append(f"  - 可继续使用：{rb.usable_part}")
            lines.append(f"  - 下一轮依据：{rb.next_round_basis}")
    else:
        lines.append("- 无因身份更正导致的抽样机会退回")
    return "\n".join(lines) + "\n"
