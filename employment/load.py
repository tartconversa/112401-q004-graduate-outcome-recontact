"""CSV 资料读取与校验。

约定见 README：UTF-8、首行列名、多值以 ``|`` 分隔、空单元格为缺失。
读取时做最小的结构性校验（表头、必填列、日期与数值可解析），
资料之间的锚点一致性由后续模块在快照构建时处理并记录，而不是静默丢弃。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TABLE_HEADERS = {
    "register": ("student", "school_options", "program", "graduated",
                 "main_status", "version", "received"),
    "crosswalk": ("mapping", "external", "student", "relation", "asof",
                  "applies_to"),
    "outcomes": ("evidence", "external", "source", "start", "end", "hours",
                 "income", "studies", "received"),
    "policies": ("policy", "area", "window_days", "days",
                 "threshold_hours", "threshold_income"),
    "frames": ("round", "frame", "area", "stratum", "codes", "cost", "kind",
               "capacity", "cutoff"),
    "visits": ("visit", "round", "frame", "code", "kind", "numer", "denom",
               "dispatched", "reply", "replied"),
    "schools": ("school", "area"),
}


def parse_day(text: str) -> date:
    """解析 YYYY-MM-DD，空白视为错误由调用方决定是否允许。"""
    return date.fromisoformat(text.strip())


def parse_int(text: str) -> int | None:
    """整数列；空单元格返回 None。"""
    text = (text or "").strip()
    return int(text) if text else None


def split_codes(text: str) -> list[str]:
    """多值字段（竖线分隔），保留顺序、去空白、去空段。"""
    return [part.strip() for part in (text or "").split("|") if part.strip()]


@dataclass
class RegisterRow:
    student: str
    school_options: list[str]
    program: str
    graduated: date
    main_status: str          # confirmed | disputed
    version: int
    received: date


@dataclass
class MappingRow:
    mapping: str
    external: str
    student: str
    relation: str             # distinct | duplicate
    asof: date
    applies_to: list[str]     # all 或轮次号列表


@dataclass
class EvidenceRow:
    evidence: str
    external: str
    source: str               # self | employer | ...
    start: date
    end: date
    hours: int | None
    income: int | None
    studies: bool
    received: date


@dataclass
class PolicyRow:
    policy: str
    area: str
    window_days: int
    days: int
    threshold_hours: int
    threshold_income: int


@dataclass
class FrameRow:
    round: str
    frame: str
    area: str
    stratum: str
    codes: list[str]
    cost: int
    kind: str                 # random | mandatory
    capacity: int
    cutoff: date


@dataclass
class VisitRow:
    visit: str
    round: str
    frame: str
    code: str
    kind: str                 # random | mandatory
    numer: int | None         # 空白 = 非概率复核
    denom: int | None
    dispatched: date
    reply: str                # 空 = 尚无回复
    replied: date | None


@dataclass
class SchoolRow:
    school: str
    area: str


@dataclass
class CohortInputs:
    """一届演练的全部输入表。"""
    register: list[RegisterRow]
    crosswalk: list[MappingRow]
    outcomes: list[EvidenceRow]
    policies: list[PolicyRow]
    frames: list[FrameRow]
    visits: list[VisitRow]
    schools: list[SchoolRow]
    load_warnings: list[str] = field(default_factory=list)

    def policy_for_area(self, area: str) -> PolicyRow:
        matches = [p for p in self.policies if p.area == area]
        if len(matches) != 1:
            raise KeyError(f"地区 {area!r} 的口径不唯一：{matches}")
        return matches[0]

    def frame(self, frame_id: str) -> FrameRow:
        matches = [f for f in self.frames if f.frame == frame_id]
        if len(matches) != 1:
            raise KeyError(f"框架 {frame_id!r} 不唯一：{matches}")
        return matches[0]


def _read_table(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames is None:
            raise ValueError(f"{path} 缺少表头")
        expected = TABLE_HEADERS[path.stem]
        if tuple(reader.fieldnames) != expected:
            raise ValueError(
                f"{path.name} 表头不符：实际 {reader.fieldnames}，期望 {expected}")
        rows = list(reader)
    for row in rows:
        if None in row or None in row.values():
            raise ValueError(f"{path.name} 存在多余列或空表头单元格：{row}")
    return rows


def load_cohort(directory: str | Path) -> CohortInputs:
    """从一个目录读取一届的七张表，并做结构性校验。"""
    directory = Path(directory)
    warnings: list[str] = []
    raw: dict[str, list[dict[str, str]]] = {}
    for table in TABLE_HEADERS:
        raw[table] = _read_table(directory / f"{table}.csv")

    register = [
        RegisterRow(
            student=r["student"],
            school_options=split_codes(r["school_options"]),
            program=r["program"],
            graduated=parse_day(r["graduated"]),
            main_status=r["main_status"],
            version=parse_int(r["version"]) or 0,
            received=parse_day(r["received"]),
        )
        for r in raw["register"]
    ]
    crosswalk = [
        MappingRow(
            mapping=r["mapping"], external=r["external"], student=r["student"],
            relation=r["relation"], asof=parse_day(r["asof"]),
            applies_to=(["all"] if r["applies_to"].strip() == "all"
                        else split_codes(r["applies_to"])),
        )
        for r in raw["crosswalk"]
    ]
    outcomes = [
        EvidenceRow(
            evidence=r["evidence"], external=r["external"], source=r["source"],
            start=parse_day(r["start"]), end=parse_day(r["end"]),
            hours=parse_int(r["hours"]), income=parse_int(r["income"]),
            studies=r["studies"].strip().lower() == "yes",
            received=parse_day(r["received"]),
        )
        for r in raw["outcomes"]
    ]
    policies = [
        PolicyRow(
            policy=r["policy"], area=r["area"],
            window_days=parse_int(r["window_days"]) or 0,
            days=parse_int(r["days"]) or 0,
            threshold_hours=parse_int(r["threshold_hours"]) or 0,
            threshold_income=parse_int(r["threshold_income"]) or 0,
        )
        for r in raw["policies"]
    ]
    frames = [
        FrameRow(
            round=r["round"], frame=r["frame"], area=r["area"],
            stratum=r["stratum"], codes=split_codes(r["codes"]),
            cost=parse_int(r["cost"]) or 0, kind=r["kind"],
            capacity=parse_int(r["capacity"]) or 0,
            cutoff=parse_day(r["cutoff"]),
        )
        for r in raw["frames"]
    ]
    visits = []
    for r in raw["visits"]:
        numer, denom = parse_int(r["numer"]), parse_int(r["denom"])
        if (numer is None) != (denom is None):
            warnings.append(
                f"visit {r['visit']} 的 numer/denom 只填了一半，按非概率复核处理")
        visits.append(VisitRow(
            visit=r["visit"], round=r["round"], frame=r["frame"],
            code=r["code"], kind=r["kind"], numer=numer, denom=denom,
            dispatched=parse_day(r["dispatched"]),
            reply=r["reply"].strip(),
            replied=parse_day(r["replied"]) if r["replied"].strip() else None,
        ))
    schools = [SchoolRow(school=r["school"], area=r["area"])
               for r in raw["schools"]]

    inputs = CohortInputs(
        register=register, crosswalk=crosswalk, outcomes=outcomes,
        policies=policies, frames=frames, visits=visits, schools=schools,
        load_warnings=warnings,
    )
    _cross_checks(inputs)
    return inputs


def _cross_checks(inputs: CohortInputs) -> None:
    """锚点一致性校验；问题记录为警告，供快照报告披露而不是中断。"""
    students = {r.student for r in inputs.register}
    mapping_ids = {m.external for m in inputs.crosswalk}

    for row in inputs.outcomes:
        if row.external not in mapping_ids:
            inputs.load_warnings.append(
                f"证据 {row.evidence} 的代号 {row.external} 在 crosswalk 中无映射")
        if row.end < row.start:
            raise ValueError(f"证据 {row.evidence} 起止日期倒置")
    for frame in inputs.frames:
        unknown = [c for c in frame.codes if c not in students]
        if unknown:
            inputs.load_warnings.append(
                f"框架 {frame.frame} 含学籍外代号 {unknown}")
    for visit in inputs.visits:
        if visit.frame not in {f.frame for f in inputs.frames}:
            inputs.load_warnings.append(f"复访 {visit.visit} 的框架不存在")
        if visit.numer is not None and visit.numer > (visit.denom or 0):
            raise ValueError(f"复访 {visit.visit} 抽中机会分子大于分母")
    area_by_school = {s.school: s.area for s in inputs.schools}
    policy_areas = {p.area for p in inputs.policies}
    for school, area in area_by_school.items():
        if area not in policy_areas:
            inputs.load_warnings.append(
                f"学校 {school} 的地区 {area} 没有对应口径")
