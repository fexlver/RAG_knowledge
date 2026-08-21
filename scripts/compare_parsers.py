"""对比 pymupdf_text 与 docling 两条解析管线的切块质量。

同一份 PDF 分别走两个解析器，用相同的切块参数生成文本块，
输出结构与溯源覆盖率统计和抽样对照，评估 Docling 接入的收益。

用法（food-rag 环境）：
    .conda/envs/food-rag/python.exe scripts/compare_parsers.py [pdf路径]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.domain.models import DocumentMetadata  # noqa: E402
from src.ingestion.splitter import build_chunks, calculate_file_hash  # noqa: E402
from src.ingestion.structured_parser import (  # noqa: E402
    PdfTextParser,
    parse_structured_document,
)

CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
SAMPLE_COUNT = 3


def _metadata(name: str) -> DocumentMetadata:
    return DocumentMetadata(source=name, content_hash="0" * 64)


def _chunk_stats(chunks: list) -> dict:
    sizes = [len(chunk.content) for chunk in chunks]
    with_section = sum(1 for chunk in chunks if chunk.section)
    with_rects = sum(1 for chunk in chunks if chunk.locator.get("rects"))
    return {
        "chunks": len(chunks),
        "avg_size": round(sum(sizes) / len(sizes)) if sizes else 0,
        "max_size": max(sizes) if sizes else 0,
        "with_section": with_section,
        "section_ratio": f"{with_section / len(chunks):.0%}" if chunks else "-",
        "with_rects": with_rects,
    }


def _sample(chunks: list, keyword: str = "") -> str:
    """抽样一个含关键词的块，用于人工对照质量。"""

    candidates = [
        chunk
        for chunk in chunks
        if (not keyword or keyword in chunk.content)
        and len(chunk.content) > 120
    ] or chunks
    if not candidates:
        return "(无样本)"
    chunk = candidates[min(SAMPLE_COUNT, len(candidates) - 1)]
    locator = chunk.locator
    return (
        f"section: {chunk.section or '(空)'}\n"
        f"page: {locator.get('page_number')}  "
        f"rects: {len(locator.get('rects', []))}\n"
        f"content[:300]: {chunk.content[:300]}"
    )


def main() -> int:
    if len(sys.argv) > 1:
        pdf_path = Path(sys.argv[1]).resolve()
    else:
        pdfs = sorted((ROOT / "corpus" / "raw").glob("*.pdf"))
        if not pdfs:
            print("[error] 未找到语料 PDF", flush=True)
            return 1
        pdf_path = pdfs[0]

    print(f"[info] 样本: {pdf_path.name}", flush=True)
    metadata = _metadata(pdf_path.name)

    print("[info] 解析中：pymupdf_text …", flush=True)
    started = time.time()
    legacy_doc = PdfTextParser().parse(pdf_path)
    legacy_parse = time.time() - started
    legacy_chunks = build_chunks(legacy_doc, metadata, CHUNK_SIZE, CHUNK_OVERLAP)

    print("[info] 解析中：docling（版面+表格，耗时较长）…", flush=True)
    started = time.time()
    docling_doc = parse_structured_document(pdf_path, parser_name="docling")
    docling_parse = time.time() - started
    docling_chunks = build_chunks(docling_doc, metadata, CHUNK_SIZE, CHUNK_OVERLAP)

    table_elements = [e for e in docling_doc.elements if e.kind == "table"]
    heading_elements = [e for e in docling_doc.elements if e.kind == "heading"]

    report = [
        f"# 解析管线对比：{pdf_path.name}",
        "",
        "| 指标 | pymupdf_text | docling |",
        "| --- | --- | --- |",
        f"| 解析耗时 | {legacy_parse:.1f}s | {docling_parse:.1f}s |",
        f"| 结构元素数 | {len(legacy_doc.elements)} | {len(docling_doc.elements)} |",
        f"| 标题元素数 | {sum(1 for e in legacy_doc.elements if e.kind == 'heading')} | {len(heading_elements)} |",
        f"| 表格元素数 | 0 | {len(table_elements)} |",
        f"| 文本块数 | {len(legacy_chunks)} | {len(docling_chunks)} |",
        f"| 平均块大小 | {_chunk_stats(legacy_chunks)['avg_size']} | {_chunk_stats(docling_chunks)['avg_size']} |",
        f"| 带章节路径的块 | {_chunk_stats(legacy_chunks)['section_ratio']} | {_chunk_stats(docling_chunks)['section_ratio']} |",
        f"| 带高亮矩形的块 | {_chunk_stats(legacy_chunks)['with_rects']} | {_chunk_stats(docling_chunks)['with_rects']} |",
    ]

    keyword = "营业收入" if "年" in pdf_path.name else ""
    report += [
        "",
        "## pymupdf_text 样本",
        "```",
        _sample(legacy_chunks, keyword),
        "```",
        "",
        "## docling 样本",
        "```",
        _sample(docling_chunks, keyword),
        "```",
    ]

    if table_elements:
        sample_table = table_elements[min(2, len(table_elements) - 1)]
        report += [
            "",
            "## docling 表格样本（markdown 投影前 400 字符）",
            "```",
            sample_table.content[:400],
            "```",
        ]

    out = ROOT / "output" / "parser-comparison" / f"{pdf_path.stem}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report), flush=True)
    print(f"\n[done] 报告已写入 {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
