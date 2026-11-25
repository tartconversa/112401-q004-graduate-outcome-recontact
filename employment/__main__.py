"""按届处理的命令行入口。

示例：
    python3 -m employment run --data samples --asof 2026-09-27 \
        --prev-asof 2026-09-22 --out out
    python3 -m employment plan --data samples --asof 2026-09-27 \
        --round R2 --key-schools A --out out
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .changes import (diff_snapshots, evaluate_rollbacks,
                      mark_comparison_status)
from .load import load_cohort
from .planning import EXCLUSION, plan_followup, planned_inclusion_table
from .report import (plan_payload, render_markdown, snapshot_payload,
                     write_json)
from .snapshot import build_snapshot
from .survey import ROUND2_DESIGNS, cumulative_inclusion


def _parse_day(text: str) -> date:
    return date.fromisoformat(text)


def _frame_area_map(inputs, snapshot):
    return {
        d.frame.frame: {"area": d.frame.area, "stratum": d.frame.stratum}
        for d in snapshot.survey.designs.values()
    }


def cmd_run(args: argparse.Namespace) -> None:
    inputs = load_cohort(args.data)
    current = build_snapshot(inputs, args.asof)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    changes = None
    rollbacks = []
    if args.prev_asof:
        previous = build_snapshot(inputs, args.prev_asof)
        changes = diff_snapshots(previous.to_bundle(), current.to_bundle())
        rollbacks = evaluate_rollbacks(
            current.to_bundle(), changes, _frame_area_map(inputs, current),
            withdrawn_pairs=current.withdrawn_pairs)
        status = mark_comparison_status(current.to_bundle(), rollbacks)
        # 把退回状态写回当前比较，保证报告与 JSON 一致。
        for comp, marked in zip(current.comparisons, status):
            comp["rollback"] = marked["rollback"]

    payload = snapshot_payload(current)
    if changes is not None:
        payload["change_attribution"] = [
            {"student": c.student, "previous_label": c.previous_label,
             "current_label": c.current_label, "reasons": c.reasons,
             "detail": c.detail}
            for c in changes]
        payload["rollbacks"] = [
            {"comparison": rb.comparison, "students": rb.students,
             "reason": rb.reason, "unusable_frames": rb.unusable_frames,
             "usable_part": rb.usable_part,
             "next_round_basis": rb.next_round_basis}
            for rb in rollbacks]

    write_json(out_dir / f"snapshot_{args.asof.isoformat()}.json", payload)
    views = render_markdown(current, changes, rollbacks)
    for filename, text in views.items():
        (out_dir / filename).write_text(text, encoding="utf-8")

    print(f"截至 {args.asof} 的届分析已写入 {out_dir}")
    for key, g in current.estimates.items():
        cell = f"{g.school}/{g.program}" if g.program else g.school
        print(f"  {cell}: 分母 {g.n}，覆盖 {g.coverage:.0%}，"
              f"达标率区间 {g.interval}")
    if rollbacks:
        print(f"  抽样机会退回 {len(rollbacks)} 项，详见 changes.md")


def cmd_plan(args: argparse.Namespace) -> None:
    inputs = load_cohort(args.data)
    current = build_snapshot(inputs, args.asof)
    key = {s.strip() for s in args.key_schools.split(",") if s.strip()}
    plan = plan_followup(
        inputs, args.round, current.roster, current.survey,
        current.estimates, current.uncovered_students(),
        key_schools=key, r2_design=args.r2_design)
    table = planned_inclusion_table(plan, current.survey,
                                    current.roster, args.r2_design)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = plan_payload(plan, table)
    write_json(out_dir / f"plan_{args.round}_{args.asof.isoformat()}.json",
               payload)

    lines = [f"# {args.round} 抽访计划（截至 {args.asof}）", "",
             f"- 总预算 {plan.budget}，指定复查预留 {plan.mandatory_reserved}，"
             f"随机抽访可用 {plan.random_budget}", ""]
    for p in plan.plans:
        lines.append(f"## {p.frame} / {p.stratum}（{p.kind}）")
        lines.append(f"- 未覆盖 {len(p.uncovered_in_frame)} 人："
                     f"{'、'.join(p.uncovered_in_frame) or '无'}")
        lines.append(f"- 影响分 {p.impact_score}，效率分 {p.efficiency_score}，"
                     f"优先级 {p.priority}")
        lines.append(f"- 建议实拨 {p.recommended_draws} 次，费用 {p.budget_used}")
        if p.kind == "random":
            lines.append(f"- {p.planned_probability_note}")
        lines.append("")
    lines.append("## 分配过程")
    lines.extend(f"- {t}" for t in plan.greedy_trace)
    (out_dir / f"plan_{args.round}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{args.round} 抽访计划已写入 {out_dir}")


def cmd_coverage(args: argparse.Namespace) -> None:
    """解释单个学生到指定轮次的真实抽中过程（跨轮不平均）。"""
    inputs = load_cohort(args.data)
    current = build_snapshot(inputs, args.asof)
    draws = {}
    if args.r2_draws:
        for item in args.r2_draws.split(","):
            frame, n = item.split("=")
            draws[frame.strip()] = int(n)
    info = cumulative_inclusion(
        args.student, current.survey, args.through_round,
        r2_design=args.r2_design, r2_draws=draws)
    for step in info["steps"]:
        print(f"{step['round']} {step.get('frame', '')}: "
              f"条件机会 {step['conditional_probability']:.4f} "
              f"— {step.get('note') or step.get('formula', '')}")
    print(f"累积抽中概率 {info['cumulative_inclusion_probability']:.4f}；"
          f"从未抽中概率 {info['never_selected_probability']:.4f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="employment",
        description="毕业九十天就业质量对比与复访抽样推断（按届处理）")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="按截至日生成一版完整届分析")
    run.add_argument("--data", required=True, help="一届资料所在目录")
    run.add_argument("--asof", required=True, type=_parse_day,
                     help="快照截至日 YYYY-MM-DD")
    run.add_argument("--prev-asof", type=_parse_day, default=None,
                     help="上一轮快照截至日，提供时输出变化归因")
    run.add_argument("--out", default="out", help="输出目录")
    run.set_defaults(func=cmd_run)

    plan = sub.add_parser("plan", help="生成下一轮抽访经费分配计划")
    plan.add_argument("--data", required=True)
    plan.add_argument("--asof", required=True, type=_parse_day)
    plan.add_argument("--round", required=True, help="如 R2")
    plan.add_argument("--key-schools", default="",
                      help="逗号分隔的关键院校，留空表示同等关键")
    plan.add_argument("--r2-design", choices=ROUND2_DESIGNS,
                      default=EXCLUSION)
    plan.add_argument("--out", default="out")
    plan.set_defaults(func=cmd_plan)

    cov = sub.add_parser("coverage", help="解释单个学生的跨轮抽中过程")
    cov.add_argument("--data", required=True)
    cov.add_argument("--asof", required=True, type=_parse_day)
    cov.add_argument("--student", required=True)
    cov.add_argument("--through-round", required=True)
    cov.add_argument("--r2-design", choices=ROUND2_DESIGNS,
                     default=EXCLUSION)
    cov.add_argument("--r2-draws", default="",
                     help="如 F4=2,F5=4 的计划实拨数")
    cov.set_defaults(func=cmd_coverage)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
