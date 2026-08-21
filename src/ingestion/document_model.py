"""文档理解阶段的结构化中间表示。

解析器只负责把原始文件转换为本模块中的对象；切块、索引和预览都基于该对象，
避免把某个 PDF 解析库的私有格式渗透到后续链路。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ElementKind = Literal["heading", "paragraph", "table", "image", "caption", "code"]


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """页面中的一个可定位文本块，保留旧切块逻辑所需字段。"""

    content: str
    start: int
    end: int
    rect: tuple[float, float, float, float] | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """按页聚合的纯文本表示，兼容现有 PDF/TXT 解析调用方。"""

    content: str
    page_number: int | None
    blocks: tuple[ParsedBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentElement:
    """可被检索和追溯的最小结构单元。

    ``locator`` 保留页码、坐标或文本行号；未来表格、图片和音频解析器只需补充
    自己的定位字段，不需要改动切块和预览的公共协议。
    """

    element_id: str
    kind: ElementKind
    content: str
    order: int
    page_number: int | None
    heading_path: tuple[str, ...] = ()
    heading_level: int | None = None
    locator: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["heading_path"] = list(self.heading_path)
        return value


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """统一文档中间表示及其可审计的解析器身份。"""

    source_name: str
    parser_name: str
    parser_version: str
    pages: tuple[ParsedPage, ...]
    elements: tuple[DocumentElement, ...]

    @property
    def content(self) -> str:
        return "\n\n".join(page.content for page in self.pages if page.content).strip()

    def layout_dict(self) -> dict[str, Any]:
        """生成前端证据窗口可用的轻量布局数据。"""

        return {
            "source_name": self.source_name,
            "parser": {"name": self.parser_name, "version": self.parser_version},
            "elements": [element.to_dict() for element in self.elements],
        }
