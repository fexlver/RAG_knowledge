"""基于 Docling 的 PDF 解析器适配层。

Docling 提供版面识别与表格结构重建，输出带页码和坐标的结构化元素；
本模块把它适配为统一的 DocumentParser 协议，与 PyMuPDF 管线并行可选。

两个已验证的工程约束：
- docling-parse 的 C++ 层在 Windows 下无法加载含中文的路径，必须通过
  DocumentStream 以字节流方式转换（与未来 MinIO 按字节取件的架构一致）；
- CPU 上版面模型 + TableFormer 约需 4.4 秒/页（表格密集年报实测），
  适合离线入库，不适合在线请求路径。
"""

from __future__ import annotations

import importlib.metadata
import io
from pathlib import Path

from src.ingestion.document_model import (
    DocumentElement,
    ElementKind,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
)
from src.ingestion.structured_parser import _element_id, _heading_level

# Docling 标签到统一元素类型的映射；未列出的标签按段落处理。
_LABEL_KINDS: dict[str, ElementKind] = {
    "title": "heading",
    "section_header": "heading",
    "paragraph": "paragraph",
    "text": "paragraph",
    "list_item": "paragraph",
    "footnote": "paragraph",
    "checkbox_selected": "paragraph",
    "checkbox_unselected": "paragraph",
    "document_index": "paragraph",
    "caption": "caption",
    "table": "table",
    "picture": "image",
}


def _item_content(document, item) -> str:
    """提取元素文本；表格导出为 Markdown 保留行列结构。"""

    if getattr(item, "label", None) is not None and item.label.value == "table":
        try:
            return item.export_to_markdown(document).strip()
        except Exception:  # noqa: BLE001 - 单个表格导出失败不应中断整份文档
            return ""
    return (getattr(item, "text", "") or "").strip()


def _normalized_rect(bbox, page_size: tuple[float, float]) -> tuple[float, float, float, float] | None:
    """把 Docling 的 PDF 坐标转为前端使用的左上原点归一化矩形。"""

    width, height = page_size
    if width <= 0 or height <= 0:
        return None
    origin = getattr(bbox, "coord_origin", None)
    bottom_left = getattr(origin, "value", str(origin)) == "BOTTOMLEFT"
    if bottom_left:
        # 底左原点下顶边坐标 t 大于底边 b，翻转成顶左原点的 top/bottom。
        top, bottom = height - bbox.t, height - bbox.b
    else:
        top, bottom = bbox.t, bbox.b
    return (
        max(0.0, bbox.l / width),
        max(0.0, top / height),
        min(1.0, bbox.r / width),
        min(1.0, bottom / height),
    )


def _page_sizes(document) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    for number, page in (getattr(document, "pages", None) or {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            sizes[number] = (float(size.width), float(size.height))
    return sizes


class DoclingParser:
    """Docling 标准管线解析器：版面 + 表格结构 + 可选 RapidOCR。"""

    name = "docling"

    def __init__(self, do_ocr: bool = True, do_table_structure: bool = True):
        self.do_ocr = do_ocr
        self.do_table_structure = do_table_structure
        self.version = importlib.metadata.version("docling")
        self._converter = None

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def _build_converter(self):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            RapidOcrOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        # 数字原生文档上 OCR 增量实测约 0.9%，常开以兜住混入的扫描页。
        options.do_ocr = self.do_ocr
        if self.do_ocr:
            options.ocr_options = RapidOcrOptions()
        options.do_table_structure = self.do_table_structure
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    def parse(self, path: Path) -> ParsedDocument:
        from docling.datamodel.base_models import DocumentStream

        if self._converter is None:
            self._converter = self._build_converter()
        # 用 ASCII 名做格式探测，绕开原生层对非 ASCII 文件名的处理；
        # 真实文件名保留在 ParsedDocument.source_name 中。
        stream = DocumentStream(
            name=f"source{path.suffix.lower() or '.pdf'}",
            stream=io.BytesIO(path.read_bytes()),
        )
        result = self._converter.convert(stream)
        return _parsed_document_from_docling(result.document, path.name, self.version)


def _parsed_document_from_docling(
    document, source_name: str, parser_version: str
) -> ParsedDocument:
    """把 DoclingDocument 映射为统一中间表示，保留溯源坐标。"""

    sizes = _page_sizes(document)
    elements: list[DocumentElement] = []
    page_blocks: dict[int, list[tuple[str, tuple[float, float, float, float] | None]]] = {}
    heading_stack: list[tuple[int, str]] = []

    for item, _depth in document.iterate_items():
        label = getattr(getattr(item, "label", None), "value", "") or ""
        kind = _LABEL_KINDS.get(label, "paragraph")
        content = _item_content(document, item)
        if not content:
            # 图片元素当前没有文本投影，待 VLM 增强阶段再入库。
            continue

        prov = (getattr(item, "prov", None) or [None])[0]
        page_number = getattr(prov, "page_no", None) if prov else None
        rect = None
        if prov is not None and page_number in sizes:
            rect = _normalized_rect(prov.bbox, sizes[page_number])

        if kind == "heading":
            # Docling 不提供标题级别，复用显式编号的启发式规则维持层级路径。
            level = _heading_level(content) or 2
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, content))
            heading_path = tuple(text for _level, text in heading_stack)
            heading_level = level
        else:
            heading_path = tuple(text for _level, text in heading_stack)
            heading_level = None

        locator: dict[str, object] = {
            "kind": "pdf",
            "page_number": page_number,
            "anchor_text": content[:240],
        }
        if rect is not None:
            locator["rects"] = [list(rect)]
        order = len(elements)
        elements.append(
            DocumentElement(
                element_id=_element_id(page_number, order, content),
                kind=kind,
                content=content,
                order=order,
                page_number=page_number,
                heading_path=heading_path,
                heading_level=heading_level,
                locator=locator,
            )
        )
        if page_number is not None:
            page_blocks.setdefault(page_number, []).append((content, rect))

    pages: list[ParsedPage] = []
    for number in sorted(page_blocks):
        parts: list[str] = []
        blocks: list[ParsedBlock] = []
        cursor = 0
        for text, rect in page_blocks[number]:
            if parts:
                parts.append("\n\n")
                cursor += 2
            start = cursor
            parts.append(text)
            cursor += len(text)
            blocks.append(ParsedBlock(text, start, cursor, rect=rect))
        content = "".join(parts).strip()
        if content:
            pages.append(ParsedPage(content, number, tuple(blocks)))

    return ParsedDocument(
        source_name=source_name,
        parser_name=DoclingParser.name,
        parser_version=parser_version,
        pages=tuple(pages),
        elements=tuple(elements),
    )
