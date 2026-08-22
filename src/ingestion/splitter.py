"""保留页码、章节和标准信息的文本切分。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.domain import DocumentChunk, DocumentMetadata
from src.ingestion.document_model import DocumentElement, ParsedDocument, ParsedPage

SECTION_PATTERN = re.compile(
    r"^(第[一二三四五六七八九十百0-9]+[章节条]|\d+(?:\.\d+)+\s+.+|[一二三四五六七八九十]+、.+)$"
)


def calculate_file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_section(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if SECTION_PATTERN.match(stripped):
            return stripped[:120]
    return ""


def _build_locator(page: ParsedPage, content: str, search_from: int) -> tuple[dict, int]:
    """把切分后的文本映射回页面文本块，生成前端可消费的定位信息。"""

    start = page.content.find(content, max(0, search_from))
    if start < 0:
        start = page.content.find(content)
    start = max(start, 0)
    end = start + len(content)
    matched = [block for block in page.blocks if block.end > start and block.start < end]
    locator: dict = {
        "kind": "pdf" if page.page_number is not None else "text",
        "page_number": page.page_number,
        "anchor_text": content[:240],
    }
    if page.page_number is not None:
        locator["rects"] = [list(block.rect) for block in matched if block.rect]
    else:
        locator.update(
            {
                "start_char": start,
                "end_char": end,
                "start_line": min(
                    (block.start_line for block in matched if block.start_line),
                    default=None,
                ),
                "end_line": max(
                    (block.end_line for block in matched if block.end_line),
                    default=None,
                ),
            }
        )
    return locator, max(start + 1, end - 32)


def _build_legacy_chunks(
    pages: list[ParsedPage],
    metadata: DocumentMetadata,
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    """逐页切分，避免一个Chunk跨页导致引用页码不准确。"""

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n第", "\n\n", "\n", "。", "；", "！", "？", "，", " "],
    )
    doc_id = metadata.content_hash[:32]
    chunks: list[DocumentChunk] = []
    chunk_index = 0
    for page in pages:
        search_from = 0
        for content in splitter.split_text(page.content):
            normalized = content.strip()
            if not normalized:
                continue
            raw_id = f"{doc_id}:{chunk_index}:{normalized}".encode()
            chunk_id = hashlib.sha256(raw_id).hexdigest()[:32]
            locator, search_from = _build_locator(page, normalized, search_from)
            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    content=normalized,
                    chunk_index=chunk_index,
                    page_number=page.page_number,
                    section=_find_section(normalized),
                    metadata=metadata,
                    locator=locator,
                )
            )
            chunk_index += 1
    return chunks


def _structured_locator(
    elements: list[DocumentElement], content: str
) -> tuple[dict, int | None]:
    """把结构单元的定位信息合并为一个检索证据窗口。"""

    first = elements[0]
    locator = dict(first.locator)
    locator.update(
        {
            "anchor_text": content[:240],
            "element_ids": [element.element_id for element in elements],
            "heading_path": list(first.heading_path),
        }
    )
    rects = [
        rect
        for element in elements
        for rect in element.locator.get("rects", [])
    ]
    if rects:
        locator["rects"] = rects
    line_starts = [
        element.locator.get("start_line")
        for element in elements
        if element.locator.get("start_line") is not None
    ]
    line_ends = [
        element.locator.get("end_line")
        for element in elements
        if element.locator.get("end_line") is not None
    ]
    if line_starts:
        locator["start_line"] = min(line_starts)
        locator["end_line"] = max(line_ends)
    return locator, first.page_number


def _append_structured_chunk(
    chunks: list[DocumentChunk],
    metadata: DocumentMetadata,
    elements: list[DocumentElement],
) -> None:
    """将同一章节、同一页面内的连续段落写成一个统一索引单元。"""

    content = "\n\n".join(element.content.strip() for element in elements).strip()
    if not content:
        return
    chunk_index = len(chunks)
    doc_id = metadata.content_hash[:32]
    raw_id = f"{doc_id}:{chunk_index}:{content}".encode()
    chunk_id = hashlib.sha256(raw_id).hexdigest()[:32]
    locator, page_number = _structured_locator(elements, content)
    heading_path = elements[0].heading_path
    chunks.append(
        DocumentChunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            content=content,
            chunk_index=chunk_index,
            page_number=page_number,
            section=" / ".join(heading_path) or _find_section(content),
            metadata=metadata,
            locator=locator,
        )
    )


TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _split_long_table(element: DocumentElement, chunk_size: int) -> list[DocumentElement]:
    """长表按行边界拆分，每个片段重复表头，保证片段脱离上下文也可读。"""

    lines = element.content.splitlines()
    header: list[str] = []
    body_start = 0
    for index, line in enumerate(lines):
        if TABLE_SEPARATOR_PATTERN.match(line):
            header = lines[: index + 1]
            body_start = index + 1
            break
    if not header or body_start >= len(lines):
        # 不是标准 markdown 表格时退回通用递归切分。
        return []
    header_text = "\n".join(header)
    # 预算扣除表头，保证“表头 + 数据行”整体不超过 chunk_size。
    budget = max(1, chunk_size - len(header_text) - 1)
    fragments: list[str] = []
    current: list[str] = []
    current_size = 0
    for line in lines[body_start:]:
        line_size = len(line) + 1
        if current and current_size + line_size > budget:
            fragments.append("\n".join(header + current))
            current = []
            current_size = 0
        current.append(line)
        current_size += line_size
    if current:
        fragments.append("\n".join(header + current))
    return [
        DocumentElement(
            element_id=f"{element.element_id}:{index}",
            kind=element.kind,
            content=fragment,
            order=element.order,
            page_number=element.page_number,
            heading_path=element.heading_path,
            heading_level=element.heading_level,
            locator=element.locator,
        )
        for index, fragment in enumerate(fragments)
        if fragment.strip()
    ]


def _split_long_element(
    element: DocumentElement, chunk_size: int, chunk_overlap: int
) -> list[DocumentElement]:
    """仅在单个段落超长时降级为递归切分，不破坏正常段落与章节。"""

    if element.kind == "table":
        table_fragments = _split_long_table(element, chunk_size)
        if table_fragments:
            return table_fragments
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n", "。", "；", "！", "？", "，", " "],
    )
    fragments = splitter.split_text(element.content)
    return [
        DocumentElement(
            element_id=f"{element.element_id}:{index}",
            kind=element.kind,
            content=fragment,
            order=element.order,
            page_number=element.page_number,
            heading_path=element.heading_path,
            heading_level=element.heading_level,
            locator=element.locator,
        )
        for index, fragment in enumerate(fragments)
        if fragment.strip()
    ]


def _build_structured_chunks(
    document: ParsedDocument,
    metadata: DocumentMetadata,
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    """按章节和连续段落聚合，避免固定窗口切断一个完整语义单元。"""

    chunks: list[DocumentChunk] = []
    pending: list[DocumentElement] = []
    # 段落在达到此大小后才会因长度而截断，较短段落优先保留完整上下文。
    preferred_min_size = max(1, int(chunk_size * 0.7))

    def flush() -> None:
        nonlocal pending
        _append_structured_chunk(chunks, metadata, pending)
        pending = []

    for element in document.elements:
        if not element.content.strip():
            continue
        if element.kind == "heading":
            # 章节改变是强边界；标题本身通过 section 元数据附着到后续段落。
            if pending:
                flush()
            continue
        if pending and (
            element.page_number != pending[0].page_number
            or element.heading_path != pending[0].heading_path
        ):
            flush()
        candidates = (
            _split_long_element(element, chunk_size, chunk_overlap)
            if len(element.content) > chunk_size
            else [element]
        )
        for candidate in candidates:
            pending_size = sum(len(item.content) for item in pending)
            prospective_size = pending_size + len(candidate.content) + (2 if pending else 0)
            if pending and prospective_size > chunk_size and pending_size >= preferred_min_size:
                flush()
            pending.append(candidate)
    if pending:
        flush()
    return chunks


def build_chunks(
    source: ParsedDocument | list[ParsedPage],
    metadata: DocumentMetadata,
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    """结构化文档采用章节优先切块；旧页面列表继续走兼容路径。"""

    if isinstance(source, ParsedDocument):
        return _build_structured_chunks(source, metadata, chunk_size, chunk_overlap)
    return _build_legacy_chunks(source, metadata, chunk_size, chunk_overlap)
