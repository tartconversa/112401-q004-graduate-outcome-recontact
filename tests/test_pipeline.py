"""端到端口径测试：用 samples 里的演练资料复现关键场景。"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from employment.changes import diff_snapshots, evaluate_rollbacks
from employment.classify import (BELOW, NOT_WORKING, QUALIFIED, STUDYING,
                                 UNKNOWN)
from employment.load import load_cohort
from employment.planning import plan_followup, planned_inclusion_table
from employment.snapshot import build_snapshot
from employment.survey import EXCLUSION, cumulative_inclusion

DATA = Path(__file__).resolve().parent.parent / "samples"
R1_FREEZE = date(2026, 9, 22)
AFTER_R1 = date(2026, 9, 27)


class SnapshotTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = load_cohort(DATA)
        cls.r1 = build_snapshot(cls.inputs, R1_FREEZE)
        cls.curr = build_snapshot(cls.inputs, AFTER_R1)


class RosterAndWindowTests(SnapshotTestBase):
    def test_window_is_ninety_days_from_day_after_graduation(self):
        rec = self.curr.roster.students["G01"]
        self.assertEqual(rec.window_start, date(2026, 6, 21))
        self.assertEqual(rec.window_end, date(2026, 9, 18))
        self.assertEqual((rec.window_end - rec.window_start).days + 1, 90)

    def test_disputed_student_excluded_from_denominator_at_r1(self):
        self.assertIn("G03", self.r1.roster.disputed)
        self.assertIsNone(self.r1.roster.students["G03"].main_school)
        impact = next(x for x in self.r1.roster.disputed_impact
                      if x["student"] == "G03")
        self.assertEqual(
            sorted(d["school"] for d in impact["affected_denominators"]),
            ["A", "B"])

    def test_version_two_confirms_main_school_b(self):
        rec = self.curr.roster.students["G03"]
        self.assertEqual(rec.main_school, "B")
        self.assertEqual(rec.area, "outer")
        self.assertNotIn("G03", self.curr.roster.disputed)
        self.assertEqual(rec.version, 2)


class IdentityTests(SnapshotTestBase):
    def test_duplicate_code_reassigned_after_dispatch(self):
        # 09-22 时 S-A51 仍指向 G05
        self.assertEqual(
            self.r1.identities.external_to_student["S-A51"], "G05")
        # 09-25 结论生效后指向 G03，并标记为重复代号
        self.assertEqual(
            self.curr.identities.external_to_student["S-A51"], "G03")
        self.assertEqual(self.curr.identities.duplicate_codes["S-A51"], "G03")
        event_types = {e["type"] for e in self.curr.identities.events}
        self.assertIn("code_reassigned", event_types)

    def test_g05_evidence_reattributed_to_sa52(self):
        g05 = self.curr.identities.student_evidence.get("G05", [])
        self.assertEqual([e.evidence for e in g05], ["E5"])
        g03 = self.curr.identities.student_evidence.get("G03", [])
        self.assertIn("E3", [e.evidence for e in g03])

    def test_employer_receipt_between_dispatch_and_reply_is_kept(self):
        # E5 received 09-24，晚于 R1 派单 09-23、早于 V2 回复 09-24；
        # 当前快照必须保留它，且 V2 这条已通知工作在视图中不隐去。
        used = self.curr.outcomes["G05"].classification.used_evidence
        self.assertIn("E5", used)
        visits = [v.visit for v in
                  self.curr.survey.visits_by_student.get("G05", [])]
        self.assertIn("V2", visits)


class ClassificationTests(SnapshotTestBase):
    def label(self, snapshot, student):
        return snapshot.classifications[student].label

    def test_qualified_requires_duration_hours_and_income(self):
        # G01：30h/3100，07-01..09-18 共 80 天 ≥60，metro 达标
        self.assertEqual(self.label(self.curr, "G01"), QUALIFIED)
        # G05：E5 窗内 06-30 裁到 06-21..09-18，40h/4900，outer 达标
        self.assertEqual(self.label(self.curr, "G05"), QUALIFIED)
        g05 = self.curr.classifications["G05"]
        self.assertIn("E5", g05.clipped_evidence)
        self.assertTrue(g05.qualifying_segments)

    def test_short_or_subthreshold_employment_is_below_standard(self):
        # G03（E3）：16h/1400 双低于 outer 门槛
        self.assertEqual(self.label(self.curr, "G03"), BELOW)
        # E2 在 09-22 已到，归属 G03：36 天 <60，同样不能达标
        g03 = self.curr.classifications["G03"]
        self.assertFalse(g03.qualifying_segments)

    def test_study_alone_is_studying_and_unknown_is_not_unemployment(self):
        # G07 只有读书证据 E4
        self.assertEqual(self.label(self.curr, "G07"), STUDYING)
        # G02 无任何证据
        self.assertEqual(self.label(self.curr, "G02"), UNKNOWN)
        # G10 本人明确答复无工作
        self.assertEqual(self.label(self.curr, "G10"), NOT_WORKING)

    def test_unreachable_remains_unknown_with_reason(self):
        g03 = self.curr.classifications["G03"]
        reasons = " ".join(g03.unknown_reasons)
        # G03 的 V1 unreachable 不应把它变成 not_working
        self.assertNotEqual(g03.label, NOT_WORKING)
        self.assertIn("联系不上", reasons)

    def test_conflicting_evidence_yields_single_exclusive_label(self):
        g05 = self.curr.classifications["G05"]
        # E5 书面就业 vs V2 口头 already-replied：只出一个标签
        self.assertEqual(g05.label, QUALIFIED)
        self.assertEqual(len({g05.label}), 1)
        # 所有学生在同一版口径下都只有一个互斥标签
        for cls in self.curr.classifications.values():
            self.assertIn(cls.label,
                          {QUALIFIED, BELOW, STUDYING, NOT_WORKING, UNKNOWN})


class SurveyTests(SnapshotTestBase):
    def test_inclusion_probability_matches_frozen_frame(self):
        f1 = self.curr.survey.designs["F1"]
        self.assertEqual(f1.probability, 2 / 3)
        self.assertTrue(f1.probability_consistent)
        f2 = self.curr.survey.designs["F2"]
        self.assertEqual(f2.probability, 3 / 7)

    def test_mandatory_frame_has_no_probability_and_stays_out_of_inference(self):
        f3 = self.curr.survey.designs["F3"]
        self.assertFalse(f3.is_probability)
        self.assertIsNone(f3.probability)
        g03 = self.curr.outcomes["G03"]
        # G03 的事实来自书面证据；V5 指定复查不得赋予推断权重
        self.assertNotIn("F3", g03.probability_frames)

    def test_r1_budget_mandatory_first_then_random(self):
        info = self.curr.survey.round_budgets["R1"]
        self.assertEqual(info["mandatory_cost"], 1)
        self.assertEqual(info["random_cost"], 7)
        self.assertEqual(info["total_cost"], 8)
        self.assertTrue(info["within_budget"])

    def test_cumulative_inclusion_conditional_not_averaged(self):
        # R2 排除式：G12 在 R1 已抽中，R2 条件机会应为 0
        info = cumulative_inclusion(
            "G12", self.curr.survey, "R2", r2_design=EXCLUSION,
            r2_draws={"F4": 2})
        r2_step = next(s for s in info["steps"] if s["round"] == "R2")
        self.assertEqual(r2_step["conditional_probability"], 0.0)
        # G04 未在 R1 出现，F4 余量 3-2=1... 实际 F4 名册 G02/G04/G12，
        # R1 F1 抽过 G12，F4 合格余量 2，机会 2/2=1
        info_g04 = cumulative_inclusion(
            "G04", self.curr.survey, "R2", r2_design=EXCLUSION,
            r2_draws={"F4": 2})
        step_g04 = next(s for s in info_g04["steps"] if s["round"] == "R2")
        self.assertEqual(step_g04["conditional_probability"], 1.0)


class EstimateTests(SnapshotTestBase):
    def test_denominators_only_confirmed_students(self):
        a = self.curr.estimates["school:A"]
        self.assertEqual(a.n, 4)   # G01 G02 G04 G12，G03 已归 B
        b = self.curr.estimates["school:B"]
        self.assertEqual(b.n, 5)
        c = self.curr.estimates["school:C"]
        self.assertEqual(c.n, 3)

    def test_coverage_and_unknown_kept_separate(self):
        a = self.curr.estimates["school:A"]
        # 仅 G01 有书面事实；G03 已不在 A
        self.assertEqual(a.coverage_detail["census"], 1)
        self.assertEqual(a.coverage_detail["unknown"], 3)
        self.assertIn("G02", a.uncovered_students)
        # 未知没有被计入 not_working
        self.assertEqual(a.label_counts[NOT_WORKING], 0)

    def test_interval_contains_partial_identification_and_se(self):
        a = self.curr.estimates["school:A"]
        lo_pi, hi_pi = a.partial_bounds
        self.assertEqual(lo_pi, 0.25)       # 1/4
        self.assertEqual(hi_pi, 1.0)        # 未知可能全部达标
        self.assertLessEqual(a.interval[0], lo_pi)
        self.assertGreaterEqual(a.interval[1], hi_pi)

    def test_school_and_program_use_same_caliber(self):
        school = self.curr.estimates["school:B"]
        program = self.curr.estimates["school_program:B:automation"]
        self.assertEqual(school.area, program.area)
        # 专业层是院校层的同口径拆分，分母之和等于院校分母
        progs = [g for g in self.curr.estimates.values()
                 if g.level == "school_program" and g.school == "B"]
        self.assertEqual(sum(g.n for g in progs), school.n)

    def test_overlapping_intervals_are_indeterminate(self):
        for comp in self.curr.comparisons:
            self.assertEqual(comp["verdict"], "indeterminate")
            self.assertIn("无法判定", comp["reason"])

    def test_dispute_candidates_listed_on_cells(self):
        r1_a = self.r1.estimates["school:A"]
        self.assertIn("G03", r1_a.dispute_candidates)
        r1_b = self.r1.estimates["school:B"]
        self.assertIn("G03", r1_b.dispute_candidates)


class PlanningTests(SnapshotTestBase):
    def test_plan_reserves_mandatory_and_allocates_by_priority(self):
        plan = plan_followup(
            self.inputs, "R2", self.curr.roster, self.curr.survey,
            self.curr.estimates, self.curr.uncovered_students(),
            key_schools={"A"})
        self.assertEqual(plan.mandatory_reserved, 0)
        self.assertEqual(plan.random_budget, 9)
        by_frame = {p.frame: p for p in plan.plans}
        self.assertLessEqual(by_frame["F4"].recommended_draws,
                             by_frame["F4"].capacity)
        self.assertLessEqual(by_frame["F5"].recommended_draws,
                             by_frame["F5"].capacity)
        total = sum(p.budget_used for p in plan.plans)
        self.assertLessEqual(total, 9)
        # 关键院校 A 的 F4 必须拿到至少一次联系
        self.assertGreaterEqual(by_frame["F4"].recommended_draws, 1)

    def test_every_frame_member_gets_traceable_inclusion(self):
        plan = plan_followup(
            self.inputs, "R2", self.curr.roster, self.curr.survey,
            self.curr.estimates, self.curr.uncovered_students())
        table = planned_inclusion_table(
            plan, self.curr.survey, self.curr.roster)
        students = {row["student"] for row in table}
        for frame in ("F4", "F5"):
            design = self.curr.survey.designs.get(frame)
            # F4 尚未派单，survey 里没有它；用 frames.csv 名册核对
        frame_rows = {f.frame: f for f in self.inputs.frames}
        for code in frame_rows["F4"].codes + frame_rows["F5"].codes:
            self.assertIn(code, students)
        for row in table:
            self.assertTrue(row["steps"])


class ChangeAttributionTests(SnapshotTestBase):
    def test_changes_classified_into_four_reasons(self):
        records = diff_snapshots(self.r1.to_bundle(),
                                 self.curr.to_bundle())
        by_student = {r.student: r for r in records}
        self.assertIn("identity_review", by_student["G03"].reasons)
        self.assertIn("data_completion", by_student["G05"].reasons)
        # G10 的标签由复访答复补齐
        self.assertEqual(by_student["G10"].previous_label, UNKNOWN)
        self.assertEqual(by_student["G10"].current_label, NOT_WORKING)
        self.assertIn("data_completion", by_student["G10"].reasons)
        # G12 两轮换了框架覆盖判断
        self.assertIn("coverage_review", by_student["G12"].reasons)

    def test_rollback_when_old_frame_area_conflicts_with_new_main_school(self):
        records = diff_snapshots(self.r1.to_bundle(),
                                 self.curr.to_bundle())
        frame_map = {
            d.frame.frame: {"area": d.frame.area,
                            "stratum": d.frame.stratum}
            for d in self.curr.survey.designs.values()}
        rollbacks = evaluate_rollbacks(
            self.curr.to_bundle(), records, frame_map,
            withdrawn_pairs=self.curr.withdrawn_pairs)
        # G03 主校确认为 B(outer)，但 R1 随机机会来自 F1(metro)
        g03_rb = [rb for rb in rollbacks
                  if "G03" in rb.comparison and "F1" in rb.unusable_frames]
        self.assertTrue(g03_rb)
        self.assertIn("下一轮", g03_rb[0].next_round_basis)
        # F2(outer) 的机会与新主校同地区，不应被退回
        all_bad = {f for rb in rollbacks for f in rb.unusable_frames}
        self.assertNotIn("F2", all_bad)

    def test_withdrawn_frame_excluded_from_weights(self):
        # 退回后 G03 不携带任何概率框架，F1 机会不进入权重
        g03 = self.curr.outcomes["G03"]
        self.assertNotIn("F1", g03.probability_frames)
        self.assertIn(("G03", "F1"), self.curr.withdrawn_pairs)
        # G12 仍属 A(metro)，F1 机会对其有效
        self.assertIn("F1", self.curr.outcomes["G12"].probability_frames)

    def test_r1_freeze_snapshot_has_no_withdrawal_or_visits(self):
        # 冻结时点无派单，也不存在可退回的机会
        self.assertEqual(self.r1.withdrawn_pairs, set())
        self.assertEqual(
            sum(len(d.dispatched) for d in
                self.r1.survey.designs.values()), 0)


class IndependentDesignTests(SnapshotTestBase):
    def test_independent_redraw_keeps_nonzero_conditional_chance(self):
        from employment.survey import INDEPENDENT
        info = cumulative_inclusion(
            "G12", self.curr.survey, "R2", r2_design=INDEPENDENT,
            r2_draws={"F4": 2})
        r2_step = next(s for s in info["steps"] if s["round"] == "R2")
        # 独立重抽：即便 R1 在 F1 已中，F4 仍按 2/3 给机会
        self.assertAlmostEqual(r2_step["conditional_probability"], 2 / 3)
        self.assertAlmostEqual(
            info["cumulative_inclusion_probability"],
            1 - (1 - 2 / 3) * (1 - 2 / 3))


class SerializationAndCliTests(SnapshotTestBase):
    def test_payload_is_json_serializable(self):
        import json
        from employment.report import snapshot_payload
        text = json.dumps(snapshot_payload(self.curr), ensure_ascii=False)
        self.assertIn("qualified_employment", text)
        self.assertIn("withdrawn_inclusion_pairs", text)

    def test_cli_run_and_plan_smoke(self):
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "employment", "run",
             "--data", str(DATA), "--asof", "2026-09-27",
             "--out", "/tmp/employment_cli_smoke"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc2 = subprocess.run(
            [sys.executable, "-m", "employment", "plan",
             "--data", str(DATA), "--asof", "2026-09-27",
             "--round", "R2", "--out", "/tmp/employment_cli_smoke"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc2.returncode, 0, proc2.stderr)


if __name__ == "__main__":
    unittest.main()
