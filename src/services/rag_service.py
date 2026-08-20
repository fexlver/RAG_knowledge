"""问答主用例与依赖装配。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from time import perf_counter

from src.agent.orchestrator import RetrievalOrchestrator
from src.agent.planner import QueryPlanner
from src.config.settings import Settings
from src.domain.models import AnswerResult
from src.generation.composer import AnswerComposer
from src.ingestion.service import DocumentIngestionService
from src.models.credentials import CredentialStore, SystemCredentialStore
from src.models.generation import GenerationModelRegistry, TokenUsage
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
        generation_models: GenerationModelRegistry | None = None,
        history_message_limit: int = 20,
    ):
        self.sqlite_store = sqlite_store
        self.ingestion = ingestion
        self.orchestrator = orchestrator
        self.composer = composer
        self.model = model
        self.generation_models = generation_models
        self.history_message_limit = history_message_limit

    def new_session(self) -> str:
        session_id = uuid.uuid4().hex
        model_profile_id = (
            self.generation_models.default_profile_id()
            if self.generation_models
            else None
        )
        self.sqlite_store.create_session(session_id)
        if model_profile_id:
            self.sqlite_store.set_session_model(session_id, model_profile_id)
        return session_id

    def session_choices(self) -> list[tuple[str, str]]:
        return [
            (f"{item['title']} · {item['updated_at'][:16]}", item["session_id"])
            for item in self.sqlite_store.list_sessions()
        ]

    def load_session(self, session_id: str) -> list[dict]:
        messages = []
        for item in self.sqlite_store.get_messages(
            session_id, limit=self.history_message_limit
        ):
            profile = (
                self.sqlite_store.get_model_profile(item["model_profile_id"])
                if item.get("model_profile_id")
                else None
            )
            messages.append(
                {
                    "id": item["id"],
                    "role": item["role"],
                    "content": item["content"],
                    "trace": json.loads(item.get("trace_json") or "[]"),
                    "citations": json.loads(item.get("citations_json") or "[]"),
                    "model_profile_id": item.get("model_profile_id"),
                    "model_name": profile.get("display_name") if profile else None,
                    "usage": {
                        "input_tokens": item.get("input_tokens"),
                        "output_tokens": item.get("output_tokens"),
                        "total_tokens": item.get("total_tokens"),
                    },
                    "refused": bool(item.get("refused")),
                    "created_at": item.get("created_at"),
                }
            )
        return messages

    def delete_session(self, session_id: str) -> str:
        self.sqlite_store.delete_session(session_id)
        return self.new_session()

    def rename_session(self, session_id: str, title: str) -> None:
        normalized = title.strip().replace("\n", " ")[:80]
        if not normalized:
            raise ValueError("会话名称不能为空。")
        self.sqlite_store.rename_session(session_id, normalized)

    def set_session_model(self, session_id: str, profile_id: str) -> None:
        if not self.generation_models:
            raise RuntimeError("生成模型注册表未启用。")
        self.generation_models.adapter_for(profile_id)
        self.sqlite_store.set_session_model(session_id, profile_id)

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
            session_id,
            "assistant",
            result.answer,
            result.trace,
            citations=[asdict(item) for item in result.citations],
            refused=result.refused,
        )
        return result

    @staticmethod
    def _trace_event(
        stage: str,
        detail: str,
        duration_ms: int | None = None,
        score: float | None = None,
    ) -> dict:
        event = {
            "stage": stage,
            "status": "completed",
            "label": detail.split("：", 1)[0],
            "detail": detail,
            "duration_ms": duration_ms,
        }
        if score is not None:
            event["score"] = round(score, 4)
        return event

    def ask_stream(
        self, question: str, session_id: str, model_profile_id: str | None = None
    ) -> Iterator[dict]:
        """执行一次问答并产出适合 SSE 的结构化事件。"""

        if not self.generation_models:
            raise RuntimeError("生成模型注册表未启用。")
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")
        session = self.sqlite_store.get_session(session_id)
        if not session:
            raise KeyError(session_id)
        profile_id = (
            model_profile_id
            or session.get("model_profile_id")
            or self.generation_models.default_profile_id()
        )
        profile, _ = self.generation_models.adapter_for(profile_id)
        self.sqlite_store.set_session_model(session_id, profile_id)
        user_message_id = self.sqlite_store.save_message(
            session_id, "user", question, model_profile_id=profile_id
        )
        yield {
            "type": "message_start",
            "message_id": user_message_id,
            "model_profile_id": profile_id,
        }

        history = self.sqlite_store.get_messages(
            session_id, limit=self.history_message_limit
        )
        rewrite_started = perf_counter()
        # 刚写入的问题不应再次作为历史的一部分传入改写模型。
        rewritten = self.model.rewrite_query(question, history[:-1])
        trace_events: list[dict] = []
        rewrite_detail = (
            f"查询改写：{rewritten}"
            if rewritten != question
            else "查询改写：当前问题信息完整，无需结合历史对话改写。"
        )
        event = self._trace_event(
            "rewrite",
            rewrite_detail,
            int((perf_counter() - rewrite_started) * 1000),
        )
        trace_events.append(event)
        yield {"type": "trace", "data": event}

        retrieval_started = perf_counter()
        evidence, plan, _ = self.orchestrator.execute(rewritten)
        retrieval_duration = int((perf_counter() - retrieval_started) * 1000)
        retrieval_events = [
            self._trace_event("route", f"查询路由：{plan.mode}；{plan.reason}"),
            self._trace_event(
                "retrieval",
                f"混合召回：执行 {len(plan.subqueries)} 个子查询，融合 Milvus 向量召回与 SQLite FTS5 关键词召回。",
                retrieval_duration,
            ),
            self._trace_event(
                "rerank",
                f"二阶段重排：RRF 融合后由 Rerank 模型保留 {len(evidence)} 条高相关证据。",
            ),
        ]
        if plan.mode == "multi_step":
            retrieval_events.append(
                self._trace_event(
                    "fusion",
                    f"跨步骤融合：合并 {len(plan.subqueries)} 路检索结果并按证据去重。",
                )
            )
        for event in retrieval_events:
            trace_events.append(event)
            yield {"type": "trace", "data": event}

        draft = self.composer.prepare(question, evidence, [])
        top_score = evidence[0].final_score if evidence else 0.0
        confidence_detail = (
            f"置信控制：最高相关分 {top_score:.3f}，通过阈值 {self.composer.minimum_score:.3f}。"
            if not draft.refused_answer
            else f"置信控制：最高相关分 {top_score:.3f}，低于阈值 {self.composer.minimum_score:.3f}，执行拒答。"
        )
        confidence_event = self._trace_event(
            "confidence", confidence_detail, score=top_score
        )
        trace_events.append(confidence_event)
        yield {"type": "trace", "data": confidence_event}
        for citation in draft.citations:
            yield {"type": "citation", "data": asdict(citation)}

        usage: TokenUsage | None = None
        generated_parts: list[str] = []
        generation_started = perf_counter()
        if draft.refused_answer:
            generated_parts.append(draft.refused_answer)
            yield {"type": "text_delta", "data": draft.refused_answer}
            result = self.composer.finalize(draft.refused_answer, draft, refused=True)
        else:
            for delta in self.generation_models.stream_answer(
                profile_id, question, draft.contexts
            ):
                if delta.text:
                    generated_parts.append(delta.text)
                    yield {"type": "text_delta", "data": delta.text}
                if delta.usage:
                    usage = delta.usage
            result = self.composer.finalize("".join(generated_parts), draft)

        generation_event = self._trace_event(
            "generation",
            f"答案生成：使用 {len(result.citations)} 条去重证据，模型 {profile['display_name']}。",
            int((perf_counter() - generation_started) * 1000),
        )
        trace_events.append(generation_event)
        yield {"type": "trace", "data": generation_event}
        result.trace = trace_events
        result.model_profile_id = profile_id
        if usage:
            result.input_tokens = usage.input_tokens
            result.output_tokens = usage.output_tokens
            result.total_tokens = usage.total_tokens
        assistant_message_id = self.sqlite_store.save_message(
            session_id,
            "assistant",
            result.answer,
            trace_events,
            citations=[asdict(item) for item in result.citations],
            model_profile_id=profile_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            refused=result.refused,
        )
        usage_data = {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.total_tokens,
            "session_total_tokens": self.sqlite_store.session_token_total(session_id),
        }
        yield {"type": "usage", "data": usage_data}
        yield {
            "type": "done",
            "data": {
                "id": assistant_message_id,
                "role": "assistant",
                "content": result.answer,
                "trace": trace_events,
                "citations": [asdict(item) for item in result.citations],
                "model_profile_id": profile_id,
                "model_name": profile["display_name"],
                "usage": usage_data,
                "refused": result.refused,
            },
        }


def build_service(
    settings: Settings | None = None,
    credential_store: CredentialStore | None = None,
) -> FoodSafetyRAGService:
    """生产环境依赖装配入口。"""

    app_settings = settings or Settings.from_env()
    sqlite_store = SQLiteStore(app_settings.sqlite_path)
    model = QwenGateway(app_settings)
    generation_models = GenerationModelRegistry(
        sqlite_store, credential_store or SystemCredentialStore(), app_settings
    )
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
        generation_models,
        app_settings.history_message_limit,
    )
