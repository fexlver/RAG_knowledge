"""年报知识库两阶段评测：检索层（不耗 LLM）与答案层（端到端）。

检索层回答"能不能找到对的证据"：期望文档/页码是否出现在 rerank 后的 top-k。
答案层回答"答得对不对"：关键词命中、拒答准确性、引用率。
数据集行字段：
  question          必填
  answerable        必填，false 表示知识库无法回答、期望拒答
  expect_doc        检索层期望命中的文件名（answerable=true 时必填）
  expect_pages      期望命中的页码列表（可选，元素页码来自解析器）
  expected_keywords 答案应包含的关键词（可选，答案层使用）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.rag_service import FoodSafetyRAGService, build_service


def load_dataset(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def doc_names(service: FoodSafetyRAGService) -> dict[str, str]:
    """doc_id -> file_name，用于把检索结果映射回文件判断命中。"""

    return {
        row["doc_id"]: row["file_name"]
        for row in service.sqlite_store.list_documents()
    }


def evaluate_retrieval(
    service: FoodSafetyRAGService, samples: list[dict], top_k: int = 5
) -> list[dict]:
    names = doc_names(service)
    rows = []
    for sample in samples:
        if not sample.get("answerable"):
            continue
        started = time.perf_counter()
        evidence, _plan, _notes = service.orchestrator.execute(sample["question"])
        elapsed = time.perf_counter() - started
        retrieved = evidence[:top_k]
        retrieved_docs = [names.get(item.chunk.doc_id, "") for item in retrieved]
        retrieved_pages = [item.chunk.page_number for item in retrieved]
        expect_doc = sample.get("expect_doc", "")
        expect_pages = sample.get("expect_pages") or []
        # 同一事实常出现在摘要/正式报表/经营讨论多处：内容含期望证据即算命中，
        # 页码命中是更严的位置指标（诊断用，不作为合格线）。
        # docling 会把长数字断开（"9 20.82"），比对时对块内容做数字重组归一化。
        expect_evidence = sample.get("expect_evidence", "")
        evidence_hit = bool(expect_evidence) and any(
            expect_evidence in re.sub(r"(?<=[\d.]) (?=\d)", "", item.chunk.content)
            for item in retrieved
        )
        doc_hit = expect_doc in retrieved_docs
        page_hit = bool(expect_pages) and any(
            page in retrieved_pages for page in expect_pages
        )
        first_rank = next(
            (
                index + 1
                for index, name in enumerate(retrieved_docs)
                if name == expect_doc
            ),
            None,
        )
        rows.append(
            {
                "question": sample["question"],
                "tier": "retrieval",
                "expect_doc": expect_doc,
                "expect_pages": expect_pages,
                "retrieved_docs": retrieved_docs,
                "retrieved_pages": retrieved_pages,
                "doc_hit": doc_hit,
                "page_hit": page_hit,
                "evidence_hit": evidence_hit,
                "first_rank": first_rank,
                "latency_seconds": round(elapsed, 3),
            }
        )
    return rows


def evaluate_answers(
    service: FoodSafetyRAGService, samples: list[dict]
) -> list[dict]:
    rows = []
    for sample in samples:
        started = time.perf_counter()
        result = service.ask(sample["question"], service.new_session())
        elapsed = time.perf_counter() - started
        keywords = sample.get("expected_keywords", [])
        # 置信控制拒答会置 refused 标志；证据分够但 LLM 依据资料声明
        # “无法确认/无法提供”同样是正确拒答，按软拒答计入。
        soft_refused = result.refused or any(
            marker in result.answer for marker in ("无法从当前知识库", "无法提供", "无法确认")
        )
        rows.append(
            {
                "question": sample["question"],
                "tier": "answer",
                "answer": result.answer,
                "answerable": sample["answerable"],
                "refused": result.refused,
                "soft_refused": soft_refused,
                "has_citation": bool(result.citations),
                "keyword_hit": all(keyword in result.answer for keyword in keywords)
                if keywords
                else None,
                "latency_seconds": round(elapsed, 3),
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    retrieval_rows = [row for row in rows if row["tier"] == "retrieval"]
    answer_rows = [row for row in rows if row["tier"] == "answer"]
    metrics: dict = {"sample_count": len(rows)}
    if retrieval_rows:
        total = max(len(retrieval_rows), 1)
        page_rows = [row for row in retrieval_rows if row["expect_pages"]]
        metrics["retrieval"] = {
            "doc_hit_at_5": sum(row["doc_hit"] for row in retrieval_rows) / total,
            "evidence_hit_at_5": sum(row["evidence_hit"] for row in retrieval_rows)
            / total,
            "page_hit_at_5": sum(row["page_hit"] for row in page_rows)
            / max(len(page_rows), 1),
            "mrr": sum(
                1 / row["first_rank"] for row in retrieval_rows if row["first_rank"]
            )
            / total,
            "average_latency_seconds": round(
                sum(row["latency_seconds"] for row in retrieval_rows) / total, 3
            ),
        }
    if answer_rows:
        total = max(len(answer_rows), 1)
        answerable_rows = [row for row in answer_rows if row["answerable"]]
        keyword_rows = [row for row in answerable_rows if row["keyword_hit"] is not None]
        metrics["answer"] = {
            "citation_rate": sum(row["has_citation"] for row in answerable_rows)
            / max(len(answerable_rows), 1),
            "refusal_accuracy": sum(
                row["soft_refused"] == (not row["answerable"]) for row in answer_rows
            )
            / total,
            "keyword_hit_rate": sum(row["keyword_hit"] for row in keyword_rows)
            / max(len(keyword_rows), 1)
            if keyword_rows
            else None,
            "average_latency_seconds": round(
                sum(row["latency_seconds"] for row in answer_rows) / total, 3
            ),
        }
    return metrics


def evaluate(dataset_path: Path, output_path: Path, tier: str) -> dict:
    service = build_service()
    samples = load_dataset(dataset_path)
    rows: list[dict] = []
    if tier in {"retrieval", "all"}:
        rows += evaluate_retrieval(service, samples)
    if tier in {"answer", "all"}:
        rows += evaluate_answers(service, samples)
    report = {
        "dataset": str(dataset_path),
        "tier": tier,
        "metrics": summarize(rows),
        "samples": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("eval/dataset.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/latest.json"))
    parser.add_argument(
        "--tier",
        choices=["retrieval", "answer", "all"],
        default="retrieval",
        help="retrieval=只测检索（快、不耗 LLM）；answer=端到端答案；all=两层都跑",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.dataset, args.output, args.tier)["metrics"],
            ensure_ascii=False,
            indent=2,
        )
    )
