# -*- coding: utf-8 -*-
"""从已入库年报的 layout.json 提取事实，生成 Golden Set v0。

事实来源是解析器落盘的结构化元素（内容+页码+章节路径），页码即检索层
期望命中的页。生成后需人工抽检题目与期望值再入库。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOADS = PROJECT_ROOT / "data" / "uploads"

# 表格行中第一个数值（标签格不含数字，即 2023 年报数）
VALUE_PATTERN = re.compile(r"\d[\d,]*\.?\d*")


def rejoin_numbers(line: str) -> str:
    """重组 docling 断开的长数字："14,486,808,9 20.82" -> "14,486,808,920.82"。"""

    return re.sub(r"(?<=[\d.]) (?=\d)", "", line)


def company_of(file_name: str) -> str:
    # 601555_东吴证券_2023.pdf -> 东吴证券
    parts = file_name.rsplit(".", 1)[0].split("_")
    return parts[1] if len(parts) >= 3 else parts[0]


def load_elements(layout_path: Path) -> list[dict]:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    return layout.get("elements", [])


def find_section_pages(elements: list[dict], keyword: str) -> list[int]:
    """标题元素命中关键词的页码（去重保序）；跳过目录区。"""

    pages: list[int] = []
    for element in elements:
        if element.get("kind") != "heading":
            continue
        path_text = " / ".join(element.get("heading_path") or [])
        text = f"{path_text}\n{element.get('content', '')}"
        page = element.get("page_number")
        if keyword in text and page and page > 3 and page not in pages:
            pages.append(page)
    return pages


def _table_candidates(elements: list[dict]) -> list[dict]:
    """主要会计数据章节下的表格元素，近 3 年表优先（分季度表数值不能当年报数）。"""

    candidates = []
    for element in elements:
        heading = " / ".join(element.get("heading_path") or [])
        if "主要会计数据" in heading and "|" in element.get("content", ""):
            priority = 0 if ("近 3 年" in heading or "近3年" in heading) else 1
            candidates.append((priority, element))
    candidates.sort(key=lambda pair: pair[0])
    return [element for _, element in candidates]


def extract_financials(elements: list[dict]) -> dict[str, dict]:
    """从近 3 年主要会计数据表提取营业收入/归母净利润及所在页。

    docling 会在长单元格文本中插空格（归属于上市 公司股东的 净利润），
    标签匹配先做去空格归一化，取值仍用原始行。
    """

    labels = [
        ("营业收入", "revenue"),
        ("归属于上市公司股东的净利润", "net_profit"),
    ]
    results: dict[str, dict] = {}
    for element in _table_candidates(elements):
        content = element.get("content", "")
        for line in content.splitlines():
            normalized = line.replace(" ", "")
            for label, key in labels:
                if key in results:
                    continue
                if normalized.startswith(f"|{label}"):
                    values = VALUE_PATTERN.findall(rejoin_numbers(line))
                    if values:
                        results[key] = {
                            "value": values[0],
                            "page": element.get("page_number"),
                        }
    return results


def build(dataset_path: Path) -> list[dict]:
    conn = sqlite3.connect(PROJECT_ROOT / "data" / "food_safety_rag.db")
    conn.row_factory = sqlite3.Row
    docs = conn.execute(
        "SELECT doc_id, file_name FROM documents WHERE is_current = 1 ORDER BY file_name"
    ).fetchall()
    samples: list[dict] = []
    for doc in docs:
        layout_path = UPLOADS / doc["doc_id"] / "layout.json"
        if not layout_path.is_file():
            print(f"skip（无 layout）: {doc['file_name']}")
            continue
        elements = load_elements(layout_path)
        company = company_of(doc["file_name"])

        financials = extract_financials(elements)
        if "revenue" in financials:
            samples.append(
                {
                    "question": f"{company}2023年营业收入是多少？",
                    "answerable": True,
                    "expect_doc": doc["file_name"],
                    "expect_pages": [financials["revenue"]["page"]],
                    # 同一数字会出现在摘要表/正式报表/经营讨论多处，任一即正确证据
                    "expect_evidence": financials["revenue"]["value"],
                    "expected_keywords": [financials["revenue"]["value"]],
                }
            )
        if "net_profit" in financials:
            samples.append(
                {
                    "question": f"{company}2023年归属于上市公司股东的净利润是多少？",
                    "answerable": True,
                    "expect_doc": doc["file_name"],
                    "expect_pages": [financials["net_profit"]["page"]],
                    "expect_evidence": financials["net_profit"]["value"],
                    "expected_keywords": [financials["net_profit"]["value"]],
                }
            )

        risk_pages = find_section_pages(elements, "可能面对的风险")
        if risk_pages:
            samples.append(
                {
                    "question": f"{company}年报中提到可能面对哪些风险？",
                    "answerable": True,
                    "expect_doc": doc["file_name"],
                    "expect_pages": risk_pages[:2],
                    "expect_evidence": "风险",
                    "expected_keywords": [company],
                }
            )

        dividend_pages = find_section_pages(elements, "利润分配")
        if dividend_pages:
            samples.append(
                {
                    "question": f"{company}2023年度的利润分配预案是什么？",
                    "answerable": True,
                    "expect_doc": doc["file_name"],
                    "expect_pages": dividend_pages[:2],
                    "expect_evidence": "利润分配",
                    "expected_keywords": [company],
                }
            )
        print(
            f"{doc['file_name']}: 财务={sorted(financials)} 风险页={risk_pages[:1]} 分红页={dividend_pages[:1]}"
        )

    # 拒答题：知识库中不存在的信息，期望拒答而不是编造
    samples += [
        {
            "question": "贵州茅台2023年的营业收入是多少？",
            "answerable": False,
        },
        {
            "question": "请给出知识库中没有入库的腾讯控股2023年年报全文。",
            "answerable": False,
        },
    ]

    dataset_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in samples) + "\n",
        encoding="utf-8",
    )
    print(f"\n写入 {len(samples)} 题到 {dataset_path}")
    return samples


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "eval" / "dataset.jsonl"
    )
    args = parser.parse_args()
    build(args.output)
