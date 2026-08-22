"""食品安全 RAG 的 FastAPI 应用。"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.api.schemas import (
    ModelProfileInput,
    ProviderInput,
    RetrievalConfigInput,
    RunRequest,
    SessionUpdate,
)
from src.config.settings import Settings
from src.ingestion.upload_jobs import FileProgress, UploadJobManager
from src.models.credentials import CredentialStore, SystemCredentialStore
from src.services.rag_service import FoodSafetyRAGService, build_service


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _remove_temporary(directory: Path) -> None:
    try:
        for item in directory.iterdir():
            if item.is_file():
                item.unlink()
        directory.rmdir()
    except OSError:
        pass


def _public_provider(provider: dict) -> dict:
    return {
        "provider_id": provider["provider_id"],
        "name": provider["name"],
        "provider_type": provider["provider_type"],
        "base_url": provider["base_url"],
        "enabled": bool(provider["enabled"]),
        "has_api_key": bool(provider["has_api_key"]),
    }


def create_app(
    service: FoodSafetyRAGService | None = None,
    settings: Settings | None = None,
    credentials: CredentialStore | None = None,
) -> FastAPI:
    app_settings = settings or Settings.from_env()
    credential_store = credentials or SystemCredentialStore()
    rag_service = service or build_service(app_settings, credential_store)
    store = rag_service.sqlite_store
    app = FastAPI(title="食品安全知识库问答系统", version="0.3.0")
    app.state.rag_service = rag_service
    app.state.settings = app_settings
    app.state.credentials = credential_store
    app.state.upload_jobs = UploadJobManager()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return store.list_sessions()

    @app.post("/api/sessions", status_code=201)
    def create_session() -> dict:
        session_id = rag_service.new_session()
        return store.get_session(session_id) or {"session_id": session_id}

    @app.patch("/api/sessions/{session_id}")
    def update_session(session_id: str, payload: SessionUpdate) -> dict:
        try:
            if payload.title is not None:
                rag_service.rename_session(session_id, payload.title)
            if payload.model_profile_id is not None:
                rag_service.set_session_model(session_id, payload.model_profile_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="会话不存在。") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return store.get_session(session_id) or {}

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> None:
        if not store.get_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在。")
        store.delete_session(session_id)

    @app.get("/api/sessions/{session_id}/messages")
    def get_messages(session_id: str) -> dict:
        if not store.get_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在。")
        return {
            "messages": rag_service.load_session(session_id),
            "session_total_tokens": store.session_token_total(session_id),
        }

    @app.post("/api/sessions/{session_id}/runs")
    def run(session_id: str, payload: RunRequest) -> StreamingResponse:
        if not store.get_session(session_id):
            raise HTTPException(status_code=404, detail="会话不存在。")

        def events() -> Iterator[str]:
            try:
                for event in rag_service.ask_stream(
                    payload.message, session_id, payload.model_profile_id
                ):
                    yield _sse(event)
            except Exception as error:  # noqa: BLE001
                yield _sse({"type": "error", "data": {"message": str(error)}})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/providers")
    def list_providers() -> list[dict]:
        return [_public_provider(item) for item in store.list_providers()]

    @app.post("/api/providers", status_code=201)
    def save_provider(payload: ProviderInput) -> dict:
        provider_id = payload.provider_id or uuid.uuid4().hex
        existing = store.get_provider(provider_id)
        if payload.api_key:
            credential_store.set(provider_id, payload.api_key)
        has_api_key = bool(payload.api_key) or bool(
            existing and existing.get("has_api_key")
        )
        provider = {
            "provider_id": provider_id,
            "name": payload.name,
            "provider_type": payload.provider_type,
            "base_url": payload.base_url,
            "enabled": payload.enabled,
            "has_api_key": has_api_key,
        }
        store.upsert_provider(provider)
        return _public_provider(store.get_provider(provider_id) or provider)

    @app.get("/api/models")
    def list_models() -> list[dict]:
        return store.list_model_profiles()

    @app.post("/api/models", status_code=201)
    def save_model(payload: ModelProfileInput) -> dict:
        if not store.get_provider(payload.provider_id):
            raise HTTPException(status_code=422, detail="模型提供方不存在。")
        profile_id = payload.profile_id or uuid.uuid4().hex
        profile = {
            "profile_id": profile_id,
            "provider_id": payload.provider_id,
            "model_id": payload.model_id,
            "display_name": payload.display_name,
            "enabled": payload.enabled,
        }
        store.upsert_model_profile(profile)
        return store.get_model_profile(profile_id) or profile

    @app.post("/api/models/{profile_id}/test")
    def test_model(profile_id: str) -> dict:
        registry = rag_service.generation_models
        if not registry:
            raise HTTPException(status_code=503, detail="模型注册表不可用。")
        try:
            profile, adapter = registry.adapter_for(profile_id)
            text = "".join(
                delta.text
                for delta in adapter.stream(
                    [{"role": "user", "content": "只回复 OK"}],
                    str(profile["model_id"]),
                )
            )
            return {"ok": True, "response": text[:100]}
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/retrieval/config")
    def get_retrieval_config() -> dict:
        return rag_service.orchestrator.retrieval_settings()

    @app.patch("/api/retrieval/config")
    def update_retrieval_config(payload: RetrievalConfigInput) -> dict:
        try:
            return rag_service.orchestrator.configure_retrieval(payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/documents")
    def list_documents() -> list[dict]:
        return store.list_documents()

    @app.get("/api/operation-logs")
    def list_operation_logs(limit: int = 100) -> list[dict]:
        return store.list_logs(limit)

    @app.post("/api/documents", status_code=202)
    async def upload_documents(
        files: Annotated[list[UploadFile], File()], duplicate_mode: str = "skip"
    ) -> dict:
        if duplicate_mode not in {"skip", "overwrite"}:
            raise HTTPException(status_code=422, detail="同名策略参数错误。")
        allowed = {".pdf", ".txt"}
        incoming = app_settings.upload_dir / ".incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        entries: list[FileProgress] = []
        pairs: list[tuple[FileProgress, Path]] = []
        for upload in files:
            safe_name = Path(upload.filename or "document").name
            suffix = Path(safe_name).suffix.lower()
            if suffix not in allowed:
                entries.append(
                    FileProgress(
                        name=safe_name,
                        stage="failed",
                        detail="仅支持 PDF/TXT。",
                        chunk_count=0,
                        finished_at=time.time(),
                    )
                )
                await upload.close()
                continue
            temporary_directory = Path(tempfile.mkdtemp(dir=incoming))
            temporary_path = temporary_directory / safe_name
            progress = FileProgress(name=safe_name)
            try:
                with temporary_path.open("wb") as temporary:
                    while chunk := await upload.read(1024 * 1024):
                        temporary.write(chunk)
                entries.append(progress)
                pairs.append((progress, temporary_path))
            except Exception as error:  # noqa: BLE001 - 保存失败仅影响当前文件
                entries.append(
                    FileProgress(
                        name=safe_name,
                        stage="failed",
                        detail=f"保存上传文件失败：{error}",
                        chunk_count=0,
                        finished_at=time.time(),
                    )
                )
                _remove_temporary(temporary_directory)
            finally:
                await upload.close()
        job = app.state.upload_jobs.create_job(entries, duplicate_mode)
        app.state.upload_jobs.start(job, pairs, rag_service.ingestion)
        return job.to_dict()

    @app.get("/api/documents/upload-jobs/{job_id}")
    def get_upload_job(job_id: str) -> dict:
        job = app.state.upload_jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="上传任务不存在或已过期。")
        return job.to_dict()

    @app.delete("/api/documents/{doc_id}", status_code=204)
    def delete_document(doc_id: str) -> None:
        if not store.get_document(doc_id):
            raise HTTPException(status_code=404, detail="文档不存在。")
        rag_service.ingestion.delete(doc_id)

    @app.get("/api/documents/{doc_id}/versions")
    def list_document_versions(doc_id: str) -> list[dict]:
        if not store.get_document(doc_id):
            raise HTTPException(status_code=404, detail="文档不存在。")
        return store.list_document_versions(doc_id)

    @app.post("/api/documents/{doc_id}/activate")
    def activate_document_version(doc_id: str) -> dict:
        if not store.get_document(doc_id):
            raise HTTPException(status_code=404, detail="文档版本不存在。")
        try:
            rag_service.ingestion.activate(doc_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return store.get_document(doc_id) or {}

    def resolve_document_path(doc_id: str) -> tuple[dict, Path]:
        document = store.get_document(doc_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在。")
        storage_path = document.get("storage_path")
        if not storage_path:
            raise HTTPException(
                status_code=409, detail="旧文档未保留原文件，请重新上传后预览。"
            )
        root = app_settings.upload_dir.resolve()
        file_path = (root / storage_path).resolve()
        if root not in file_path.parents or not file_path.is_file():
            raise HTTPException(status_code=404, detail="原文件不存在。")
        return document, file_path

    def evidence_window(document: dict, chunk: dict, radius: int = 1) -> dict:
        """只返回命中结构单元及其邻居，避免预览接口回传整篇文档。"""

        fallback = {
            "heading_path": chunk["locator"].get("heading_path", []),
            "elements": [
                {
                    "kind": "paragraph",
                    "content": chunk["content"],
                    "matched": True,
                }
            ],
            "degraded": True,
        }
        layout_path = document.get("layout_path")
        if not layout_path:
            return fallback
        root = app_settings.upload_dir.resolve()
        path = (root / layout_path).resolve()
        if root not in path.parents or not path.is_file():
            return fallback
        try:
            layout = json.loads(path.read_text(encoding="utf-8"))
            elements = layout.get("elements", [])
            wanted = set(chunk["locator"].get("element_ids", []))
            matched_indexes = [
                index
                for index, element in enumerate(elements)
                if element.get("element_id") in wanted
            ]
            if not matched_indexes:
                return fallback
            start = max(0, min(matched_indexes) - radius)
            end = min(len(elements), max(matched_indexes) + radius + 1)
            return {
                "heading_path": elements[min(matched_indexes)].get("heading_path", []),
                "elements": [
                    {
                        "element_id": element.get("element_id"),
                        "kind": element.get("kind"),
                        "content": element.get("content", ""),
                        "page_number": element.get("page_number"),
                        "matched": element.get("element_id") in wanted,
                    }
                    for element in elements[start:end]
                ],
                "degraded": False,
            }
        except (OSError, ValueError, TypeError):
            return fallback

    @app.get("/api/documents/{doc_id}/file")
    def document_file(doc_id: str) -> FileResponse:
        document, file_path = resolve_document_path(doc_id)
        return FileResponse(
            file_path,
            media_type=document.get("mime_type") or "application/octet-stream",
            filename=document["file_name"],
            content_disposition_type="inline",
        )

    @app.get("/api/documents/{doc_id}/preview")
    def document_preview(doc_id: str, chunk_id: str) -> dict:
        document, _ = resolve_document_path(doc_id)
        chunk = store.get_chunk(chunk_id, doc_id)
        if not chunk:
            raise HTTPException(status_code=404, detail="引用文本块不存在。")
        return {
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "file_name": document["file_name"],
            "mime_type": document.get("mime_type"),
            "file_url": f"/api/documents/{doc_id}/file",
            "excerpt": chunk["content"],
            "locator": chunk["locator"],
            "evidence": evidence_window(document, chunk),
        }

    @app.exception_handler(KeyError)
    async def key_error_handler(_request, _error: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "资源不存在。"})

    frontend_dist = app_settings.project_root / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app
