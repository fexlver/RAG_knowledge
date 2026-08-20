"""问答主用例与依赖装配。"""

from __future__ import annotations

import uuid

from src.agent.orchestrator import RetrievalOrchestrator
from src.agent.planner import QueryPlanner
from src.config.settings import Settings
from src.domain.models import AnswerResult
from src.generation.composer import AnswerComposer
from src.ingestion.service import DocumentIngestionService
from src.models.qwen_gateway import QwenGateway
from src.retrieval.hybrid import HybridRetriever
from src.storage.milvus_store import MilvusDenseStore
from src.storage.sqlite_store import SQLiteStore


class FoodSafetyRAGService:
    def __init__(
        self,
        sqlite_store: SQLiteStore,
        ingestion: DocumentIngestionService,
        orchestrator: RetrievalOrchestrator,
        composer: AnswerComposer,
        model: QwenGateway,
        history_message_limit: int = 20,
    ):
        self.sqlite_store = sqlite_store
        self.ingestion = ingestion
        self.orchestrator = orchestrator
        self.composer = composer
        self.model = model
        self.history_message_limit = history_message_limit

    def new_session(self) -> str:
        session_id = uuid.uuid4().hex
        self.sqlite_store.create_session(session_id)
        return session_id

    def session_choices(self) -> list[tuple[str, str]]:
        return [
            (f"{item['title']} · {item['updated_at'][:16]}", item["session_id"])
            for item in self.sqlite_store.list_sessions()
        ]

    def load_session(self, session_id: str) -> list[dict]:
        return [
            {"role": item["role"], "content": item["content"]}
            for item in self.sqlite_store.get_messages(
                session_id, limit=self.history_message_limit
            )
        ]

    def delete_session(self, session_id: str) -> str:
        self.sqlite_store.delete_session(session_id)
        return self.new_session()

    def document_rows(self) -> list[list[str]]:
        return [
            [
                item["doc_id"],
                item["source"],
                item.get("standard_code", ""),
                item.get("document_type", ""),
                item.get("validity_status", ""),
                item["created_at"][:19],
            ]
            for item in self.sqlite_store.list_documents()
        ]

    def document_choices(self) -> list[tuple[str, str]]:
        return [
            (
                f"{item['source']} | {item.get('standard_code') or '无标准号'}",
                item["doc_id"],
            )
            for item in self.sqlite_store.list_documents()
        ]

    def ask(self, question: str, session_id: str) -> AnswerResult:
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")
        history = self.sqlite_store.get_messages(
            session_id, limit=self.history_message_limit
        )
        rewritten = self.model.rewrite_query(question, history)
        evidence, _, trace = self.orchestrator.execute(rewritten)
        if rewritten != question:
            trace.insert(0, f"历史问题改写：{rewritten}")
        result = self.composer.compose(question, evidence, trace)
        self.sqlite_store.save_message(session_id, "user", question)
        self.sqlite_store.save_message(
            session_id, "assistant", result.answer, result.trace
        )
        return result


def build_service(settings: Settings | None = None) -> FoodSafetyRAGService:
    """生产环境依赖装配入口。"""

    app_settings = settings or Settings.from_env()
    sqlite_store = SQLiteStore(app_settings.sqlite_path)
    model = QwenGateway(app_settings)
    vector_store = MilvusDenseStore(
        app_settings.milvus_uri,
        app_settings.milvus_collection,
        app_settings.embedding_dimension,
    )
    ingestion = DocumentIngestionService(
        sqlite_store, vector_store, model, app_settings
    )
    retriever = HybridRetriever(vector_store, sqlite_store, model, app_settings)
    orchestrator = RetrievalOrchestrator(
        QueryPlanner(app_settings.max_agent_steps), retriever, app_settings.rrf_k
    )
    composer = AnswerComposer(
        model, app_settings.rerank_min_score, app_settings.citation_limit
    )
    return FoodSafetyRAGService(
        sqlite_store,
        ingestion,
        orchestrator,
        composer,
        model,
        app_settings.history_message_limit,
    )
