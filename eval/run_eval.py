"""食品安全 RAG 端到端轻量评测。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.services.rag_service import build_service


def load_dataset(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def evaluate(dataset_path: Path, output_path: Path) -> dict:
    service = build_service()
    rows = []
    for sample in load_dataset(dataset_path):
        started = time.perf_counter()
        result = service.ask(sample["question"], service.new_session())
        elapsed = time.perf_counter() - started
        keywords = sample.get("expected_keywords", [])
        rows.append(
            {
                "question": sample["question"],
                "answer": result.answer,
                "answerable": sample["answerable"],
                "refused": result.refused,
                "has_citation": bool(result.citations),
                "keyword_hit": all(keyword in result.answer for keyword in keywords),
                "latency_seconds": round(elapsed, 3),
            }
        )

    total = max(len(rows), 1)
    answerable_rows = [row for row in rows if row["answerable"]]
    metrics = {
        "sample_count": len(rows),
        "citation_rate": sum(row["has_citation"] for row in answerable_rows)
        / max(len(answerable_rows), 1),
        "refusal_accuracy": sum(
            row["refused"] == (not row["answerable"]) for row in rows
        )
        / total,
        "keyword_hit_rate": sum(row["keyword_hit"] for row in answerable_rows)
        / max(len(answerable_rows), 1),
        "average_latency_seconds": round(
            sum(row["latency_seconds"] for row in rows) / total, 3
        ),
    }
    report = {"metrics": metrics, "samples": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("eval/dataset.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/latest.json"))
    args = parser.parse_args()
    print(
        json.dumps(
            evaluate(args.dataset, args.output)["metrics"], ensure_ascii=False, indent=2
        )
    )
