"""LLM-as-a-Judge 服务（Phase 2）。

设计要点（对应评测体系方案的"judge 服务化"）：
- 契约化：qwen-plus + temperature=0 + response_format=json_object，
  解析失败重试一次后记 None（调用方判 judge_error，绝不计 agent 失败）
- 版本化：PROMPT_VERSION 变更 = 评分语义变更，进运行指纹
- 缓存：按 (model, prompt_version, system, user) 哈希落盘，
  回归重跑 90%+ judge 调用命中缓存（成本趋零）
- 校准：--calibrate 跑金标集算 Cohen's kappa（二值 pass@threshold），
  kappa < 0.7 退出码 1（judge prompt 变更的门禁）

金标集 judge/golden.json 的答案取自真实运行录制（2026-09-09 首跑），
人工标注期望分——"judge 的锚必须是人，自动化的是队列与回流"。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

_EVAL_DIR = Path(__file__).resolve().parent
_REPO = _EVAL_DIR.parents[1]
CACHE_PATH = _EVAL_DIR / "results" / ".judge_cache.json"
GOLDEN_PATH = _EVAL_DIR / "judge" / "golden.json"

PROMPT_VERSION = "rubric-v2"
DEFAULT_MODEL = "qwen-plus"
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
KAPPA_GATE = 0.7

_SYSTEM = (
    "你是严格的评测裁判。按参考答案与评分维度给待评回答打分，"
    "每个维度 0~1 分并给一句话依据。只输出 JSON，格式："
    '{"dimensions": {"维度名": 分数}, "reasons": {"维度名": "依据"}}。'
    "评分纪律：以参考答案为锚，不因文笔华丽加分；回答与参考冲突即低分；"
    "回答承认不知道且参考确实未涵盖时不算错；"
    "覆盖参考的关键要点即得分，额外正确的补充不扣分，缺失关键要点或与参考矛盾才扣分。"
)


def _load_api_key() -> str:
    if os.environ.get("QWEN_API_KEY"):
        return os.environ["QWEN_API_KEY"]
    env = _REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("QWEN_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _cache_key(model: str, system: str, user: str) -> str:
    raw = f"{model}|{PROMPT_VERSION}|{system}|{user}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class JudgeUnavailable(Exception):
    pass


def _read_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def judge_call(system: str, user: str, model: str = DEFAULT_MODEL,
               use_cache: bool = True, retries: int = 1) -> dict | None:
    """带缓存的 judge 调用。返回解析后的 dict；失败返回 None（=judge_error）。"""
    key = _cache_key(model, system, user)
    cache = _read_cache() if use_cache else {}
    if use_cache and key in cache:
        return cache[key]

    import requests  # 局部导入：纯单测环境无网络依赖

    api_key = _load_api_key()
    if not api_key:
        raise JudgeUnavailable("QWEN_API_KEY 未配置（judge 不可用 → 用例记 judge_error）")

    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(retries + 1):
        try:
            r = requests.post(DASHSCOPE_URL, json=body, timeout=180,
                              headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{[\s\S]*\}", content)
            if not m:
                raise ValueError(f"非 JSON 输出: {content[:120]}")
            parsed = json.loads(m.group(0))
            if "dimensions" not in parsed or not isinstance(parsed["dimensions"], dict):
                raise ValueError(f"缺 dimensions 字段: {content[:120]}")
            if use_cache:
                cache[key] = parsed
                CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            return parsed
        except Exception as e:
            if attempt >= retries:
                print(f"    [judge] 调用失败: {type(e).__name__}: {e}", file=sys.stderr)
                return None
            time.sleep(2)


def rubric_judge(question: str, answer: str, reference: str,
                 dimensions: dict[str, float], model: str = DEFAULT_MODEL) -> dict | None:
    """通用 Rubric 评分入口。返回 {dimensions, reasons} 或 None。"""
    dims_text = "\n".join(f"- {name}（权重 {weight}）" for name, weight in dimensions.items())
    user = (
        f"## 问题\n{question}\n\n## 参考答案（评分锚）\n{reference}\n\n"
        f"## 待评回答\n{answer}\n\n## 评分维度（各 0~1）\n{dims_text}"
    )
    return judge_call(_SYSTEM, user, model=model)


def weighted_total(parsed: dict, dimensions: dict[str, float]) -> float:
    scores = parsed.get("dimensions", {})
    total_w = sum(dimensions.values()) or 1.0
    return sum(float(scores.get(d, 0.0)) * w for d, w in dimensions.items()) / total_w


# ---------- 金标校准 ----------

def calibrate(model: str = DEFAULT_MODEL, threshold: float = 0.7) -> float:
    """跑金标集 → Cohen's kappa（二值：加权分 ≥ threshold 记 pass）。"""
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    entries = golden["entries"]
    judge_pass: list[int] = []
    label_pass: list[int] = []
    print(f"[calibrate] 金标 {len(entries)} 条 | model={model} prompt={PROMPT_VERSION} threshold={threshold}")
    bad = 0
    for i, e in enumerate(entries):
        parsed = rubric_judge(e["question"], e["answer"], e["reference"],
                              e["dimensions"], model=model)
        if parsed is None:
            print(f"  [{i}] judge 失败 → 计为不一致（judge 故障）")
            judge_pass.append(0)
            label_pass.append(1 if e["label_pass"] else 0)
            bad += 1
            continue
        total = weighted_total(parsed, e["dimensions"])
        jp = int(total >= threshold)
        lp = int(e["label_pass"])
        judge_pass.append(jp)
        label_pass.append(lp)
        mark = "✓" if jp == lp else "✗ DISAGREE"
        extra = ""
        if jp != lp:
            reasons = parsed.get("reasons", {})
            extra = " | " + "; ".join(str(v)[:70] for v in reasons.values())
        print(f"  [{i}] judge={total:.2f}({'pass' if jp else 'fail'}) "
              f"label={'pass' if lp else 'fail'} {mark} {e['note'][:40]}{extra}")
    kappa = _cohen_kappa(judge_pass, label_pass)
    print(f"\n[calibrate] kappa={kappa:.3f}（门禁 {KAPPA_GATE}） judge故障 {bad} 条")
    if kappa < KAPPA_GATE:
        print("[calibrate] ✗ 未过门禁：judge prompt 不得合并进数据集/报告")
    else:
        print("[calibrate] ✓ 通过门禁")
    return kappa


def _cohen_kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    if not n:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="LLM-judge 服务：金标校准")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    if args.calibrate:
        sys.exit(0 if calibrate(args.model) >= KAPPA_GATE else 1)
    print("用法: python testing/eval/judge.py --calibrate")
