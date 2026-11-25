"""跨轮快照对比：结论变化归因与抽样机会退回。

四类变化原因（可并存，逐条列出而不是只给一个标签）：

- ``data_completion``：新证据或新复访答复到达（数据补齐）；
- ``identity_review``：代号映射/主校身份被更正（身份复核）；
- ``caliber_review``：地区口径门槛或观察窗参数变化（口径复核）；
- ``coverage_review``：框架冻结、抽中机会或覆盖判定变化（抽样覆盖判断）。

当主校或身份更正导致旧抽样机会无法解释（例如旧框架名册把学生挂在
与新主校不一致的地区层），受影响的比较退回为不可推断，并写明：
仍可继续使用的部分、退回原因、以及下一轮复访的补救依据。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .estimate import GroupEstimate


@dataclass
class SnapshotBundle:
    """一次完整分析的产物，供两轮之间做差异归因。"""
    asof: str
    labels: dict[str, str]
    label_detail: dict[str, dict[str, object]]
    estimates: dict[str, GroupEstimate]
    comparisons: list[dict[str, object]]
    disputed: list[str]
    active_mappings: dict[str, str]
    frame_cutoffs: dict[str, str]
    policy_signature: dict[str, tuple]
    events: list[dict[str, object]]
    inclusion_records: dict[str, list[dict[str, object]]]


@dataclass
class ChangeRecord:
    student: str
    previous_label: str
    current_label: str
    reasons: list[str]
    detail: dict[str, object] = field(default_factory=dict)


@dataclass
class Rollback:
    comparison: str
    students: list[str]
    reason: str
    unusable_frames: list[str]
    usable_part: str
    next_round_basis: str


def diff_snapshots(prev: SnapshotBundle,
                   curr: SnapshotBundle) -> list[ChangeRecord]:
    """逐学生归因两轮之间的标签变化；无标签变化也可能有过程性变化。"""
    records: list[ChangeRecord] = []
    students = sorted(set(prev.labels) | set(curr.labels))
    prev_events = _event_index(prev)
    curr_events = _event_index(curr)

    for student in students:
        old_label = prev.labels.get(student, "absent")
        new_label = curr.labels.get(student, "absent")
        reasons: list[str] = []
        detail: dict[str, object] = {}

        identity_events = [
            e for e in curr.events
            if e.get("student") == student
            or e.get("to_student") == student
            or e.get("from_student") == student]
        old_owner = _owner_of_student_evidence(prev, student)
        new_owner = _owner_of_student_evidence(curr, student)
        if identity_events or old_owner != new_owner:
            reasons.append("identity_review")
            detail["identity_events"] = identity_events
            detail["evidence_owner_changed"] = {
                "previous": sorted(old_owner), "current": sorted(new_owner)}

        old_ev = set(prev.label_detail.get(student, {}).get("used_evidence", []))
        new_ev = set(curr.label_detail.get(student, {}).get("used_evidence", []))
        old_visits = set(prev.label_detail.get(student, {}).get("used_visits", []))
        new_visits = set(curr.label_detail.get(student, {}).get("used_visits", []))
        if new_ev - old_ev or new_visits - old_visits:
            reasons.append("data_completion")
            detail["new_evidence"] = sorted(new_ev - old_ev)
            detail["new_visits"] = sorted(new_visits - old_visits)

        if prev.policy_signature != curr.policy_signature:
            reasons.append("caliber_review")
            detail["policy_changed"] = {
                "previous": {k: list(v) for k, v in
                             prev.policy_signature.items()},
                "current": {k: list(v) for k, v in
                            curr.policy_signature.items()}}

        old_inc = prev.inclusion_records.get(student, [])
        new_inc = curr.inclusion_records.get(student, [])
        if old_inc != new_inc:
            reasons.append("coverage_review")
            detail["inclusion_changed"] = {
                "previous": old_inc, "current": new_inc}

        if student in prev.disputed and student not in curr.disputed:
            reasons.append("identity_review")
            detail["main_school_confirmed"] = True
        elif student not in prev.disputed and student in curr.disputed:
            reasons.append("identity_review")
            detail["main_school_became_disputed"] = True

        if old_label != new_label or reasons:
            # 同一原因可能由多个分支触发（如身份复核+主校确认），去重保序。
            unique_reasons = list(dict.fromkeys(reasons)) or ["data_completion"]
            records.append(ChangeRecord(
                student=student, previous_label=old_label,
                current_label=new_label,
                reasons=unique_reasons, detail=detail))
    return records


def _event_index(snapshot: SnapshotBundle) -> dict[str, list]:
    index: dict[str, list] = {}
    for event in snapshot.events:
        for key in ("student", "to_student", "from_student"):
            if event.get(key):
                index.setdefault(event[key], []).append(event)
    return index


def _owner_of_student_evidence(snapshot: SnapshotBundle,
                               student: str) -> set[str]:
    """从标签明细里取回该学生在该快照下实际使用的证据编号集合。"""
    return set(snapshot.label_detail.get(student, {})
               .get("used_evidence", []))


def evaluate_rollbacks(curr: SnapshotBundle,
                       changes: list[ChangeRecord],
                       frame_school_area: dict[str, dict[str, str]],
                       withdrawn_pairs: set[tuple[str, str]] | None = None
                       ) -> list[Rollback]:
    """检查身份/主校更正后，旧抽样机会是否还能解释新分母。

    frame_school_area: frame -> {"area": ..., "stratum": ...} 的框架事实；
    withdrawn_pairs: 快照构建时已判定退回的 (学生, 框架) 机会对。
    """
    withdrawn_pairs = withdrawn_pairs or set()
    rollbacks: list[Rollback] = []
    identity_students = {c.student for c in changes
                         if "identity_review" in c.reasons}
    for student in sorted(identity_students):
        bad_frames = sorted(frame for sid, frame in withdrawn_pairs
                            if sid == student)
        if not bad_frames:
            continue
        detail = curr.label_detail.get(student, {})
        current_area = detail.get("area")
        rollbacks.append(Rollback(
            comparison=f"涉及学生 {student} 的院校/专业比较",
            students=[student],
            reason=(f"身份更正后学生地区口径为 {current_area}，"
                    f"但旧框架 {bad_frames} 的冻结地区与新分母不一致，"
                    "旧抽中机会无法映射到新分母"),
            unusable_frames=bad_frames,
            usable_part=("同框架内未受身份更正影响的其他学生，"
                         "其抽中机会与权重仍可继续使用"),
            next_round_basis=("在下一轮按更正后的主校地区重建随机框，"
                              "对该学生赋予可追溯的新抽中机会")))
    return rollbacks


def mark_comparison_status(curr: SnapshotBundle,
                           rollbacks: list[Rollback]) -> list[dict[str, object]]:
    """把退回结论挂到受影响学生所属院校的比较上；其余比较保留原判定。"""
    result = []
    for comp in curr.comparisons:
        touched = []
        for rb in rollbacks:
            rb_schools = {
                bundle_school
                for s in rb.students
                if (bundle_school :=
                    curr.label_detail.get(s, {}).get("main_school"))}
            cells = {comp["left"].split("/")[0], comp["right"].split("/")[0]}
            if cells & rb_schools:
                touched.append(rb)
        result.append({**comp, "rollback": None})
        if touched:
            result[-1]["rollback"] = {
                "status": "withdrawn",
                "frames_concerned": sorted(
                    {f for rb in touched for f in rb.unusable_frames}),
                "students": sorted(
                    {s for rb in touched for s in rb.students}),
                "reasons": [rb.reason for rb in touched],
            }
    return result
