import fitz

from src.domain.models import DocumentMetadata
from src.ingestion.parser import parse_document
from src.ingestion.splitter import build_chunks


def test_txt_chunk_keeps_line_locator(tmp_path):
    path = tmp_path / "公告.txt"
    path.write_text("标题\n\n第一段食品安全内容。\n仍属于第一段。\n\n第二段。", encoding="utf-8")
    pages = parse_document(path)
    metadata = DocumentMetadata(source=path.name, content_hash="a" * 64)
    chunks = build_chunks(pages, metadata, chunk_size=40, chunk_overlap=5)

    assert chunks[0].locator["kind"] == "text"
    assert chunks[0].locator["start_line"] is not None
    assert chunks[0].locator["anchor_text"]


def test_pdf_chunk_keeps_normalized_highlight_rectangles(tmp_path):
    path = tmp_path / "标准.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 90), "Food safety standard paragraph", fontsize=12)
    document.save(path)
    document.close()

    pages = parse_document(path)
    metadata = DocumentMetadata(source=path.name, content_hash="b" * 64)
    chunks = build_chunks(pages, metadata, chunk_size=100, chunk_overlap=10)

    rect = chunks[0].locator["rects"][0]
    assert chunks[0].locator["kind"] == "pdf"
    assert chunks[0].page_number == 1
    assert all(0 <= value <= 1 for value in rect)
