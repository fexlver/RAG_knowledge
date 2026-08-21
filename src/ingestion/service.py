"""文档入库用例。"""

from __future__ import annotations

import mimetypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.config.settings import Settings
from src.domain.models import DocumentChunk
from src.ingestion.artifacts import persist_artifacts
from src.ingestion.metadata import extract_metadata
from src.ingestion.parser import parse_structured_document
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

    def _persist_original(self, source: Path, doc_id: str) -> tuple[Path, str]:
        """把上传临时文件复制到受控目录，返回文件路径和相对存储标识。"""

        upload_root = Path(self.settings.upload_dir).resolve()
        target_dir = (upload_root / doc_id).resolve()
        if upload_root not in target_dir.parents:
            raise ValueError("文档存储路径越界。")
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(source.name).name
        target = (target_dir / safe_name).resolve()
        if target.parent != target_dir:
            raise ValueError("文档文件名不安全。")
        temporary = target.with_suffix(target.suffix + ".uploading")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
        return target, target.relative_to(upload_root).as_posix()

    def _delete_stored_file(self, storage_path: str | None) -> None:
        """删除原文件及当前版本已知的结构化派生产物。"""

        if not storage_path:
            return
        upload_root = Path(self.settings.upload_dir).resolve()
        target = (upload_root / storage_path).resolve()
        if upload_root not in target.parents:
            raise ValueError("拒绝删除上传目录之外的文件。")
        if target.is_file():
            target.unlink()
        # 仅清理本版本固定命名的两个产物，避免影响未来扩展文件或其他数据。
        for artifact_name in ("canonical.md", "layout.json"):
            artifact = target.parent / artifact_name
            if artifact.is_file():
                artifact.unlink()
        if target.parent != upload_root and target.parent.is_dir():
            try:
                target.parent.rmdir()
            except OSError:
                pass

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

        parsed_document = parse_structured_document(file_path)
        if not parsed_document.pages:
            raise ValueError(f"文档没有可提取文本：{file_path.name}")
        full_text = parsed_document.content
        metadata = extract_metadata(file_path.name, full_text, content_hash)
        chunks = build_chunks(
            parsed_document,
            metadata,
            self.settings.chunk_size,
            self.settings.chunk_overlap,
        )
        if not chunks:
            raise ValueError(f"文档没有可索引的结构化文本：{file_path.name}")
        embeddings = self.model.embed_documents([chunk.content for chunk in chunks])
        doc_id = content_hash[:32]
        series_id = (
            (old_document.get("series_id") or old_document["doc_id"])
            if old_document
            else doc_id
        )
        version_number = (
            self.sqlite_store.next_document_version(series_id) if old_document else 1
        )
        stored_relative_path: str | None = None
        try:
            self.vector_store.add(chunks, embeddings)
            _, stored_relative_path = self._persist_original(file_path, doc_id)
            canonical_path, layout_path = persist_artifacts(
                parsed_document, Path(self.settings.upload_dir), doc_id
            )
            self.sqlite_store.save_document(
                doc_id,
                metadata,
                chunks,
                storage_path=stored_relative_path,
                mime_type=mimetypes.guess_type(file_path.name)[0],
                series_id=series_id,
                version_number=version_number,
                file_size=file_path.stat().st_size,
                parser_name=parsed_document.parser_name,
                parser_version=parsed_document.parser_version,
                canonical_path=canonical_path,
                layout_path=layout_path,
            )
        except Exception:
            # 两个存储不支持分布式事务，尽最大努力清理本次写入。
            try:
                self.vector_store.delete_document(doc_id)
            finally:
                self.sqlite_store.delete_document(doc_id)
                if stored_relative_path:
                    self._delete_stored_file(stored_relative_path)
            raise

        if old_document and duplicate_mode == "overwrite":
            old_doc_id = old_document["doc_id"]
            # 覆盖采用可回退的版本切换：旧原文和元数据保留，但不再参与检索。
            self.vector_store.delete_document(old_doc_id)
            self.sqlite_store.set_current_document(doc_id)

        detail = (
            f"已建立 {len(chunks)} 个带页码与章节信息的文本块，"
            f"保存为第 {version_number} 版。"
        )
        self.sqlite_store.log("文档入库", file_path.name, detail)
        return IngestionResult(file_path.name, "success", len(chunks), detail)

    def delete(self, doc_id: str) -> None:
        document = self.sqlite_store.get_document(doc_id)
        if not document:
            raise KeyError(doc_id)
        versions = self.sqlite_store.list_document_versions(doc_id)
        fallback = next((item for item in versions if item["doc_id"] != doc_id), None)
        if bool(document.get("is_current")) and fallback:
            self.activate(fallback["doc_id"])
        self.vector_store.delete_document(doc_id)
        self.sqlite_store.delete_document(doc_id)
        self._delete_stored_file(document.get("storage_path"))
        self.sqlite_store.log(
            "删除文档版本",
            document["file_name"],
            f"已删除第 {document.get('version_number', 1)} 版及其索引。",
        )

    def activate(self, doc_id: str) -> None:
        """把历史版本重新向量化并切换为当前检索版本。"""

        document = self.sqlite_store.get_document(doc_id)
        if not document:
            raise KeyError(doc_id)
        if bool(document.get("is_current")):
            return
        versions = self.sqlite_store.list_document_versions(doc_id)
        current = next((item for item in versions if bool(item.get("is_current"))), None)
        chunks = self.sqlite_store.get_document_chunks(doc_id)
        if not chunks:
            raise ValueError("目标版本没有可用文本块，无法设为当前版本。")
        embeddings = self.model.embed_documents([chunk.content for chunk in chunks])
        # 先恢复目标索引，确认成功后再移除旧版本，减少切换失败时的不可用窗口。
        self.vector_store.delete_document(doc_id)
        self.vector_store.add(chunks, embeddings)
        if current:
            self.vector_store.delete_document(current["doc_id"])
        self.sqlite_store.set_current_document(doc_id)
        self.sqlite_store.log(
            "切换文档版本",
            document["file_name"],
            f"第 {document.get('version_number', 1)} 版已设为当前检索版本。",
        )
