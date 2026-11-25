"""学籍清单的按届主校认定。

规则（对应需求）：

- 总体只来自经确认的学籍清单，按 ``student`` 取截至时点的最新版本。
- 联合培养毕业生按该届确认的主校认定；``main_status=disputed`` 且
  截至时点没有确认版本时，主校未定，不进入任何院校/专业的分母，
  但要列出争议院校以及受影响的分母条目。
- 主校在两轮之间被更正时，旧分母与旧抽样机会的影响在 changes 模块解释。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .load import CohortInputs, RegisterRow


@dataclass
class StudentRecord:
    student: str
    program: str
    graduated: date
    window_start: date
    window_end: date
    school_options: list[str]
    main_status: str          # confirmed | disputed
    main_school: str | None
    area: str | None
    version: int
    received: date
    history: list[RegisterRow]


@dataclass
class Roster:
    students: dict[str, StudentRecord]
    disputed: list[str]
    """主校未定、不能进入任何可比较分母的学生。"""
    disputed_impact: list[dict[str, object]]
    """每个争议学生影响到的院校/专业分母说明。"""

    def comparable(self) -> list[StudentRecord]:
        return [s for s in self.students.values() if s.main_school is not None]


def build_roster(inputs: CohortInputs, asof: date,
                 window_days: int) -> Roster:
    """按截至日 ``asof`` 解析学籍版本。

    同一 student 可能有多版；取 received <= asof 的最高 version。
    观察窗从毕业次日计起，长度取口径表的 window_days（本演练为 90）。
    """
    area_by_school = {s.school: s.area for s in inputs.schools}
    by_student: dict[str, list[RegisterRow]] = {}
    for row in inputs.register:
        if row.received <= asof:
            by_student.setdefault(row.student, []).append(row)

    students: dict[str, StudentRecord] = {}
    for student, rows in by_student.items():
        rows.sort(key=lambda r: (r.version, r.received))
        latest = rows[-1]
        window_start = latest.graduated.toordinal() + 1
        window_end = window_start + window_days - 1
        if latest.main_status == "confirmed" and len(latest.school_options) == 1:
            main_school = latest.school_options[0]
            area = area_by_school.get(main_school)
        elif latest.main_status == "confirmed" and len(latest.school_options) > 1:
            # 资料矛盾：标了 confirmed 却给了多个候选，按未确认处理并留痕。
            main_school, area = None, None
        else:
            main_school, area = None, None
        students[student] = StudentRecord(
            student=student, program=latest.program,
            graduated=latest.graduated,
            window_start=date.fromordinal(window_start),
            window_end=date.fromordinal(window_end),
            school_options=latest.school_options,
            main_status=latest.main_status, main_school=main_school,
            area=area, version=latest.version, received=latest.received,
            history=rows,
        )

    disputed = sorted(s for s, rec in students.items() if rec.main_school is None)
    impact: list[dict[str, object]] = []
    for student in disputed:
        rec = students[student]
        impact.append({
            "student": student,
            "program": rec.program,
            "candidate_schools": rec.school_options,
            "reason": ("main_status=disputed 且截至 %s 无确认版本"
                       % asof.isoformat()),
            "affected_denominators": [
                {"school": school,
                 "area": area_by_school.get(school),
                 "program": rec.program}
                for school in rec.school_options
            ],
            "window": [rec.window_start.isoformat(), rec.window_end.isoformat()],
        })
    return Roster(students=students, disputed=disputed,
                  disputed_impact=impact)
