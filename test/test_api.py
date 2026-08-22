import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.domain.models import DocumentChunk, DocumentMetadata
from src.ingestion.service import IngestionResult
from src.models.credentials import MemoryCredentialStore
from src.storage.sqlite_store import SQLiteStore


class FakeIngestion:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def delete(self, _doc_id):
        return None

    def ingest(self, path, duplicate_mode="skip", progress=None):
        from pathlib import Path

        self.calls.append((Path(path).name, duplicate_mode))
        if progress:
            progress("parsing", "正在解析文档（测试）")
        return IngestionResult(
            file_name=Path(path).name,
            status="success",
            chunk_count=3,
            detail="入库完成",
            parser="fake_parser",
            duration_seconds=0.01,
        )


class FakeOrchestrator:
    def __init__(self):
        self.config = {
            "retriever_ids": ["dense", "lexical"],
            "fusion_id": "rrf",
            "rerank_enabled": True,
        }

    def retrieval_settings(self):
        return {
            "config": self.config,
            "retrievers": [
                {
                    "plugin_id": "dense",
                    "label": "语义向量检索",
                    "description": "测试",
                    "category": "retriever",
                }
            ],
            "fusion_strategies": [],
            "postprocessors": [],
        }

    def configure_retrieval(self, value):
        self.config = value
        return self.retrieval_settings()


class FakeService:
    def __init__(self, store):
        self.sqlite_store = store
        self.ingestion = FakeIngestion()
        self.generation_models = None
        self.orchestrator = FakeOrchestrator()

    def new_session(self):
        self.sqlite_store.create_session("session-1")
        return "session-1"

    def rename_session(self, session_id, title):
        self.sqlite_store.rename_session(session_id, title)

    def set_session_model(self, session_id, profile_id):
        self.sqlite_store.set_session_model(session_id, profile_id)

    def load_session(self, session_id):
        return [
            {"id": item["id"], "role": item["role"], "content": item["content"]}
            for item in self.sqlite_store.get_messages(session_id)
        ]

    def ask_stream(self, question, session_id, _model_profile_id=None):
        yield {"type": "trace", "data": {"stage": "retrieval", "detail": question}}
        yield {"type": "text_delta", "data": "测试回答"}
        yield {"type": "done", "data": {"role": "assistant", "content": "测试回答"}}


def make_client(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    settings = SimpleNamespace(upload_dir=tmp_path / "uploads", project_root=tmp_path)
    return TestClient(
        create_app(FakeService(store), settings, MemoryCredentialStore())
    ), store


def test_session_crud_and_sse_event_order(tmp_path):
    client, _ = make_client(tmp_path)
    created = client.post("/api/sessions").json()
    session_id = created["session_id"]
    renamed = client.patch(
        f"/api/sessions/{session_id}", json={"title": "新名称"}
    ).json()
    response = client.post(
        f"/api/sessions/{session_id}/runs", json={"message": "问题"}
    )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert renamed["title"] == "新名称"
    assert [item["type"] for item in events] == ["trace", "text_delta", "done"]
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204


def test_provider_api_masks_api_key(tmp_path):
    client, store = make_client(tmp_path)
    response = client.post(
        "/api/providers",
        json={
            "name": "本地模型",
            "provider_type": "openai_compatible",
            "base_url": "http://127.0.0.1:8000/v1",
            "api_key": "plain-secret",
        },
    )

    assert response.status_code == 201
    assert "plain-secret" not in response.text
    assert "plain-secret" not in str(store.list_providers())


def test_retrieval_pipeline_configuration_api(tmp_path):
    client, _ = make_client(tmp_path)

    before = client.get("/api/retrieval/config").json()
    updated = client.patch(
        "/api/retrieval/config",
        json={
            "retriever_ids": ["dense"],
            "fusion_id": "rrf",
            "rerank_enabled": False,
        },
    ).json()

    assert before["config"]["retriever_ids"] == ["dense", "lexical"]
    assert updated["config"] == {
        "retriever_ids": ["dense"],
        "fusion_id": "rrf",
        "rerank_enabled": False,
    }


def test_document_preview_returns_small_structured_evidence_window(tmp_path):
    client, store = make_client(tmp_path)
    metadata = DocumentMetadata(source="法规.txt", content_hash="d" * 64)
    chunk = DocumentChunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        content="命中段落",
        chunk_index=0,
        page_number=None,
        section="第一章",
        metadata=metadata,
        locator={"element_ids": ["e2"], "heading_path": ["第一章"]},
    )
    upload_dir = tmp_path / "uploads" / "doc-1"
    upload_dir.mkdir(parents=True)
    (upload_dir / "法规.txt").write_text("原文", encoding="utf-8")
    (upload_dir / "layout.json").write_text(
        json.dumps(
            {
                "elements": [
                    {"element_id": "e1", "kind": "heading", "content": "第一章"},
                    {"element_id": "e2", "kind": "paragraph", "content": "命中段落"},
                    {"element_id": "e3", "kind": "paragraph", "content": "相邻段落"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store.save_document(
        "doc-1",
        metadata,
        [chunk],
        storage_path="doc-1/法规.txt",
        canonical_path="doc-1/canonical.md",
        layout_path="doc-1/layout.json",
    )

    response = client.get("/api/documents/doc-1/preview?chunk_id=chunk-1")

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence["degraded"] is False
    assert [item["content"] for item in evidence["elements"]] == [
        "第一章",
        "命中段落",
        "相邻段落",
    ]
    assert evidence["elements"][1]["matched"] is True


def test_upload_documents_runs_as_background_job(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.post(
        "/api/documents",
        params={"duplicate_mode": "skip"},
        files=[
            ("files", ("标准.txt", "第1章 总则\n\n内容".encode("utf-8"), "text/plain")),
            ("files", ("报表.docx", b"not supported", "application/octet-stream")),
        ],
    )

    assert response.status_code == 202
    job = response.json()
    assert job["job_id"]
    assert job["total_files"] == 2

    deadline = time.monotonic() + 5
    current = job
    while time.monotonic() < deadline and current["status"] != "done":
        time.sleep(0.05)
        current = client.get(f"/api/documents/upload-jobs/{job['job_id']}").json()
    assert current["status"] == "done"

    by_name = {item["name"]: item for item in current["files"]}
    assert by_name["标准.txt"]["stage"] == "success"
    assert by_name["标准.txt"]["stage_label"] == "完成"
    assert by_name["标准.txt"]["chunk_count"] == 3
    assert by_name["标准.txt"]["parser"] == "fake_parser"
    assert by_name["报表.docx"]["stage"] == "failed"
    assert by_name["报表.docx"]["detail"] == "仅支持 PDF/TXT。"
    assert current["finished_files"] == 2

    # 临时上传文件处理完即清理。
    leftovers = list((tmp_path / "uploads" / ".incoming").rglob("*"))
    assert not [item for item in leftovers if item.is_file()]

    unknown = client.get("/api/documents/upload-jobs/not-exist")
    assert unknown.status_code == 404
