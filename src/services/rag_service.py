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
        *,
        event_id: str | None = None,
        status: str = "completed",
        label: str | None = None,
    ) -> dict:
        event = {
            "event_id": event_id or stage,
            "stage": stage,
            "status": status,
            "label": label or detail.split("：", 1)[0],
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
        trace_events: dict[str, dict] = {}

        def remember(event: dict) -> dict:
            """相同 event_id 的运行态和完成态在持久化时合并为一个步骤。"""

            trace_events[str(event["event_id"])] = event
            return event

        rewrite_started = perf_counter()
        event = self._trace_event(
            "rewrite",
            "正在结合当前问题与历史对话生成检索查询…",
            event_id="rewrite",
            status="running",
            label="查询改写",
        )
        remember(event)
        yield {"type": "trace", "data": event}
        # 刚写入的问题不应再次作为历史的一部分传入改写模型。
        rewritten = self.model.rewrite_query(question, history[:-1])
        rewrite_detail = (
            f"查询改写：{rewritten}"
            if rewritten != question
            else "查询改写：当前问题信息完整，无需结合历史对话改写。"
        )
        event = self._trace_event(
            "rewrite",
            rewrite_detail,
            int((perf_counter() - rewrite_started) * 1000),
            event_id="rewrite",
        )
        remember(event)
        yield {"type": "trace", "data": event}

        evidence = []
        if hasattr(self.orchestrator, "execute_stream"):
            for update in self.orchestrator.execute_stream(rewritten):
                if update.progress:
                    progress = update.progress
                    event = self._trace_event(
                        progress.stage,
                        progress.detail,
                        progress.duration_ms,
                        event_id=progress.event_id,
                        status=progress.status,
                        label=progress.label,
                    )
                    remember(event)
                    yield {"type": "trace", "data": event}
                if update.evidence is not None:
                    evidence = update.evidence
        else:
            # 兼容只实现 execute 的自定义编排器和离线测试桩。
            event = self._trace_event(
                "retrieval",
                "正在执行已配置的检索流水线…",
                event_id="retrieval",
                status="running",
                label="知识库检索",
            )
            remember(event)
            yield {"type": "trace", "data": event}
            retrieval_started = perf_counter()
            evidence, _plan, _ = self.orchestrator.execute(rewritten)
            event = self._trace_event(
                "retrieval",
                f"知识库检索完成，获得 {len(evidence)} 条候选证据。",
                int((perf_counter() - retrieval_started) * 1000),
                event_id="retrieval",
                label="知识库检索",
            )
            remember(event)
            yield {"type": "trace", "data": event}

        draft = self.composer.prepare(question, evidence, [])
        top_score = evidence[0].final_score if evidence else 0.0
        confidence_detail = (
            f"置信控制：最高相关分 {top_score:.3f}，通过阈值 {self.composer.minimum_score:.3f}。"
            if not draft.refused_answer
            else f"置信控制：最高相关分 {top_score:.3f}，低于阈值 {self.composer.minimum_score:.3f}，执行拒答。"
        )
        confidence_event = self._trace_event(
            "confidence", confidence_detail, score=top_score, event_id="confidence"
        )
        remember(confidence_event)
        yield {"type": "trace", "data": confidence_event}
        for citation in draft.citations:
            yield {"type": "citation", "data": asdict(citation)}

        usage: TokenUsage | None = None
        generated_parts: list[str] = []
        generation_started = perf_counter()
        generation_event = self._trace_event(
            "generation",
            f"正在使用 {profile['display_name']} 基于检索证据生成回答…",
            event_id="generation",
            status="running",
            label="答案生成",
        )
        remember(generation_event)
        yield {"type": "trace", "data": generation_event}
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
            event_id="generation",
        )
        remember(generation_event)
        yield {"type": "trace", "data": generation_event}
        persisted_trace = list(trace_events.values())
        result.trace = persisted_trace
        result.model_profile_id = profile_id
        if usage:
            result.input_tokens = usage.input_tokens
            result.output_tokens = usage.output_tokens
            result.total_tokens = usage.total_tokens
        assistant_message_id = self.sqlite_store.save_message(
            session_id,
            "assistant",
            result.answer,
            persisted_trace,
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
                "trace": persisted_trace,
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
    retriever = HybridRetriever(
        vector_store,
        sqlite_store,
        model,
        app_settings,
        config_loader=lambda: sqlite_store.get_setting("retrieval_pipeline"),
        config_saver=lambda value: sqlite_store.set_setting(
            "retrieval_pipeline", value
        ),
    )
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
