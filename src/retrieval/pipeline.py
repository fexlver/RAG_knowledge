"""可插拔、可组合的检索流水线。

检索源、融合策略和后处理器通过注册表解耦。后续接入多模态检索、
知识图谱检索时，只需实现对应协议并注册，不需要修改问答编排流程。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from time import perf_counter
from typing import Protocol

from src.config.settings import Settings
from src.domain.models import RetrievedChunk
from src.retrieval.fusion import reciprocal_rank_fusion


@dataclass(frozen=True, slots=True)
class RetrievalPluginDescriptor:
    """供 API 和设置页展示的检索组件元数据。"""

    plugin_id: str
    label: str
    description: str
    category: str


@dataclass(frozen=True, slots=True)
class RetrievalPipelineConfig:
    """知识库全局检索组合配置。"""

    retriever_ids: tuple[str, ...] = ("dense", "lexical")
    fusion_id: str = "rrf"
    rerank_enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict | None) -> RetrievalPipelineConfig:
        if not value:
            return cls()
        raw_retrievers = value.get("retriever_ids")
        retriever_ids = (
            cls().retriever_ids
            if raw_retrievers is None
            else tuple(str(item) for item in raw_retrievers if str(item).strip())
        )
        return cls(
            retriever_ids=retriever_ids,
            fusion_id=str(value.get("fusion_id") or "rrf"),
            rerank_enabled=bool(value.get("rerank_enabled", True)),
        )

    def to_dict(self) -> dict:
        return {
            "retriever_ids": list(self.retriever_ids),
            "fusion_id": self.fusion_id,
            "rerank_enabled": self.rerank_enabled,
        }


@dataclass(frozen=True, slots=True)
class RetrievalProgress:
    """检索流水线产生的可观测进度，不包含模型隐藏推理。"""

    event_id: str
    stage: str
    status: str
    label: str
    detail: str
    duration_ms: int | None = None


@dataclass(slots=True)
class RetrievalStreamItem:
    """流式检索的单个输出，最终一项携带检索结果。"""

    progress: RetrievalProgress | None = None
    results: list[RetrievedChunk] | None = None


class RetrieverPlugin(Protocol):
    descriptor: RetrievalPluginDescriptor

    def retrieve(self, query: str, limit: int) -> list[RetrievedChunk]: ...


class FusionPlugin(Protocol):
    descriptor: RetrievalPluginDescriptor

    def fuse(
        self,
        rankings: list[list[RetrievedChunk]],
        limit: int,
    ) -> list[RetrievedChunk]: ...


class RerankPlugin(Protocol):
    descriptor: RetrievalPluginDescriptor

    def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        limit: int,
    ) -> list[RetrievedChunk]: ...


class DenseStore(Protocol):
    def search(self, embedding: list[float], limit: int) -> list[RetrievedChunk]: ...


class LexicalStore(Protocol):
    def lexical_search(self, query: str, limit: int) -> list[RetrievedChunk]: ...


class RetrievalModel(Protocol):
    def embed_query(self, query: str) -> list[float]: ...

    def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]: ...


class DenseRetrieverPlugin:
    descriptor = RetrievalPluginDescriptor(
        "dense",
        "语义向量检索",
        "使用 Embedding 与 Milvus 召回语义相近的文本块。",
        "retriever",
    )

    def __init__(self, store: DenseStore, model: RetrievalModel):
        self.store = store
        self.model = model

    def retrieve(self, query: str, limit: int) -> list[RetrievedChunk]:
        return self.store.search(self.model.embed_query(query), limit)


class LexicalRetrieverPlugin:
    descriptor = RetrievalPluginDescriptor(
        "lexical",
        "关键词检索",
        "使用 SQLite FTS5 召回标准号、条款号和精确关键词。",
        "retriever",
    )

    def __init__(self, store: LexicalStore):
        self.store = store

    def retrieve(self, query: str, limit: int) -> list[RetrievedChunk]:
        return self.store.lexical_search(query, limit)


class ReciprocalRankFusionPlugin:
    descriptor = RetrievalPluginDescriptor(
        "rrf",
        "RRF 排名融合",
        "融合不同检索源的名次，避免直接比较不同尺度的原始分数。",
        "fusion",
    )

    def __init__(self, rrf_k: int):
        self.rrf_k = rrf_k

    def fuse(
        self,
        rankings: list[list[RetrievedChunk]],
        limit: int,
    ) -> list[RetrievedChunk]:
        return reciprocal_rank_fusion(rankings, self.rrf_k, limit)


class ModelRerankPlugin:
    descriptor = RetrievalPluginDescriptor(
        "model_rerank",
        "模型二阶段重排",
        "使用 Rerank 模型重新判断问题与候选片段的相关性。",
        "postprocessor",
    )

    def __init__(self, model: RetrievalModel):
        self.model = model

    def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        limit: int,
    ) -> list[RetrievedChunk]:
        ranking = self.model.rerank(
            query,
            [item.chunk.content for item in candidates],
            limit,
        )
        reranked: list[RetrievedChunk] = []
        for index, score in ranking:
            if 0 <= index < len(candidates):
                candidates[index].rerank_score = score
                reranked.append(candidates[index])
        return sorted(reranked, key=lambda item: item.final_score, reverse=True)


class RetrievalPluginRegistry:
    """统一注册检索源、融合策略和重排阶段。"""

    def __init__(self):
        self.retrievers: dict[str, RetrieverPlugin] = {}
        self.fusions: dict[str, FusionPlugin] = {}
        self.rerankers: dict[str, RerankPlugin] = {}

    def register_retriever(self, plugin: RetrieverPlugin) -> None:
        self.retrievers[plugin.descriptor.plugin_id] = plugin

    def register_fusion(self, plugin: FusionPlugin) -> None:
        self.fusions[plugin.descriptor.plugin_id] = plugin

    def register_reranker(self, plugin: RerankPlugin) -> None:
        self.rerankers[plugin.descriptor.plugin_id] = plugin

    def descriptors(self, category: str) -> list[dict]:
        mapping = {
            "retriever": self.retrievers,
            "fusion": self.fusions,
            "postprocessor": self.rerankers,
        }
        return [asdict(plugin.descriptor) for plugin in mapping[category].values()]


class ComposableRetrievalPipeline:
    """按照持久化配置动态组合多个检索插件。"""

    def __init__(
        self,
        registry: RetrievalPluginRegistry,
        settings: Settings,
        config_loader: Callable[[], dict | None] | None = None,
        config_saver: Callable[[dict], None] | None = None,
    ):
        self.registry = registry
        self.settings = settings
        self.config_loader = config_loader
        self.config_saver = config_saver

    def get_config(self) -> RetrievalPipelineConfig:
        raw = self.config_loader() if self.config_loader else None
        config = RetrievalPipelineConfig.from_dict(raw)
        self._validate(config)
        return config

    def configure(self, config: RetrievalPipelineConfig) -> dict:
        self._validate(config)
        if self.config_saver:
            self.config_saver(config.to_dict())
        return self.describe(config)

    def describe(self, config: RetrievalPipelineConfig | None = None) -> dict:
        active = config or self.get_config()
        return {
            "config": active.to_dict(),
            "retrievers": self.registry.descriptors("retriever"),
            "fusion_strategies": self.registry.descriptors("fusion"),
            "postprocessors": self.registry.descriptors("postprocessor"),
        }

    def _validate(self, config: RetrievalPipelineConfig) -> None:
        if not config.retriever_ids:
            raise ValueError("至少启用一种检索方式。")
        unknown = set(config.retriever_ids) - set(self.registry.retrievers)
        if unknown:
            raise ValueError(f"未知检索插件：{', '.join(sorted(unknown))}")
        if config.fusion_id not in self.registry.fusions:
            raise ValueError(f"未知融合策略：{config.fusion_id}")
        if config.rerank_enabled and "model_rerank" not in self.registry.rerankers:
            raise ValueError("模型重排插件尚未注册。")

    @staticmethod
    def _prepare_single_ranking(
        ranking: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """单路检索无需融合，但统一写入可供置信控制使用的分数。"""

        prepared: list[RetrievedChunk] = []
        for rank, item in enumerate(ranking, start=1):
            raw_score = item.dense_score
            if raw_score is None:
                raw_score = item.lexical_score
            score = max(float(raw_score or 0.0), 1.0 / rank)
            prepared.append(replace(item, fusion_score=score, routes=set(item.routes)))
        return prepared

    @staticmethod
    def _normalize_fusion_scores(
        ranking: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """把融合名次分数归一化，保证关闭重排时仍可进行置信判断。"""

        maximum = max((item.fusion_score for item in ranking), default=0.0)
        if maximum <= 0:
            return ranking
        for item in ranking:
            item.fusion_score /= maximum
        return ranking

    def retrieve(self, query: str) -> list[RetrievedChunk]:
        results: list[RetrievedChunk] = []
        for item in self.retrieve_stream(query):
            if item.results is not None:
                results = item.results
        return results

    def retrieve_stream(
        self,
        query: str,
        *,
        event_prefix: str = "",
    ) -> Iterator[RetrievalStreamItem]:
        config = self.get_config()
        rankings: list[list[RetrievedChunk]] = []
        for plugin_id in config.retriever_ids:
            plugin = self.registry.retrievers[plugin_id]
            event_id = f"{event_prefix}retriever:{plugin_id}"
            yield RetrievalStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "retrieval",
                    "running",
                    plugin.descriptor.label,
                    f"正在通过{plugin.descriptor.label}召回候选文本块…",
                )
            )
            started = perf_counter()
            limit = (
                self.settings.dense_top_k
                if plugin_id == "dense"
                else self.settings.lexical_top_k
            )
            ranking = plugin.retrieve(query, limit)
            rankings.append(ranking)
            yield RetrievalStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "retrieval",
                    "completed",
                    plugin.descriptor.label,
                    f"{plugin.descriptor.label}完成，召回 {len(ranking)} 个候选文本块。",
                    int((perf_counter() - started) * 1000),
                )
            )

        if len(rankings) == 1:
            candidates = self._prepare_single_ranking(rankings[0])
        else:
            fusion = self.registry.fusions[config.fusion_id]
            event_id = f"{event_prefix}fusion:{config.fusion_id}"
            yield RetrievalStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "fusion",
                    "running",
                    fusion.descriptor.label,
                    f"正在融合 {len(rankings)} 路召回结果…",
                )
            )
            started = perf_counter()
            candidates = fusion.fuse(rankings, self.settings.fusion_top_k)
            candidates = self._normalize_fusion_scores(candidates)
            yield RetrievalStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "fusion",
                    "completed",
                    fusion.descriptor.label,
                    f"{fusion.descriptor.label}完成，保留 {len(candidates)} 个融合候选。",
                    int((perf_counter() - started) * 1000),
                )
            )

        if config.rerank_enabled and candidates:
            reranker = self.registry.rerankers["model_rerank"]
            event_id = f"{event_prefix}rerank:model_rerank"
            yield RetrievalStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "rerank",
                    "running",
                    reranker.descriptor.label,
                    "正在对融合候选执行二阶段相关性重排…",
                )
            )
            started = perf_counter()
            candidates = reranker.rerank(
                query, candidates, self.settings.rerank_top_k
            )
            yield RetrievalStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "rerank",
                    "completed",
                    reranker.descriptor.label,
                    f"模型重排完成，保留 {len(candidates)} 条高相关证据。",
                    int((perf_counter() - started) * 1000),
                )
            )

        yield RetrievalStreamItem(results=candidates)


def build_retrieval_registry(
    dense_store: DenseStore,
    lexical_store: LexicalStore,
    model: RetrievalModel,
    settings: Settings,
) -> RetrievalPluginRegistry:
    """构建默认插件注册表，调用方可继续注册多模态或图检索插件。"""

    registry = RetrievalPluginRegistry()
    registry.register_retriever(DenseRetrieverPlugin(dense_store, model))
    registry.register_retriever(LexicalRetrieverPlugin(lexical_store))
    registry.register_fusion(ReciprocalRankFusionPlugin(settings.rrf_k))
    registry.register_reranker(ModelRerankPlugin(model))
    return registry
