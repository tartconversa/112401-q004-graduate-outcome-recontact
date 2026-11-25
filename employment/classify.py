"""九十天观察窗内的证据分类。

互斥结论（每人每版口径只有一个标签）：

- ``qualified_employment``：窗内至少一段连续可证期间同时达到当地工时与
  收入门槛，且持续日数不少于 policy.days；继续读书不抵消符合条件的工作。
- ``working_below_standard``：存在可证工作期间，但不满足上面的条件。
- ``studying``：仅有继续读书证据、没有可证工作。
- ``not_working``：有效证据表明窗内没有工作（如复访中本人明确答复无工作）。
- ``unknown``：证据缺失或无法证明；**未知永远不会被记成失业**。

冲突处理：同一人的多来源证据先按窗裁剪、归并后只输出一个标签，
冲突本身逐条记录；禁止把同一人的冲突证据拆到两个互斥结果里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .load import EvidenceRow, PolicyRow
from .roster import StudentRecord

QUALIFIED = "qualified_employment"
BELOW = "working_below_standard"
STUDYING = "studying"
NOT_WORKING = "not_working"
UNKNOWN = "unknown"

LABELS = (QUALIFIED, BELOW, STUDYING, NOT_WORKING, UNKNOWN)

# 标签的证据强度顺序：只用于互斥裁决，冲突仍会被记录。
STRENGTH = {QUALIFIED: 4, BELOW: 3, STUDYING: 2, NOT_WORKING: 1, UNKNOWN: 0}

# 复访答复到事实类别的映射。
VISIT_FACTS = {
    "reachable-no-work": NOT_WORKING,
    "reachable-no-proof": UNKNOWN,
    "unreachable": UNKNOWN,
    "already-replied": UNKNOWN,   # 事实由其他证据回答，本条不独立定性
    "needs-link": UNKNOWN,        # 身份/关联待核实的个案复核
}

UNKNOWN_REASONS = {
    "unreachable": "联系不上（保持未知，不计失业）",
    "reachable-no-proof": "本人有说法但窗内无书面佐证",
    "already-replied": "答复称已通过其他渠道回复，本条不重复定性",
    "needs-link": "代号关联待核实的指定复查，不作概率定性",
    "no_evidence": "九十天窗内没有任何有效证据",
}


@dataclass
class VisitFact:
    visit: str
    round: str
    frame: str
    kind: str
    reply: str
    replied: date | None
    is_probability: bool
    numer: int | None
    denom: int | None


@dataclass
class Segment:
    start: date
    end: date
    days: int
    evidence_ids: list[str]


@dataclass
class Classification:
    student: str
    policy: str
    area: str
    label: str = UNKNOWN
    studies: bool = False
    has_work_evidence: bool = False
    qualifying_segments: list[Segment] = field(default_factory=list)
    used_evidence: list[str] = field(default_factory=list)
    visit_visits: list[str] = field(default_factory=list)
    conflicts: list[dict[str, object]] = field(default_factory=list)
    unknown_reasons: list[str] = field(default_factory=list)
    clipped_evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "student": self.student,
            "policy": self.policy,
            "area": self.area,
            "label": self.label,
            "studies": self.studies,
            "has_work_evidence": self.has_work_evidence,
            "qualifying_segments": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(),
                 "days": s.days, "evidence": s.evidence_ids}
                for s in self.qualifying_segments
            ],
            "used_evidence": self.used_evidence,
            "used_visits": self.visit_visits,
            "conflicts": self.conflicts,
            "unknown_reasons": self.unknown_reasons,
            "clipped_evidence": self.clipped_evidence,
        }


def _clipped(ev: EvidenceRow, rec: StudentRecord) -> tuple[date, date] | None:
    start = max(ev.start, rec.window_start)
    end = min(ev.end, rec.window_end)
    return (start, end) if start <= end else None


def _merge_components(spans: list[tuple[date, date, str]]) -> list[Segment]:
    """把重叠或首尾相接（允许零天间隙）的区间合并为连续段。"""
    if not spans:
        return []
    spans = sorted(spans, key=lambda x: (x[0], x[1]))
    components: list[Segment] = []
    cur_start, cur_end, cur_ids = spans[0][0], spans[0][1], [spans[0][2]]
    for start, end, ev_id in spans[1:]:
        if start.toordinal() <= cur_end.toordinal() + 1:
            if end > cur_end:
                cur_end = end
            cur_ids.append(ev_id)
        else:
            components.append(Segment(
                cur_start, cur_end,
                cur_end.toordinal() - cur_start.toordinal() + 1, cur_ids))
            cur_start, cur_end, cur_ids = start, end, [ev_id]
    components.append(Segment(
        cur_start, cur_end,
        cur_end.toordinal() - cur_start.toordinal() + 1, cur_ids))
    return components


def classify_student(rec: StudentRecord,
                     evidence: list[EvidenceRow],
                     visits: list[VisitFact],
                     policy: PolicyRow) -> Classification:
    """按给定地区口径对一名学生出唯一一个互斥结论。"""
    result = Classification(
        student=rec.student, policy=policy.policy, area=policy.area)

    qualifying_spans: list[tuple[date, date, str]] = []
    work_spans: list[tuple[date, date, str]] = []
    study_spans: list[tuple[date, date, str]] = []
    work_claims: list[str] = []
    no_work_claims: list[str] = []

    for ev in evidence:
        clipped = _clipped(ev, rec)
        if clipped is None:
            result.conflicts.append({
                "type": "evidence_outside_window",
                "evidence": ev.evidence,
                "window": [rec.window_start.isoformat(),
                           rec.window_end.isoformat()],
            })
            continue
        start, end = clipped
        if ev.start < rec.window_start or ev.end > rec.window_end:
            result.clipped_evidence.append(ev.evidence)
        result.used_evidence.append(ev.evidence)
        if ev.studies:
            result.studies = True
            study_spans.append((start, end, ev.evidence))
        if ev.hours is not None or ev.income is not None:
            result.has_work_evidence = True
            work_spans.append((start, end, ev.evidence))
            work_claims.append(ev.evidence)
            meets = ((ev.hours or 0) >= policy.threshold_hours
                     and (ev.income or 0) >= policy.threshold_income)
            if meets:
                qualifying_spans.append((start, end, ev.evidence))

    # 复访答复只作为补充事实；记录每条答复的处理方式，保证已通知工作不隐去。
    visit_labels: list[str] = []
    for fact in visits:
        result.visit_visits.append(fact.visit)
        label = VISIT_FACTS.get(fact.reply, UNKNOWN)
        visit_labels.append(label)
        if fact.reply == "reachable-no-work":
            no_work_claims.append(fact.visit)
        elif fact.reply in UNKNOWN_REASONS:
            result.unknown_reasons.append(
                f"{fact.visit}: {UNKNOWN_REASONS[fact.reply]}")

    # 连续可证期间判定：达标区间合并后任一段长度 ≥ policy.days。
    components = _merge_components(qualifying_spans)
    result.qualifying_segments = [c for c in components if c.days >= policy.days]

    if result.qualifying_segments:
        label = QUALIFIED
    elif work_spans:
        label = BELOW
    elif study_spans:
        label = STUDYING
    elif NOT_WORKING in visit_labels:
        label = NOT_WORKING
    else:
        label = UNKNOWN
        if not result.unknown_reasons:
            result.unknown_reasons.append(UNKNOWN_REASONS["no_evidence"])

    # 冲突留痕：书面工作证据 vs 口头无工作；读书+兼职不算冲突（可并存）。
    if work_claims and no_work_claims:
        result.conflicts.append({
            "type": "work_vs_no_work",
            "work_evidence": work_claims,
            "no_work_visits": no_work_claims,
            "resolution": "窗内书面工作证据优先，口头无工作答复留痕不拆分结论",
        })
    if not result.has_work_evidence and NOT_WORKING in visit_labels \
            and study_spans:
        result.conflicts.append({
            "type": "study_vs_no_work",
            "study_evidence": [s[2] for s in study_spans],
            "no_work_visits": no_work_claims,
            "resolution": "读书与无工作可并存，记 studying",
        })
        label = STUDYING

    result.label = label
    return result
