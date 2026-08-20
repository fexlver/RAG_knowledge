"""文档入库用例。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.config.settings import Settings
from src.domain.models import DocumentChunk
from src.ingestion.metadata import extract_metadata
from src.ingestion.parser import parse_document
from src.ingestion.splitter import build_chunks, calculate_file_hash
from src.storage.sqlite_store import SQLiteStore


class EmbeddingModel(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class VectorWriter(Protocol):
    def add(
        self, chunks: list[DocumentChunk], embeddings: list[list[float]]
    ) -> None: ...

    def delete_document(self, doc_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class IngestionResult:
    file_name: str
    status: str
    chunk_count: int
    detail: str


class DocumentIngestionService:
    """协调解析、去重、向量化和双索引写入，并在失败时回滚。"""

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        vector_store: VectorWriter,
        model: EmbeddingModel,
        settings: Settings,
    ):
        self.sqlite_store = sqlite_store
        self.vector_store = vector_store
        self.model = model
        self.settings = settings

    def ingest(self, path: str | Path, duplicate_mode: str = "skip") -> IngestionResult:
        file_path = Path(path)
        content_hash = calculate_file_hash(file_path)
        existing_hash = self.sqlite_store.find_document_by_hash(content_hash)
        if existing_hash:
            detail = "内容指纹已存在，避免重复向量化。"
            self.sqlite_store.log("跳过重复文档", file_path.name, detail)
            return IngestionResult(file_path.name, "skipped", 0, detail)

        old_document = self.sqlite_store.find_document_by_name(file_path.name)
        if old_document and duplicate_mode == "skip":
            detail = "存在同名但内容不同的文档，请选择覆盖或重命名后上传。"
            self.sqlite_store.log("跳过同名文档", file_path.name, detail)
            return IngestionResult(file_path.name, "skipped", 0, detail)
        if duplicate_mode not in {"skip", "overwrite"}:
            raise ValueError("duplicate_mode 仅支持 skip 或 overwrite。")

        pages = parse_document(file_path)
        if not pages:
            raise ValueError(f"文档没有可提取文本：{file_path.name}")
        full_text = "\n".join(page.content for page in pages)
        metadata = extract_metadata(file_path.name, full_text, content_hash)
        chunks = build_chunks(
            pages, metadata, self.settings.chunk_size, self.settings.chunk_overlap
        )
        embeddings = self.model.embed_documents([chunk.content for chunk in chunks])
        doc_id = content_hash[:32]
        try:
            self.vector_store.add(chunks, embeddings)
            self.sqlite_store.save_document(doc_id, metadata, chunks)
        except Exception:
            # 两个存储不支持分布式事务，尽最大努力清理本次写入。
            try:
                self.vector_store.delete_document(doc_id)
            finally:
                self.sqlite_store.delete_document(doc_id)
            raise

        if old_document and duplicate_mode == "overwrite":
            old_doc_id = old_document["doc_id"]
            self.vector_store.delete_document(old_doc_id)
            self.sqlite_store.delete_document(old_doc_id)

        detail = f"已建立 {len(chunks)} 个带页码与章节信息的文本块。"
        self.sqlite_store.log("文档入库", file_path.name, detail)
        return IngestionResult(file_path.name, "success", len(chunks), detail)

    def delete(self, doc_id: str) -> None:
        self.vector_store.delete_document(doc_id)
        self.sqlite_store.delete_document(doc_id)
        self.sqlite_store.log("删除文档", doc_id, "已同步删除稠密索引和关键词索引。")
