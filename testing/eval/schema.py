"""用例与数据集 schema：声明式 JSON → dataclass + 加载校验。

数据集文件结构：
{
  "name": "capability",
  "version": "2026.09.8",
  "description": "...",
  "default_trials": 3,
  "meta": {...},                       # 自由字段（corpus 等）
  "cases": [ {CaseSpec 字段}, ... ]
}

CaseSpec 字段（scorers 里的 type 必须已在评分器注册表注册）：
  id* / dataset* / crew / scenario(常规|边界|异常) / intent / origin
  input* {message, crew_id?, session_id?, single?}
  scorers* [{type, spec, weight?}]
  trials（缺省取数据集 default_trials）
  cost_gate {max_tokens_avg?}          # Phase 2 接 token_budget，先收下不强制
  version（缺省取数据集 version）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SCENARIOS = ("常规", "边界", "异常")


class SchemaError(ValueError):
    pass


@dataclass
class ScorerSpec:
    type: str
    spec: dict = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class CaseSpec:
    id: str
    dataset: str
    input: dict
    scorers: list[ScorerSpec]
    crew: str | None = None
    scenario: str = "常规"
    intent: str | None = None
    origin: str | None = None
    trials: int | None = None
    cost_gate: dict | None = None
    version: str | None = None

    def effective_trials(self, default_trials: int) -> int:
        return self.trials if self.trials and self.trials > 0 else default_trials


@dataclass
class Dataset:
    name: str
    version: str
    description: str
    default_trials: int
    path: Path
    cases: list[CaseSpec]
    meta: dict = field(default_factory=dict)

    def case_index(self) -> dict[str, CaseSpec]:
        return {c.id: c for c in self.cases}


def load_dataset(path: str | Path, scorer_types: set[str] | None = None) -> Dataset:
    """加载并校验数据集。scorer_types 传入注册表键集，缺省用当前 REGISTRY。"""
    if scorer_types is None:
        from testing.eval.scorers import REGISTRY
        scorer_types = set(REGISTRY)

    p = Path(path)
    if not p.exists():
        raise SchemaError(f"数据集不存在: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))

    name = raw.get("name") or p.stem
    version = raw.get("version") or "unversioned"
    default_trials = int(raw.get("default_trials", 3))
    if default_trials < 1:
        raise SchemaError("default_trials 必须 ≥ 1")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise SchemaError("cases 必须为非空列表")

    seen_ids: set[str] = set()
    cases: list[CaseSpec] = []
    for i, c in enumerate(cases_raw):
        cid = c.get("id")
        if not cid:
            raise SchemaError(f"cases[{i}] 缺 id")
        if cid in seen_ids:
            raise SchemaError(f"用例 id 重复: {cid}")
        seen_ids.add(cid)
        if not c.get("input", {}).get("message"):
            raise SchemaError(f"{cid}: input.message 必填")
        scenario = c.get("scenario", "常规")
        if scenario not in SCENARIOS:
            raise SchemaError(f"{cid}: scenario 非法 {scenario!r}，取值 {SCENARIOS}")
        scorers_raw = c.get("scorers")
        if not scorers_raw:
            raise SchemaError(f"{cid}: scorers 必填（至少一个评分器）")
        scorers = []
        for s in scorers_raw:
            stype = s.get("type")
            if stype not in scorer_types:
                raise SchemaError(f"{cid}: 未知评分器类型 {stype!r}，可用: {sorted(scorer_types)}")
            scorers.append(ScorerSpec(type=stype, spec=s.get("spec") or {}, weight=float(s.get("weight", 1.0))))
        cases.append(CaseSpec(
            id=cid, dataset=name, input=c["input"], scorers=scorers,
            crew=c.get("crew"), scenario=scenario, intent=c.get("intent"),
            origin=c.get("origin"), trials=c.get("trials"), cost_gate=c.get("cost_gate"),
            version=c.get("version") or version,
        ))

    return Dataset(name=name, version=version, description=raw.get("description", ""),
                   default_trials=default_trials, path=p, cases=cases, meta=raw.get("meta", {}))


def load_datasets(paths: list[str | Path], **kw) -> list[Dataset]:
    return [load_dataset(p, **kw) for p in paths]
