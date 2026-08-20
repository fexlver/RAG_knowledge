"""Dense + FTS5 + RRF + Rerank 混合检索链路。"""

from __future__ import annotations

from typing import Protocol

from src.config.settings import Settings
from src.domain.models import RetrievedChunk
from src.retrieval.fusion import reciprocal_rank_fusion


class DenseStore(Protocol):
    def search(self, embedding: list[float], limit: int) -> list[RetrievedChunk]: ...


class LexicalStore(Protocol):
    def lexical_search(self, query: str, limit: int) -> list[RetrievedChunk]: ...


class RetrievalModel(Protocol):
    def embed_query(self, query: str) -> list[float]: ...

    def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]: ...


class HybridRetriever:
    """可替换依赖的检索器，便于离线单元测试。"""

    def __init__(
        self,
        dense_store: DenseStore,
        lexical_store: LexicalStore,
        model: RetrievalModel,
        settings: Settings,
    ):
        self.dense_store = dense_store
        self.lexical_store = lexical_store
        self.model = model
        self.settings = settings

    def retrieve(self, query: str) -> list[RetrievedChunk]:
        dense = self.dense_store.search(
            self.model.embed_query(query), self.settings.dense_top_k
        )
        lexical = self.lexical_store.lexical_search(query, self.settings.lexical_top_k)
        fused = reciprocal_rank_fusion(
            [dense, lexical], self.settings.rrf_k, self.settings.fusion_top_k
        )
        if not fused:
            return []
        ranking = self.model.rerank(
            query,
            [item.chunk.content for item in fused],
            self.settings.rerank_top_k,
        )
        reranked: list[RetrievedChunk] = []
        for index, score in ranking:
            if 0 <= index < len(fused):
                fused[index].rerank_score = score
                reranked.append(fused[index])
        return sorted(reranked, key=lambda item: item.final_score, reverse=True)
