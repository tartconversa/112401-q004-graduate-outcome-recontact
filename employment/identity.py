"""来源代号与学籍身份的时点解析。

crosswalk 的每条结论都有 asof（结论成立日）与 applies_to（适用轮次）。
截至某一时点，同一个 external 代号可能先后指向不同学生；后到的
``duplicate`` 结论说明该代号是重复代号，需要把此前挂在它名下的证据
重新归并到真正的学生，并保留事件轨迹，使"派单后才发现两个代号是同一人"
这类情况可以被完整解释，而不是悄悄改数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .load import CohortInputs, EvidenceRow, MappingRow


@dataclass
class IdentityResolution:
    asof: date
    external_to_student: dict[str, str]
    """截至时点有效的 代号 -> 学生 映射。"""
    student_evidence: dict[str, list[EvidenceRow]]
    """按当前映射归并后的学生证据（含重复代号并入）。"""
    duplicate_codes: dict[str, str]
    """被认定为重复代号的 external -> 当前持有者。"""
    events: list[dict[str, object]] = field(default_factory=list)
    """映射变更轨迹，供跨轮归因与抽样机会退回判断。"""
    unresolved_evidence: list[str] = field(default_factory=list)
    """截至时点没有任何有效映射的证据编号。"""


def active_mappings(inputs: CohortInputs, asof: date,
                    round_id: str | None = None) -> list[MappingRow]:
    """截至时点生效的映射；applies_to 限定轮次时按轮次过滤。"""
    result = []
    for row in inputs.crosswalk:
        if row.asof > asof:
            continue
        if "all" not in row.applies_to and round_id is not None:
            if round_id not in row.applies_to:
                continue
        result.append(row)
    return result


def resolve_identities(inputs: CohortInputs, asof: date) -> IdentityResolution:
    """按 asof 复现当时的代号归属，并把证据归并到学生。

    同一 external 若有多条结论，按 asof（再按 mapping 编号）取最新一条；
    被 duplicate 结论取代的旧 distinct 结论记入事件轨迹。
    """
    mappings = sorted(active_mappings(inputs, asof),
                      key=lambda m: (m.asof, m.mapping))
    external_to_student: dict[str, str] = {}
    duplicate_codes: dict[str, str] = {}
    events: list[dict[str, object]] = []

    for row in mappings:
        previous = external_to_student.get(row.external)
        external_to_student[row.external] = row.student
        if row.relation == "duplicate":
            duplicate_codes[row.external] = row.student
        if previous is not None and previous != row.student:
            events.append({
                "type": "code_reassigned",
                "external": row.external,
                "from_student": previous,
                "to_student": row.student,
                "relation": row.relation,
                "mapping": row.mapping,
                "asof": row.asof.isoformat(),
                "note": "重复代号合并：原归属学生的证据需要重新归并",
            })
        elif row.relation == "duplicate":
            events.append({
                "type": "duplicate_recognized",
                "external": row.external,
                "student": row.student,
                "mapping": row.mapping,
                "asof": row.asof.isoformat(),
            })

    student_evidence: dict[str, list[EvidenceRow]] = {}
    unresolved: list[str] = []
    for ev in inputs.outcomes:
        if ev.received > asof:
            # 时点之后才到的证据不进入该快照，但在 changes 中算作数据补齐。
            continue
        owner = external_to_student.get(ev.external)
        if owner is None:
            unresolved.append(ev.evidence)
            events.append({
                "type": "unmapped_evidence",
                "evidence": ev.evidence,
                "external": ev.external,
                "received": ev.received.isoformat(),
            })
            continue
        student_evidence.setdefault(owner, []).append(ev)

    return IdentityResolution(
        asof=asof, external_to_student=external_to_student,
        student_evidence=student_evidence, duplicate_codes=duplicate_codes,
        events=events, unresolved_evidence=unresolved,
    )


def mapping_events_between(inputs: CohortInputs,
                           start: date, end: date) -> list[MappingRow]:
    """两个快照之间新生效的映射结论。"""
    return [m for m in inputs.crosswalk if start < m.asof <= end]
