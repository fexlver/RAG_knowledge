"""结构化文档的派生产物生成与安全落盘。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.ingestion.document_model import ParsedDocument


def render_canonical_markdown(document: ParsedDocument) -> str:
    """把结构化元素投影为 Markdown，作为检索和人工校验的统一文本视图。"""

    lines: list[str] = []
    last_page: int | None = None
    for element in document.elements:
        if element.page_number != last_page and element.page_number is not None:
            lines.append(f"<!-- page: {element.page_number} -->")
            last_page = element.page_number
        if element.kind == "heading":
            level = min(max(element.heading_level or 1, 1), 6)
            lines.append(f"{'#' * level} {element.content}")
        else:
            lines.append(element.content)
    return "\n\n".join(line for line in lines if line.strip()).strip() + "\n"


def persist_artifacts(
    document: ParsedDocument, upload_root: Path, doc_id: str
) -> tuple[str, str]:
    """原子写入 canonical.md 与 layout.json，并返回受控相对路径。"""

    root = upload_root.resolve()
    target_dir = (root / doc_id).resolve()
    if root not in target_dir.parents:
        raise ValueError("文档派生产物路径越界。")
    target_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = target_dir / "canonical.md"
    layout_path = target_dir / "layout.json"
    _atomic_write_text(canonical_path, render_canonical_markdown(document))
    _atomic_write_text(
        layout_path,
        json.dumps(document.layout_dict(), ensure_ascii=False, indent=2),
    )
    return (
        canonical_path.relative_to(root).as_posix(),
        layout_path.relative_to(root).as_posix(),
    )


def _atomic_write_text(path: Path, content: str) -> None:
    """避免服务中断时留下半写入的可见产物。"""

    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
