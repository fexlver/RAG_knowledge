"""PDF/TXT文档解析。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True, slots=True)
class ParsedPage:
    content: str
    page_number: int | None


def _normalize_text(text: str) -> str:
    """清理常见空白字符，同时保留段落结构。"""

    lines = [
        " ".join(line.split()) for line in text.replace("\u3000", " ").splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def parse_document(path: str | Path) -> list[ParsedPage]:
    """解析PDF或TXT，PDF按页返回以便回答时精确引用。"""

    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(file_path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            content = _normalize_text(page.extract_text() or "")
            if content:
                pages.append(ParsedPage(content=content, page_number=index))
        return pages
    if suffix == ".txt":
        content = _normalize_text(file_path.read_text(encoding="utf-8"))
        return [ParsedPage(content=content, page_number=None)] if content else []
    raise ValueError(f"暂不支持的文档类型: {suffix}")
