"""兼容入口：旧调用方仍可按页获取解析结果。"""

from __future__ import annotations

from pathlib import Path

from src.ingestion.document_model import ParsedBlock, ParsedPage
from src.ingestion.structured_parser import (
    DEFAULT_PARSER_REGISTRY,
    DocumentParser,
    ParserRegistry,
    parse_structured_document,
)


def parse_document(path: str | Path) -> list[ParsedPage]:
    """兼容旧调用方：仍按页返回 PDF/TXT 文本结果。"""

    return list(parse_structured_document(path).pages)


__all__ = [
    "DEFAULT_PARSER_REGISTRY",
    "DocumentParser",
    "ParsedBlock",
    "ParsedPage",
    "ParserRegistry",
    "parse_document",
    "parse_structured_document",
]
