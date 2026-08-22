"""结构化文档中间表示、派生产物与章节切块测试。"""

import json

from src.domain.models import DocumentMetadata
from src.ingestion.artifacts import persist_artifacts, render_canonical_markdown
from src.ingestion.document_model import DocumentElement, ParsedDocument
from src.ingestion.parser import parse_structured_document
from src.ingestion.splitter import build_chunks


def test_text_parser_preserves_heading_path_and_element_locator(tmp_path):
    path = tmp_path / "食品标准.txt"
    path.write_text(
        "第1章 总则\n\n本标准规定食品添加剂使用原则。\n\n"
        "第2章 管理要求\n\n经营者应建立进货查验记录。",
        encoding="utf-8",
    )

    document = parse_structured_document(path)

    assert document.parser_name == "plain_text"
    assert [element.kind for element in document.elements] == [
        "heading",
        "paragraph",
        "heading",
        "paragraph",
    ]
    assert document.elements[1].heading_path == ("第1章 总则",)
    assert document.elements[3].heading_path == ("第2章 管理要求",)
    assert document.elements[1].locator["start_line"] == 3


def test_structured_chunks_do_not_cross_sections_and_keep_element_ids(tmp_path):
    path = tmp_path / "食品标准.txt"
    path.write_text(
        "第1章 总则\n\n第一段规定食品添加剂使用原则。\n\n"
        "第二段规定经营者应建立记录。\n\n"
        "第2章 管理要求\n\n第三段规定临期食品管理要求。",
        encoding="utf-8",
    )
    document = parse_structured_document(path)
    metadata = DocumentMetadata(source=path.name, content_hash="c" * 64)

    chunks = build_chunks(document, metadata, chunk_size=100, chunk_overlap=10)

    assert len(chunks) == 2
    assert chunks[0].section == "第1章 总则"
    assert chunks[1].section == "第2章 管理要求"
    assert len(chunks[0].locator["element_ids"]) == 2
    assert chunks[0].locator["heading_path"] == ["第1章 总则"]


def test_long_table_splits_at_row_boundaries_with_repeated_header():
    header = "| 项目 | 本期金额 | 上期金额 | 变动比例 |"
    separator = "|---|---|---|---|"
    rows = [f"| 收入项目{index} | {index * 100} | {index * 90} | {index}% |" for index in range(1, 41)]
    table_content = "\n".join([header, separator] + rows)
    document = ParsedDocument(
        source_name="年报.txt",
        parser_name="docling",
        parser_version="2",
        pages=(),
        elements=(
            DocumentElement(
                element_id="t1",
                kind="table",
                content=table_content,
                order=0,
                page_number=7,
                heading_path=("财务报表",),
                locator={"kind": "pdf", "page_number": 7, "rects": [[0, 0, 1, 1]]},
            ),
        ),
    )
    metadata = DocumentMetadata(source="年报.txt", content_hash="d" * 64)

    chunks = build_chunks(document, metadata, chunk_size=500, chunk_overlap=50)

    assert len(chunks) > 1
    collected_rows: list[str] = []
    for chunk in chunks:
        lines = chunk.content.splitlines()
        # 每个片段都以列名行 + 分隔行开头，脱离上下文也能读懂。
        assert lines[0] == header
        assert lines[1] == separator
        assert len(chunk.content) <= 500
        collected_rows.extend(lines[2:])
    # 行数据一条不丢、不重复。
    assert collected_rows == rows
    assert all(chunk.locator["rects"] == [[0, 0, 1, 1]] for chunk in chunks)


def test_short_table_stays_intact():
    table_content = "| 项目 | 金额 |\n|---|---|\n| 收入 | 100 |"
    document = ParsedDocument(
        source_name="年报.txt",
        parser_name="docling",
        parser_version="2",
        pages=(),
        elements=(
            DocumentElement(
                element_id="t1",
                kind="table",
                content=table_content,
                order=0,
                page_number=3,
                heading_path=("摘要",),
                locator={"kind": "pdf", "page_number": 3},
            ),
        ),
    )
    metadata = DocumentMetadata(source="年报.txt", content_hash="e" * 64)

    chunks = build_chunks(document, metadata, chunk_size=500, chunk_overlap=50)

    assert len(chunks) == 1
    assert chunks[0].content == table_content


def test_canonical_and_layout_artifacts_are_written_under_document_directory(tmp_path):
    path = tmp_path / "食品标准.txt"
    path.write_text("第1章 总则\n\n食品安全要求。", encoding="utf-8")
    document = parse_structured_document(path)

    canonical_path, layout_path = persist_artifacts(document, tmp_path / "uploads", "doc-1")
    canonical = (tmp_path / "uploads" / canonical_path).read_text(encoding="utf-8")
    layout = json.loads((tmp_path / "uploads" / layout_path).read_text(encoding="utf-8"))

    assert render_canonical_markdown(document) == canonical
    assert "# 第1章 总则" in canonical
    assert layout["parser"]["name"] == "plain_text"
    assert layout["elements"][1]["locator"]["start_line"] == 3
