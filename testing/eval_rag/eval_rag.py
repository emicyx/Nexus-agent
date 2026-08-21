#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Nexus RAG 知识库量化评估脚本（host 侧，针对 localhost:8000 API）。

评估维度：
  1. 文档入库（ingest）全程耗时 + 分块数
  2. 检索层：Recall@k / Precision@k / MRR@k / NDCG@k / 检索延迟
  3. 生成层（knowledge_qa crew 问答）：相关性 / 忠实度 / 完整性 / 端到端延迟
  4. embedding 单次调用延迟采样

用法（在宿主机 venv 中运行）：
  .venv/Scripts/python.exe testing/eval_rag/eval_rag.py [--preflight | --chat-only | --retrieval-only]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("NEXUS_API", "http://localhost:8000")
TEST_SET_PATH = Path(__file__).resolve().parent / "test_set.json"
OUT_DIR = Path(__file__).resolve().parent / "results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 检索评测使用的 top_k 档位（一次请求 top_k=10，切片出 @3/@5/@10）
RETRIEVAL_TOP_K = 10


# ---------- 环境 / 工具 ----------

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
API_KEY = ENV.get("QWEN_API_KEY") or os.getenv("QWEN_API_KEY")


def parse_sse_block(block: str) -> dict:
    evt_type = None
    data: dict = {}
    for line in block.strip().split("\n"):
        if line.startswith("event: "):
            evt_type = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):].strip())
    return {"type": evt_type, "data": data}


# ---------- API 封装 ----------

def api_ingest(name: str, content: str, source_type: str = "text") -> tuple[dict, float]:
    t0 = time.perf_counter()
    r = requests.post(
        f"{BASE_URL}/v1/documents",
        json={"name": name, "content": content, "source_type": source_type},
        timeout=600,
    )
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    return r.json(), elapsed


def api_delete_doc(doc_id: int) -> None:
    try:
        requests.delete(f"{BASE_URL}/v1/documents/{doc_id}", timeout=60)
    except Exception:
        pass


def api_list_docs() -> list[dict]:
    r = requests.get(f"{BASE_URL}/v1/documents", timeout=60)
    r.raise_for_status()
    return r.json()


def api_search(q: str, top_k: int = RETRIEVAL_TOP_K, doc_id: int | None = None) -> tuple[list[dict], float]:
    params = {"q": q, "top_k": top_k}
    if doc_id:
        params["document_id"] = doc_id
    t0 = time.perf_counter()
    r = requests.get(f"{BASE_URL}/v1/documents/search", params=params, timeout=120)
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    return r.json(), elapsed


def api_get_crews() -> list[dict]:
    r = requests.get(f"{BASE_URL}/v1/crews", timeout=60)
    r.raise_for_status()
    return r.json()


def api_chat_stream(payload: dict, timeout: int = 240) -> tuple[list[dict], list[float], float]:
    """返回 (events, arrival_offsets[每事件相对请求起点的秒数], total_elapsed)。"""
    events: list[dict] = []
    offsets: list[float] = []
    t0 = time.perf_counter()
    with requests.post(
        f"{BASE_URL}/v1/chat/stream", json=payload, stream=True, timeout=timeout
    ) as resp:
        if resp.status_code != 200:
            body = resp.text[:500]
            raise RuntimeError(f"chat HTTP {resp.status_code}: {body}")
        block = ""
        for raw in resp.iter_lines(decode_unicode=True):
            line = raw if raw is not None else ""
            if line == "":
                if block.strip():
                    evt = parse_sse_block(block)
                    events.append(evt)
                    offsets.append(time.perf_counter() - t0)
                block = ""
            else:
                block += line + "\n"
    return events, offsets, time.perf_counter() - t0


def judge_call(prompt: str, system: str | None = None, model: str = "qwen-plus") -> dict:
    """调用 DashScope 兼容模式 chat completions，强制 JSON 输出，返回解析后的 dict。"""
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": msgs,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        url, json=body,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        timeout=180,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return {"score": None, "reason": f"judge 输出非 JSON: {content[:200]}"}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"score": None, "reason": f"judge JSON 解析失败: {content[:200]}"}


# ---------- DB 辅助：黄金分块定位（从实际入库分块查询） ----------

def psql(sql: str) -> str:
    """在 postgres 容器执行 SQL，返回 psql 文本输出。"""
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "nexus", "-d", "nexus", "-c", sql],
        capture_output=True, text=True, cwd=ROOT,
    )
    return r.stdout


def fetch_doc_chunks(doc_name: str) -> dict[int, str]:
    """从 document_chunks 拉取某文档全部分块的 {position: content}。

    用 json_agg 输出单行 JSON（多行内容被 JSON 转义为 \\n），避免 psql 对齐输出截断。
    """
    out = psql(
        "SELECT json_agg(json_build_object('position', dc.position, 'content', dc.content) "
        f"ORDER BY dc.position) FROM document_chunks dc "
        f"JOIN document_configs doc ON dc.document_id=doc.id WHERE doc.name='{doc_name}';"
    )
    result: dict[int, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            items = json.loads(line)
        except json.JSONDecodeError:
            continue
        for it in items:
            result[int(it["position"])] = it["content"]
    return result


def find_golden_positions(doc_name: str, phrase: str, golden_chunks: dict[str, dict[int, str]]) -> list[int]:
    """在指定文档的实际分块中查找包含 golden_phrase 的 position 列表。"""
    hits = []
    for pos, content in golden_chunks.get(doc_name, {}).items():
        if phrase in content:
            hits.append(pos)
    return hits


# ---------- 检索指标 ----------

def compute_retrieval_metrics(retrieved: list[dict], golden: set[tuple[str, int]]) -> dict:
    """retrieved: [{document_name, position, score}]；golden: {(doc_name, position)}。"""
    hits_bool = []
    for r in retrieved:
        key = (r["document_name"], int(r["position"]))
        hits_bool.append(key in golden)
    n_golden = len(golden)

    def recall(k: int) -> float:
        if n_golden == 0:
            return 0.0
        return sum(hits_bool[:k]) / n_golden

    def precision(k: int) -> float:
        if k == 0:
            return 0.0
        return sum(hits_bool[:k]) / k

    def mrr() -> float:
        for i, h in enumerate(hits_bool):
            if h:
                return 1.0 / (i + 1)
        return 0.0

    def ndcg(k: int) -> float:
        if k == 0:
            return 0.0
        rel = hits_bool[:k]
        dcg = sum(rel[i] / math.log2(i + 2) for i in range(len(rel)))
        ideal = sorted(rel, reverse=True)
        idcg = sum(ideal[i] / math.log2(i + 2) for i in range(len(ideal)))
        return dcg / idcg if idcg > 0 else 0.0

    return {
        "recall@3": recall(3),
        "recall@5": recall(5),
        "recall@10": recall(10),
        "precision@3": precision(3),
        "precision@5": precision(5),
        "precision@10": precision(10),
        "mrr@10": mrr(),
        "ndcg@10": ndcg(10),
        "n_golden": n_golden,
        "hit_positions": [i for i, h in enumerate(hits_bool) if h],
    }


# ---------- 主流程 ----------

def load_test_set() -> dict:
    return json.loads(TEST_SET_PATH.read_text(encoding="utf-8"))


def run_preflight(test_set: dict) -> None:
    print("=" * 70)
    print("PREFLIGHT: 校验 golden_phrase 是否存在于语料源文件文本中")
    ok = True
    for q in test_set["queries"]:
        src_file = next(c["file"] for c in test_set["corpus"] if c["name"] == q["source_doc"])
        content = (ROOT / src_file).read_text(encoding="utf-8")
        present = q["golden_phrase"] in content
        if not present:
            ok = False
        print(f"  {q['id']:>5} [{'OK' if present else 'MISSING':7}] {q['golden_phrase'][:40]!r} -> 源文件存在={present}")
    print("=" * 70)
    if not ok:
        print("存在 golden_phrase 未命中的用例，请调整测试集。")
        sys.exit(2)
    print("所有 golden_phrase 存在于语料源文件，测试集有效。（分块级命中将在入库后按实际分块校验）")
    sys.exit(0)


def run_ingestion(test_set: dict) -> dict:
    print("\n[1/4] 文档入库（ingest，语义分块）")
    # 清理同名旧文档（幂等）
    existing = api_list_docs()
    eval_names = {c["name"] for c in test_set["corpus"]}
    for d in existing:
        if d["name"] in eval_names:
            api_delete_doc(d["id"])
            print(f"  删除旧文档 {d['name']} (id={d['id']})")

    results = []
    golden_chunks: dict[str, dict[int, str]] = {}
    for item in test_set["corpus"]:
        fpath = ROOT / item["file"]
        content = fpath.read_text(encoding="utf-8")
        doc, elapsed = api_ingest(item["name"], content, item.get("source_type", "text"))
        actual = doc.get("chunk_count", 0)
        rec = {
            "name": item["name"],
            "file": item["file"],
            "chars": len(content),
            "chunk_count": actual,
            "ingest_seconds": round(elapsed, 3),
            "doc_id": doc.get("id"),
        }
        results.append(rec)
        golden_chunks[item["name"]] = fetch_doc_chunks(item["name"])
        print(
            f"  {rec['name']:>14} 字符={rec['chars']:>6} 分块={rec['chunk_count']:>4} "
            f"入库耗时={rec['ingest_seconds']:>6.2f}s"
        )
    return {"docs": results, "created_doc_ids": [r["doc_id"] for r in results], "golden_chunks": golden_chunks}


def run_retrieval(test_set: dict, golden_chunks: dict[str, dict[int, str]]) -> dict:
    print("\n[2/4] 检索层评测（Recall/Precision/MRR/NDCG）")
    per_query = []
    all_latencies = []
    for q in test_set["queries"]:
        golden_pos = find_golden_positions(q["source_doc"], q["golden_phrase"], golden_chunks)
        if not golden_pos:
            print(f"  !!! {q['id']} golden_phrase 未命中任何实际分块: {q['golden_phrase']!r}")
        golden = {(q["source_doc"], p) for p in golden_pos}
        retrieved, latency = api_search(q["query"], top_k=RETRIEVAL_TOP_K)
        all_latencies.append(latency)
        m = compute_retrieval_metrics(retrieved, golden)
        top1 = retrieved[0] if retrieved else None
        rec = {
            "id": q["id"],
            "query": q["query"],
            "source_doc": q["source_doc"],
            "n_golden": m["n_golden"],
            "golden_positions": golden_pos,
            "retrieved_positions": [(r["document_name"], r["position"]) for r in retrieved[:5]],
            "retrieved_docs": [r["document_name"] for r in retrieved[:5]],
            "top1_score": round(top1["score"], 4) if top1 else None,
            "top1_is_golden": bool(top1 and (top1["document_name"], top1["position"]) in golden),
            "retrieval_latency_s": round(latency, 4),
            **m,
        }
        per_query.append(rec)
        print(
            f"  {q['id']:>5}  R@3={m['recall@3']:.2f} R@5={m['recall@5']:.2f} "
            f"R@10={m['recall@10']:.2f} P@5={m['precision@5']:.2f} "
            f"MRR={m['mrr@10']:.2f} NDCG@10={m['ndcg@10']:.2f} "
            f"top1_golden={rec['top1_is_golden']} 延迟={latency*1000:.0f}ms"
        )
    agg = {
        "recall@3": statistics.mean([r["recall@3"] for r in per_query]),
        "recall@5": statistics.mean([r["recall@5"] for r in per_query]),
        "recall@10": statistics.mean([r["recall@10"] for r in per_query]),
        "precision@3": statistics.mean([r["precision@3"] for r in per_query]),
        "precision@5": statistics.mean([r["precision@5"] for r in per_query]),
        "precision@10": statistics.mean([r["precision@10"] for r in per_query]),
        "mrr@10": statistics.mean([r["mrr@10"] for r in per_query]),
        "ndcg@10": statistics.mean([r["ndcg@10"] for r in per_query]),
        "top1_golden_rate": statistics.mean([1.0 if r["top1_is_golden"] else 0.0 for r in per_query]),
        "latency_s": _summarize_latency(all_latencies),
        "n_queries": len(per_query),
    }
    print(f"  >>> 汇总: R@5={agg['recall@5']:.3f} P@5={agg['precision@5']:.3f} "
          f"MRR@10={agg['mrr@10']:.3f} NDCG@10={agg['ndcg@10']:.3f} "
          f"top1黄金命中率={agg['top1_golden_rate']:.3f}")
    return {"per_query": per_query, "aggregate": agg}


def _summarize_latency(vals: list[float]) -> dict:
    if not vals:
        return {}
    return {
        "p50_ms": round(statistics.median(vals) * 1000, 1),
        "p90_ms": round(sorted(vals)[int(len(vals) * 0.9 - 0.5)] * 1000, 1),
        "max_ms": round(max(vals) * 1000, 1),
        "n": len(vals),
    }


def run_generation(test_set: dict, crew_id: int) -> dict:
    print("\n[3/4] 生成层评测（knowledge_qa crew 问答 + LLM 判分）")
    chat_queries = [q for q in test_set["queries"] if q.get("run_chat")]
    per_query = []
    all_e2e = []
    all_first_event = []
    for q in chat_queries:
        qid = q["id"]
        payload = {
            "message": q["query"],
            "session_id": str(uuid.uuid4()),
            "crew_id": crew_id,
            "single": False,
        }
        print(f"  {qid:>5} 对话中: {q['query'][:50]}...")
        events, offsets, total = api_chat_stream(payload)
        evt_types = [e["type"] for e in events]
        fa = next((e for e in events if e["type"] == "final_answer"), None)
        answer = fa["data"].get("content", "") if fa else ""
        rag_call = next((e for e in events if e["type"] == "tool_call" and e["data"].get("tool") == "rag_search"), None)
        rag_result = next((e for e in events if e["type"] == "tool_result" and e["data"].get("tool") == "rag_search"), None)
        first_event = offsets[0] if offsets else total
        # 检索证据（作为忠实度判断的上下文）：混合检索 top-5
        evidence, _ = api_search(q["query"], top_k=5)
        evidence_text = "\n---\n".join(f"[{i}] {e['content']}" for i, e in enumerate(evidence, 1))
        if len(evidence_text) > 6000:
            evidence_text = evidence_text[:6000]

        # LLM 判分
        rel = judge_call(
            f"用户问题：{q['query']}\n\n系统回答：{answer}\n\n"
            "请判断回答是否与问题相关、切题。输出 JSON {\"score\":0~1,\"reason\":\"\"}。"
        )
        faith = judge_call(
            f"检索到的上下文片段：\n{evidence_text}\n\n基于上下文的回答：\n{answer}\n\n"
            "逐句核对回答中的每个断言是否被上下文支持。输出 JSON {\"score\":0~1,\"reason\":\"\",\"unsupported\":[]}。"
        )
        comp = judge_call(
            f"标准答案：{q['expected_answer']}\n\n系统回答：{answer}\n\n"
            "判断系统回答是否覆盖标准答案的所有关键信息点。输出 JSON {\"score\":0~1,\"reason\":\"\",\"missing\":[]}。"
        )
        all_e2e.append(total)
        all_first_event.append(first_event)
        rec = {
            "id": qid,
            "query": q["query"],
            "e2e_seconds": round(total, 2),
            "first_event_seconds": round(first_event, 2),
            "rag_search_called": rag_call is not None,
            "rag_tool_result_preview": (rag_result["data"].get("output") or "")[:300] if rag_result else "",
            "event_types": evt_types,
            "answer": answer,
            "judge_relevance": _norm_score(rel),
            "judge_relevance_reason": rel.get("reason", ""),
            "judge_faithfulness": _norm_score(faith),
            "judge_faithfulness_reason": faith.get("reason", ""),
            "judge_completeness": _norm_score(comp),
            "judge_completeness_reason": comp.get("reason", ""),
        }
        per_query.append(rec)
        print(
            f"      e2e={total:5.1f}s first={first_event:5.2f}s rag调用={'是' if rag_call else '否'} "
            f"相关={rec['judge_relevance']:.2f} 忠实={rec['judge_faithfulness']:.2f} "
            f"完整={rec['judge_completeness']:.2f}"
        )
        print(f"      回答: {answer[:120]}")
    agg = {
        "relevance": statistics.mean([r["judge_relevance"] for r in per_query]),
        "faithfulness": statistics.mean([r["judge_faithfulness"] for r in per_query]),
        "completeness": statistics.mean([r["judge_completeness"] for r in per_query]),
        "e2e_seconds": _summarize_latency(all_e2e),
        "first_event_seconds": _summarize_latency(all_first_event),
        "rag_call_rate": statistics.mean([1.0 if r["rag_search_called"] else 0.0 for r in per_query]),
        "n_queries": len(per_query),
    }
    print(f"  >>> 汇总: 相关={agg['relevance']:.3f} 忠实={agg['faithfulness']:.3f} "
          f"完整={agg['completeness']:.3f} e2e p50={agg['e2e_seconds'].get('p50_ms')}ms "
          f"rag调用率={agg['rag_call_rate']:.2f}")
    return {"per_query": per_query, "aggregate": agg}


def _norm_score(judge_result: dict) -> float:
    s = judge_result.get("score")
    try:
        s = float(s)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, s))


def run_embedding_probe(sample_chunks: list[str], batch: int = 6) -> dict:
    print("\n[4/4] embedding 延迟采样")
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    samples = sample_chunks[:20]
    if not samples:
        return {}
    latencies = []
    batches = [samples[i:i + batch] for i in range(0, len(samples), batch)]
    for b in batches:
        t0 = time.perf_counter()
        r = requests.post(
            url,
            json={
                "model": "text-embedding-v3",
                "input": b,
                "dimensions": 1024,
                "encoding_format": "float",
            },
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            timeout=120,
        )
        r.raise_for_status()
        latencies.append((time.perf_counter() - t0) / len(b))
    per_call = _summarize_latency(latencies)
    print(f"  单条嵌入延迟(per chunk, {len(latencies)} 批): p50={per_call.get('p50_ms')}ms "
          f"p90={per_call.get('p90_ms')}ms")
    return {"per_chunk_ms": per_call, "sample_n": len(samples)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight", action="store_true", help="只校验测试集黄金短语命中")
    ap.add_argument("--chat-only", action="store_true", help="只跑生成层")
    ap.add_argument("--retrieval-only", action="store_true", help="只跑检索层")
    args = ap.parse_args()

    if not API_KEY:
        print("缺少 QWEN_API_KEY（root .env）")
        sys.exit(1)

    test_set = load_test_set()

    if args.preflight:
        run_preflight(test_set)

    # 找 knowledge_qa crew
    crews = api_get_crews()
    kb_crew = next((c for c in crews if c["name"] == "knowledge_qa"), None)
    if kb_crew is None:
        print("未找到 knowledge_qa crew")
        sys.exit(1)
    kb_crew_id = kb_crew["id"]

    results: dict = {
        "meta": {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "base_url": BASE_URL,
            "crew": "knowledge_qa",
            "crew_id": kb_crew_id,
            "n_queries": len(test_set["queries"]),
            "n_chat": sum(1 for q in test_set["queries"] if q.get("run_chat")),
        },
        "test_set": test_set,
    }

    ingestion = run_ingestion(test_set)
    results["ingestion"] = ingestion
    created_doc_ids = ingestion["created_doc_ids"]
    golden_chunks = ingestion["golden_chunks"]

    try:
        if not args.chat_only:
            results["retrieval"] = run_retrieval(test_set, golden_chunks)
        if not args.retrieval_only:
            results["generation"] = run_generation(test_set, kb_crew_id)
        # embedding 采样
        sample = []
        for ch in golden_chunks.values():
            sample.extend(list(ch.values())[:10])
        results["embedding_probe"] = run_embedding_probe(sample)
    finally:
        # 清理入库文档，恢复原知识库状态
        for did in created_doc_ids:
            api_delete_doc(did)
            print(f"  已清理评估文档 id={did}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"results_{ts}.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out_path}")


if __name__ == "__main__":
    main()
