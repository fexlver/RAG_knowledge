"""保留页码、章节和标准信息的文本切分。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.domain import DocumentChunk, DocumentMetadata
from src.ingestion.parser import ParsedPage

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


def build_chunks(
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
        for content in splitter.split_text(page.content):
            normalized = content.strip()
            if not normalized:
                continue
            raw_id = f"{doc_id}:{chunk_index}:{normalized}".encode()
            chunk_id = hashlib.sha256(raw_id).hexdigest()[:32]
            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    content=normalized,
                    chunk_index=chunk_index,
                    page_number=page.page_number,
                    section=_find_section(normalized),
                    metadata=metadata,
                )
            )
            chunk_index += 1
    return chunks
