"""PDF/TXT 文档解析，并保留原文定位信息。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """页面中的一个可定位文本块。"""

    content: str
    start: int
    end: int
    rect: tuple[float, float, float, float] | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedPage:
    content: str
    page_number: int | None
    blocks: tuple[ParsedBlock, ...] = ()


def _normalize_text(text: str) -> str:
    """清理常见空白字符，同时保留段落结构。"""

    lines = [
        " ".join(line.split()) for line in text.replace("\u3000", " ").splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def _parse_pdf(file_path: Path) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    with fitz.open(file_path) as document:
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
                # 保存归一化坐标，避免前端渲染比例变化后定位失效。
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
    return pages


def _parse_txt(file_path: Path) -> list[ParsedPage]:
    raw_text = file_path.read_text(encoding="utf-8")
    normalized_lines = [
        " ".join(line.replace("\u3000", " ").split()) for line in raw_text.splitlines()
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
    return [ParsedPage(content, None, tuple(blocks))] if content else []


def parse_document(path: str | Path) -> list[ParsedPage]:
    """解析 PDF/TXT；PDF 按页返回，TXT 保留段落行号。"""

    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(file_path)
    if suffix == ".txt":
        return _parse_txt(file_path)
    raise ValueError(f"暂不支持的文档类型: {suffix}")
