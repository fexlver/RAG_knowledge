"""多路召回结果融合。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.domain.models import RetrievedChunk


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievedChunk]], rrf_k: int = 60, limit: int = 12
) -> list[RetrievedChunk]:
    """按 RRF 融合结果，不依赖不同检索器不可比较的原始分数。"""

    merged: dict[str, RetrievedChunk] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            chunk_id = item.chunk.chunk_id
            if chunk_id not in merged:
                merged[chunk_id] = replace(
                    item, fusion_score=0.0, routes=set(item.routes)
                )
            target = merged[chunk_id]
            target.fusion_score += 1.0 / (rrf_k + rank)
            target.routes.update(item.routes)
            if item.dense_score is not None:
                target.dense_score = item.dense_score
            if item.lexical_score is not None:
                target.lexical_score = item.lexical_score
    return sorted(merged.values(), key=lambda item: item.fusion_score, reverse=True)[
        :limit
    ]
