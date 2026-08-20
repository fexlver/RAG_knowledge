from types import SimpleNamespace

from src.agent.planner import QueryPlan
from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk
from src.generation.composer import AnswerComposer
from src.models.generation import GenerationDelta, TokenUsage
from src.services.rag_service import FoodSafetyRAGService
from src.storage.sqlite_store import SQLiteStore


class FakeModel:
    def rewrite_query(self, question, _history):
        return question

    def generate_answer(self, _question, _contexts):
        return ""


class FakeOrchestrator:
    def execute(self, _query):
        chunk = DocumentChunk(
            chunk_id="chunk-1",
            doc_id="doc-1",
            content="国家坚持多措并举、精准施策、科学管理、社会共治。",
            chunk_index=0,
            page_number=1,
            section="第三条",
            metadata=DocumentMetadata(source="反食品浪费法.pdf"),
            locator={"kind": "pdf", "page_number": 1},
        )
        return (
            [RetrievedChunk(chunk=chunk, rerank_score=0.91)],
            QueryPlan("direct", ("问题",), "普通事实查询，执行一次混合检索。"),
            [],
        )


class FakeGenerationRegistry:
    def default_profile_id(self):
        return "model-1"

    def adapter_for(self, _profile_id):
        return {"profile_id": "model-1", "display_name": "测试模型"}, object()

    def stream_answer(self, _profile_id, _question, _contexts):
        yield GenerationDelta("回答依据[证据1]，错误条款引用[16]。")
        yield GenerationDelta(usage=TokenUsage(10, 5, 15))


def test_rag_stream_emits_structured_stages_and_sanitized_answer(tmp_path):
    store = SQLiteStore(tmp_path / "rag.db")
    store.upsert_provider(
        {
            "provider_id": "provider-1",
            "name": "测试提供方",
            "provider_type": "openai_compatible",
            "base_url": "http://localhost/v1",
            "has_api_key": False,
        }
    )
    store.upsert_model_profile(
        {
            "profile_id": "model-1",
            "provider_id": "provider-1",
            "model_id": "test-model",
            "display_name": "测试模型",
            "enabled": True,
        }
    )
    store.create_session("session-1")
    store.set_session_model("session-1", "model-1")
    service = FoodSafetyRAGService(
        sqlite_store=store,
        ingestion=SimpleNamespace(),
        orchestrator=FakeOrchestrator(),
        composer=AnswerComposer(FakeModel(), minimum_score=0.2),
        model=FakeModel(),
        generation_models=FakeGenerationRegistry(),
    )

    events = list(service.ask_stream("问题", "session-1"))
    stages = [item["data"]["stage"] for item in events if item["type"] == "trace"]
    event_types = [item["type"] for item in events]
    done = next(item["data"] for item in events if item["type"] == "done")

    assert stages == [
        "rewrite",
        "route",
        "retrieval",
        "rerank",
        "confidence",
        "generation",
    ]
    assert event_types[-2:] == ["usage", "done"]
    assert done["usage"]["total_tokens"] == 15
    assert "[1]" in done["content"]
    assert "[16]" not in done["content"]
