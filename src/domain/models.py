"""食品安全知识库领域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class DocumentMetadata:
    """用于法规、标准版本过滤与引用展示的文档元数据。"""

    source: str
    title: str = ""
    document_type: str = "其他"
    standard_code: str = ""
    issuer: str = ""
    region: str = "全国"
    publish_date: str = ""
    effective_date: str = ""
    expiry_date: str = ""
    validity_status: str = "未知"
    version: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DocumentChunk:
    """进入稠密索引和关键词索引的统一文本单元。"""

    chunk_id: str
    doc_id: str
    content: str
    chunk_index: int
    page_number: int | None
    section: str
    metadata: DocumentMetadata

    def vector_metadata(self) -> dict[str, Any]:
        values = self.metadata.to_dict()
        values.update(
            {
                "chunk_id": self.chunk_id,
                "doc_id": self.doc_id,
                "chunk_index": self.chunk_index,
                "page_number": self.page_number or 0,
                "section": self.section,
            }
        )
        return values


@dataclass(slots=True)
class RetrievedChunk:
    """携带多路检索分数的召回结果。"""

    chunk: DocumentChunk
    dense_score: float | None = None
    lexical_score: float | None = None
    fusion_score: float = 0.0
    rerank_score: float | None = None
    routes: set[str] = field(default_factory=set)

    @property
    def final_score(self) -> float:
        if self.rerank_score is not None:
            return self.rerank_score
        return self.fusion_score


@dataclass(frozen=True, slots=True)
class Citation:
    label: int
    source: str
    standard_code: str
    page_number: int | None
    section: str
    excerpt: str

    @property
    def display_name(self) -> str:
        code = f" {self.standard_code}" if self.standard_code else ""
        page = f"，第{self.page_number}页" if self.page_number else ""
        section = f"，{self.section}" if self.section else ""
        return f"[{self.label}] {self.source}{code}{page}{section}"


@dataclass(slots=True)
class AnswerResult:
    answer: str
    citations: list[Citation]
    trace: list[str]
    refused: bool = False
