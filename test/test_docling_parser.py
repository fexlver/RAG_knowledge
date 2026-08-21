"""DoclingParser 适配层测试。

依赖 docling 与已下载的版面模型；未安装 docling 的环境自动跳过。
用 do_ocr=False 避免单元测试触发 RapidOCR 模型下载。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("docling", reason="docling 未安装")


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory) -> Path:
    """生成一页含标题与正文的 PDF 样张。"""

    import pymupdf

    path = tmp_path_factory.mktemp("docling") / "sample.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Section One Overview", fontsize=16)
        page.insert_text(
            (72, 110),
            "This paragraph explains the scope of the document in plain text.",
        )
        page.insert_text(
            (72, 130),
            "A second paragraph provides additional context for retrieval.",
        )
        document.save(path)
    return path


@pytest.fixture(scope="module")
def parsed_doc(sample_pdf):
    from src.ingestion.docling_parser import DoclingParser

    return DoclingParser(do_ocr=False).parse(sample_pdf)


def test_parse_returns_parser_identity(parsed_doc):
    assert parsed_doc.parser_name == "docling"
    assert parsed_doc.parser_version
    assert parsed_doc.source_name == "sample.pdf"


def test_elements_cover_text_with_normalized_locator(parsed_doc):
    assert parsed_doc.elements, "至少应解析出一个结构元素"
    contents = "\n".join(element.content for element in parsed_doc.elements)
    assert "plain text" in contents
    assert "additional context" in contents
    for element in parsed_doc.elements:
        assert element.page_number == 1
        assert element.element_id
        rects = element.locator.get("rects", [])
        assert rects, "PDF 元素应带归一化高亮矩形"
        for rect in rects:
            assert len(rect) == 4
            assert all(0.0 <= value <= 1.0 for value in rect), rect
            assert rect[0] < rect[2] and rect[1] < rect[3]


def test_pages_keep_legacy_block_view(parsed_doc):
    assert parsed_doc.pages, "兼容视图应保留按页文本"
    page = parsed_doc.pages[0]
    assert page.page_number == 1
    assert page.blocks
    assert page.content.strip()


def test_headings_build_path_for_following_paragraphs(parsed_doc):
    headings = [e for e in parsed_doc.elements if e.kind == "heading"]
    paragraphs = [e for e in parsed_doc.elements if e.kind == "paragraph"]
    if headings and paragraphs:
        first_heading = headings[0].content
        after = next(
            (p for p in paragraphs if p.order > headings[0].order), None
        )
        if after is not None:
            assert first_heading in after.heading_path


def test_registry_resolves_by_name_and_rejects_unknown(sample_pdf, tmp_path):
    from src.ingestion.structured_parser import ParserRegistry

    registry = ParserRegistry()
    docling = registry._load_docling()
    assert docling.name == "docling"
    assert any(parser.name == "docling" for parser in registry._parsers)

    with pytest.raises(ValueError, match="未知解析器"):
        registry.parse(sample_pdf, parser_name="no_such_parser")

    text_file = tmp_path / "note.txt"
    text_file.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持"):
        registry.parse(text_file, parser_name="docling")
