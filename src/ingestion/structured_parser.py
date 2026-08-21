"""可注册的 PDF/TXT 文档解析器，并保留可追溯的结构信息。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

import pymupdf as fitz

from src.ingestion.document_model import (
    DocumentElement,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
)

_CHINESE_HEADING = re.compile(r"^第[一二三四五六七八九十百千0-9]+([章节条])")
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,5})[.、\s]+.+")
_LIST_HEADING = re.compile(r"^[一二三四五六七八九十]+、.+")


class DocumentParser(Protocol):
    """统一解析器协议；Docling、OCR、VLM 适配器可在此协议下扩展。"""

    name: str
    version: str

    def supports(self, path: Path) -> bool: ...

    def parse(self, path: Path) -> ParsedDocument: ...


def _normalize_text(text: str) -> str:
    lines = [
        " ".join(line.split()) for line in text.replace("\u3000", " ").splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def _heading_level(text: str) -> int | None:
    """使用保守规则识别显式章节，避免把普通短句误当标题。"""

    candidate = text.strip()
    chinese = _CHINESE_HEADING.match(candidate)
    if chinese:
        return {"章": 1, "节": 2, "条": 3}[chinese.group(1)]
    numbered = _NUMBERED_HEADING.match(candidate)
    if numbered and len(candidate) <= 120:
        return min(numbered.group(1).count(".") + 1, 6)
    if _LIST_HEADING.match(candidate) and len(candidate) <= 120:
        return 2
    return None


def _element_id(page_number: int | None, order: int, content: str) -> str:
    raw = f"{page_number or 0}:{order}:{content}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _build_elements(pages: list[ParsedPage]) -> tuple[DocumentElement, ...]:
    """从页面文本块构建章节路径，作为后续语义切块的边界。"""

    elements: list[DocumentElement] = []
    headings: list[str] = []
    for page in pages:
        for block in page.blocks:
            level = _heading_level(block.content)
            if level:
                headings = headings[: level - 1]
                headings.append(block.content)
                kind = "heading"
                heading_path = tuple(headings)
            else:
                kind = "paragraph"
                heading_path = tuple(headings)
            locator: dict[str, object] = {
                "kind": "pdf" if page.page_number is not None else "text",
                "page_number": page.page_number,
                "anchor_text": block.content[:240],
            }
            if block.rect:
                locator["rects"] = [list(block.rect)]
            if block.start_line is not None:
                locator["start_line"] = block.start_line
                locator["end_line"] = block.end_line
                locator["start_char"] = block.start
                locator["end_char"] = block.end
            order = len(elements)
            elements.append(
                DocumentElement(
                    element_id=_element_id(page.page_number, order, block.content),
                    kind=kind,
                    content=block.content,
                    order=order,
                    page_number=page.page_number,
                    heading_path=heading_path,
                    heading_level=level,
                    locator=locator,
                )
            )
    return tuple(elements)


def _document_from_pages(
    path: Path, pages: list[ParsedPage], parser_name: str, parser_version: str
) -> ParsedDocument:
    return ParsedDocument(
        source_name=path.name,
        parser_name=parser_name,
        parser_version=parser_version,
        pages=tuple(pages),
        elements=_build_elements(pages),
    )


class PdfTextParser:
    """基于 PyMuPDF 的原生文本与坐标解析器。"""

    name = "pymupdf_text"
    version = "1"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path) -> ParsedDocument:
        pages: list[ParsedPage] = []
        with fitz.open(path) as document:
            for page_number, page in enumerate(document, start=1):
                page_rect = page.rect
                parts: list[str] = []
                blocks: list[ParsedBlock] = []
                cursor = 0
                for raw_block in page.get_text("blocks", sort=True):
                    block_text = _normalize_text(str(raw_block[4]))
                    if not block_text:
                        continue
                    if parts:
                        parts.append("\n\n")
                        cursor += 2
                    start = cursor
                    parts.append(block_text)
                    cursor += len(block_text)
                    x0, y0, x1, y1 = (float(value) for value in raw_block[:4])
                    rect = (
                        x0 / page_rect.width,
                        y0 / page_rect.height,
                        x1 / page_rect.width,
                        y1 / page_rect.height,
                    )
                    blocks.append(ParsedBlock(block_text, start, cursor, rect=rect))
                content = "".join(parts).strip()
                if content:
                    pages.append(ParsedPage(content, page_number, tuple(blocks)))
        return _document_from_pages(path, pages, self.name, self.version)


class TextParser:
    """TXT 段落解析器，保留字符范围和原始行号。"""

    name = "plain_text"
    version = "1"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".txt"

    def parse(self, path: Path) -> ParsedDocument:
        raw_text = path.read_text(encoding="utf-8")
        normalized_lines = [
            " ".join(line.replace("\u3000", " ").split())
            for line in raw_text.splitlines()
        ]
        parts: list[str] = []
        blocks: list[ParsedBlock] = []
        cursor = 0
        paragraph: list[str] = []
        paragraph_start_line = 1

        def flush(end_line: int) -> None:
            nonlocal cursor, paragraph
            text = "\n".join(line for line in paragraph if line).strip()
            paragraph = []
            if not text:
                return
            if parts:
                parts.append("\n\n")
                cursor += 2
            start = cursor
            parts.append(text)
            cursor += len(text)
            blocks.append(
                ParsedBlock(
                    text,
                    start,
                    cursor,
                    start_line=paragraph_start_line,
                    end_line=end_line,
                )
            )

        for line_number, line in enumerate(normalized_lines, start=1):
            if line:
                if not paragraph:
                    paragraph_start_line = line_number
                paragraph.append(line)
            else:
                flush(line_number - 1)
        flush(len(normalized_lines))
        content = "".join(parts).strip()
        pages = [ParsedPage(content, None, tuple(blocks))] if content else []
        return _document_from_pages(path, pages, self.name, self.version)


class ParserRegistry:
    """按文件类型选择解析器，支持以后以插件形式增加 Docling/OCR/VLM。"""

    def __init__(self, parsers: tuple[DocumentParser, ...] | None = None):
        self._parsers = list(parsers or (PdfTextParser(), TextParser()))

    def register(self, parser: DocumentParser) -> None:
        self._parsers.insert(0, parser)

    def _load_docling(self) -> DocumentParser:
        """Docling 依赖较重，仅在显式选择时才导入并注册。"""

        from src.ingestion.docling_parser import DoclingParser

        parser = DoclingParser()
        self.register(parser)
        return parser

    def parse(self, path: str | Path, parser_name: str | None = None) -> ParsedDocument:
        file_path = Path(path)
        if parser_name:
            parser = next(
                (item for item in self._parsers if item.name == parser_name), None
            )
            if parser is None and parser_name == "docling":
                parser = self._load_docling()
            if parser is None:
                available = ", ".join(item.name for item in self._parsers)
                raise ValueError(f"未知解析器 {parser_name}，可用解析器: {available}")
            if not parser.supports(file_path):
                raise ValueError(
                    f"解析器 {parser_name} 不支持 {file_path.suffix.lower()} 文件。"
                )
            return parser.parse(file_path)
        parser = next(
            (item for item in self._parsers if item.supports(file_path)), None
        )
        if not parser:
            raise ValueError(f"暂不支持的文档类型: {file_path.suffix.lower()}")
        return parser.parse(file_path)


DEFAULT_PARSER_REGISTRY = ParserRegistry()


def parse_structured_document(
    path: str | Path, parser_name: str | None = None
) -> ParsedDocument:
    """解析并返回结构化中间表示；新代码应优先使用此函数。"""

    return DEFAULT_PARSER_REGISTRY.parse(path, parser_name=parser_name)

